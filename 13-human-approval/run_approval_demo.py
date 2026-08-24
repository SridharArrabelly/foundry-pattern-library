"""Run the current Foundry MCP approval request/response flow.

The prompt agent uses a project connection so the MCP credential remains in
Foundry. The operator-side audit and replay probes read the same credential from
the process environment; it is never printed or written to disk.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai.types.responses.response_output_item import McpApprovalResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.foundry import agent_model, env, project_client

from approval_store import (
    SERVER_LABEL,
    WRITE_TOOL,
    canonical_json,
    normalize_schedule_arguments,
)


AGENT_NAME = "consequential-change-approval"
CONNECTION_NAME = env("MCP_CHANGE_CONTROL_CONNECTION_NAME")
MCP_SERVER_URL = env("MCP_CHANGE_CONTROL_URL")
MCP_TOOL_API_KEY = env("MCP_CHANGE_CONTROL_TOOL_API_KEY")
MCP_OPERATOR_API_KEY = env("MCP_CHANGE_CONTROL_OPERATOR_API_KEY")


def require_remote_url(url: str | None) -> str:
    if not url:
        raise SystemExit("Set MCP_CHANGE_CONTROL_URL to the remote HTTPS .../mcp endpoint")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit(
            "Foundry cannot call localhost. MCP_CHANGE_CONTROL_URL must be a remote HTTPS endpoint."
        )
    return url.rstrip("/")


def service_url(mcp_url: str, suffix: str) -> str:
    parsed = urlsplit(mcp_url)
    path = parsed.path[:-4] if parsed.path.endswith("/mcp") else parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}{suffix}", "", ""))


def approval_tool(connection_id: str) -> MCPTool:
    return MCPTool(
        server_label=SERVER_LABEL,
        project_connection_id=connection_id,
        allowed_tools=["get_change_request", WRITE_TOOL],
        require_approval={
            "never": {"tool_names": ["get_change_request"]},
            "always": {"tool_names": [WRITE_TOOL]},
        },
    )


def approval_items(response) -> list:
    return [
        item
        for item in getattr(response, "output", [])
        if getattr(item, "type", None) == "mcp_approval_request"
    ]


def normalize_approval_item(item, expected_request_id: str) -> tuple[dict[str, str], str]:
    required = ("id", "arguments", "name", "server_label", "type")
    if any(not hasattr(item, field) for field in required):
        raise RuntimeError("malformed MCP approval request: required fields are missing")
    if item.type != "mcp_approval_request":
        raise RuntimeError(f"unexpected approval item type: {item.type!r}")
    if item.server_label != SERVER_LABEL or item.name != WRITE_TOOL:
        raise RuntimeError(
            f"mismatched approval target: {item.server_label!r}/{item.name!r}"
        )
    arguments, canonical = normalize_schedule_arguments(item.arguments)
    if arguments["change_request_id"] != expected_request_id:
        raise RuntimeError("approval request references the wrong change request")
    return arguments, canonical


def decision_envelope(item, arguments: dict[str, str], approve: bool) -> dict:
    decision_id = f"decision-{uuid4()}"
    response = McpApprovalResponse(
        type="mcp_approval_response",
        id=decision_id,
        approval_request_id=item.id,
        approve=approve,
        reason=(
            "Approved by the operator after reviewing exact normalized arguments."
            if approve
            else "Rejected by the operator; no side effect is authorized."
        ),
    )
    return {
        **response.model_dump(mode="json"),
        "pending_approval_id": arguments["pending_approval_id"],
        "server_label": item.server_label,
        "tool_name": item.name,
        "arguments": arguments,
    }


def operator_headers() -> dict[str, str]:
    if not MCP_OPERATOR_API_KEY:
        raise SystemExit(
            "Set MCP_CHANGE_CONTROL_OPERATOR_API_KEY for the operator channel. "
            "The value is not printed or persisted."
        )
    return {"x-operator-api-key": MCP_OPERATOR_API_KEY}


def tool_headers() -> dict[str, str]:
    if not MCP_TOOL_API_KEY:
        raise SystemExit(
            "Set MCP_CHANGE_CONTROL_TOOL_API_KEY for the replay probe. "
            "The Foundry project connection stores the same tool-only credential."
        )
    return {"x-mcp-api-key": MCP_TOOL_API_KEY}


def register_pending(mcp_url: str, item, arguments: dict[str, str]) -> dict:
    response = requests.post(
        service_url(mcp_url, "/pending-approvals"),
        headers=operator_headers(),
        json={
            "pending_approval_id": arguments["pending_approval_id"],
            "approval_request_id": item.id,
            "server_label": item.server_label,
            "tool_name": item.name,
            "arguments": arguments,
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"pending approval registration failed closed: HTTP {response.status_code}"
        )
    return response.json()


def record_decision(mcp_url: str, envelope: dict) -> dict:
    response = requests.post(
        service_url(mcp_url, "/decisions"),
        headers=operator_headers(),
        json=envelope,
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"decision record failed closed: HTTP {response.status_code}")
    return response.json()


def fetch_audit(mcp_url: str, request_id: str) -> dict:
    response = requests.get(
        service_url(mcp_url, f"/audit/{request_id}"),
        headers=operator_headers(),
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"audit read failed: HTTP {response.status_code}")
    return response.json()


async def replay_tool_call(mcp_url: str, arguments: dict[str, str]) -> dict:
    async with streamablehttp_client(mcp_url, headers=tool_headers()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(WRITE_TOOL, arguments)
            if result.isError:
                raise RuntimeError("replay probe returned an MCP tool error")
            payload = next(
                (json.loads(block.text) for block in result.content if hasattr(block, "text")),
                None,
            )
            if not isinstance(payload, dict):
                raise RuntimeError("replay probe returned no structured schedule result")
            return payload


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--change-request",
        default="CRQ-1003",
        choices=("CRQ-1001", "CRQ-1002", "CRQ-1003"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mcp_url = require_remote_url(MCP_SERVER_URL)
    if not CONNECTION_NAME:
        raise SystemExit(
            "Set MCP_CHANGE_CONTROL_CONNECTION_NAME to the Foundry project connection "
            "that stores the MCP endpoint and x-mcp-api-key."
        )

    with project_client() as project:
        connection = project.connections.get(CONNECTION_NAME)
        agent = project.agents.create_version(
            agent_name=AGENT_NAME,
            definition=PromptAgentDefinition(
                model=agent_model(),
                instructions=(
                    "You are a change-control assistant. Always call get_change_request "
                    "before schedule_change. Copy the exact schedule_arguments returned by "
                    "the service. Never claim a change was scheduled without the tool result."
                ),
                tools=[approval_tool(connection.id)],
            ),
        )
        client = project.get_openai_client()
        reference = {"name": agent.name, "type": "agent_reference"}

        print("1) READ-ONLY: get_change_request runs without an approval prompt")
        read_response = client.responses.create(
            input=(
                f"Call get_change_request for {args.change_request}. "
                "Return a concise summary of the exact proposed schedule."
            ),
            extra_body={"agent_reference": reference},
        )
        if approval_items(read_response):
            raise RuntimeError("read-only get_change_request unexpectedly requested approval")
        if not (read_response.output_text or "").strip():
            raise RuntimeError("read-only call returned no answer")
        print(read_response.output_text.strip())

        print("\n2) CONSEQUENTIAL: schedule_change must stop at Foundry approval")
        write_response = client.responses.create(
            input=(
                f"Schedule change request {args.change_request}. First read it, then call "
                "schedule_change with exactly the service-provided arguments."
            ),
            extra_body={"agent_reference": reference},
        )
        approvals = approval_items(write_response)
        if len(approvals) != 1:
            raise RuntimeError(
                f"expected exactly one MCP approval request, received {len(approvals)}"
            )
        approval = approvals[0]
        arguments, canonical = normalize_approval_item(approval, args.change_request)
        print(f"Foundry approval request: {approval.id}")
        print(f"Tool: {approval.server_label}/{approval.name}")
        print(f"Normalized arguments: {canonical}")
        registered = register_pending(mcp_url, approval, arguments)
        if registered["pending_approval_id"] != arguments["pending_approval_id"]:
            raise RuntimeError("service registered a different pending approval nonce")

        choice = input("Type APPROVE or REJECT after reviewing the exact call: ").strip().upper()
        if choice not in {"APPROVE", "REJECT"}:
            raise SystemExit("No valid decision supplied; failing closed without recording approval.")
        approve = choice == "APPROVE"
        envelope = decision_envelope(approval, arguments, approve)
        recorded = record_decision(mcp_url, envelope)

        approval_response = {
            key: envelope[key]
            for key in ("type", "id", "approval_request_id", "approve", "reason")
        }
        final_response = client.responses.create(
            previous_response_id=write_response.id,
            input=[approval_response],
            extra_body={"agent_reference": reference},
        )
        if approval_items(final_response):
            raise RuntimeError("approval response produced another unexpected approval request")

        audit = fetch_audit(mcp_url, args.change_request)
        if not approve:
            if audit["status"] != "rejected" or audit["side_effects"]:
                raise RuntimeError("rejected request changed state or created a side effect")
            print(
                "\nREJECTED: decision recorded; side effects = 0 "
                f"(request={args.change_request}, decision={recorded['decision_id']})"
            )
            return 0

        if audit["status"] != "scheduled" or len(audit["side_effects"]) != 1:
            raise RuntimeError("approved request did not produce exactly one side effect")
        effect = audit["side_effects"][0]
        if effect["decision_id"] != recorded["decision_id"]:
            raise RuntimeError("side effect is not correlated to the recorded decision")
        print(
            "\nAPPROVED: exactly one side effect "
            f"(request={args.change_request}, approval={recorded['approval_request_id']}, "
            f"decision={recorded['decision_id']}, side_effect={effect['side_effect_id']})"
        )

        print("\n3) REPLAY: duplicate decision and tool call remain idempotent")
        duplicate = record_decision(mcp_url, envelope)
        replay = asyncio.run(replay_tool_call(mcp_url, arguments))
        after_replay = fetch_audit(mcp_url, args.change_request)
        if (
            not duplicate["duplicate"]
            or not replay["idempotent_replay"]
            or len(after_replay["side_effects"]) != 1
            or replay["side_effect_id"] != effect["side_effect_id"]
        ):
            raise RuntimeError("replay protection failed")
        print(f"Replay returned the same side effect: {effect['side_effect_id']} (count=1)")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as error:
        raise SystemExit(f"Remote MCP service request failed: {error}") from error
