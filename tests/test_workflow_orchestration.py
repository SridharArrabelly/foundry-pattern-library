"""Graph, routing, failure, and checkpoint tests for Pattern 5B."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from agent_framework import AgentResponse, AgentSession, FileCheckpointStorage, Message


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "05b-workflow-orchestration"
    / "hosted"
    / "src"
    / "workflow-orchestrator"
)
sys.path.insert(0, str(SOURCE))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


graph = load_module("workflow_graph_test", SOURCE / "workflow_graph.py")


class ScriptedAgent:
    def __init__(self, agent_id: str, response):
        self.id = agent_id
        self.name = agent_id
        self.description = agent_id
        self.response = response
        self.calls = []

    def create_session(self, *, session_id=None):
        return AgentSession(session_id=session_id)

    def get_session(self, service_session_id, *, session_id=None):
        return AgentSession(
            service_session_id=service_session_id,
            session_id=session_id,
        )

    async def run(self, messages=None, *, stream=False, session=None, **kwargs):
        self.calls.append(messages)
        response = self.response(messages) if callable(self.response) else self.response
        text = response if isinstance(response, str) else json.dumps(response)
        return AgentResponse(
            messages=[Message(role="assistant", contents=[text])]
        )


def classifier(risk="low"):
    return ScriptedAgent(
        "classifier",
        {
            "semantic_risk": risk,
            "rationale": f"semantic risk is {risk}",
        },
    )


def reviewer(recommendation="APPROVE"):
    return ScriptedAgent(
        "reviewer",
        {
            "recommendation": recommendation,
            "rationale": "rollback is credible",
            "required_controls": ["change-window", "health-probes"],
        },
    )


def parse_output(result):
    outputs = result.get_outputs()
    if len(outputs) != 1:
        raise AssertionError(f"expected one output, got {outputs!r}")
    return graph.AuditRecord.model_validate_json(outputs[0])


class WorkflowOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_path_uses_code_and_skips_exception_agent(self):
        risk = classifier("low")
        exception = reviewer()
        workflow = graph.build_workflow(
            classifier_agent=risk,
            reviewer_agent=exception,
        )
        record = parse_output(await workflow.run(graph.STANDARD_REQUEST))
        self.assertEqual(record.route, "standard")
        self.assertEqual(record.decision, "APPROVED")
        self.assertEqual(len(risk.calls), 1)
        self.assertEqual(exception.calls, [])
        self.assertEqual(
            record.path,
            [
                "validate_request",
                "risk_classifier",
                "normalize_classification",
                "standard_processor",
                "audit",
            ],
        )

    async def test_code_policy_overrides_low_risk_agent_for_production(self):
        risk = classifier("low")
        exception = reviewer("APPROVE")
        workflow = graph.build_workflow(
            classifier_agent=risk,
            reviewer_agent=exception,
        )
        record = parse_output(await workflow.run(graph.EXCEPTION_REQUEST))
        self.assertEqual(record.route, "exception")
        self.assertEqual(record.decision, "REVIEW_REQUIRED")
        self.assertEqual(len(exception.calls), 1)
        self.assertIn("human-approval-required", record.controls)
        self.assertIn("production-environment", graph._policy_overrides(record.request))

    async def test_high_semantic_risk_routes_to_exception_in_test(self):
        risk = classifier("high")
        exception = reviewer("BLOCK")
        workflow = graph.build_workflow(
            classifier_agent=risk,
            reviewer_agent=exception,
        )
        record = parse_output(await workflow.run(graph.STANDARD_REQUEST))
        self.assertEqual(record.route, "exception")
        self.assertEqual(record.decision, "BLOCKED")
        self.assertEqual(len(exception.calls), 1)

    async def test_malformed_classifier_output_uses_fail_closed_default(self):
        risk = ScriptedAgent("classifier", "not-json")
        exception = reviewer()
        workflow = graph.build_workflow(
            classifier_agent=risk,
            reviewer_agent=exception,
        )
        record = parse_output(await workflow.run(graph.STANDARD_REQUEST))
        self.assertEqual(record.route, "invalid")
        self.assertEqual(record.decision, "BLOCKED")
        self.assertIn("invalid-agent-output-blocked", record.controls)
        self.assertEqual(exception.calls, [])

    async def test_invalid_input_fails_before_any_agent_runs(self):
        risk = classifier()
        exception = reviewer()
        workflow = graph.build_workflow(
            classifier_agent=risk,
            reviewer_agent=exception,
        )
        invalid = {**graph.STANDARD_REQUEST, "rollback_plan": "short"}
        with self.assertRaisesRegex(RuntimeError, "validation failed closed"):
            await workflow.run(invalid)
        self.assertEqual(risk.calls, [])
        self.assertEqual(exception.calls, [])

    async def test_file_checkpoint_resume_preserves_route_decision_and_audit_id(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = FileCheckpointStorage(
                temp,
                allowed_checkpoint_types=graph.checkpoint_allowed_types(),
            )
            first = graph.build_workflow(
                classifier_agent=classifier(),
                reviewer_agent=reviewer(),
                checkpoint_storage=storage,
            )
            original = parse_output(await first.run(graph.STANDARD_REQUEST))
            checkpoints = await storage.list_checkpoints(
                workflow_name=graph.WORKFLOW_NAME
            )
            self.assertGreaterEqual(len(checkpoints), 5)
            entry = next(
                item for item in checkpoints if item.iteration_count == 0
            )

            resumed = graph.build_workflow(
                classifier_agent=classifier(),
                reviewer_agent=reviewer(),
                checkpoint_storage=storage,
            )
            replay = parse_output(
                await resumed.run(
                    checkpoint_id=entry.checkpoint_id,
                    checkpoint_storage=storage,
                )
            )
            self.assertEqual(replay.audit_id, original.audit_id)
            self.assertEqual(replay.route, original.route)
            self.assertEqual(replay.decision, original.decision)

    def test_workflow_can_be_exposed_as_an_agent(self):
        workflow = graph.build_workflow(
            classifier_agent=classifier(),
            reviewer_agent=reviewer(),
        )
        wrapped = workflow.as_agent(name="workflow-orchestrator")
        self.assertEqual(wrapped.name, "workflow-orchestrator")

    def test_graph_requires_both_agents_or_a_client(self):
        with self.assertRaisesRegex(ValueError, "classifier_agent"):
            graph.build_workflow(reviewer_agent=reviewer())
        with self.assertRaisesRegex(ValueError, "reviewer_agent"):
            graph.build_workflow(classifier_agent=classifier())


if __name__ == "__main__":
    unittest.main()
