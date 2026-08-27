"""Run Pattern 5B locally, including deterministic branch and checkpoint resume proof."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from agent_framework import FileCheckpointStorage

ROOT = Path(__file__).resolve().parents[1]
HOSTED_SOURCE = (
    Path(__file__).resolve().parent
    / "hosted"
    / "src"
    / "workflow-orchestrator"
)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HOSTED_SOURCE))

from common.foundry import (  # noqa: E402
    GATEWAY_ENDPOINT,
    GATEWAY_KEY,
    GATEWAY_MODEL,
    GATEWAY_V1_API_VERSION,
)
from workflow_graph import (  # noqa: E402
    AuditRecord,
    EXCEPTION_REQUEST,
    STANDARD_REQUEST,
    WORKFLOW_NAME,
    build_workflow,
    checkpoint_allowed_types,
)


def build_client():
    """Use direct Foundry when explicitly set, otherwise Pattern 5A's APIM route."""
    project_endpoint = os.environ.get("WORKFLOW_PROJECT_ENDPOINT", "").strip()
    if project_endpoint:
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        return FoundryChatClient(
            project_endpoint=project_endpoint,
            model=os.environ.get("WORKFLOW_MODEL", GATEWAY_MODEL),
            credential=DefaultAzureCredential(),
        )

    from agent_framework.openai import OpenAIChatClient

    endpoint = GATEWAY_ENDPOINT.rstrip("/")
    if "your-" in endpoint:
        raise SystemExit(
            "Set WORKFLOW_PROJECT_ENDPOINT for direct Foundry, or configure "
            "GATEWAY_ENDPOINT for the APIM route."
        )
    if GATEWAY_KEY:
        return OpenAIChatClient(
            model=GATEWAY_MODEL,
            azure_endpoint=endpoint,
            api_key=GATEWAY_KEY,
            api_version=GATEWAY_V1_API_VERSION,
        )
    from azure.identity import DefaultAzureCredential

    return OpenAIChatClient(
        model=GATEWAY_MODEL,
        azure_endpoint=endpoint,
        credential=DefaultAzureCredential(),
        api_version=GATEWAY_V1_API_VERSION,
    )


def parse_record(outputs) -> AuditRecord:
    if len(outputs) != 1 or not isinstance(outputs[0], str):
        raise RuntimeError(f"expected one JSON audit output, received {len(outputs)}")
    return AuditRecord.model_validate_json(outputs[0])


async def run_case(client, name: str, payload: dict, checkpoint_root: Path) -> None:
    checkpoint_dir = checkpoint_root / name
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    storage = FileCheckpointStorage(
        checkpoint_dir,
        allowed_checkpoint_types=checkpoint_allowed_types(),
    )
    before = {
        str(checkpoint.checkpoint_id)
        for checkpoint in await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    }
    workflow = build_workflow(client, checkpoint_storage=storage)
    result = await workflow.run(payload)
    record = parse_record(result.get_outputs())

    created = [
        checkpoint
        for checkpoint in await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
        if str(checkpoint.checkpoint_id) not in before
    ]
    entry = next(
        (checkpoint for checkpoint in created if checkpoint.iteration_count == 0),
        None,
    )
    if entry is None:
        raise RuntimeError("workflow produced no resumable entry checkpoint")

    resumed_workflow = build_workflow(client, checkpoint_storage=storage)
    resumed = await resumed_workflow.run(
        checkpoint_id=entry.checkpoint_id,
        checkpoint_storage=storage,
    )
    resumed_record = parse_record(resumed.get_outputs())
    if (
        resumed_record.audit_id != record.audit_id
        or resumed_record.route != record.route
        or resumed_record.decision != record.decision
    ):
        raise RuntimeError("checkpoint resume changed routing or audit identity")

    print(f"\n===== {name.upper()} PATH =====")
    print(json.dumps(record.model_dump(mode="json"), indent=2))
    print(
        f"CHECKPOINT RESUME: {entry.checkpoint_id} -> "
        f"{resumed_record.audit_id} ({resumed_record.decision})"
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=("standard", "exception", "both"),
        default="both",
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        help=(
            "Trusted checkpoint directory. Omit for an automatically removed "
            "single-process demo directory."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    client = build_client()
    cases = {
        "standard": STANDARD_REQUEST,
        "exception": EXCEPTION_REQUEST,
    }
    selected = cases if args.case == "both" else {args.case: cases[args.case]}

    print(
        "TOPOLOGY: validate(code) -> classify(agent) -> normalize(code) -> "
        "switch -> standard(code) | exception-review(agent) -> audit(code)"
    )
    if args.checkpoint_dir:
        args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in selected.items():
            await run_case(client, name, payload, args.checkpoint_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="pattern-5b-checkpoints-") as temp:
            for name, payload in selected.items():
                await run_case(client, name, payload, Path(temp))


if __name__ == "__main__":
    asyncio.run(main())
