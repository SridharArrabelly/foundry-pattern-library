"""Negative-path, credential-separation, and replay tests for Pattern 13."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "13-human-approval"))


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store_module = load_module(
    "human_approval_store_test", "13-human-approval/approval_store.py"
)
demo_module = load_module(
    "human_approval_demo_test", "13-human-approval/run_approval_demo.py"
)


class Clock:
    def __init__(self, value=1_800_000_000):
        self.value = value

    def __call__(self):
        return self.value


class HumanApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        nonces = iter(("nonce-one", "nonce-two", "nonce-three"))
        self.store = store_module.ApprovalStore(
            Path(self.temp.name) / "approval.sqlite3",
            now=self.clock,
            nonce_factory=lambda: next(nonces),
        )

    def arguments(self, request_id="CRQ-1003"):
        return self.store.get_change_request(request_id)["schedule_arguments"]

    def pending_payload(self, request_id="CRQ-1003", approval_id="approval-1"):
        arguments = self.arguments(request_id)
        return {
            "pending_approval_id": arguments["pending_approval_id"],
            "approval_request_id": approval_id,
            "server_label": store_module.SERVER_LABEL,
            "tool_name": store_module.WRITE_TOOL,
            "arguments": arguments,
        }

    def register(self, request_id="CRQ-1003", approval_id="approval-1"):
        return self.store.register_pending(
            self.pending_payload(request_id, approval_id)
        )

    def envelope(
        self,
        *,
        request_id="CRQ-1003",
        decision_id="decision-1",
        approval_id="approval-1",
        approve=True,
        arguments=None,
    ):
        arguments = arguments or self.arguments(request_id)
        return {
            "type": "mcp_approval_response",
            "id": decision_id,
            "approval_request_id": approval_id,
            "pending_approval_id": arguments["pending_approval_id"],
            "approve": approve,
            "reason": "reviewed",
            "server_label": store_module.SERVER_LABEL,
            "tool_name": store_module.WRITE_TOOL,
            "arguments": arguments,
        }

    def approve(self, request_id="CRQ-1003"):
        self.register(request_id)
        return self.store.record_decision(self.envelope(request_id=request_id))

    def test_selective_policy_never_approves_read_and_always_approves_write(self):
        tool = dict(demo_module.approval_tool("connection-id"))
        self.assertEqual(
            tool["require_approval"]["never"]["tool_names"], ["get_change_request"]
        )
        self.assertEqual(
            tool["require_approval"]["always"]["tool_names"], ["schedule_change"]
        )

    def test_demo_windows_are_generated_in_the_future(self):
        for request_id in ("CRQ-1001", "CRQ-1002", "CRQ-1003"):
            scheduled = self.arguments(request_id)["scheduled_for"]
            self.assertGreater(store_module.parse_utc_epoch(scheduled), self.clock.value)

    def test_tool_and_operator_credentials_are_strictly_separated(self):
        database = str(Path(self.temp.name) / "server.sqlite3")
        with patch.dict(
            os.environ,
            {
                "APPROVAL_DB_PATH": database,
                "MCP_TOOL_API_KEY": "tool-only",
                "MCP_OPERATOR_API_KEY": "operator-only",
            },
        ):
            server = load_module(
                "human_approval_server_auth_test",
                "13-human-approval/mcp_server.py",
            )
        self.assertTrue(
            server.is_authorized("/mcp", {"x-mcp-api-key": "tool-only"})
        )
        self.assertFalse(
            server.is_authorized(
                "/decisions", {"x-mcp-api-key": "tool-only"}
            )
        )
        self.assertTrue(
            server.is_authorized(
                "/decisions", {"x-operator-api-key": "operator-only"}
            )
        )
        self.assertFalse(
            server.is_authorized(
                "/mcp", {"x-operator-api-key": "operator-only"}
            )
        )

    def test_reject_records_decision_and_never_schedules(self):
        self.register()
        result = self.store.record_decision(self.envelope(approve=False))
        self.assertFalse(result.approve)
        with self.assertRaises(store_module.MissingApproval):
            self.store.schedule_change(self.arguments())
        audit = self.store.audit("CRQ-1003")
        self.assertEqual(audit["status"], "rejected")
        self.assertEqual(audit["pending_approval"]["status"], "rejected")
        self.assertEqual(audit["side_effects"], [])

    def test_approve_schedules_exactly_once_with_correlated_ids(self):
        decision = self.approve()
        first = self.store.schedule_change(self.arguments())
        replay = self.store.schedule_change(self.arguments())
        audit = self.store.audit("CRQ-1003")
        self.assertEqual(first["decision_id"], decision.decision_id)
        self.assertEqual(first["approval_request_id"], decision.approval_request_id)
        self.assertEqual(first["pending_approval_id"], decision.pending_approval_id)
        self.assertEqual(replay["side_effect_id"], first["side_effect_id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(audit["pending_approval"]["status"], "consumed")
        self.assertEqual(len(audit["side_effects"]), 1)

    def test_invented_or_rebound_pending_ids_are_rejected(self):
        invented = self.pending_payload()
        invented["pending_approval_id"] = "pending-invented"
        invented["arguments"] = {
            **invented["arguments"],
            "pending_approval_id": "pending-invented",
        }
        with self.assertRaises(store_module.MissingApproval):
            self.store.register_pending(invented)

        registered = self.pending_payload()
        self.store.register_pending(registered)
        rebound = {**registered, "approval_request_id": "approval-other"}
        with self.assertRaises(store_module.ReplayConflict):
            self.store.register_pending(rebound)

    def test_changed_arguments_and_nonce_reuse_cannot_authorize_another_effect(self):
        self.approve()
        first = self.store.schedule_change(self.arguments())
        changed = {**self.arguments(), "reason": "different"}
        with self.assertRaises(store_module.MismatchedApproval):
            self.store.schedule_change(changed)

        other = self.arguments("CRQ-1002")
        stolen_nonce = {
            **other,
            "pending_approval_id": self.arguments()["pending_approval_id"],
        }
        with self.assertRaises(store_module.MismatchedApproval):
            self.store.schedule_change(stolen_nonce)
        self.assertEqual(len(self.store.audit("CRQ-1003")["side_effects"]), 1)
        self.assertEqual(
            self.store.audit("CRQ-1003")["side_effects"][0]["side_effect_id"],
            first["side_effect_id"],
        )

    def test_duplicate_decision_is_idempotent_but_changed_replay_fails(self):
        self.register()
        first = self.store.record_decision(self.envelope())
        duplicate = self.store.record_decision(self.envelope())
        self.assertFalse(first.duplicate)
        self.assertTrue(duplicate.duplicate)
        changed = self.envelope(approve=False)
        with self.assertRaises(store_module.ReplayConflict):
            self.store.record_decision(changed)

    def test_missing_malformed_mismatched_and_stale_approvals_fail_closed(self):
        with self.assertRaises(store_module.MissingApproval):
            self.store.schedule_change(self.arguments())

        malformed = self.envelope()
        malformed.pop("pending_approval_id")
        with self.assertRaises(store_module.MalformedApproval):
            self.store.record_decision(malformed)

        self.register()
        mismatched = self.envelope()
        mismatched["arguments"] = {**self.arguments(), "reason": "different"}
        with self.assertRaises(store_module.MismatchedApproval):
            self.store.record_decision(mismatched)

        second_clock = Clock()
        nonces = iter(("fresh-one", "fresh-two", "fresh-three"))
        second = store_module.ApprovalStore(
            Path(self.temp.name) / "second.sqlite3",
            now=second_clock,
            nonce_factory=lambda: next(nonces),
        )
        pending = second.get_change_request("CRQ-1003")["schedule_arguments"]
        second.register_pending(
            {
                "pending_approval_id": pending["pending_approval_id"],
                "approval_request_id": "approval-stale",
                "server_label": store_module.SERVER_LABEL,
                "tool_name": store_module.WRITE_TOOL,
                "arguments": pending,
            }
        )
        second.record_decision(
            {
                "type": "mcp_approval_response",
                "id": "decision-stale",
                "approval_request_id": "approval-stale",
                "pending_approval_id": pending["pending_approval_id"],
                "approve": True,
                "reason": "reviewed",
                "server_label": store_module.SERVER_LABEL,
                "tool_name": store_module.WRITE_TOOL,
                "arguments": pending,
            }
        )
        second_clock.value += store_module.DECISION_TTL_SECONDS + 1
        with self.assertRaises(store_module.StaleApproval):
            second.schedule_change(pending)

    def test_past_schedule_is_rejected_before_decision_and_execution(self):
        self.register()
        self.clock.value = (
            store_module.parse_utc_epoch(self.arguments()["scheduled_for"]) + 1
        )
        with self.assertRaises(store_module.StaleApproval):
            self.store.record_decision(self.envelope())

        short_clock = Clock()
        short_requests = (
            ("CRQ-1001", "First", 60, "First reason."),
            ("CRQ-1002", "Second", 120, "Second reason."),
            ("CRQ-1003", "Third", 180, "Third reason."),
        )
        nonces = iter(("short-one", "short-two", "short-three"))
        with patch.object(store_module, "DEMO_REQUESTS", short_requests):
            short = store_module.ApprovalStore(
                Path(self.temp.name) / "short.sqlite3",
                now=short_clock,
                nonce_factory=lambda: next(nonces),
            )
        arguments = short.get_change_request("CRQ-1003")["schedule_arguments"]
        short.register_pending(
            {
                "pending_approval_id": arguments["pending_approval_id"],
                "approval_request_id": "approval-short",
                "server_label": store_module.SERVER_LABEL,
                "tool_name": store_module.WRITE_TOOL,
                "arguments": arguments,
            }
        )
        short.record_decision(
            {
                "type": "mcp_approval_response",
                "id": "decision-short",
                "approval_request_id": "approval-short",
                "pending_approval_id": arguments["pending_approval_id"],
                "approve": True,
                "reason": "reviewed",
                "server_label": store_module.SERVER_LABEL,
                "tool_name": store_module.WRITE_TOOL,
                "arguments": arguments,
            }
        )
        short_clock.value += 181
        with self.assertRaises(store_module.StaleApproval):
            short.schedule_change(arguments)

    def test_malformed_or_mismatched_foundry_item_is_rejected(self):
        valid = SimpleNamespace(
            id="approval-1",
            arguments=store_module.canonical_json(self.arguments()),
            name="schedule_change",
            server_label=store_module.SERVER_LABEL,
            type="mcp_approval_request",
        )
        arguments, canonical = demo_module.normalize_approval_item(valid, "CRQ-1003")
        self.assertEqual(arguments, self.arguments())
        self.assertEqual(canonical, store_module.canonical_json(self.arguments()))
        with self.assertRaisesRegex(RuntimeError, "wrong change request"):
            demo_module.normalize_approval_item(valid, "CRQ-1002")
        with self.assertRaisesRegex(RuntimeError, "mismatched approval target"):
            demo_module.normalize_approval_item(
                SimpleNamespace(**{**vars(valid), "server_label": "other"}),
                "CRQ-1003",
            )


if __name__ == "__main__":
    unittest.main()
