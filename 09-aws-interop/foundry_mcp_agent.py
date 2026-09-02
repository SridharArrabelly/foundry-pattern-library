"""Create, invoke, verify, and normally delete a Foundry prompt agent using APIM MCP."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from azure.ai.projects.models import MCPTool, PromptAgentDefinition

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.foundry import agent_model, env, project_client


SERVER_LABEL = "cross_cloud_protocol_gateway"
QUOTE_TOOL = "create_quote"
SIMULATION_LABEL = "AWS Lambda / Amazon Bedrock (simulated)"


def require_remote_url(value: str | None) -> str:
    if not value:
        raise SystemExit("Set PATTERN9_MCP_URL to the APIM .../mcp HTTPS endpoint.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("PATTERN9_MCP_URL must be a remote HTTPS endpoint.")
    return value.rstrip("/")


def mcp_tool(connection_id: str, server_url: str) -> MCPTool:
    return MCPTool(
        server_label=SERVER_LABEL,
        server_url=server_url,
        project_connection_id=connection_id,
        allowed_tools=[QUOTE_TOOL],
        require_approval="never",
    )


def mcp_call_items(response) -> list:
    return [
        item
        for item in getattr(response, "output", [])
        if getattr(item, "type", None) == "mcp_call"
    ]


def quote_from_mcp_call(item) -> dict:
    if getattr(item, "status", None) != "completed":
        raise RuntimeError("Foundry MCP call did not complete")
    raw = getattr(item, "output", None)
    if not isinstance(raw, str):
        raise RuntimeError("Foundry MCP call returned no structured output")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Foundry MCP call output was not JSON") from error
    candidates = [payload]
    if isinstance(payload, dict):
        structured = payload.get("structuredContent")
        if isinstance(structured, dict):
            candidates.append(structured)
        content = payload.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                continue
            try:
                candidates.append(json.loads(block["text"]))
            except json.JSONDecodeError:
                continue
    quote = next(
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("correlationId")
        ),
        None,
    )
    if not isinstance(quote, dict) or not quote.get("correlationId"):
        raise RuntimeError("Foundry MCP result omitted deterministic correlation")
    if quote.get("simulatedBackend") != SIMULATION_LABEL or quote.get("simulation") is not True:
        raise RuntimeError("Foundry MCP result omitted the simulation label")
    return quote


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--agent-name",
        default=f"pattern9-protocol-gateway-{uuid4().hex[:8]}",
    )
    parser.add_argument(
        "--retain-agent",
        action="store_true",
        help="Retain the pattern-specific prompt agent after verification.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mcp_url = require_remote_url(env("PATTERN9_MCP_URL"))
    connection_name = env("PATTERN9_FOUNDRY_CONNECTION_NAME")
    if not connection_name:
        raise SystemExit(
            "Set PATTERN9_FOUNDRY_CONNECTION_NAME to the Foundry project connection "
            "that stores the APIM subscription key."
        )

    with project_client() as project:
        connection = project.connections.get(connection_name)
        agent = project.agents.create_version(
            agent_name=args.agent_name,
            definition=PromptAgentDefinition(
                model=agent_model(),
                instructions=(
                    "You are a cross-cloud capacity assistant. For every quote request, "
                    "call create_quote exactly once. Return the quote ID, correlation ID, "
                    "currency, total price, and the explicit simulated backend label. "
                    "Never claim that AWS or Amazon Bedrock was called for real."
                ),
                tools=[mcp_tool(connection.id, mcp_url)],
            ),
        )
        try:
            client = project.get_openai_client()
            validation_errors = []
            for response_attempt in range(1, 4):
                response = client.responses.create(
                    input=(
                        "Call create_quote with capability=data-analysis, "
                        "serviceLevel=priority, units=250, and termMonths=12. "
                        "Use the tool schema exactly and do not invent fields."
                    ),
                    extra_body={
                        "agent_reference": {
                            "name": agent.name,
                            "type": "agent_reference",
                        }
                    },
                )
                calls = mcp_call_items(response)
                completed_calls = [
                    item
                    for item in calls
                    if getattr(item, "status", None) == "completed"
                ]
                if not completed_calls:
                    validation_errors.append(
                        f"attempt {response_attempt}: no completed MCP call "
                        f"among {len(calls)} call items"
                    )
                    continue
                if any(
                    getattr(item, "name", QUOTE_TOOL) != QUOTE_TOOL
                    for item in completed_calls
                ):
                    raise RuntimeError("Foundry invoked an MCP tool outside the allow-list")
                quotes = []
                rejected_results = []
                for item in completed_calls:
                    try:
                        quotes.append(quote_from_mcp_call(item))
                    except RuntimeError as error:
                        rejected_results.append(str(error))
                if not quotes:
                    validation_errors.append(
                        f"attempt {response_attempt}: "
                        + "; ".join(rejected_results)
                    )
                    continue
                quote = quotes[0]
                if any(candidate != quote for candidate in quotes[1:]):
                    raise RuntimeError(
                        "completed MCP retries returned different quote results"
                    )
                break
            else:
                raise RuntimeError(
                    "no Foundry response contained a valid MCP quote after 3 attempts: "
                    + " | ".join(validation_errors)
                )
            print(
                json.dumps(
                    {
                        "agentName": agent.name,
                        "agentVersion": str(agent.version),
                        "responseAttempt": response_attempt,
                        "mcpCallCount": len(calls),
                        "mcpCompletedCallCount": len(completed_calls),
                        "mcpValidatedCallCount": len(quotes),
                        "mcpRejectedResultCount": len(rejected_results),
                        "mcpTool": getattr(completed_calls[0], "name", QUOTE_TOOL),
                        "quote": quote,
                        "output": response.output_text,
                        "simulation": True,
                        "simulatedBackend": SIMULATION_LABEL,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        finally:
            if not args.retain_agent:
                project.agents.delete_version(
                    agent_name=agent.name,
                    agent_version=agent.version,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
