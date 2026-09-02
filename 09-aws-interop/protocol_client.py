"""Live REST, MCP, and A2A clients for the Pattern 9 APIM protocol gateway."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import requests


DEFAULT_QUOTE = {
    "capability": "data-analysis",
    "serviceLevel": "priority",
    "units": 250,
    "termMonths": 12,
}
SIMULATION_LABEL = "AWS Lambda / Amazon Bedrock (simulated)"


def require_https(name: str, value: str | None) -> str:
    if not value:
        raise SystemExit(f"Set {name} to the deployed APIM HTTPS endpoint.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit(f"{name} must be a remote HTTPS endpoint.")
    return value.rstrip("/")


def apim_headers(subscription_key: str | None) -> dict[str, str]:
    if not subscription_key:
        raise SystemExit("Set PATTERN9_APIM_SUBSCRIPTION_KEY in the current process.")
    return {"Ocp-Apim-Subscription-Key": subscription_key}


def validate_response_id(expected_id: str | int, payload: dict[str, Any]) -> None:
    if payload.get("jsonrpc") != "2.0":
        raise RuntimeError("A2A response is not a JSON-RPC 2.0 envelope")
    if payload.get("id") != expected_id:
        raise RuntimeError(
            f"A2A response ID mismatch: expected {expected_id!r}, got {payload.get('id')!r}"
        )


def extract_a2a_quote(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        quote = payload["result"]["artifacts"][0]["parts"][0]["data"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("A2A response did not contain a quote artifact") from error
    if quote.get("simulatedBackend") != SIMULATION_LABEL:
        raise RuntimeError("A2A result did not retain the simulated AWS/Bedrock label")
    return quote


def call_rest(
    rest_url: str,
    subscription_key: str,
    quote_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.post(
        f"{rest_url.rstrip('/')}/v1/quotes",
        headers=apim_headers(subscription_key),
        json=quote_request or DEFAULT_QUOTE,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("simulatedBackend") != SIMULATION_LABEL:
        raise RuntimeError("REST result did not retain the simulated AWS/Bedrock label")
    return payload


def call_a2a(
    a2a_url: str,
    subscription_key: str,
    quote_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = apim_headers(subscription_key)
    card_response = requests.get(
        f"{a2a_url.rstrip('/')}/.well-known/agent-card.json",
        headers=headers,
        timeout=30,
    )
    card_response.raise_for_status()
    card = card_response.json()
    if card.get("preferredTransport") != "JSONRPC":
        raise RuntimeError("APIM agent card does not advertise JSONRPC")
    runtime_url = card.get("url")
    base_parts = urlsplit(a2a_url)
    runtime_parts = urlsplit(str(runtime_url))
    if (
        runtime_parts.scheme != "https"
        or runtime_parts.hostname != base_parts.hostname
        or not runtime_parts.path.startswith(base_parts.path.rstrip("/") + "/")
    ):
        raise RuntimeError("APIM agent card returned an unexpected runtime URL")

    request_id = f"a2a-{uuid4().hex[:12]}"
    message_id = f"message-{uuid4().hex[:12]}"
    response = requests.post(
        runtime_url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": message_id,
                    "parts": [
                        {
                            "kind": "data",
                            "data": quote_request or DEFAULT_QUOTE,
                        }
                    ],
                }
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    validate_response_id(request_id, payload)
    quote = extract_a2a_quote(payload)
    return {
        "agentCard": card,
        "runtimeUrl": runtime_url,
        "envelope": payload,
        "quote": quote,
    }


def extract_mcp_quote(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise RuntimeError("APIM MCP create_quote returned a tool error")
    candidates = []
    for attribute in ("structuredContent", "structured_content"):
        structured = getattr(result, attribute, None)
        if isinstance(structured, dict):
            candidates.append(structured)
    content = getattr(result, "content", None)
    for block in content if isinstance(content, list) else []:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            candidates.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    payload = next(
        (candidate for candidate in candidates if isinstance(candidate, dict)),
        None,
    )
    if payload is None:
        raise RuntimeError("APIM MCP returned no structured quote result")
    if payload.get("simulatedBackend") != SIMULATION_LABEL:
        raise RuntimeError("MCP result did not retain the simulated AWS/Bedrock label")
    return payload


async def call_mcp(
    mcp_url: str,
    subscription_key: str,
    quote_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        mcp_url,
        headers=apim_headers(subscription_key),
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = [tool.name for tool in tools.tools]
            if "create_quote" not in tool_names:
                raise RuntimeError(
                    f"APIM MCP did not expose create_quote; received {tool_names}"
                )
            result = await session.call_tool(
                "create_quote",
                quote_request or DEFAULT_QUOTE,
            )
            payload = extract_mcp_quote(result)
            return {"tools": tool_names, "quote": payload}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "lane",
        choices=("rest", "mcp", "a2a", "all"),
        nargs="?",
        default="all",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    key = os.getenv("PATTERN9_APIM_SUBSCRIPTION_KEY")
    results: dict[str, Any] = {}
    if args.lane in {"rest", "all"}:
        results["rest"] = call_rest(
            require_https("PATTERN9_REST_URL", os.getenv("PATTERN9_REST_URL")),
            key or "",
        )
    if args.lane in {"mcp", "all"}:
        results["mcp"] = asyncio.run(
            call_mcp(
                require_https("PATTERN9_MCP_URL", os.getenv("PATTERN9_MCP_URL")),
                key or "",
            )
        )
    if args.lane in {"a2a", "all"}:
        results["a2a"] = call_a2a(
            require_https("PATTERN9_A2A_URL", os.getenv("PATTERN9_A2A_URL")),
            key or "",
        )
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
