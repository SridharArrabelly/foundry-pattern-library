"""Run live negative and positive checks through the deployed APIM gateway."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import requests

from protocol_client import (
    DEFAULT_QUOTE,
    apim_headers,
    call_a2a,
    call_mcp,
    call_rest,
    require_https,
    validate_response_id,
)


def post_jsonrpc(
    url: str,
    key: str,
    request_id: str,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    response = requests.post(
        url,
        headers=apim_headers(key),
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    validate_response_id(request_id, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Write sanitized evidence outside the repository.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    key = os.getenv("PATTERN9_APIM_SUBSCRIPTION_KEY")
    if not key:
        raise SystemExit("Set PATTERN9_APIM_SUBSCRIPTION_KEY in the current process.")
    rest_url = require_https("PATTERN9_REST_URL", os.getenv("PATTERN9_REST_URL"))
    mcp_url = require_https("PATTERN9_MCP_URL", os.getenv("PATTERN9_MCP_URL"))
    a2a_url = require_https("PATTERN9_A2A_URL", os.getenv("PATTERN9_A2A_URL"))
    backend_url = require_https(
        "PATTERN9_BACKEND_URL",
        os.getenv("PATTERN9_BACKEND_URL"),
    )

    backend_health_response = requests.get(
        f"{backend_url}/health",
        timeout=30,
    )
    backend_health_response.raise_for_status()
    backend_health = backend_health_response.json()
    direct_backend_bypass = {
        "capabilities": requests.get(
            f"{backend_url}/v1/capabilities",
            timeout=30,
        ).status_code,
        "quote": requests.post(
            f"{backend_url}/v1/quotes",
            json=DEFAULT_QUOTE,
            timeout=30,
        ).status_code,
        "agentCard": requests.get(
            f"{backend_url}/.well-known/agent-card.json",
            timeout=30,
        ).status_code,
        "a2a": requests.post(
            f"{backend_url}/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "direct-bypass",
                "method": "message/send",
                "params": {},
            },
            timeout=30,
        ).status_code,
    }
    if set(direct_backend_bypass.values()) != {401}:
        raise RuntimeError(
            f"direct backend bypass was not rejected: {direct_backend_bypass}"
        )
    gateway_health_response = requests.get(
        f"{rest_url}/health",
        headers=apim_headers(key),
        timeout=30,
    )
    gateway_health_response.raise_for_status()
    gateway_health = gateway_health_response.json()

    unauthenticated = {
        "rest": requests.get(f"{rest_url}/health", timeout=30).status_code,
        "mcp": requests.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": "unauthenticated",
                "method": "initialize",
                "params": {},
            },
            timeout=30,
        ).status_code,
        "a2a": requests.get(
            f"{a2a_url}/.well-known/agent-card.json",
            timeout=30,
        ).status_code,
    }
    if set(unauthenticated.values()) != {401}:
        raise RuntimeError(
            f"APIM subscription enforcement did not return 401: {unauthenticated}"
        )

    rest_first = call_rest(rest_url, key)
    rest_second = call_rest(rest_url, key)
    if rest_first != rest_second:
        raise RuntimeError("REST quote is not deterministic for identical inputs")

    mcp = asyncio.run(call_mcp(mcp_url, key))
    a2a = call_a2a(a2a_url, key)
    if len({rest_first["correlationId"], mcp["quote"]["correlationId"], a2a["quote"]["correlationId"]}) != 1:
        raise RuntimeError("REST, MCP, and A2A lanes returned different correlation IDs")
    if mcp["quote"] != rest_first or a2a["quote"] != rest_first:
        raise RuntimeError("REST, MCP, and A2A lanes returned different quote payloads")

    unsupported = post_jsonrpc(
        a2a["runtimeUrl"],
        key,
        "unsupported-method",
        "tasks/cancel",
        {"id": a2a["envelope"]["result"]["id"]},
    )
    if unsupported.get("error", {}).get("code") != -32601:
        raise RuntimeError("unsupported A2A method did not return -32601")

    missing_task = post_jsonrpc(
        a2a["runtimeUrl"],
        key,
        "missing-task",
        "tasks/get",
        {"id": "task-does-not-exist"},
    )
    if missing_task.get("error", {}).get("code") != -32001:
        raise RuntimeError("unknown A2A task did not return -32001")

    malformed_response = requests.post(
        a2a["runtimeUrl"],
        headers={
            **apim_headers(key),
            "Content-Type": "application/json",
        },
        data="{not-json",
        timeout=30,
    )
    malformed_response.raise_for_status()
    malformed = malformed_response.json()
    if malformed.get("error", {}).get("code") != -32700:
        raise RuntimeError("malformed JSON-RPC did not return -32700")

    wrong_transport = requests.post(
        a2a["runtimeUrl"],
        headers={
            **apim_headers(key),
            "Content-Type": "text/plain",
        },
        data="unsupported",
        timeout=30,
    )
    if wrong_transport.status_code != 415:
        raise RuntimeError("unsupported A2A transport did not return HTTP 415")

    evidence = {
        "schemaVersion": "1.0",
        "verifiedAtUtc": datetime.now(timezone.utc).isoformat(),
        "realComponents": [
            "Azure Container Apps HTTPS backend",
            "Azure API Management REST API",
            "Azure API Management REST-backed MCP API",
            "Azure API Management A2A API",
            "MCP streamable HTTP client",
            "A2A JSON-RPC client",
        ],
        "simulatedComponents": ["AWS Lambda", "Amazon Bedrock agent"],
        "checks": {
            "backendHealth": backend_health["status"],
            "directBackendBypassHttpStatus": direct_backend_bypass,
            "gatewayHealth": gateway_health["status"],
            "unauthenticatedHttpStatus": unauthenticated,
            "restDeterministic": True,
            "crossLaneQuoteEquality": True,
            "mcpTools": mcp["tools"],
            "a2aPreferredTransport": a2a["agentCard"]["preferredTransport"],
            "correlationId": rest_first["correlationId"],
            "malformedJsonRpcCode": malformed["error"]["code"],
            "unknownTaskCode": missing_task["error"]["code"],
            "unsupportedMethodCode": unsupported["error"]["code"],
            "unsupportedTransportHttpStatus": wrong_transport.status_code,
        },
        "quote": rest_first,
    }
    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    if key in rendered:
        raise RuntimeError("subscription key leaked into evidence")
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
