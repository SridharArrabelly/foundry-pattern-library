"""Pattern 5B: explicit graph workflow mixing agents and deterministic executors."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Case,
    CheckpointStorage,
    Default,
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    executor,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing_extensions import Never


WORKFLOW_NAME = "pattern-5b-enterprise-change-workflow"

STANDARD_REQUEST = {
    "request_id": "CHG-1001",
    "operation": "restart_service",
    "environment": "test",
    "estimated_users": 20,
    "rollback_plan": "Restart the previously approved service revision.",
}

EXCEPTION_REQUEST = {
    "request_id": "CHG-1002",
    "operation": "rotate_deployment",
    "environment": "production",
    "estimated_users": 500,
    "rollback_plan": "Restore the current deployment and verify health probes.",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChangeRequest(StrictModel):
    request_id: str = Field(pattern=r"^CHG-[0-9]{4}$")
    operation: Literal[
        "restart_service",
        "apply_configuration",
        "rotate_deployment",
    ]
    environment: Literal["test", "production"]
    estimated_users: int = Field(ge=0, le=1_000_000)
    rollback_plan: str = Field(min_length=12, max_length=300)


class ClassificationDecision(StrictModel):
    semantic_risk: Literal["low", "high", "uncertain"]
    rationale: str = Field(min_length=3, max_length=300)


class ClassificationEnvelope(StrictModel):
    request: ChangeRequest
    validation_id: str
    semantic_risk: Literal["low", "high", "uncertain", "invalid"]
    route: Literal["standard", "exception", "invalid"]
    rationale: str
    policy_overrides: list[str]


class ExceptionReviewDecision(StrictModel):
    recommendation: Literal["APPROVE", "BLOCK"]
    rationale: str = Field(min_length=3, max_length=300)
    required_controls: list[
        Annotated[str, Field(min_length=3, max_length=160)]
    ] = Field(max_length=8)


class AuditDraft(StrictModel):
    request: ChangeRequest
    validation_id: str
    route: Literal["standard", "exception", "invalid"]
    decision: Literal["APPROVED", "REVIEW_REQUIRED", "BLOCKED"]
    rationale: str
    controls: list[str]
    path: list[str]


class AuditRecord(AuditDraft):
    audit_id: str


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def checkpoint_allowed_types() -> list[str]:
    return [
        f"{model.__module__}:{model.__qualname__}"
        for model in (
            ChangeRequest,
            ClassificationDecision,
            ClassificationEnvelope,
            ExceptionReviewDecision,
            AuditDraft,
            AuditRecord,
        )
    ]


def _input_payload(value: dict[str, Any] | str | list[Message]) -> dict[str, Any]:
    if isinstance(value, list):
        user_messages = [
            message.text
            for message in value
            if str(message.role).lower() == "user" and message.text
        ]
        if not user_messages:
            raise ValueError("workflow-as-agent input contains no user message")
        value = user_messages[-1]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("workflow input must be a JSON object") from error
    if not isinstance(value, dict):
        raise ValueError("workflow input must be an object, JSON string, or user message")
    return value


def _validated_message(request: ChangeRequest) -> tuple[str, str]:
    request_json = request.model_dump(mode="json")
    validation_id = hashlib.sha256(
        canonical_json(request_json).encode("utf-8")
    ).hexdigest()[:16]
    return validation_id, canonical_json(
        {
            "validation_id": validation_id,
            "validated_request": request_json,
        }
    )


class ValidationExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="validate_request")

    async def _validate(
        self,
        payload: dict[str, Any] | str | list[Message],
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        try:
            request = ChangeRequest.model_validate(_input_payload(payload))
        except (ValueError, ValidationError) as error:
            raise RuntimeError(f"request validation failed closed: {error}") from error
        validation_id, content = _validated_message(request)
        del validation_id
        await ctx.send_message(
            AgentExecutorRequest(
                messages=[Message(role="user", contents=[content])],
                should_respond=True,
            )
        )

    @handler
    async def from_dict(
        self,
        payload: dict[str, Any],
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        await self._validate(payload, ctx)

    @handler
    async def from_text(
        self,
        payload: str,
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        await self._validate(payload, ctx)

    @handler
    async def from_messages(
        self,
        payload: list[Message],
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        await self._validate(payload, ctx)


def _conversation_json(
    response: AgentExecutorResponse,
    required_key: str,
) -> dict[str, Any]:
    for message in reversed(response.full_conversation):
        if str(message.role).lower() != "user" or not message.text:
            continue
        try:
            value = json.loads(message.text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and required_key in value:
            return value
    raise ValueError(f"agent conversation is missing {required_key!r}")


def _response_model(response: AgentExecutorResponse, model_type):
    value = response.agent_response.value
    if isinstance(value, model_type):
        return value
    if isinstance(value, dict):
        return model_type.model_validate(value)
    return model_type.model_validate_json(response.agent_response.text)


def _policy_overrides(request: ChangeRequest) -> list[str]:
    reasons = []
    if request.environment == "production":
        reasons.append("production-environment")
    if request.estimated_users > 100:
        reasons.append("user-impact-over-100")
    if request.operation == "rotate_deployment":
        reasons.append("deployment-rotation")
    return reasons


@executor(id="normalize_classification")
async def normalize_classification(
    response: AgentExecutorResponse,
    ctx: WorkflowContext[ClassificationEnvelope],
) -> None:
    source = _conversation_json(response, "validated_request")
    request = ChangeRequest.model_validate(source["validated_request"])
    validation_id = source.get("validation_id")
    expected_validation_id, _ = _validated_message(request)
    if validation_id != expected_validation_id:
        raise RuntimeError("classifier conversation validation ID is mismatched")

    try:
        decision = _response_model(response, ClassificationDecision)
        overrides = _policy_overrides(request)
        route: Literal["standard", "exception", "invalid"] = (
            "exception"
            if decision.semantic_risk != "low" or overrides
            else "standard"
        )
        envelope = ClassificationEnvelope(
            request=request,
            validation_id=validation_id,
            semantic_risk=decision.semantic_risk,
            route=route,
            rationale=decision.rationale,
            policy_overrides=overrides,
        )
    except (ValueError, ValidationError) as error:
        envelope = ClassificationEnvelope(
            request=request,
            validation_id=validation_id,
            semantic_risk="invalid",
            route="invalid",
            rationale=f"Classifier output failed schema validation: {type(error).__name__}",
            policy_overrides=_policy_overrides(request),
        )
    await ctx.send_message(envelope)


@executor(id="standard_processor")
async def standard_processor(
    envelope: ClassificationEnvelope,
    ctx: WorkflowContext[AuditDraft],
) -> None:
    if envelope.route != "standard" or envelope.policy_overrides:
        raise RuntimeError("standard processor received a non-standard request")
    await ctx.send_message(
        AuditDraft(
            request=envelope.request,
            validation_id=envelope.validation_id,
            route="standard",
            decision="APPROVED",
            rationale=envelope.rationale,
            controls=["validated-input", "deterministic-standard-policy"],
            path=[
                "validate_request",
                "risk_classifier",
                "normalize_classification",
                "standard_processor",
                "audit",
            ],
        )
    )


@executor(id="build_exception_review")
async def build_exception_review(
    envelope: ClassificationEnvelope,
    ctx: WorkflowContext[AgentExecutorRequest],
) -> None:
    if envelope.route != "exception":
        raise RuntimeError("exception review builder received the wrong route")
    await ctx.send_message(
        AgentExecutorRequest(
            messages=[
                Message(
                    role="user",
                    contents=[
                        canonical_json(
                            {"classification": envelope.model_dump(mode="json")}
                        )
                    ],
                )
            ],
            should_respond=True,
        )
    )


@executor(id="finalize_exception")
async def finalize_exception(
    response: AgentExecutorResponse,
    ctx: WorkflowContext[AuditDraft],
) -> None:
    source = _conversation_json(response, "classification")
    envelope = ClassificationEnvelope.model_validate(source["classification"])
    try:
        review = _response_model(response, ExceptionReviewDecision)
    except (ValueError, ValidationError) as error:
        review = ExceptionReviewDecision(
            recommendation="BLOCK",
            rationale=f"Reviewer output failed schema validation: {type(error).__name__}",
            required_controls=[],
        )
    decision: Literal["REVIEW_REQUIRED", "BLOCKED"] = (
        "REVIEW_REQUIRED" if review.recommendation == "APPROVE" else "BLOCKED"
    )
    controls = list(
        dict.fromkeys(
            [
                "validated-input",
                "deterministic-exception-policy",
                *review.required_controls,
                *(
                    ["human-approval-required"]
                    if decision == "REVIEW_REQUIRED"
                    else []
                ),
            ]
        )
    )
    await ctx.send_message(
        AuditDraft(
            request=envelope.request,
            validation_id=envelope.validation_id,
            route="exception",
            decision=decision,
            rationale=review.rationale,
            controls=controls,
            path=[
                "validate_request",
                "risk_classifier",
                "normalize_classification",
                "build_exception_review",
                "exception_reviewer",
                "finalize_exception",
                "audit",
            ],
        )
    )


@executor(id="fail_closed")
async def fail_closed(
    envelope: ClassificationEnvelope,
    ctx: WorkflowContext[AuditDraft],
) -> None:
    await ctx.send_message(
        AuditDraft(
            request=envelope.request,
            validation_id=envelope.validation_id,
            route="invalid",
            decision="BLOCKED",
            rationale=envelope.rationale,
            controls=["validated-input", "invalid-agent-output-blocked"],
            path=[
                "validate_request",
                "risk_classifier",
                "normalize_classification",
                "fail_closed",
                "audit",
            ],
        )
    )


class AuditExecutor(Executor):
    def __init__(self) -> None:
        super().__init__(id="audit")
        self.audit_ids: list[str] = []

    @handler
    async def record(
        self,
        draft: AuditDraft,
        ctx: WorkflowContext[Never, str],
    ) -> None:
        identity = {
            "request_id": draft.request.request_id,
            "validation_id": draft.validation_id,
            "route": draft.route,
            "decision": draft.decision,
            "path": draft.path,
        }
        audit_id = "AUD-" + hashlib.sha256(
            canonical_json(identity).encode("utf-8")
        ).hexdigest()[:16].upper()
        if audit_id not in self.audit_ids:
            self.audit_ids.append(audit_id)
        record = AuditRecord(
            **draft.model_dump(),
            audit_id=audit_id,
        )
        await ctx.yield_output(record.model_dump_json())

    async def on_checkpoint_save(self) -> dict[str, Any]:
        return {"audit_ids": list(self.audit_ids)}

    async def on_checkpoint_restore(self, state: dict[str, Any]) -> None:
        self.audit_ids = list(state.get("audit_ids", []))


def create_classifier_agent(client):
    return Agent(
        client,
        id="workflow-risk-classifier-agent",
        name="workflow_risk_classifier",
        description="Classifies semantic change risk; application policy owns routing.",
        instructions=(
            "Classify the validated enterprise change request as semantic_risk low, "
            "high, or uncertain. A routine reversible test change is low. Ambiguous, "
            "broad-impact, irreversible, privileged, or novel work is high/uncertain. "
            'Return only JSON shaped as {"semantic_risk":"low|high|uncertain",'
            '"rationale":"concise reason"}. Do not decide the route. The downstream '
            "code executor validates this schema and fails closed."
        ),
        default_options={
            "temperature": 0,
            "store": False,
        },
    )


def create_exception_reviewer_agent(client):
    return Agent(
        client,
        id="workflow-exception-reviewer-agent",
        name="workflow_exception_reviewer",
        description="Reviews exceptional change requests and proposes controls.",
        instructions=(
            "Review the classified enterprise change. Recommend APPROVE only when the "
            "rollback plan is credible and list concrete controls; otherwise recommend "
            "BLOCK. This is advice to the deterministic workflow, not final authority. "
            'Return only JSON shaped as {"recommendation":"APPROVE|BLOCK",'
            '"rationale":"concise reason","required_controls":["control"]}. The '
            "downstream code executor validates this schema and fails closed."
        ),
        default_options={
            "temperature": 0,
            "store": False,
        },
    )


def build_workflow(
    client=None,
    *,
    classifier_agent=None,
    reviewer_agent=None,
    checkpoint_storage: CheckpointStorage | None = None,
):
    if classifier_agent is None:
        if client is None:
            raise ValueError("client or classifier_agent is required")
        classifier_agent = create_classifier_agent(client)
    if reviewer_agent is None:
        if client is None:
            raise ValueError("client or reviewer_agent is required")
        reviewer_agent = create_exception_reviewer_agent(client)

    validate = ValidationExecutor()
    classifier = AgentExecutor(classifier_agent, id="risk_classifier")
    reviewer = AgentExecutor(reviewer_agent, id="exception_reviewer")
    audit = AuditExecutor()

    return (
        WorkflowBuilder(
            name=WORKFLOW_NAME,
            description=(
                "Explicit change-control graph with deterministic routing, "
                "mixed code/agent executors, audit output, and checkpoints."
            ),
            start_executor=validate,
            checkpoint_storage=checkpoint_storage,
            output_from=[audit],
        )
        .add_edge(validate, classifier)
        .add_edge(classifier, normalize_classification)
        .add_switch_case_edge_group(
            normalize_classification,
            [
                Case(
                    condition=lambda value: (
                        isinstance(value, ClassificationEnvelope)
                        and value.route == "standard"
                    ),
                    target=standard_processor,
                ),
                Case(
                    condition=lambda value: (
                        isinstance(value, ClassificationEnvelope)
                        and value.route == "exception"
                    ),
                    target=build_exception_review,
                ),
                Default(target=fail_closed),
            ],
        )
        .add_edge(standard_processor, audit)
        .add_edge(build_exception_review, reviewer)
        .add_edge(reviewer, finalize_exception)
        .add_edge(finalize_exception, audit)
        .add_edge(fail_closed, audit)
        .build()
    )
