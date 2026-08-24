"""Pattern 15: immutable agent versions, release gate, promotion, and rollback."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any
from uuid import uuid4

import requests
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig,
    FixedRatioVersionSelectionRule,
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonSchema,
    VersionSelector,
)
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai.types.eval_create_params import DataSourceConfigCustom
from openai.types.evals.create_eval_jsonl_run_data_source_param import (
    CreateEvalJSONLRunDataSourceParam,
    SourceFileID,
)


load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = BASE_DIR / "release-manifest.json"
ARTIFACT_DIR = Path(
    os.environ.get("LIFECYCLE_ARTIFACT_DIR", BASE_DIR / "artifacts")
)
ENVIRONMENTS = ("dev", "test", "prod")
CATEGORIES = {"AX7", "BR2", "CZ9", "UNSUPPORTED"}
AGENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": sorted(CATEGORIES)},
        "release": {"type": "string", "enum": ["approved", "candidate"]},
    },
    "required": ["category", "release"],
    "additionalProperties": False,
}
EVAL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "input": {"type": "string"},
        "response_category": {"type": "string"},
        "expected_category": {"type": "string"},
        "schema_valid": {"type": "string"},
    },
    "required": [
        "id",
        "input",
        "response_category",
        "expected_category",
        "schema_valid",
    ],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def signing_key(value: str | None = None) -> bytes:
    candidate = value if value is not None else os.environ.get(
        "LIFECYCLE_RECORD_SIGNING_KEY",
        "",
    )
    if len(candidate.encode("utf-8")) < 32:
        raise RuntimeError(
            "LIFECYCLE_RECORD_SIGNING_KEY must be a high-entropy value of at least "
            "32 bytes"
        )
    return candidate.encode("utf-8")


def record_signature(value: dict[str, Any], key: str | None = None) -> str:
    payload = {
        name: item
        for name, item in value.items()
        if name != "record_hmac_sha256"
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(signing_key(key), encoded, hashlib.sha256).hexdigest()


def seal_record(value: dict[str, Any], key: str | None = None) -> dict[str, Any]:
    value["record_hmac_sha256"] = record_signature(value, key)
    return value


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if set(row) != {"id", "input", "expected_category"}:
                raise ValueError(
                    f"{path.name}:{line_number} has an invalid evaluation shape"
                )
            if row["expected_category"] not in CATEGORIES:
                raise ValueError(
                    f"{path.name}:{line_number} has an unsupported category"
                )
            rows.append(row)
    if not rows:
        raise ValueError(f"{path} contains no evaluation rows")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("evaluation row IDs must be unique")
    return rows


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "canonical_agent_name",
        "model_alias",
        "instructions_file",
        "previous_instructions_file",
        "aliases",
        "evaluation",
        "release",
    }
    if set(manifest) != required or manifest["schema_version"] != 1:
        raise ValueError("release manifest shape or schema_version is invalid")
    if not isinstance(manifest["canonical_agent_name"], str):
        raise ValueError("canonical_agent_name is required")
    for key in ("instructions_file", "previous_instructions_file"):
        path = BASE_DIR / manifest[key]
        if not path.is_file():
            raise ValueError(f"manifest references missing {key}: {path.name}")
    dataset = BASE_DIR / manifest["evaluation"]["dataset"]
    read_jsonl(dataset)
    aliases = manifest["aliases"]
    if set(aliases) != {"models", "connections", "toolboxes"}:
        raise ValueError("manifest aliases must define models, connections, and toolboxes")
    if manifest["model_alias"] not in aliases["models"]:
        raise ValueError("model_alias is not declared under aliases.models")
    required_metrics = set(manifest["evaluation"]["required_metrics"])
    if required_metrics != {"classification_accuracy", "schema_validity"}:
        raise ValueError("release gate must require accuracy and schema validity")
    return manifest


def resolve_aliases(
    manifest: dict[str, Any],
    environment: dict[str, str] | os._Environ[str] = os.environ,
) -> dict[str, dict[str, str | None]]:
    resolved: dict[str, dict[str, str | None]] = {}
    for alias_type, declarations in manifest["aliases"].items():
        resolved[alias_type] = {}
        for alias, declaration in declarations.items():
            variable = declaration["environment_variable"]
            value = (environment.get(variable) or "").strip() or None
            if declaration.get("required") and not value:
                raise RuntimeError(
                    f"required {alias_type} alias {alias!r} is unresolved; set {variable}"
                )
            resolved[alias_type][alias] = value
    return resolved


def project_endpoints(
    environment: dict[str, str] | os._Environ[str] = os.environ,
) -> dict[str, str]:
    endpoints = {}
    for name in ENVIRONMENTS:
        variable = f"LIFECYCLE_{name.upper()}_PROJECT_ENDPOINT"
        value = (environment.get(variable) or "").rstrip("/")
        if not value.startswith("https://") or "/api/projects/" not in value:
            raise RuntimeError(f"set {variable} to a Foundry project HTTPS endpoint")
        endpoints[name] = value
    if len(set(endpoints.values())) != len(ENVIRONMENTS):
        raise RuntimeError("dev, test, and prod project endpoints must be distinct")
    return endpoints


def normalized_status(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def assert_current_agent(details) -> None:
    if getattr(details, "agent_endpoint", None) is None:
        raise RuntimeError("agent has no native agent_endpoint; legacy surface is forbidden")
    if getattr(details, "instance_identity", None) is None:
        raise RuntimeError(
            "agent has no instance_identity and appears to use the legacy/shared identity"
        )
    if getattr(details, "blueprint", None) is None:
        raise RuntimeError("agent has no identity blueprint; publishing contract mismatched")


def selected_version(details) -> str:
    assert_current_agent(details)
    selector = getattr(details.agent_endpoint, "version_selector", None)
    rules = getattr(selector, "version_selection_rules", None) if selector else None
    if not rules:
        raise RuntimeError(
            "agent endpoint has no explicit FixedRatio pin; auto-latest is unsafe"
        )
    if len(rules) != 1:
        raise RuntimeError("agent endpoint must contain exactly one version selection rule")
    rule = rules[0]
    if getattr(rule, "traffic_percentage", None) != 100:
        raise RuntimeError("agent endpoint must route 100 percent to one version")
    return str(rule.agent_version)


def stable_endpoint(project_endpoint: str, agent_name: str) -> str:
    return (
        f"{project_endpoint}/agents/{agent_name}/endpoint/protocols/openai/responses"
    )


def parse_agent_output(text: str, expected_release: str) -> tuple[str, bool]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return "", False
    valid = (
        isinstance(value, dict)
        and set(value) == {"category", "release"}
        and value.get("category") in CATEGORIES
        and value.get("release") == expected_release
    )
    return str(value.get("category", "")), valid


def gate_failures(
    run,
    *,
    expected_rows: int,
    required_metrics: set[str],
) -> list[str]:
    failures = []
    if getattr(run, "status", None) != "completed":
        failures.append(f"evaluation status is {getattr(run, 'status', None)!r}")
    results = getattr(run, "per_testing_criteria_results", None)
    if results is None:
        return failures + ["per_testing_criteria_results is missing"]
    by_name = {}
    for result in results:
        name = getattr(result, "testing_criteria", None)
        if not name or name in by_name:
            failures.append("evaluation criteria are missing or duplicated")
            continue
        by_name[name] = result
    for name in sorted(required_metrics - by_name.keys()):
        failures.append(f"{name} result is missing")
    for name in sorted(required_metrics & by_name.keys()):
        result = by_name[name]
        passed = getattr(result, "passed", None)
        failed = getattr(result, "failed", None)
        errored = getattr(result, "errored", 0) or 0
        if passed != expected_rows or failed != 0 or errored != 0:
            failures.append(
                f"{name} expected {expected_rows} pass/0 fail/0 error, "
                f"received {passed}/{failed}/{errored}"
            )
    return failures


def validate_evidence(
    evidence: dict[str, Any],
    evaluation_config: dict[str, Any],
) -> None:
    required = {
        "evaluation_id",
        "run_id",
        "status",
        "candidate_version",
        "metrics",
        "failures",
        "passed",
    }
    if not required.issubset(evidence):
        raise RuntimeError("evaluation evidence is incomplete")
    if evidence["status"] != "completed" or evidence["failures"]:
        raise RuntimeError("evaluation evidence is unsuccessful")
    metrics = evidence["metrics"]
    for name in evaluation_config["required_metrics"]:
        if name not in metrics:
            raise RuntimeError(f"evaluation evidence is missing {name}")
    if metrics["classification_accuracy"] < evaluation_config["minimum_accuracy"]:
        raise RuntimeError("classification accuracy is below the release threshold")
    if metrics["schema_validity"] < evaluation_config["minimum_schema_validity"]:
        raise RuntimeError("schema validity is below the release threshold")
    if evidence["passed"] is not True:
        raise RuntimeError("evaluation evidence did not pass")


def validate_release_record(
    record: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
    endpoints: dict[str, str] | None = None,
    expected_commit: str | None = None,
    key: str | None = None,
) -> None:
    required = {
        "record_type",
        "record_id",
        "created_at",
        "completed_at",
        "commit",
        "approver",
        "agent_name",
        "change_reference",
        "aliases",
        "environments",
        "evaluation",
        "project_endpoints",
        "record_hmac_sha256",
        "status",
    }
    missing = required - record.keys()
    if missing:
        raise RuntimeError(f"release record is incomplete: missing {sorted(missing)}")
    if not record["commit"] or not record["approver"]:
        raise RuntimeError("release record requires commit and approver")
    expected_signature = record_signature(record, key)
    if not hmac.compare_digest(
        expected_signature,
        record["record_hmac_sha256"],
    ):
        raise RuntimeError("release record HMAC signature does not match")
    if expected_commit is not None and record["commit"] != expected_commit:
        raise RuntimeError("release record commit does not match the current release commit")
    if manifest is not None:
        if record["agent_name"] != manifest["canonical_agent_name"]:
            raise RuntimeError("release record agent does not match the manifest")
        if record["change_reference"] != manifest["release"]["change_reference"]:
            raise RuntimeError("release record change reference does not match the manifest")
    if endpoints is not None and record["project_endpoints"] != endpoints:
        raise RuntimeError("release record environment endpoints do not match current config")
    if record["status"] == "promoted":
        promotion = record["environments"].get("prod", {}).get("promotion")
        if not promotion or not record.get("conversation_state"):
            raise RuntimeError(
                "promoted release record lacks promotion or state evidence"
            )
        if (
            promotion.get("stable_endpoint_before")
            != promotion.get("stable_endpoint_after")
        ):
            raise RuntimeError("release record shows a changed stable endpoint")
        if endpoints is not None:
            expected_endpoint = stable_endpoint(
                endpoints["prod"],
                record["agent_name"],
            )
            if promotion.get("stable_endpoint_after") != expected_endpoint:
                raise RuntimeError("release record stable endpoint does not match prod")
        if manifest is not None and str(promotion.get("previous_version")) != str(
            manifest["release"]["previous_approved_version"]
        ):
            raise RuntimeError("release record previous version does not match manifest")
        if str(promotion.get("promoted_version")) == str(
            promotion.get("previous_version")
        ):
            raise RuntimeError("release record did not change versions")
        if str(promotion.get("selector_precondition_observed")) != str(
            promotion.get("previous_version")
        ):
            raise RuntimeError("release record selector precondition is inconsistent")
        if str(promotion.get("selector_postcondition_observed")) != str(
            promotion.get("promoted_version")
        ):
            raise RuntimeError("release record selector postcondition is inconsistent")
    elif record["status"] == "blocked":
        if record.get("production_unchanged") is not True:
            raise RuntimeError("blocked record lacks production-unchanged evidence")
    else:
        raise RuntimeError(f"release record status {record['status']!r} is not final")


def promote_if_passing(
    adapter,
    *,
    candidate_version: str,
    evidence: dict[str, Any],
    evaluation_config: dict[str, Any],
    expected_previous: str | None = None,
    mutation_callback=None,
) -> dict[str, Any]:
    validate_evidence(evidence, evaluation_config)
    endpoint_before = adapter.endpoint_url()
    version_before = adapter.selected_version()
    if expected_previous is not None and version_before != str(expected_previous):
        raise RuntimeError(
            f"selector drifted to {version_before}; expected {expected_previous}"
        )
    if mutation_callback is not None:
        mutation_callback()
    adapter.pin(candidate_version)
    endpoint_after = adapter.endpoint_url()
    version_after = adapter.selected_version()
    if endpoint_after != endpoint_before:
        raise RuntimeError("stable endpoint URL changed during promotion")
    if version_after != str(candidate_version):
        raise RuntimeError(
            f"publishing mismatch: selector is {version_after}, expected {candidate_version}"
        )
    return {
        "previous_version": version_before,
        "promoted_version": version_after,
        "selector_precondition_observed": version_before,
        "selector_postcondition_observed": version_after,
        "stable_endpoint_before": endpoint_before,
        "stable_endpoint_after": endpoint_after,
    }


def rollback_release(
    adapter,
    *,
    previous_version: str,
    promoted_version: str,
    conversation_state: dict[str, Any],
    mutation_callback=None,
) -> dict[str, Any]:
    if adapter.selected_version() != str(promoted_version):
        raise RuntimeError(
            "rollback record is stale: current selector is not the recorded promoted version"
        )
    endpoint_before = adapter.endpoint_url()
    if mutation_callback is not None:
        mutation_callback()
    adapter.pin(previous_version)
    if adapter.selected_version() != str(previous_version):
        raise RuntimeError("rollback selector verification failed")
    convergence_smoke = adapter.smoke("approved")
    conversation_id = conversation_state["conversation_id"]
    continuity = adapter.continue_conversation(
        conversation_id,
        expected_release="approved",
    )
    if (
        continuity["metadata"].get("release_sentinel")
        != "preserve-across-selector-change"
    ):
        raise RuntimeError("conversation state sentinel was not preserved")
    if continuity["item_count_after"] <= conversation_state["item_count_before"]:
        raise RuntimeError("conversation did not continue after rollback")
    endpoint_after = adapter.endpoint_url()
    if endpoint_after != endpoint_before:
        raise RuntimeError("stable endpoint URL changed during rollback")
    return {
        "rolled_back_to": str(previous_version),
        "stable_endpoint_before": endpoint_before,
        "stable_endpoint_after": endpoint_after,
        "conversation_id": conversation_id,
        "state_preserved": True,
        "routing_convergence_smoke": convergence_smoke,
        "conversation_items_before": conversation_state["item_count_before"],
        "conversation_items_after": continuity["item_count_after"],
        "continued_release": continuity["continued_release"],
    }


def compensate_release(
    adapter,
    *,
    previous_version: str,
    toolbox_update: dict[str, Any] | None,
) -> dict[str, Any]:
    if toolbox_update is not None:
        adapter.restore_toolbox_default(toolbox_update)
    adapter.pin(previous_version)
    smoke = adapter.smoke("approved")
    return {
        "selector": str(previous_version),
        "stable_endpoint": adapter.endpoint_url(),
        "smoke": smoke,
        "toolbox_restored": toolbox_update is not None,
    }


def compensate_rollback(
    adapter,
    *,
    promoted_version: str,
    toolbox_update: dict[str, Any] | None,
    toolbox_mutated: bool,
) -> dict[str, Any]:
    if toolbox_update is not None and toolbox_mutated:
        adapter.set_toolbox_default(
            toolbox_update["name"],
            toolbox_update["default"],
        )
    adapter.pin(promoted_version)
    smoke = adapter.smoke("candidate")
    return {
        "selector": str(promoted_version),
        "stable_endpoint": adapter.endpoint_url(),
        "smoke": smoke,
        "toolbox_restored_to_promoted": bool(
            toolbox_update is not None and toolbox_mutated
        ),
    }


class FoundryEnvironment:
    def __init__(
        self,
        name: str,
        endpoint: str,
        credential,
        agent_name: str,
        model: str,
    ):
        self.name = name
        self.project_endpoint = endpoint
        self.agent_name = agent_name
        self.model = model
        self.credential = credential
        self.project = AIProjectClient(endpoint=endpoint, credential=credential)
        self.openai = self.project.get_openai_client()

    def close(self) -> None:
        self.openai.close()
        self.project.close()

    def endpoint_url(self) -> str:
        return stable_endpoint(self.project_endpoint, self.agent_name)

    def get_agent(self):
        details = self.project.agents.get(self.agent_name)
        assert_current_agent(details)
        return details

    def exists(self) -> bool:
        try:
            self.get_agent()
            return True
        except ResourceNotFoundError:
            return False

    def selected_version(self) -> str:
        return selected_version(self.get_agent())

    def wait_for_selected_version(
        self,
        expected: str,
        *,
        attempts: int = 12,
        delay_seconds: int = 5,
    ) -> str:
        observed = ""
        for attempt in range(attempts):
            observed = self.selected_version()
            if observed == str(expected):
                return observed
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        raise RuntimeError(
            f"{self.name}: selector did not converge to {expected}; last observed {observed}"
        )

    def verify_pre_promotion_isolation(
        self,
        previous_version: str,
        *,
        attempts: int = 12,
        delay_seconds: int = 5,
    ) -> dict[str, Any]:
        last_version = ""
        last_output = ""
        for attempt in range(attempts):
            last_version = self.selected_version()
            last_output = self.invoke(
                "A reporting API is timing out. Route this request."
            )
            category, approved = parse_agent_output(last_output, "approved")
            _, candidate = parse_agent_output(last_output, "candidate")
            if candidate:
                raise RuntimeError(
                    "unsafe activation: stable endpoint served the candidate before "
                    "the explicit production selector update"
                )
            if last_version == str(previous_version) and approved and category == "BR2":
                return {
                    "selected_version": last_version,
                    "release": "approved",
                    "endpoint": self.endpoint_url(),
                }
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        raise RuntimeError(
            "pre-promotion isolation did not converge to the prior selector and behavior; "
            f"last selector {last_version}, output {last_output[:160]!r}"
        )

    def create_version(self, instructions: str) -> str:
        try:
            version = self.project.agents.create_version(
                agent_name=self.agent_name,
                definition=PromptAgentDefinition(
                    model=self.model,
                    instructions=instructions,
                    temperature=0,
                    tools=[],
                    text=PromptAgentDefinitionTextOptions(
                        format=TextResponseFormatJsonSchema(
                            name="lifecycle_route",
                            description="Stable route and release marker.",
                            schema=AGENT_RESPONSE_SCHEMA,
                            strict=True,
                        )
                    ),
                ),
            )
        except HttpResponseError as error:
            raise RuntimeError(
                f"{self.name}: version creation failed (permission or definition error): "
                f"{error.message}"
            ) from error
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            current = self.project.agents.get_version(
                self.agent_name,
                str(version.version),
            )
            status = normalized_status(current.status)
            if status == "active":
                self.get_agent()
                return str(current.version)
            if status == "failed":
                raise RuntimeError(f"{self.name}: agent version creation failed")
            time.sleep(5)
        raise TimeoutError(f"{self.name}: agent version did not become active")

    def pin(self, version: str) -> None:
        endpoint = AgentEndpointConfig(
            version_selector=VersionSelector(
                version_selection_rules=[
                    FixedRatioVersionSelectionRule(
                        agent_version=str(version),
                        traffic_percentage=100,
                    )
                ]
            )
        )
        try:
            self.project.agents.update_details(
                self.agent_name,
                agent_endpoint=endpoint,
            )
        except HttpResponseError as error:
            raise RuntimeError(
                f"{self.name}: selector update failed: {error.message}"
            ) from error
        self.wait_for_selected_version(str(version))

    def stable_protocol_base(self) -> str:
        return self.endpoint_url().removesuffix("/responses")

    def _stable_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self.credential.get_token("https://ai.azure.com/.default").token
        try:
            response = requests.request(
                method,
                self.stable_protocol_base() + path + "?api-version=v1",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=120,
            )
        except requests.RequestException as error:
            raise RuntimeError(
                f"{self.name}: stable protocol {path} request failed"
            ) from error
        if response.status_code != 200:
            raise RuntimeError(
                f"{self.name}: stable protocol {path} returned HTTP "
                f"{response.status_code}"
            )
        try:
            return response.json()
        except requests.JSONDecodeError as error:
            raise RuntimeError(
                f"{self.name}: stable protocol {path} returned invalid JSON"
            ) from error

    def invoke(self, prompt: str, conversation_id: str | None = None) -> str:
        body: dict[str, Any] = {"input": prompt}
        if conversation_id:
            body["conversation"] = conversation_id
        payload = self._stable_request("POST", "/responses", body=body)
        if payload.get("status") != "completed":
            raise RuntimeError(
                f"{self.name}: stable endpoint response did not complete"
            )
        text = "".join(
            content.get("text", "")
            for item in payload.get("output", [])
            for content in item.get("content", [])
            if content.get("type") == "output_text"
        ).strip()
        if not text:
            raise RuntimeError(f"{self.name}: agent response was empty")
        return text

    def smoke(
        self,
        expected_release: str,
        *,
        attempts: int = 12,
        delay_seconds: int = 5,
    ) -> dict[str, Any]:
        last = ""
        for attempt in range(attempts):
            last = self.invoke("A reporting API is timing out. Route this request.")
            category, valid = parse_agent_output(last, expected_release)
            if valid and category == "BR2":
                return {"category": category, "release": expected_release}
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
        raise RuntimeError(
            f"{self.name}: smoke test did not observe selector propagation; "
            f"last output {last[:200]!r}"
        )

    def create_conversation_sentinel(self) -> dict[str, Any]:
        conversation = self._stable_request(
            "POST",
            "/conversations",
            body={
                "metadata": {
                    "pattern": "15",
                    "release_sentinel": "preserve-across-selector-change",
                }
            },
        )
        conversation_id = conversation["id"]
        output = self.invoke(
            "A reporting API is timing out. Route this request.",
            conversation_id,
        )
        _, valid = parse_agent_output(output, "approved")
        if not valid:
            raise RuntimeError(
                "stable endpoint conversation did not begin on approved behavior"
            )
        items = self._stable_request(
            "GET",
            f"/conversations/{conversation_id}/items",
        )
        return {
            "conversation_id": conversation_id,
            "item_count_before": len(items.get("data", [])),
            "initial_release": "approved",
        }

    def continue_conversation(
        self,
        conversation_id: str,
        *,
        expected_release: str,
    ) -> dict[str, Any]:
        conversation = self._stable_request(
            "GET",
            f"/conversations/{conversation_id}",
        )
        output = self.invoke(
            "An account needs read access. Route this request.",
            conversation_id,
        )
        _, valid = parse_agent_output(output, expected_release)
        if not valid:
            raise RuntimeError(
                "stable endpoint conversation did not continue on expected behavior"
            )
        items = self._stable_request(
            "GET",
            f"/conversations/{conversation_id}/items",
        )
        return {
            "metadata": dict(conversation.get("metadata") or {}),
            "item_count_after": len(items.get("data", [])),
            "continued_release": expected_release,
        }

    def update_toolbox_default(
        self,
        toolbox_name: str | None,
        toolbox_version: str | None,
        changed: bool,
    ) -> dict[str, Any] | None:
        if not changed:
            return None
        if not toolbox_name or not toolbox_version:
            raise RuntimeError(
                "tools changed but Toolbox alias/version did not resolve"
            )
        toolbox = self.project.toolboxes.get(toolbox_name)
        before = str(toolbox.default_version)
        self.project.toolboxes.get_version(toolbox_name, toolbox_version)
        self.project.toolboxes.update(
            toolbox_name,
            default_version=toolbox_version,
        )
        after = str(self.project.toolboxes.get(toolbox_name).default_version)
        if after != str(toolbox_version):
            self.project.toolboxes.update(
                toolbox_name,
                default_version=before,
            )
            raise RuntimeError("Toolbox default promotion did not match requested version")
        return {"name": toolbox_name, "previous": before, "default": after}

    def set_toolbox_default(self, toolbox_name: str, version: str) -> None:
        self.project.toolboxes.update(
            toolbox_name,
            default_version=version,
        )
        observed = str(
            self.project.toolboxes.get(toolbox_name).default_version
        )
        if observed != str(version):
            raise RuntimeError(
                f"Toolbox default did not converge to requested version {version}"
            )

    def restore_toolbox_default(
        self,
        update: dict[str, Any],
        *,
        mutation_callback=None,
    ) -> None:
        current = str(
            self.project.toolboxes.get(update["name"]).default_version
        )
        if current != str(update["default"]):
            raise RuntimeError(
                "Toolbox default drifted since release; rollback record is stale"
            )
        if mutation_callback is not None:
            mutation_callback()
        self.project.toolboxes.update(
            update["name"],
            default_version=update["previous"],
        )
        restored = str(
            self.project.toolboxes.get(update["name"]).default_version
        )
        if restored != str(update["previous"]):
            raise RuntimeError("Toolbox default recovery did not restore prior version")

    def verify_aliases(
        self,
        resolved: dict[str, dict[str, str | None]],
    ) -> dict[str, Any]:
        verified: dict[str, Any] = {"connections": {}, "toolboxes": {}}
        for alias, connection_name in resolved["connections"].items():
            if connection_name is None:
                verified["connections"][alias] = None
                continue
            connection = self.project.connections.get(connection_name)
            verified["connections"][alias] = connection.id
        for alias, toolbox_name in resolved["toolboxes"].items():
            if toolbox_name is None:
                verified["toolboxes"][alias] = None
                continue
            toolbox = self.project.toolboxes.get(toolbox_name)
            verified["toolboxes"][alias] = {
                "name": toolbox_name,
                "default_version": str(toolbox.default_version),
            }
        return verified


def evaluate_candidate(
    environment: FoundryEnvironment,
    manifest: dict[str, Any],
    candidate_version: str,
    *,
    demo_failure: bool,
) -> dict[str, Any]:
    dataset_path = BASE_DIR / manifest["evaluation"]["dataset"]
    rows = read_jsonl(dataset_path)
    generated = []
    for index, row in enumerate(rows, start=1):
        text = environment.invoke(row["input"])
        category, valid = parse_agent_output(text, "candidate")
        if demo_failure and index == 1:
            category = "UNSUPPORTED" if row["expected_category"] != "UNSUPPORTED" else "AX7"
        generated.append(
            {
                **row,
                "response_category": category,
                "schema_valid": "true" if valid else "false",
            }
        )
    local_accuracy = sum(
        row["response_category"] == row["expected_category"] for row in generated
    ) / len(generated)
    local_schema = sum(row["schema_valid"] == "true" for row in generated) / len(
        generated
    )

    version = datetime.now().strftime("%Y%m%d%H%M%S")
    with tempfile.TemporaryDirectory(prefix="pattern15-eval-") as temp:
        generated_path = Path(temp) / "candidate.jsonl"
        with generated_path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in generated:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        data_id = environment.project.datasets.upload_file(
            name="pattern15-agent-release",
            version=version,
            file_path=str(generated_path),
        ).id

    evaluation = environment.openai.evals.create(
        name="pattern15-agent-release-gate",
        data_source_config=DataSourceConfigCustom(
            type="custom",
            item_schema=EVAL_ITEM_SCHEMA,
        ),
        testing_criteria=[
            {
                "type": "string_check",
                "name": "classification_accuracy",
                "input": "{{item.response_category}}",
                "reference": "{{item.expected_category}}",
                "operation": "eq",
            },
            {
                "type": "string_check",
                "name": "schema_validity",
                "input": "{{item.schema_valid}}",
                "reference": "true",
                "operation": "eq",
            },
        ],
    )
    run = environment.openai.evals.runs.create(
        eval_id=evaluation.id,
        name=f"candidate-{candidate_version}-{version}"
        + ("-demo-failure" if demo_failure else ""),
        data_source=CreateEvalJSONLRunDataSourceParam(
            type="jsonl",
            source=SourceFileID(type="file_id", id=data_id),
        ),
    )
    while run.status in ("queued", "in_progress"):
        time.sleep(5)
        run = environment.openai.evals.runs.retrieve(
            run_id=run.id,
            eval_id=evaluation.id,
        )
    required = set(manifest["evaluation"]["required_metrics"])
    failures = gate_failures(
        run,
        expected_rows=len(generated),
        required_metrics=required,
    )
    return {
        "evaluation_id": evaluation.id,
        "run_id": run.id,
        "status": run.status,
        "candidate_version": str(candidate_version),
        "metrics": {
            "classification_accuracy": local_accuracy,
            "schema_validity": local_schema,
        },
        "report_url": getattr(run, "report_url", None),
        "failures": failures,
        "passed": not failures
        and local_accuracy >= manifest["evaluation"]["minimum_accuracy"]
        and local_schema >= manifest["evaluation"]["minimum_schema_validity"],
        "demo_failure": demo_failure,
    }


def git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def bootstrap(manifest: dict[str, Any], approver: str) -> Path:
    endpoints = project_endpoints()
    aliases = resolve_aliases(manifest)
    model = aliases["models"][manifest["model_alias"]]
    assert model is not None
    credential = DefaultAzureCredential(process_timeout=30)
    environments = {
        name: FoundryEnvironment(
            name,
            endpoint,
            credential,
            manifest["canonical_agent_name"],
            model,
        )
        for name, endpoint in endpoints.items()
    }
    record = {
        "record_type": "bootstrap",
        "record_id": f"bootstrap-{uuid4()}",
        "created_at": now_iso(),
        "commit": git_commit(),
        "approver": approver,
        "agent_name": manifest["canonical_agent_name"],
        "environments": {},
    }
    try:
        instructions = (BASE_DIR / manifest["previous_instructions_file"]).read_text(
            encoding="utf-8"
        )
        for name, environment in environments.items():
            if environment.exists():
                raise RuntimeError(
                    f"{name}: agent already exists; bootstrap refuses to overwrite it"
                )
            verified_aliases = environment.verify_aliases(aliases)
            version = environment.create_version(instructions)
            if version != str(manifest["release"]["previous_approved_version"]):
                raise RuntimeError(
                    f"{name}: bootstrap version {version} does not match manifest previous "
                    f"version {manifest['release']['previous_approved_version']}"
                )
            environment.pin(version)
            smoke = environment.smoke("approved")
            record["environments"][name] = {
                "aliases": verified_aliases,
                "version": version,
                "endpoint": environment.endpoint_url(),
                "smoke": smoke,
            }
    finally:
        for environment in environments.values():
            environment.close()
        credential.close()
    path = ARTIFACT_DIR / f"{record['record_id']}.json"
    write_json(path, record)
    return path


def release(manifest: dict[str, Any], approver: str, demo_failure: bool) -> Path:
    endpoints = project_endpoints()
    aliases = resolve_aliases(manifest)
    model = aliases["models"][manifest["model_alias"]]
    assert model is not None
    credential = DefaultAzureCredential(process_timeout=30)
    environments = {
        name: FoundryEnvironment(
            name,
            endpoint,
            credential,
            manifest["canonical_agent_name"],
            model,
        )
        for name, endpoint in endpoints.items()
    }
    record = {
        "record_type": "release",
        "record_id": f"release-{uuid4()}",
        "created_at": now_iso(),
        "commit": git_commit(),
        "approver": approver,
        "agent_name": manifest["canonical_agent_name"],
        "change_reference": manifest["release"]["change_reference"],
        "aliases": aliases,
        "project_endpoints": endpoints,
        "environments": {},
        "status": "started",
    }
    path = ARTIFACT_DIR / f"{record['record_id']}.json"
    previous = str(manifest["release"]["previous_approved_version"])
    mutation: dict[str, Any] = {"agent": False, "toolbox": None}
    try:
        for name, environment in environments.items():
            if not environment.exists():
                raise RuntimeError(f"{name}: agent is missing; run bootstrap first")
            record["environments"].setdefault(name, {})["aliases"] = (
                environment.verify_aliases(aliases)
            )
        environments["prod"].wait_for_selected_version(previous)
        record["environments"]["prod"]["initial_smoke"] = environments["prod"].smoke(
            "approved"
        )
        prod_endpoint_before = environments["prod"].endpoint_url()
        conversation_state = environments["prod"].create_conversation_sentinel()

        candidate_instructions = (
            BASE_DIR / manifest["instructions_file"]
        ).read_text(encoding="utf-8")
        dev_version = environments["dev"].create_version(candidate_instructions)
        environments["dev"].pin(dev_version)
        record["environments"]["dev"].update({
            "candidate_version": dev_version,
            "smoke": environments["dev"].smoke("candidate"),
        })

        test_version = environments["test"].create_version(candidate_instructions)
        environments["test"].pin(test_version)
        record["environments"]["test"].update({
            "candidate_version": test_version,
            "smoke": environments["test"].smoke("candidate"),
        })
        evidence = evaluate_candidate(
            environments["test"],
            manifest,
            test_version,
            demo_failure=demo_failure,
        )
        record["evaluation"] = evidence
        if demo_failure:
            if evidence["passed"]:
                raise RuntimeError("demo-failure evaluation unexpectedly passed")
            environments["prod"].wait_for_selected_version(previous)
            record["environments"]["prod"]["blocked_smoke"] = environments[
                "prod"
            ].smoke("approved")
            record["status"] = "blocked"
            record["production_unchanged"] = True
            record["completed_at"] = now_iso()
        else:
            validate_evidence(evidence, manifest["evaluation"])
            prod_version = environments["prod"].create_version(
                candidate_instructions
            )
            pre_promotion = environments["prod"].verify_pre_promotion_isolation(
                previous
            )

            def mark_agent_mutated():
                mutation["agent"] = True

            promotion = promote_if_passing(
                environments["prod"],
                candidate_version=prod_version,
                evidence=evidence,
                evaluation_config=manifest["evaluation"],
                expected_previous=previous,
                mutation_callback=mark_agent_mutated,
            )
            promoted_smoke = environments["prod"].smoke("candidate")
            toolbox = manifest["aliases"]["toolboxes"]["governed-tools"]
            toolbox_update = environments["prod"].update_toolbox_default(
                aliases["toolboxes"]["governed-tools"],
                (
                    os.environ.get(toolbox["version_environment_variable"], "").strip()
                    or None
                ),
                bool(toolbox["changed"]),
            )
            mutation["toolbox"] = toolbox_update
            if promotion["stable_endpoint_after"] != prod_endpoint_before:
                raise RuntimeError("production stable endpoint changed")
            record["environments"]["prod"].update({
                "candidate_version": prod_version,
                "pre_promotion_isolation": pre_promotion,
                "promotion": promotion,
                "smoke": promoted_smoke,
                "toolbox": toolbox_update,
            })
            record["conversation_state"] = conversation_state
            record["status"] = "promoted"
            record["completed_at"] = now_iso()
        seal_record(record)
        validate_release_record(
            record,
            manifest=manifest,
            endpoints=endpoints,
            expected_commit=record["commit"],
        )
        write_json(path, record)
    except (ValueError, RuntimeError, TimeoutError, HttpResponseError, OSError) as error:
        recovery_error = None
        if mutation["agent"]:
            try:
                record["recovery"] = compensate_release(
                    environments["prod"],
                    previous_version=previous,
                    toolbox_update=mutation["toolbox"],
                )
            except (RuntimeError, TimeoutError, HttpResponseError) as caught:
                recovery_error = caught
                record["recovery_error"] = str(caught)
        record["status"] = "failed"
        record["error"] = str(error)
        record["completed_at"] = now_iso()
        seal_record(record)
        write_json(path, record)
        if recovery_error is not None:
            raise RuntimeError(
                f"release failed ({error}) and compensating rollback failed "
                f"({recovery_error})"
            ) from error
        raise
    finally:
        for environment in environments.values():
            environment.close()
        credential.close()
    return path


def rollback(manifest: dict[str, Any], record_path: Path, approver: str) -> Path:
    release_record = read_json(record_path)
    endpoints = project_endpoints()
    validate_release_record(
        release_record,
        manifest=manifest,
        endpoints=endpoints,
        expected_commit=git_commit(),
    )
    aliases = resolve_aliases(manifest)
    model = aliases["models"][manifest["model_alias"]]
    assert model is not None
    credential = DefaultAzureCredential(process_timeout=30)
    environment = FoundryEnvironment(
        "prod",
        endpoints["prod"],
        credential,
        manifest["canonical_agent_name"],
        model,
    )
    rollback_mutated = False
    toolbox_mutated = False
    toolbox_update = release_record["environments"]["prod"].get("toolbox")
    promoted = release_record["environments"]["prod"]["promotion"][
        "promoted_version"
    ]
    previous = release_record["environments"]["prod"]["promotion"][
        "previous_version"
    ]
    path = ARTIFACT_DIR / f"rollback-{uuid4()}.json"
    try:
        def mark_rollback_mutated():
            nonlocal rollback_mutated
            rollback_mutated = True

        result = rollback_release(
            environment,
            previous_version=previous,
            promoted_version=promoted,
            conversation_state=release_record["conversation_state"],
            mutation_callback=mark_rollback_mutated,
        )
        if toolbox_update is not None:
            def mark_toolbox_mutated():
                nonlocal toolbox_mutated
                toolbox_mutated = True

            environment.restore_toolbox_default(
                toolbox_update,
                mutation_callback=mark_toolbox_mutated,
            )
            result["toolbox"] = {
                "name": toolbox_update["name"],
                "restored_default": toolbox_update["previous"],
            }
        result["smoke"] = environment.smoke("approved")
        rollback_record = {
            "record_type": "rollback",
            "record_id": path.stem,
            "created_at": now_iso(),
            "commit": git_commit(),
            "approver": approver,
            "agent_name": manifest["canonical_agent_name"],
            "change_reference": manifest["release"]["change_reference"],
            "project_endpoints": endpoints,
            "source_release_record": release_record["record_id"],
            "source_release_hmac_sha256": release_record["record_hmac_sha256"],
            "result": result,
            "status": "rolled_back",
        }
        seal_record(rollback_record)
        write_json(path, rollback_record)
    except (OSError, RuntimeError, TimeoutError, HttpResponseError) as error:
        if rollback_mutated:
            try:
                compensate_rollback(
                    environment,
                    promoted_version=promoted,
                    toolbox_update=toolbox_update,
                    toolbox_mutated=toolbox_mutated,
                )
            except (RuntimeError, TimeoutError, HttpResponseError) as recovery_error:
                raise RuntimeError(
                    f"rollback failed ({error}) and promoted-version recovery failed "
                    f"({recovery_error})"
                ) from error
            raise RuntimeError(
                f"rollback failed ({error}); restored promoted version"
            ) from error
        raise
    finally:
        environment.close()
        credential.close()
    return path


def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--approver", required=True)
    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--approver", required=True)
    release_parser.add_argument("--demo-failure", action="store_true")
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--record", type=Path, required=True)
    rollback_parser.add_argument("--approver", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = validate_manifest(read_json(MANIFEST_PATH))
    if args.command == "validate":
        resolved = resolve_aliases(manifest)
        endpoints = project_endpoints()
        print(
            json.dumps(
                {
                    "manifest": "valid",
                    "agent": manifest["canonical_agent_name"],
                    "aliases": resolved,
                    "environments": sorted(endpoints),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "bootstrap":
        print(f"Bootstrap record: {bootstrap(manifest, args.approver)}")
        return 0
    if args.command == "release":
        path = release(manifest, args.approver, args.demo_failure)
        record = read_json(path)
        print(f"Release record: {path}")
        return 0 if record["status"] == "promoted" else 1
    if args.command == "rollback":
        print(f"Rollback record: {rollback(manifest, args.record, args.approver)}")
        return 0
    raise AssertionError(f"unknown command {args.command}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        ValueError,
        RuntimeError,
        TimeoutError,
        HttpResponseError,
    ) as error:
        print(f"AGENT LIFECYCLE FAILED: {error}", file=sys.stderr)
        sys.exit(1)
