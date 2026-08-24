"""Fail-closed lifecycle and promotion tests for Pattern 15."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "15-agent-lifecycle"))


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lifecycle = load_module(
    "agent_lifecycle_test",
    "15-agent-lifecycle/lifecycle.py",
)


class FakeAdapter:
    def __init__(self, version="1"):
        self.version = version
        self.endpoint = "https://example.test/stable"
        self.pins = []
        self.toolbox_restores = []
        self.toolbox_sets = []

    def endpoint_url(self):
        return self.endpoint

    def selected_version(self):
        return self.version

    def pin(self, version):
        self.pins.append(str(version))
        self.version = str(version)

    def smoke(self, expected_release):
        return {"category": "BR2", "release": expected_release}

    def restore_toolbox_default(self, update, *, mutation_callback=None):
        if mutation_callback:
            mutation_callback()
        self.toolbox_restores.append(update)

    def set_toolbox_default(self, name, version):
        self.toolbox_sets.append((name, version))

    def continue_conversation(self, conversation_id, *, expected_release):
        return {
            "metadata": {
                "release_sentinel": "preserve-across-selector-change"
            },
            "item_count_after": 4,
            "continued_release": expected_release or "candidate",
        }


class AgentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.manifest = lifecycle.validate_manifest(
            lifecycle.read_json(lifecycle.MANIFEST_PATH)
        )
        self.evaluation = self.manifest["evaluation"]

    def evidence(self, passed=True):
        failures = [] if passed else ["classification_accuracy failed"]
        return {
            "evaluation_id": "eval-1",
            "run_id": "run-1",
            "status": "completed",
            "candidate_version": "2",
            "metrics": {
                "classification_accuracy": 1.0 if passed else 0.75,
                "schema_validity": 1.0,
            },
            "failures": failures,
            "passed": passed,
        }

    def test_non_object_agent_output_fails_closed(self):
        for value in ("[]", "null", '"text"', "42", "true"):
            with self.subTest(value=value):
                self.assertEqual(
                    lifecycle.parse_agent_output(value, "candidate"),
                    ("", False),
                )

    def test_manifest_aliases_dataset_and_environment_topology_validate(self):
        environment = {
            "LIFECYCLE_MODEL_DEPLOYMENT": "gpt-4.1-mini",
            "LIFECYCLE_DEV_PROJECT_ENDPOINT": (
                "https://resource.services.ai.azure.com/api/projects/dev"
            ),
            "LIFECYCLE_TEST_PROJECT_ENDPOINT": (
                "https://resource.services.ai.azure.com/api/projects/test"
            ),
            "LIFECYCLE_PROD_PROJECT_ENDPOINT": (
                "https://resource.services.ai.azure.com/api/projects/prod"
            ),
        }
        aliases = lifecycle.resolve_aliases(self.manifest, environment)
        endpoints = lifecycle.project_endpoints(environment)
        self.assertEqual(aliases["models"]["primary-chat"], "gpt-4.1-mini")
        self.assertEqual(set(endpoints), {"dev", "test", "prod"})
        self.assertEqual(len(set(endpoints.values())), 3)

    def test_unresolved_required_alias_and_reused_project_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "unresolved"):
            lifecycle.resolve_aliases(self.manifest, {})
        same = "https://resource.services.ai.azure.com/api/projects/same"
        with self.assertRaisesRegex(RuntimeError, "must be distinct"):
            lifecycle.project_endpoints(
                {
                    "LIFECYCLE_DEV_PROJECT_ENDPOINT": same,
                    "LIFECYCLE_TEST_PROJECT_ENDPOINT": same,
                    "LIFECYCLE_PROD_PROJECT_ENDPOINT": same,
                }
            )

    def test_legacy_or_identityless_agent_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "native agent_endpoint"):
            lifecycle.assert_current_agent(
                SimpleNamespace(
                    agent_endpoint=None,
                    instance_identity=None,
                    blueprint=None,
                )
            )
        with self.assertRaisesRegex(RuntimeError, "legacy/shared identity"):
            lifecycle.assert_current_agent(
                SimpleNamespace(
                    agent_endpoint=SimpleNamespace(),
                    instance_identity=None,
                    blueprint=SimpleNamespace(),
                )
            )

    def test_selector_requires_one_hundred_percent_single_version(self):
        details = SimpleNamespace(
            agent_endpoint=SimpleNamespace(
                version_selector=SimpleNamespace(
                    version_selection_rules=[
                        SimpleNamespace(
                            agent_version="2",
                            traffic_percentage=100,
                        )
                    ]
                )
            ),
            instance_identity=SimpleNamespace(),
            blueprint=SimpleNamespace(),
            versions=SimpleNamespace(latest=SimpleNamespace(version="3")),
        )
        self.assertEqual(lifecycle.selected_version(details), "2")
        details.agent_endpoint.version_selector.version_selection_rules[0].traffic_percentage = 50
        with self.assertRaisesRegex(RuntimeError, "100 percent"):
            lifecycle.selected_version(details)
        details.agent_endpoint.version_selector.version_selection_rules = []
        with self.assertRaisesRegex(RuntimeError, "auto-latest is unsafe"):
            lifecycle.selected_version(details)

    def test_missing_or_failed_evidence_cannot_promote(self):
        adapter = FakeAdapter()
        incomplete = {"status": "completed"}
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            lifecycle.promote_if_passing(
                adapter,
                candidate_version="2",
                evidence=incomplete,
                evaluation_config=self.evaluation,
            )
        with self.assertRaisesRegex(RuntimeError, "unsuccessful"):
            lifecycle.promote_if_passing(
                adapter,
                candidate_version="2",
                evidence=self.evidence(False),
                evaluation_config=self.evaluation,
            )
        self.assertEqual(adapter.pins, [])
        self.assertEqual(adapter.selected_version(), "1")

    def test_passing_candidate_promotes_without_changing_endpoint(self):
        adapter = FakeAdapter()
        result = lifecycle.promote_if_passing(
            adapter,
            candidate_version="2",
            evidence=self.evidence(True),
            evaluation_config=self.evaluation,
            expected_previous="1",
        )
        self.assertEqual(adapter.pins, ["2"])
        self.assertEqual(result["previous_version"], "1")
        self.assertEqual(result["promoted_version"], "2")
        self.assertEqual(
            result["stable_endpoint_before"],
            result["stable_endpoint_after"],
        )

    def test_stable_smoke_calls_native_endpoint_not_project_agent_reference(self):
        environment = object.__new__(lifecycle.FoundryEnvironment)
        environment.name = "prod"
        environment.project_endpoint = (
            "https://resource.services.ai.azure.com/api/projects/prod"
        )
        environment.agent_name = "enterprise-triage-router"
        environment.credential = SimpleNamespace(
            get_token=lambda scope: SimpleNamespace(token="not-logged")
        )
        response = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "completed",
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"category":"BR2","release":"approved"}',
                            },
                            {
                                "type": "output_text",
                                "text": '{"category":"BR2","release":"approved"}',
                            },
                        ]
                    }
                ],
            },
        )
        with patch.object(lifecycle.requests, "request", return_value=response) as post:
            text = environment.invoke("route this")
        self.assertIn('"approved"', text)
        url = post.call_args.args[1]
        self.assertIn(
            "/agents/enterprise-triage-router/endpoint/protocols/openai/responses",
            url,
        )
        self.assertIn("api-version=v1", url)
        self.assertNotIn("agent_reference", post.call_args.kwargs["json"])
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer not-logged",
        )

        conflicting = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "completed",
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"category":"BR2","release":"approved"}',
                            },
                            {
                                "type": "output_text",
                                "text": '{"category":"AX7","release":"approved"}',
                            },
                        ]
                    }
                ],
            },
        )
        with patch.object(
            lifecycle.requests,
            "request",
            return_value=conflicting,
        ), self.assertRaisesRegex(RuntimeError, "conflicting output_text"):
            environment.invoke("route this")

    def test_selector_wait_handles_eventual_consistency_and_blocks_unsafe_activation(self):
        class EventuallyConsistent:
            name = "prod"

            def __init__(self):
                self.versions = iter(("2", "2", "1"))

            def selected_version(self):
                return next(self.versions)

        adapter = EventuallyConsistent()
        observed = lifecycle.FoundryEnvironment.wait_for_selected_version(
            adapter,
            "1",
            attempts=3,
            delay_seconds=0,
        )
        self.assertEqual(observed, "1")

        unsafe = SimpleNamespace(
            name="prod",
            selected_version=lambda: "1",
            invoke=lambda prompt: '{"category":"BR2","release":"candidate"}',
            endpoint_url=lambda: "https://stable",
        )
        with self.assertRaisesRegex(RuntimeError, "unsafe activation"):
            lifecycle.FoundryEnvironment.verify_pre_promotion_isolation(
                unsafe,
                "1",
                attempts=1,
                delay_seconds=0,
            )

    def test_rollback_repins_prior_version_and_preserves_state(self):
        class SelectorBeforeEndpointAdapter(FakeAdapter):
            def __init__(self):
                super().__init__(version="2")
                self.routing_converged = False

            def smoke(self, expected_release):
                self.routing_converged = expected_release == "approved"
                return super().smoke(expected_release)

            def continue_conversation(self, conversation_id, *, expected_release):
                if not self.routing_converged:
                    raise AssertionError(
                        "conversation continued before stable routing converged"
                    )
                return super().continue_conversation(
                    conversation_id,
                    expected_release=expected_release,
                )

        adapter = SelectorBeforeEndpointAdapter()
        result = lifecycle.rollback_release(
            adapter,
            previous_version="1",
            promoted_version="2",
            conversation_state={
                "conversation_id": "conversation-1",
                "item_count_before": 2,
            },
        )
        self.assertEqual(adapter.selected_version(), "1")
        self.assertTrue(result["state_preserved"])
        self.assertEqual(
            result["stable_endpoint_before"],
            result["stable_endpoint_after"],
        )
        self.assertGreater(
            result["conversation_items_after"],
            result["conversation_items_before"],
        )
        self.assertEqual(
            result["routing_convergence_smoke"]["release"],
            "approved",
        )

    def test_stale_selector_blocks_promotion_and_rollback(self):
        drifted = FakeAdapter(version="3")
        with self.assertRaisesRegex(RuntimeError, "selector drifted"):
            lifecycle.promote_if_passing(
                drifted,
                candidate_version="2",
                evidence=self.evidence(True),
                evaluation_config=self.evaluation,
                expected_previous="1",
            )
        self.assertEqual(drifted.pins, [])
        with self.assertRaisesRegex(RuntimeError, "record is stale"):
            lifecycle.rollback_release(
                drifted,
                previous_version="1",
                promoted_version="2",
                conversation_state={
                    "conversation_id": "conversation-1",
                    "item_count_before": 2,
                },
            )

    def test_every_post_pin_failure_uses_same_compensating_recovery(self):
        for stage in ("toolbox", "endpoint", "smoke", "audit-persistence"):
            with self.subTest(stage=stage):
                adapter = FakeAdapter(version="2")
                update = (
                    {"name": "tools", "previous": "1", "default": "2"}
                    if stage == "toolbox"
                    else None
                )
                recovery = lifecycle.compensate_release(
                    adapter,
                    previous_version="1",
                    toolbox_update=update,
                )
                self.assertEqual(adapter.selected_version(), "1")
                self.assertEqual(recovery["smoke"]["release"], "approved")
                self.assertEqual(bool(adapter.toolbox_restores), update is not None)

    def test_toolbox_rollback_and_partial_failure_recovery_are_reciprocal(self):
        update = {"name": "tools", "previous": "1", "default": "2"}
        adapter = FakeAdapter(version="2")
        toolbox_mutated = False

        def mark_toolbox():
            nonlocal toolbox_mutated
            toolbox_mutated = True

        adapter.restore_toolbox_default(update, mutation_callback=mark_toolbox)
        self.assertTrue(toolbox_mutated)
        recovery = lifecycle.compensate_rollback(
            adapter,
            promoted_version="2",
            toolbox_update=update,
            toolbox_mutated=toolbox_mutated,
        )
        self.assertEqual(adapter.toolbox_sets, [("tools", "2")])
        self.assertEqual(adapter.selected_version(), "2")
        self.assertTrue(recovery["toolbox_restored_to_promoted"])

    def test_compensation_attempts_agent_and_toolbox_independently(self):
        class FailingRecovery(FakeAdapter):
            def __init__(self, fail_agent, fail_toolbox):
                super().__init__(version="2")
                self.fail_agent = fail_agent
                self.fail_toolbox = fail_toolbox
                self.agent_attempted = False
                self.toolbox_attempted = False

            def pin(self, version):
                self.agent_attempted = True
                if self.fail_agent:
                    raise RuntimeError("pin failed")
                super().pin(version)

            def restore_toolbox_default(self, update, *, mutation_callback=None):
                self.toolbox_attempted = True
                if self.fail_toolbox:
                    raise RuntimeError("toolbox failed")
                super().restore_toolbox_default(
                    update,
                    mutation_callback=mutation_callback,
                )

        update = {"name": "tools", "previous": "1", "default": "2"}
        for fail_agent, fail_toolbox in ((False, True), (True, False), (True, True)):
            with self.subTest(
                fail_agent=fail_agent,
                fail_toolbox=fail_toolbox,
            ):
                adapter = FailingRecovery(fail_agent, fail_toolbox)
                with self.assertRaises(RuntimeError) as raised:
                    lifecycle.compensate_release(
                        adapter,
                        previous_version="1",
                        toolbox_update=update,
                    )
                self.assertTrue(adapter.agent_attempted)
                self.assertTrue(adapter.toolbox_attempted)
                if fail_agent and fail_toolbox:
                    self.assertIn("agent selector recovery failed", str(raised.exception))
                    self.assertIn("Toolbox recovery failed", str(raised.exception))

    def test_toolbox_mutation_intent_is_captured_before_verification(self):
        toolboxes = SimpleNamespace()
        toolboxes.get = Mock(
            side_effect=[
                SimpleNamespace(default_version="1"),
                SimpleNamespace(default_version="unexpected"),
            ]
        )
        toolboxes.get_version = Mock()
        toolboxes.update = Mock()
        environment = object.__new__(lifecycle.FoundryEnvironment)
        environment.project = SimpleNamespace(toolboxes=toolboxes)
        captured = {}
        with self.assertRaisesRegex(RuntimeError, "did not match"):
            environment.update_toolbox_default(
                "tools",
                "2",
                True,
                mutation_callback=lambda intent: captured.update(intent),
            )
        self.assertEqual(
            captured,
            {"name": "tools", "previous": "1", "default": "2"},
        )
        toolboxes.update.assert_called_once_with("tools", default_version="2")

    def test_cloud_gate_fails_on_missing_or_incomplete_metrics(self):
        valid_results = [
            SimpleNamespace(
                testing_criteria=name,
                passed=4,
                failed=0,
                errored=0,
            )
            for name in ("classification_accuracy", "schema_validity")
        ]
        run = SimpleNamespace(
            status="completed",
            per_testing_criteria_results=valid_results,
        )
        self.assertEqual(
            lifecycle.gate_failures(
                run,
                expected_rows=4,
                required_metrics={"classification_accuracy", "schema_validity"},
            ),
            [],
        )
        run.per_testing_criteria_results = valid_results[:1]
        failures = lifecycle.gate_failures(
            run,
            expected_rows=4,
            required_metrics={"classification_accuracy", "schema_validity"},
        )
        self.assertIn("schema_validity result is missing", failures)

    def test_workflow_uses_oidc_and_code_has_no_legacy_application_route(self):
        workflow = (lifecycle.BASE_DIR / "release.yml").read_text(encoding="utf-8")
        source = (lifecycle.BASE_DIR / "lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("id-token: write", workflow)
        self.assertIn("azure/login@v2", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertNotIn("client-secret", workflow)
        self.assertNotIn("/applications", source)

    def test_promoted_release_record_requires_complete_audit_fields(self):
        record = {
            "record_type": "release",
            "record_id": "release-1",
            "created_at": "now",
            "completed_at": "later",
            "commit": "abc",
            "approver": "operator",
            "agent_name": "agent",
            "change_reference": "change",
            "aliases": {},
            "project_endpoints": {
                "dev": "https://dev",
                "test": "https://test",
                "prod": "https://prod",
            },
            "environments": {
                "prod": {
                    "promotion": {
                        "previous_version": "1",
                        "promoted_version": "2",
                        "selector_precondition_observed": "1",
                        "selector_postcondition_observed": "2",
                        "stable_endpoint_before": "https://stable",
                        "stable_endpoint_after": "https://stable",
                    }
                }
            },
            "evaluation": self.evidence(True),
            "conversation_state": {
                "conversation_id": "conversation-1",
                "item_count_before": 2,
            },
            "status": "promoted",
        }
        test_key = "test-signing-key-32-bytes-minimum"
        lifecycle.seal_record(record, test_key)
        lifecycle.validate_release_record(record, key=test_key)
        record["approver"] = "tampered"
        forged_payload = {
            key: value
            for key, value in record.items()
            if key != "record_hmac_sha256"
        }
        record["record_hmac_sha256"] = hashlib.sha256(
            json.dumps(
                forged_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(RuntimeError, "HMAC"):
            lifecycle.validate_release_record(record, key=test_key)
        record["approver"] = "operator"
        lifecycle.seal_record(record, test_key)
        with self.assertRaisesRegex(RuntimeError, "HMAC"):
            lifecycle.validate_release_record(
                record,
                key="different-test-signing-key-32-bytes",
            )


if __name__ == "__main__":
    unittest.main()
