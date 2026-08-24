"""Negative-path and idempotency tests for Pattern 13."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest


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
        self.store = store_module.ApprovalStore(
            Path(self.temp.name) / "approval.sqlite3", now=self.clock
        )

    def arguments(self, request_id="CRQ-1003"):
        return self.store.get_change_request(request_id)["schedule_arguments"]

    def envelope(
        self,
        *,
        request_id="CRQ-1003",
        decision_id="decision-1",
        approval_id="approval-1",
        approve=True,
        arguments=None,
    ):
        return {
            "type": "mcp_approval_response",
            "id": decision_id,
            "approval_request_id": approval_id,
            "approve": approve,
            "reason": "reviewed",
            "server_label": store_module.SERVER_LABEL,
            "tool_name": store_module.WRITE_TOOL,
            "arguments": arguments or self.arguments(request_id),
        }

    def test_selective_policy_never_approves_read_and_always_approves_write(self):
        tool = dict(demo_module.approval_tool("connection-id"))
        self.assertEqual(
            tool["require_approval"]["never"]["tool_names"], ["get_change_request"]
        )
        self.assertEqual(
            tool["require_approval"]["always"]["tool_names"], ["schedule_change"]
        )

    def test_reject_records_decision_and_never_schedules(self):
        result = self.store.record_decision(self.envelope(approve=False))
        self.assertFalse(result.approve)
        with self.assertRaises(store_module.MissingApproval):
            self.store.schedule_change(self.arguments())
        audit = self.store.audit("CRQ-1003")
        self.assertEqual(audit["status"], "rejected")
        self.assertEqual(audit["side_effects"], [])

    def test_approve_schedules_exactly_once_with_correlated_ids(self):
        decision = self.store.record_decision(self.envelope())
        first = self.store.schedule_change(self.arguments())
        replay = self.store.schedule_change(self.arguments())
        audit = self.store.audit("CRQ-1003")
        self.assertEqual(first["decision_id"], decision.decision_id)
        self.assertEqual(first["approval_request_id"], decision.approval_request_id)
        self.assertEqual(replay["side_effect_id"], first["side_effect_id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(audit["side_effects"]), 1)

    def test_exact_duplicate_decision_is_idempotent_but_changed_replay_fails(self):
        first = self.store.record_decision(self.envelope())
        duplicate = self.store.record_decision(self.envelope())
        self.assertFalse(first.duplicate)
        self.assertTrue(duplicate.duplicate)
        changed = self.envelope(approve=False)
        with self.assertRaises(store_module.ReplayConflict):
            self.store.record_decision(changed)

    def test_missing_malformed_and_mismatched_approvals_fail_closed(self):
        with self.assertRaises(store_module.MissingApproval):
            self.store.schedule_change(self.arguments())

        malformed = self.envelope()
        malformed.pop("approval_request_id")
        with self.assertRaises(store_module.MalformedApproval):
            self.store.record_decision(malformed)

        mismatched = self.envelope()
        mismatched["arguments"] = {**self.arguments(), "reason": "different"}
        with self.assertRaises(store_module.MismatchedApproval):
            self.store.record_decision(mismatched)

        wrong_tool = self.envelope()
        wrong_tool["tool_name"] = "get_change_request"
        with self.assertRaises(store_module.MismatchedApproval):
            self.store.record_decision(wrong_tool)

    def test_stale_decision_and_stale_execution_fail_closed(self):
        self.clock.value += 86_401
        with self.assertRaises(store_module.StaleApproval):
            self.store.record_decision(self.envelope())

        second_clock = Clock()
        second = store_module.ApprovalStore(
            Path(self.temp.name) / "second.sqlite3", now=second_clock
        )
        second.record_decision(
            {
                **self.envelope(),
                "arguments": second.get_change_request("CRQ-1003")["schedule_arguments"],
            }
        )
        second_clock.value += store_module.DECISION_TTL_SECONDS + 1
        with self.assertRaises(store_module.StaleApproval):
            second.schedule_change(
                second.get_change_request("CRQ-1003")["schedule_arguments"]
            )

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
