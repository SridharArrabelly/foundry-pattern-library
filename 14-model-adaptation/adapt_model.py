"""Pattern 14: baseline, fine-tune, deploy, evaluate, gate, and clean up.

The data plane uses AIProjectClient.get_openai_client() and its current v1
files/fine_tuning/responses APIs. Deployment remains an ARM control-plane operation.
"""
from __future__ import annotations

import argparse
from io import BytesIO
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import requests
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai import NotFoundError, OpenAIError


load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
TRAIN_PATH = BASE_DIR / "train.jsonl"
VALIDATION_PATH = BASE_DIR / "validation.jsonl"
TEST_PATH = BASE_DIR / "test.jsonl"
INSTRUCTIONS_PATH = BASE_DIR / "instructions.md"
THRESHOLDS_PATH = BASE_DIR / "release_thresholds.json"
ARTIFACT_DIR = Path(
    os.environ.get("FINETUNE_ARTIFACT_DIR", BASE_DIR / "artifacts")
)

CATEGORIES = {
    "AX7",
    "BR2",
    "CZ9",
    "UNSUPPORTED",
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": sorted(CATEGORIES)},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 160},
    },
    "required": ["category", "rationale"],
    "additionalProperties": False,
}
PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,}(?!\d)"),
    "credit-card-like": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    "government-id-like": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}
SUCCESS_STATES = {"succeeded"}
TERMINAL_STATES = SUCCESS_STATES | {"failed", "cancelled", "canceled"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluation_protocol_hash() -> str:
    protocol = {
        "instructions_sha256": file_hash(INSTRUCTIONS_PATH),
        "output_schema": OUTPUT_SCHEMA,
        "temperature": 0,
        "test_sha256": file_hash(TEST_PATH),
        "thresholds_sha256": file_hash(THRESHOLDS_PATH),
        "metrics": [
            "schema_validity",
            "classification_accuracy",
            "task_adherence",
            "input_tokens",
            "output_tokens",
            "latency",
        ],
    }
    return hashlib.sha256(
        json.dumps(
            protocol,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name}:{line_number} is not valid JSON") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{line_number} must be a JSON object")
            rows.append(value)
    if not rows:
        raise ValueError(f"{path.name} contains no rows")
    return rows


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_no_pii(text: str, where: str) -> None:
    for name, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            raise ValueError(f"{where} contains {name} data")


def validate_output_text(text: str) -> dict[str, str]:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("output is not valid JSON") from error
    if not isinstance(value, dict) or set(value) != {"category", "rationale"}:
        raise ValueError("output must contain exactly category and rationale")
    if value["category"] not in CATEGORIES:
        raise ValueError("output category is unsupported")
    rationale = value["rationale"]
    if (
        not isinstance(rationale, str)
        or not rationale.strip()
        or len(rationale) > 160
        or "\n" in rationale
    ):
        raise ValueError("rationale must be one concise non-empty line (max 160 chars)")
    return {"category": value["category"], "rationale": rationale.strip()}


def training_example(row: dict[str, Any], where: str) -> tuple[str, str]:
    if set(row) != {"messages"} or not isinstance(row["messages"], list):
        raise ValueError(f"{where} must contain only a messages array")
    messages = row["messages"]
    if len(messages) != 3:
        raise ValueError(f"{where} must contain system, user, and assistant messages")
    expected_roles = ("system", "user", "assistant")
    for message, role in zip(messages, expected_roles):
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] != role
            or not isinstance(message["content"], str)
            or not message["content"].strip()
        ):
            raise ValueError(f"{where} has a malformed {role} message")
        validate_no_pii(message["content"], where)
    output = validate_output_text(messages[2]["content"])
    return messages[1]["content"].strip(), output["category"]


def test_example(row: dict[str, Any], where: str) -> tuple[str, str, str]:
    if set(row) != {"id", "input", "expected_category"}:
        raise ValueError(f"{where} must contain exactly id, input, expected_category")
    if not all(isinstance(row[key], str) and row[key].strip() for key in row):
        raise ValueError(f"{where} fields must be non-empty strings")
    if row["expected_category"] not in CATEGORIES:
        raise ValueError(f"{where} expected_category is unsupported")
    validate_no_pii(row["input"], where)
    return row["id"], row["input"].strip(), row["expected_category"]


def validate_datasets() -> dict[str, Any]:
    train = read_jsonl(TRAIN_PATH)
    validation = read_jsonl(VALIDATION_PATH)
    test = read_jsonl(TEST_PATH)
    if len(train) < 10:
        raise ValueError("fine-tuning requires at least 10 training examples")
    if len(validation) < 4:
        raise ValueError("validation set must contain at least 4 examples")

    seen_inputs: dict[str, str] = {}
    seen_ids: set[str] = set()
    distribution: dict[str, dict[str, int]] = {}
    for split, rows in (("train", train), ("validation", validation)):
        distribution[split] = {category: 0 for category in sorted(CATEGORIES)}
        for index, row in enumerate(rows, start=1):
            user_input, category = training_example(row, f"{split}:{index}")
            key = user_input.casefold()
            if key in seen_inputs:
                raise ValueError(
                    f"duplicate input across splits: {split}:{index} and {seen_inputs[key]}"
                )
            seen_inputs[key] = f"{split}:{index}"
            distribution[split][category] += 1

    distribution["test"] = {category: 0 for category in sorted(CATEGORIES)}
    for index, row in enumerate(test, start=1):
        row_id, user_input, category = test_example(row, f"test:{index}")
        if row_id in seen_ids:
            raise ValueError(f"duplicate held-out id: {row_id}")
        seen_ids.add(row_id)
        key = user_input.casefold()
        if key in seen_inputs:
            raise ValueError(
                f"held-out leakage: test:{index} duplicates {seen_inputs[key]}"
            )
        seen_inputs[key] = f"test:{index}"
        distribution["test"][category] += 1

    for split, counts in distribution.items():
        missing = [category for category, count in counts.items() if count == 0]
        if missing:
            raise ValueError(f"{split} is missing categories: {missing}")

    return {
        "validated_at": now_iso(),
        "counts": {
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        },
        "distribution": distribution,
        "hashes": {
            "train_sha256": file_hash(TRAIN_PATH),
            "validation_sha256": file_hash(VALIDATION_PATH),
            "test_sha256": file_hash(TEST_PATH),
            "instructions_sha256": file_hash(INSTRUCTIONS_PATH),
            "thresholds_sha256": file_hash(THRESHOLDS_PATH),
        },
        "held_out_separate": True,
        "pii_scan": "passed",
        "evaluation_protocol_sha256": evaluation_protocol_hash(),
    }


@dataclass(frozen=True)
class Config:
    project_endpoint: str
    resource_id: str
    region: str
    base_model: str
    base_model_version: str
    base_deployment: str
    tuned_deployment: str
    training_type: str
    deployment_sku: str
    n_epochs: int
    batch_size: int
    learning_rate_multiplier: float
    seed: int
    training_price_per_million_usd: float | None
    arm_account_api_version: str
    arm_deployment_api_version: str

    @classmethod
    def from_env(cls) -> "Config":
        price = os.environ.get("FINETUNE_TRAINING_PRICE_PER_MILLION_USD", "").strip()
        return cls(
            project_endpoint=os.environ.get(
                "FINETUNE_PROJECT_ENDPOINT", os.environ.get("PROJECT_ENDPOINT", "")
            ).strip(),
            resource_id=os.environ.get("FINETUNE_RESOURCE_ID", "").rstrip("/"),
            region=os.environ.get("FINETUNE_REGION", "").lower().replace(" ", ""),
            base_model=os.environ.get("FINETUNE_BASE_MODEL", "gpt-4.1-mini"),
            base_model_version=os.environ.get(
                "FINETUNE_BASE_MODEL_VERSION", "2025-04-14"
            ),
            base_deployment=os.environ.get(
                "FINETUNE_BASE_DEPLOYMENT", "gpt-4.1-mini"
            ),
            tuned_deployment=os.environ.get(
                "FINETUNE_DEPLOYMENT_NAME", "triage-adapted-eval"
            ),
            training_type=os.environ.get(
                "FINETUNE_TRAINING_TYPE", "developerTier"
            ),
            deployment_sku=os.environ.get(
                "FINETUNE_DEPLOYMENT_SKU", "DeveloperTier"
            ),
            n_epochs=int(os.environ.get("FINETUNE_N_EPOCHS", "3")),
            batch_size=int(os.environ.get("FINETUNE_BATCH_SIZE", "1")),
            learning_rate_multiplier=float(
                os.environ.get("FINETUNE_LEARNING_RATE_MULTIPLIER", "0.1")
            ),
            seed=int(os.environ.get("FINETUNE_SEED", "42")),
            training_price_per_million_usd=float(price) if price else None,
            arm_account_api_version=os.environ.get(
                "FINETUNE_ARM_ACCOUNT_API_VERSION", "2026-07-01"
            ),
            arm_deployment_api_version=os.environ.get(
                "FINETUNE_ARM_DEPLOYMENT_API_VERSION", "2026-07-01"
            ),
        )

    def validate(self, *, require_price: bool = False) -> None:
        if not self.project_endpoint:
            raise ValueError("Set FINETUNE_PROJECT_ENDPOINT or PROJECT_ENDPOINT")
        if not self.resource_id.startswith("/subscriptions/"):
            raise ValueError("Set FINETUNE_RESOURCE_ID to the Cognitive Services account ID")
        if not self.region:
            raise ValueError("Set FINETUNE_REGION to the resource region")
        if self.training_type not in {"Standard", "GlobalStandard", "developerTier"}:
            raise ValueError(
                "FINETUNE_TRAINING_TYPE wire value must be Standard, "
                "GlobalStandard, or developerTier"
            )
        if (
            self.training_type == "Standard"
            and self.region not in {"swedencentral", "northcentralus"}
        ):
            raise ValueError(
                "gpt-4.1-mini Standard fine-tuning is supported only in "
                "swedencentral or northcentralus"
            )
        if self.deployment_sku != "DeveloperTier":
            raise ValueError(
                "This evaluation pipeline intentionally requires the Developer "
                "evaluation SKU (wire value 'DeveloperTier' for ARM 2026-07-01); "
                "use a separate production promotion process"
            )
        if self.n_epochs <= 0 or self.batch_size <= 0:
            raise ValueError("fine-tuning hyperparameters must be positive")
        if self.learning_rate_multiplier <= 0:
            raise ValueError("learning rate multiplier must be positive")
        if require_price and (
            self.training_price_per_million_usd is None
            or self.training_price_per_million_usd <= 0
        ):
            raise ValueError(
                "Set FINETUNE_TRAINING_PRICE_PER_MILLION_USD from the current "
                "Azure pricing page before starting a billable job"
            )


def project_clients(config: Config):
    credential = DefaultAzureCredential(process_timeout=30)
    project = AIProjectClient(
        endpoint=config.project_endpoint,
        credential=credential,
    )
    return credential, project, project.get_openai_client()


def arm_request(
    credential,
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    expected: frozenset[int] = frozenset({200}),
) -> requests.Response:
    token = credential.get_token("https://management.azure.com/.default").token
    response = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=json_body,
        timeout=60,
    )
    if response.status_code not in expected:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(
            f"ARM {method} failed with HTTP {response.status_code}"
            + (f": {detail[:300]}" if detail else "")
        )
    return response


def account_url(config: Config) -> str:
    return (
        f"https://management.azure.com{config.resource_id}"
        f"?api-version={config.arm_account_api_version}"
    )


def project_name_from_endpoint(endpoint: str) -> str:
    name = unquote(urlsplit(endpoint).path.rstrip("/").split("/")[-1])
    if not name:
        raise ValueError("project endpoint does not contain a project name")
    return name


def deployment_url(config: Config, deployment_name: str | None = None) -> str:
    suffix = f"/{deployment_name}" if deployment_name else ""
    return (
        f"https://management.azure.com{config.resource_id}/deployments{suffix}"
        f"?api-version={config.arm_deployment_api_version}"
    )


def action_allowed(permission_rows: list[dict[str, Any]], action: str) -> bool:
    action = action.lower()
    for row in permission_rows:
        actions = [value.lower() for value in row.get("actions", [])]
        denied = [value.lower() for value in row.get("notActions", [])]
        if any(fnmatchcase(action, pattern) for pattern in actions) and not any(
            fnmatchcase(action, pattern) for pattern in denied
        ):
            return True
    return False


def online_preflight(
    config: Config,
    credential,
    openai_client,
) -> dict[str, Any]:
    config.validate()
    # This call verifies data-plane authentication and fine-tuning job read access.
    list(openai_client.fine_tuning.jobs.list(limit=1))

    account = arm_request(credential, "GET", account_url(config)).json()
    actual_region = str(account.get("location", "")).lower().replace(" ", "")
    if actual_region != config.region:
        raise RuntimeError(
            f"FINETUNE_REGION={config.region!r} does not match resource location "
            f"{actual_region!r}"
        )
    project_name = project_name_from_endpoint(config.project_endpoint)
    project_resource_url = (
        f"https://management.azure.com{config.resource_id}/projects/{project_name}"
        f"?api-version={config.arm_account_api_version}"
    )
    project_resource = arm_request(
        credential, "GET", project_resource_url
    ).json()
    if not project_resource.get("properties", {}).get("isDefault"):
        raise RuntimeError(
            "fine-tuning through the Foundry project endpoint currently requires "
            f"the account's default project; {project_name!r} is not default"
        )

    deployments = arm_request(
        credential, "GET", deployment_url(config)
    ).json().get("value", [])
    base = next(
        (
            item
            for item in deployments
            if item.get("name") == config.base_deployment
        ),
        None,
    )
    if base is None:
        raise RuntimeError(
            f"base deployment {config.base_deployment!r} does not exist on the resource"
        )
    model = base.get("properties", {}).get("model", {})
    if model.get("name") != config.base_model:
        raise RuntimeError(
            f"base deployment uses model {model.get('name')!r}, expected {config.base_model!r}"
        )
    if model.get("version") != config.base_model_version:
        raise RuntimeError(
            f"base deployment version is {model.get('version')!r}, expected "
            f"{config.base_model_version!r}"
        )
    if any(item.get("name") == config.tuned_deployment for item in deployments):
        raise RuntimeError(
            f"temporary deployment name {config.tuned_deployment!r} already exists; "
            "choose a unique FINETUNE_DEPLOYMENT_NAME rather than replacing it"
        )

    permissions_url = (
        f"https://management.azure.com{config.resource_id}/providers/"
        "Microsoft.Authorization/permissions?api-version=2022-04-01"
    )
    permissions = arm_request(
        credential, "GET", permissions_url
    ).json().get("value", [])
    deployment_write = action_allowed(
        permissions, "Microsoft.CognitiveServices/accounts/deployments/write"
    )
    if not deployment_write:
        raise RuntimeError(
            "caller can read fine-tuning jobs but lacks "
            "Microsoft.CognitiveServices/accounts/deployments/write"
        )
    deployment_delete = action_allowed(
        permissions, "Microsoft.CognitiveServices/accounts/deployments/delete"
    )
    if not deployment_delete:
        raise RuntimeError(
            "caller can create deployments but lacks "
            "Microsoft.CognitiveServices/accounts/deployments/delete required for cleanup"
        )

    subscription_id = config.resource_id.split("/")[2]
    usage_url = (
        "https://management.azure.com/subscriptions/"
        f"{subscription_id}/providers/Microsoft.CognitiveServices/locations/"
        f"{config.region}/usages?api-version=2023-05-01"
    )
    usages = arm_request(credential, "GET", usage_url).json().get("value", [])
    relevant_usage = [
        {
            "name": item.get("name", {}).get("value"),
            "current_value": item.get("currentValue"),
            "limit": item.get("limit"),
            "unit": item.get("unit"),
        }
        for item in usages
        if any(
            term in str(item.get("name", {}).get("value", "")).lower()
            for term in ("fine", "gpt-4.1-mini", "gpt41mini")
        )
    ]
    return {
        "checked_at": now_iso(),
        "project_endpoint_host": config.project_endpoint.split("/")[2],
        "project_name": project_name,
        "project_is_default": True,
        "resource_id": config.resource_id,
        "region": actual_region,
        "base_deployment": config.base_deployment,
        "base_model": model.get("name"),
        "base_model_version": model.get("version"),
        "training_type": config.training_type,
        "deployment_write": True,
        "deployment_delete": True,
        "quota_endpoint_checked": True,
        "relevant_usage": relevant_usage,
        "quota_note": (
            "The regional usages endpoint is a general Azure OpenAI quota check; "
            "job submission remains the definitive fine-tuning capacity check."
        ),
    }


def percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def evaluate(
    openai_client,
    *,
    deployment: str,
    label: str,
    test_path: Path = TEST_PATH,
) -> dict[str, Any]:
    rows = read_jsonl(test_path)
    results = []
    instructions = INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    for index, row in enumerate(rows, start=1):
        row_id, user_input, expected = test_example(row, f"test:{index}")
        started = time.perf_counter()
        try:
            response = openai_client.responses.create(
                model=deployment,
                instructions=instructions,
                input=user_input,
                temperature=0,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "triage_result",
                        "schema": OUTPUT_SCHEMA,
                        "strict": True,
                    }
                },
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            if getattr(response, "status", None) != "completed":
                raise RuntimeError(
                    f"response status is {getattr(response, 'status', None)!r}"
                )
            raw = (getattr(response, "output_text", "") or "").strip()
            parsed = validate_output_text(raw)
            schema_valid = True
            predicted = parsed["category"]
            adherence = (
                len(parsed["rationale"]) <= 160
                and (expected != "UNSUPPORTED" or predicted == "UNSUPPORTED")
            )
            error = None
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
        except (ValueError, RuntimeError) as caught:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            raw = ""
            schema_valid = False
            predicted = None
            adherence = False
            error = str(caught)
            input_tokens = None
            output_tokens = None
        results.append(
            {
                "id": row_id,
                "expected_category": expected,
                "predicted_category": predicted,
                "schema_valid": schema_valid,
                "correct": predicted == expected,
                "task_adherent": adherence,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "error": error,
                "raw_output": raw,
            }
        )
        print(f"  evaluated {index}/{len(rows)}: {row_id}")

    total = len(results)
    latencies = [row["latency_ms"] for row in results]
    input_values = [
        row["input_tokens"] for row in results if row["input_tokens"] is not None
    ]
    output_values = [
        row["output_tokens"] for row in results if row["output_tokens"] is not None
    ]
    by_category = {}
    for category in sorted(CATEGORIES):
        category_rows = [
            row for row in results if row["expected_category"] == category
        ]
        by_category[category] = {
            "count": len(category_rows),
            "accuracy": sum(row["correct"] for row in category_rows)
            / len(category_rows),
        }
    return {
        "evaluation_id": f"eval-{uuid4()}",
        "created_at": now_iso(),
        "label": label,
        "deployment": deployment,
        "test_sha256": file_hash(test_path),
        "metrics": {
            "schema_validity": sum(row["schema_valid"] for row in results) / total,
            "classification_accuracy": sum(row["correct"] for row in results) / total,
            "task_adherence": sum(row["task_adherent"] for row in results) / total,
            "input_tokens_total": sum(input_values) if len(input_values) == total else None,
            "output_tokens_total": sum(output_values) if len(output_values) == total else None,
            "latency_mean_ms": round(statistics.mean(latencies), 2),
            "latency_p95_ms": round(percentile95(latencies), 2),
            "by_category": by_category,
        },
        "rows": results,
    }


def compare_evaluations(
    baseline: dict[str, Any],
    tuned: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    failures = []
    if baseline.get("test_sha256") != tuned.get("test_sha256"):
        failures.append("baseline and tuned evaluations used different held-out datasets")
    base = baseline["metrics"]
    candidate = tuned["metrics"]
    accuracy_gain = (
        candidate["classification_accuracy"] - base["classification_accuracy"]
    )
    adherence_gain = candidate["task_adherence"] - base["task_adherence"]
    latency_regression = (
        candidate["latency_mean_ms"] / base["latency_mean_ms"] - 1
        if base["latency_mean_ms"]
        else float("inf")
    )
    if (
        base["output_tokens_total"] is None
        or candidate["output_tokens_total"] is None
        or base["output_tokens_total"] == 0
    ):
        output_token_regression = None
        failures.append("complete output-token usage is required for the release gate")
    else:
        output_token_regression = (
            candidate["output_tokens_total"] / base["output_tokens_total"] - 1
        )

    checks = (
        (
            candidate["classification_accuracy"]
            >= thresholds["minimum_tuned_accuracy"],
            "tuned classification accuracy is below the configured minimum",
        ),
        (
            accuracy_gain >= thresholds["minimum_accuracy_gain"],
            "classification accuracy gain is below the configured minimum",
        ),
        (
            candidate["schema_validity"] >= thresholds["minimum_schema_validity"],
            "schema validity regressed or is below the configured minimum",
        ),
        (
            candidate["task_adherence"] >= thresholds["minimum_task_adherence"],
            "task adherence is below the configured minimum",
        ),
        (
            adherence_gain >= thresholds["minimum_task_adherence_gain"],
            "task-adherence gain is below the configured minimum",
        ),
        (
            latency_regression
            <= thresholds["maximum_latency_regression_ratio"],
            "latency regression exceeds the configured maximum",
        ),
    )
    for passed, message in checks:
        if not passed:
            failures.append(message)
    if (
        output_token_regression is not None
        and output_token_regression
        > thresholds["maximum_output_token_regression_ratio"]
    ):
        failures.append("output-token regression exceeds the configured maximum")
    if thresholds["block_per_category_accuracy_regression"]:
        for category in sorted(CATEGORIES):
            before = base["by_category"][category]["accuracy"]
            after = candidate["by_category"][category]["accuracy"]
            if after < before:
                failures.append(f"{category} accuracy regressed from {before} to {after}")

    return {
        "gate_id": f"gate-{uuid4()}",
        "evaluated_at": now_iso(),
        "baseline_evaluation_id": baseline["evaluation_id"],
        "tuned_evaluation_id": tuned["evaluation_id"],
        "measurements": {
            "accuracy_gain": round(accuracy_gain, 6),
            "task_adherence_gain": round(adherence_gain, 6),
            "latency_regression_ratio": round(latency_regression, 6),
            "output_token_regression_ratio": (
                round(output_token_regression, 6)
                if output_token_regression is not None
                else None
            ),
        },
        "thresholds": thresholds,
        "passed": not failures,
        "failures": failures,
    }


def require_baseline(path: Path, dataset_report: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("baseline evaluation is required before fine-tuning submission")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if baseline.get("label") != "base":
        raise RuntimeError("baseline artifact label must be 'base'")
    if baseline.get("test_sha256") != dataset_report["hashes"]["test_sha256"]:
        raise RuntimeError("baseline does not match the current held-out dataset")
    return baseline


def validate_resume_provenance(
    config: Config,
    dataset_report: dict[str, Any],
    submission: dict[str, Any],
    baseline: dict[str, Any],
    job,
) -> None:
    job_data = job.model_dump(mode="json")
    required = {
        "job_id",
        "base_model",
        "base_model_version",
        "base_deployment",
        "training_type",
        "hyperparameters",
        "training_file_id",
        "validation_file_id",
        "dataset",
        "baseline_evaluation_id",
        "evaluation_protocol_sha256",
    }
    missing = required - submission.keys()
    if missing:
        raise RuntimeError(
            f"submission record is incomplete for resume: missing {sorted(missing)}"
        )
    if submission["job_id"] != job_data["id"]:
        raise RuntimeError("submission record job ID does not match retrieved job")
    if (
        submission["base_model"] != config.base_model
        or submission["base_model_version"] != config.base_model_version
        or submission["base_deployment"] != config.base_deployment
    ):
        raise RuntimeError("submission record base model/deployment does not match config")
    expected_job_model = f"{config.base_model}-{config.base_model_version}"
    if job_data.get("model") != expected_job_model:
        raise RuntimeError(
            f"job model {job_data.get('model')!r} does not match "
            f"{expected_job_model!r}"
        )
    metadata = job_data.get("metadata") or {}
    if (
        metadata.get("base_model") not in (None, config.base_model)
        or metadata.get("model_version") not in (None, config.base_model_version)
    ):
        raise RuntimeError("job metadata model/version does not match submission")
    if str(job_data.get("trainingType", "")).casefold() != str(
        submission["training_type"]
    ).casefold():
        raise RuntimeError("job trainingType does not match submission record")
    if job_data.get("training_file") != submission["training_file_id"]:
        raise RuntimeError("job training file ID does not match submission record")
    if job_data.get("validation_file") != submission["validation_file_id"]:
        raise RuntimeError("job validation file ID does not match submission record")
    if job_data.get("seed") != submission.get("seed"):
        raise RuntimeError("job seed does not match submission record")
    if dict(job_data.get("hyperparameters") or {}) != submission["hyperparameters"]:
        raise RuntimeError("job hyperparameters do not match submission record")
    if submission["dataset"].get("hashes") != dataset_report["hashes"]:
        raise RuntimeError("submission dataset hashes do not match checked-in datasets")
    protocol_hash = dataset_report["evaluation_protocol_sha256"]
    if (
        submission["evaluation_protocol_sha256"] != protocol_hash
        or submission["dataset"].get("evaluation_protocol_sha256") != protocol_hash
    ):
        raise RuntimeError("evaluation protocol hash changed since job submission")
    if (
        baseline["evaluation_id"] != submission["baseline_evaluation_id"]
        or baseline["test_sha256"] != dataset_report["hashes"]["test_sha256"]
        or baseline["deployment"] != config.base_deployment
    ):
        raise RuntimeError("baseline evidence does not match the immutable submission")


def submit_job(
    openai_client,
    config: Config,
    dataset_report: dict[str, Any],
    baseline_path: Path,
    record_to_update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config.validate(require_price=True)
    baseline = require_baseline(baseline_path, dataset_report)
    train_bytes = b"\xef\xbb\xbf" + TRAIN_PATH.read_bytes()
    validation_bytes = b"\xef\xbb\xbf" + VALIDATION_PATH.read_bytes()
    train_file = openai_client.files.create(
        file=(TRAIN_PATH.name, BytesIO(train_bytes), "application/jsonl"),
        purpose="fine-tune",
    )
    if record_to_update is not None:
        record_to_update["training_file_id"] = train_file.id
    validation_file = openai_client.files.create(
        file=(VALIDATION_PATH.name, BytesIO(validation_bytes), "application/jsonl"),
        purpose="fine-tune",
    )
    if record_to_update is not None:
        record_to_update["validation_file_id"] = validation_file.id
    openai_client.files.wait_for_processing(train_file.id)
    openai_client.files.wait_for_processing(validation_file.id)
    hyperparameters = {
        "n_epochs": config.n_epochs,
        "batch_size": config.batch_size,
        "learning_rate_multiplier": config.learning_rate_multiplier,
    }
    job = openai_client.fine_tuning.jobs.create(
        **fine_tuning_job_arguments(
            config,
            train_file_id=train_file.id,
            validation_file_id=validation_file.id,
        )
    )
    if record_to_update is not None:
        record_to_update["job_id"] = job.id
    return {
        "record_version": 1,
        "created_at": now_iso(),
        "base_model": config.base_model,
        "base_model_version": config.base_model_version,
        "base_deployment": config.base_deployment,
        "dataset": dataset_report,
        "hyperparameters": hyperparameters,
        "seed": config.seed,
        "training_type": config.training_type,
        "job_id": job.id,
        "job_status": getattr(job, "status", None),
        "training_file_id": train_file.id,
        "validation_file_id": validation_file.id,
        "baseline_evaluation_id": baseline["evaluation_id"],
        "training_price_per_million_usd": config.training_price_per_million_usd,
    }


def monitor_job(openai_client, record: dict[str, Any], poll_seconds: int) -> dict[str, Any]:
    started = time.monotonic()
    while True:
        job = openai_client.fine_tuning.jobs.retrieve(record["job_id"])
        status = str(getattr(job, "status", "")).lower()
        print(f"fine-tuning job {job.id}: {status}")
        if status in TERMINAL_STATES:
            break
        time.sleep(poll_seconds)
    record["job_status"] = status
    record["job_duration_seconds"] = round(time.monotonic() - started, 2)
    record["fine_tuned_model"] = getattr(job, "fine_tuned_model", None)
    record["trained_tokens"] = getattr(job, "trained_tokens", None)
    record["finished_at"] = now_iso()
    if record["trained_tokens"] is not None:
        record["training_cost_usd"] = round(
            record["trained_tokens"]
            / 1_000_000
            * record["training_price_per_million_usd"],
            6,
        )
    else:
        record["training_cost_usd"] = None
    if status not in SUCCESS_STATES or not record["fine_tuned_model"]:
        raise RuntimeError(f"fine-tuning job ended in {status!r}")
    return record


def fine_tuning_job_arguments(
    config: Config,
    *,
    train_file_id: str,
    validation_file_id: str,
) -> dict[str, Any]:
    return {
        "model": config.base_model,
        "training_file": train_file_id,
        "validation_file": validation_file_id,
        "seed": config.seed,
        "suffix": "triage-json",
        "method": {
            "type": "supervised",
            "supervised": {
                "hyperparameters": {
                    "n_epochs": config.n_epochs,
                    "batch_size": config.batch_size,
                    "learning_rate_multiplier": config.learning_rate_multiplier,
                }
            },
        },
        "extra_body": {"trainingType": config.training_type},
    }


def evaluation_deployment_body(
    config: Config,
    fine_tuned_model: str,
) -> dict[str, Any]:
    return {
        "sku": {"name": config.deployment_sku, "capacity": 1},
        "properties": {
            "model": {
                "format": "OpenAI",
                "name": fine_tuned_model,
                "version": "1",
            },
            "versionUpgradeOption": "NoAutoUpgrade",
        },
    }


def deploy_for_evaluation(
    credential,
    config: Config,
    fine_tuned_model: str,
) -> dict[str, Any]:
    body = evaluation_deployment_body(config, fine_tuned_model)
    url = deployment_url(config, config.tuned_deployment)
    arm_request(
        credential,
        "PUT",
        url,
        json_body=body,
        expected=frozenset({200, 201, 202}),
    )
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        deployment = arm_request(credential, "GET", url).json()
        state = deployment.get("properties", {}).get("provisioningState")
        if state == "Succeeded":
            return {
                "deployment_name": config.tuned_deployment,
                "sku": config.deployment_sku,
                "created_at": now_iso(),
                "lifetime": "DeveloperTier is evaluation-only and limited to 24 hours",
            }
        if state in {"Failed", "Canceled", "Cancelled"}:
            raise RuntimeError(f"fine-tuned deployment provisioning ended in {state}")
        time.sleep(15)
    raise TimeoutError("fine-tuned DeveloperTier deployment did not become ready")


def cleanup(
    credential,
    openai_client,
    config: Config,
    record: dict[str, Any],
) -> dict[str, Any]:
    cleanup_result: dict[str, Any] = {"started_at": now_iso(), "errors": []}
    job_id = record.get("job_id")
    if job_id:
        try:
            job = openai_client.fine_tuning.jobs.retrieve(job_id)
            status = str(getattr(job, "status", "")).lower()
            cleanup_result["job_status_before_cleanup"] = status
            if status not in TERMINAL_STATES:
                openai_client.fine_tuning.jobs.cancel(job_id)
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    job = openai_client.fine_tuning.jobs.retrieve(job_id)
                    status = str(getattr(job, "status", "")).lower()
                    if status in TERMINAL_STATES:
                        break
                    time.sleep(10)
                else:
                    raise RuntimeError(
                        "fine-tuning job did not reach a terminal state after cancellation"
                    )
            cleanup_result["job_status_after_cleanup"] = status
        except (OpenAIError, RuntimeError) as error:
            cleanup_result["errors"].append(
                f"fine-tuning job cleanup failed: {type(error).__name__}: {error}"
            )
    if record.get("deployment_requested") == config.tuned_deployment:
        temporary_deployment_url = deployment_url(config, config.tuned_deployment)
        try:
            response = arm_request(
                credential,
                "DELETE",
                temporary_deployment_url,
                expected=frozenset({200, 202, 204, 404}),
            )
            cleanup_result["deployment_deleted"] = response.status_code != 404
            if response.status_code != 404:
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    probe = arm_request(
                        credential,
                        "GET",
                        temporary_deployment_url,
                        expected=frozenset({200, 404}),
                    )
                    if probe.status_code == 404:
                        break
                    time.sleep(10)
                else:
                    raise RuntimeError(
                        "temporary deployment deletion did not complete in 10 minutes"
                    )
        except (RuntimeError, requests.RequestException) as error:
            cleanup_result["errors"].append(
                f"deployment cleanup failed: {type(error).__name__}: {error}"
            )
    fine_tuned_model = record.get("fine_tuned_model")
    if fine_tuned_model:
        try:
            openai_client.models.delete(fine_tuned_model)
            cleanup_result["fine_tuned_model_deleted"] = True
        except NotFoundError:
            cleanup_result["fine_tuned_model_deleted"] = False
            cleanup_result["fine_tuned_model_note"] = (
                "The project Models API did not expose a deletable model object. "
                "The billable deployment is deleted; the completed job/checkpoint "
                "remains as service-side reproducibility metadata."
            )
        except OpenAIError as error:
            cleanup_result["errors"].append(
                f"fine-tuned model cleanup failed: {type(error).__name__}: {error}"
            )
    for key in ("training_file_id", "validation_file_id"):
        file_id = record.get(key)
        if not file_id:
            continue
        try:
            openai_client.files.delete(file_id)
            cleanup_result[f"{key}_deleted"] = True
        except NotFoundError:
            cleanup_result[f"{key}_deleted"] = True
            cleanup_result[f"{key}_note"] = "already absent"
        except OpenAIError as error:
            cleanup_result["errors"].append(
                f"{key} cleanup failed: {type(error).__name__}: {error}"
            )
    cleanup_result["finished_at"] = now_iso()
    record["cleanup"] = cleanup_result
    if cleanup_result["errors"]:
        raise RuntimeError("cleanup incomplete: " + "; ".join(cleanup_result["errors"]))
    return record


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def run_pipeline(config: Config, *, confirm_cost: bool, poll_seconds: int) -> int:
    if not confirm_cost:
        raise SystemExit(
            "Billable training is disabled by default. Re-run with --confirm-cost after "
            "reviewing current Azure pricing and FINETUNE_TRAINING_PRICE_PER_MILLION_USD."
        )
    dataset = validate_datasets()
    config.validate(require_price=True)
    credential, project, client = project_clients(config)
    record: dict[str, Any] = {
        "record_version": 1,
        "started_at": now_iso(),
        "config": asdict(config),
        "dataset": dataset,
    }
    # Do not persist endpoints or secrets in the reproducibility record.
    record["config"].pop("project_endpoint", None)
    record["config"].pop("resource_id", None)
    output_path = ARTIFACT_DIR / f"repro-{datetime.now():%Y%m%d-%H%M%S}.json"
    baseline_path = ARTIFACT_DIR / "baseline-current.json"
    return_code = 1
    pipeline_error = None
    cleanup_error = None
    with credential, project, client:
        try:
            record["preflight"] = online_preflight(config, credential, client)
            baseline = evaluate(
                client,
                deployment=config.base_deployment,
                label="base",
            )
            record["baseline_evaluation"] = baseline
            record["baseline_evaluation_id"] = baseline["evaluation_id"]
            record["evaluation_protocol_sha256"] = dataset[
                "evaluation_protocol_sha256"
            ]
            # Persist the immutable baseline/protocol before uploading any billable job data.
            write_json(output_path, record)
            write_json(baseline_path, baseline)
            submitted = submit_job(
                client,
                config,
                dataset,
                baseline_path,
                record_to_update=record,
            )
            record.update(submitted)
            write_json(output_path, record)
            record = monitor_job(client, record, poll_seconds)
            record["deployment_requested"] = config.tuned_deployment
            record["temporary_deployment"] = deploy_for_evaluation(
                credential,
                config,
                record["fine_tuned_model"],
            )
            tuned = evaluate(
                client,
                deployment=config.tuned_deployment,
                label="tuned",
            )
            thresholds = load_json(THRESHOLDS_PATH)
            gate = compare_evaluations(baseline, tuned, thresholds)
            record["tuned_evaluation"] = tuned
            record["release_gate"] = gate
            record["completed_at"] = now_iso()
            return_code = 0 if gate["passed"] else 1
        except (
            ValueError,
            RuntimeError,
            TimeoutError,
            OpenAIError,
            requests.RequestException,
        ) as error:
            pipeline_error = error
            record["pipeline_error"] = f"{type(error).__name__}: {error}"
        finally:
            try:
                cleanup(credential, client, config, record)
            except RuntimeError as error:
                cleanup_error = error
            write_json(output_path, record)
            if baseline_path.exists():
                baseline_path.unlink()
            print(f"Reproducibility record: {output_path}")
    if pipeline_error is not None:
        raise RuntimeError(f"pipeline failed: {pipeline_error}") from pipeline_error
    if cleanup_error is not None:
        raise cleanup_error
    return return_code


def resume_evaluation(
    config: Config,
    *,
    submission_record_path: Path,
    baseline_path: Path,
    output_path: Path,
    confirm_evaluation_cost: bool,
) -> int:
    if not confirm_evaluation_cost:
        raise SystemExit(
            "Temporary DeveloperTier hosting and inference incur cost. Re-run with "
            "--confirm-evaluation-cost after review."
        )
    dataset = validate_datasets()
    config.validate(require_price=True)
    submission = load_json(submission_record_path)
    baseline = require_baseline(baseline_path, dataset)
    record: dict[str, Any] = {
        "record_version": 1,
        "resume_started_at": now_iso(),
        "job_id": submission.get("job_id"),
        "submission_record_sha256": file_hash(submission_record_path),
        "base_model": config.base_model,
        "base_model_version": config.base_model_version,
        "base_deployment": config.base_deployment,
        "dataset": dataset,
        "baseline_evaluation": baseline,
        "baseline_evaluation_id": baseline["evaluation_id"],
        "training_price_per_million_usd": config.training_price_per_million_usd,
    }
    credential, project, client = project_clients(config)
    pipeline_error = None
    cleanup_error = None
    return_code = 1
    with credential, project, client:
        try:
            record["preflight"] = online_preflight(config, credential, client)
            job = client.fine_tuning.jobs.retrieve(submission["job_id"])
            validate_resume_provenance(
                config,
                dataset,
                submission,
                baseline,
                job,
            )
            status = str(getattr(job, "status", "")).lower()
            fine_tuned_model = getattr(job, "fine_tuned_model", None)
            if status != "succeeded" or not fine_tuned_model:
                raise RuntimeError(
                    f"fine-tuning job {submission['job_id']} is {status!r}, "
                    "not succeeded/deployable"
                )
            record["job_status"] = status
            record["fine_tuned_model"] = fine_tuned_model
            record["trained_tokens"] = getattr(job, "trained_tokens", None)
            if record["trained_tokens"] is not None:
                record["training_cost_usd"] = round(
                    record["trained_tokens"]
                    / 1_000_000
                    * config.training_price_per_million_usd,
                    6,
                )
            record["deployment_requested"] = config.tuned_deployment
            record["temporary_deployment"] = deploy_for_evaluation(
                credential,
                config,
                fine_tuned_model,
            )
            tuned = evaluate(
                client,
                deployment=config.tuned_deployment,
                label="tuned",
            )
            gate = compare_evaluations(
                baseline,
                tuned,
                load_json(THRESHOLDS_PATH),
            )
            record["tuned_evaluation"] = tuned
            record["release_gate"] = gate
            record["resume_completed_at"] = now_iso()
            return_code = 0 if gate["passed"] else 1
        except (
            ValueError,
            RuntimeError,
            TimeoutError,
            OpenAIError,
            requests.RequestException,
        ) as error:
            pipeline_error = error
            record["pipeline_error"] = f"{type(error).__name__}: {error}"
        finally:
            try:
                cleanup(credential, client, config, record)
            except RuntimeError as error:
                cleanup_error = error
            write_json(output_path, record)
            print(f"Resumed reproducibility record: {output_path}")
    if pipeline_error is not None:
        raise RuntimeError(f"resume failed: {pipeline_error}") from pipeline_error
    if cleanup_error is not None:
        raise cleanup_error
    return return_code


def parse_args():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--offline", action="store_true")

    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--deployment", required=True)
    evaluate_parser.add_argument("--label", choices=("base", "tuned"), required=True)
    evaluate_parser.add_argument("--output", type=Path, required=True)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--tuned", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--confirm-cost", action="store_true")
    run.add_argument("--poll-seconds", type=int, default=60)
    resume = subparsers.add_parser("resume")
    resume.add_argument("--submission-record", type=Path, required=True)
    resume.add_argument("--baseline", type=Path, required=True)
    resume.add_argument("--output", type=Path, required=True)
    resume.add_argument("--confirm-evaluation-cost", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = Config.from_env()
    if args.command == "preflight":
        report = {"dataset": validate_datasets()}
        if not args.offline:
            credential, project, client = project_clients(config)
            with credential, project, client:
                report["cloud"] = online_preflight(config, credential, client)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "evaluate":
        config.validate()
        credential, project, client = project_clients(config)
        with credential, project, client:
            result = evaluate(
                client,
                deployment=args.deployment,
                label=args.label,
            )
        write_json(args.output, result)
        print(json.dumps(result["metrics"], indent=2, sort_keys=True))
        return 0
    if args.command == "compare":
        gate = compare_evaluations(
            load_json(args.baseline),
            load_json(args.tuned),
            load_json(THRESHOLDS_PATH),
        )
        print(json.dumps(gate, indent=2, sort_keys=True))
        return 0 if gate["passed"] else 1
    if args.command == "run":
        return run_pipeline(
            config,
            confirm_cost=args.confirm_cost,
            poll_seconds=args.poll_seconds,
        )
    if args.command == "resume":
        return resume_evaluation(
            config,
            submission_record_path=args.submission_record,
            baseline_path=args.baseline,
            output_path=args.output,
            confirm_evaluation_cost=args.confirm_evaluation_cost,
        )
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, TimeoutError) as error:
        print(f"MODEL ADAPTATION FAILED: {error}", file=sys.stderr)
        sys.exit(1)
