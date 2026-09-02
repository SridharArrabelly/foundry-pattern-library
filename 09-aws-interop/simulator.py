"""Deterministic simulated AWS/Bedrock REST and A2A backend for Pattern 9."""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import os
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


SERVICE_NAME = "pattern-9-protocol-gateway-simulator"
SIMULATED_BACKEND = "AWS Lambda / Amazon Bedrock (simulated)"
AGENT_ID = "simulated-aws-capability-agent"
A2A_PROTOCOL_VERSION = "0.3.0"
A2A_RUNTIME_PATH = "/a2a"
MAX_TASKS = 100

RATE_BY_SERVICE_LEVEL = {
    "standard": Decimal("2.40"),
    "priority": Decimal("3.60"),
    "critical": Decimal("5.20"),
}
DISCOUNT_BY_TERM = {
    1: Decimal("0.00"),
    12: Decimal("0.05"),
    24: Decimal("0.09"),
    36: Decimal("0.12"),
}


class QuoteRequest(BaseModel):
    """Industry-neutral inputs for the deterministic quote capability."""

    model_config = ConfigDict(extra="forbid")

    capability: Literal[
        "document-processing",
        "data-analysis",
        "workflow-automation",
    ]
    serviceLevel: Literal["standard", "priority", "critical"] = "standard"
    units: int = Field(ge=1, le=100_000)
    termMonths: Literal[1, 12, 24, 36] = 12


class QuoteResponse(QuoteRequest):
    quoteId: str
    correlationId: str
    currency: Literal["USD"] = "USD"
    unitPrice: str
    totalPrice: str
    validForMinutes: Literal[15] = 15
    simulation: Literal[True] = True
    simulatedBackend: str = SIMULATED_BACKEND


app = FastAPI(
    title="Pattern 9 simulated AWS/Bedrock capability",
    description=(
        "Industry-neutral deterministic REST capability and a minimal A2A JSON-RPC "
        "runtime. AWS Lambda and Amazon Bedrock are explicitly simulated."
    ),
    version="1.0.0",
)

_tasks: OrderedDict[str, dict[str, Any]] = OrderedDict()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_id(prefix: str, value: Any, length: int = 16) -> str:
    encoded = canonical_json(value).encode("utf-8")
    gateway_key = os.getenv("PATTERN9_BACKEND_GATEWAY_KEY")
    digest = (
        hmac.new(gateway_key.encode("utf-8"), encoded, hashlib.sha256).hexdigest()
        if gateway_key
        else hashlib.sha256(encoded).hexdigest()
    )
    return f"{prefix}-{digest[:length]}"


def require_gateway(request: Request) -> None:
    expected = os.getenv("PATTERN9_BACKEND_GATEWAY_KEY")
    if not expected:
        return
    provided = request.headers.get("x-pattern9-backend-key", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="APIM gateway authentication required")


def create_quote(request: QuoteRequest) -> QuoteResponse:
    normalized = request.model_dump(mode="json")
    unit_price = RATE_BY_SERVICE_LEVEL[request.serviceLevel]
    discount = DISCOUNT_BY_TERM[request.termMonths]
    total = (
        Decimal(request.units) * unit_price * (Decimal("1.00") - discount)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return QuoteResponse(
        **normalized,
        quoteId=stable_id("quote", normalized),
        correlationId=stable_id("corr", normalized),
        unitPrice=f"{unit_price:.2f}",
        totalPrice=f"{total:.2f}",
    )


def public_base_url(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_host:
        scheme = forwarded_proto or "https"
        return f"{scheme}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def build_agent_card(base_url: str) -> dict[str, Any]:
    return {
        "name": "Simulated AWS/Bedrock capability agent",
        "description": (
            "A deterministic industry-neutral quote agent. AWS Lambda and Amazon "
            "Bedrock are simulated; the A2A protocol surface is real."
        ),
        "url": f"{base_url.rstrip('/')}{A2A_RUNTIME_PATH}",
        "version": "1.0.0",
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "preferredTransport": "JSONRPC",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": "deterministic-capability-quote",
                "name": "Create deterministic capability quote",
                "description": (
                    "Quotes document processing, data analysis, or workflow "
                    "automation capacity with deterministic correlation."
                ),
                "tags": ["quote", "deterministic", "simulated-aws-bedrock"],
                "examples": [
                    (
                        "Quote 250 units of data-analysis capacity at the priority "
                        "service level for 12 months."
                    )
                ],
            }
        ],
        "supportsAuthenticatedExtendedCard": False,
    }


def validate_agent_card(card: dict[str, Any], expected_runtime_path: str) -> None:
    required = {
        "name",
        "url",
        "version",
        "protocolVersion",
        "preferredTransport",
        "skills",
    }
    missing = required.difference(card)
    if missing:
        raise ValueError(f"agent card is missing required fields: {sorted(missing)}")
    if card["protocolVersion"] != A2A_PROTOCOL_VERSION:
        raise ValueError("agent-card protocol version does not match the runtime")
    if card["preferredTransport"] != "JSONRPC":
        raise ValueError("agent-card transport does not match the JSON-RPC runtime")
    if urlsplit(str(card["url"])).path.rstrip("/") != expected_runtime_path.rstrip("/"):
        raise ValueError("agent-card URL does not match the JSON-RPC runtime path")


def jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _validate_envelope(payload: Any) -> tuple[Any, str, dict[str, Any]] | dict:
    if not isinstance(payload, dict):
        return jsonrpc_error(None, -32600, "Invalid Request")
    request_id = payload.get("id")
    if (
        payload.get("jsonrpc") != "2.0"
        or isinstance(request_id, bool)
        or not isinstance(request_id, (str, int))
        or not isinstance(payload.get("method"), str)
        or not isinstance(payload.get("params"), dict)
    ):
        return jsonrpc_error(request_id, -32600, "Invalid Request")
    return request_id, payload["method"], payload["params"]


def _quote_from_message(params: dict[str, Any]) -> tuple[QuoteRequest, dict[str, Any]]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise ValueError("params.message must be an object")
    if message.get("role") != "user":
        raise ValueError("params.message.role must be 'user'")
    message_id = message.get("messageId")
    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("params.message.messageId must be a non-empty string")
    parts = message.get("parts")
    if not isinstance(parts, list) or len(parts) != 1:
        raise ValueError("params.message.parts must contain exactly one data part")
    part = parts[0]
    if not isinstance(part, dict) or part.get("kind") != "data":
        raise ValueError("only A2A data parts are supported")
    data = part.get("data")
    if not isinstance(data, dict):
        raise ValueError("the A2A data part must contain an object")
    return QuoteRequest.model_validate(data), message


def _store_task(task: dict[str, Any]) -> None:
    _tasks[task["id"]] = task
    _tasks.move_to_end(task["id"])
    while len(_tasks) > MAX_TASKS:
        _tasks.popitem(last=False)


def _send_message(request_id: Any, params: dict[str, Any]) -> dict:
    quote_request, message = _quote_from_message(params)
    quote = create_quote(quote_request).model_dump(mode="json")
    correlation = {
        "messageId": message["messageId"],
        "quote": quote,
    }
    task_id = stable_id("task", correlation)
    task = {
        "id": task_id,
        "contextId": stable_id("ctx", correlation),
        "status": {"state": "completed"},
        "artifacts": [
            {
                "artifactId": stable_id("artifact", correlation),
                "name": "deterministic-capability-quote",
                "parts": [{"kind": "data", "data": quote}],
            }
        ],
        "history": [message],
        "metadata": {
            "simulation": True,
            "simulatedBackend": SIMULATED_BACKEND,
            "correlationId": quote["correlationId"],
        },
    }
    _store_task(task)
    return {"jsonrpc": "2.0", "id": request_id, "result": task}


def _get_task(request_id: Any, params: dict[str, Any]) -> dict:
    task_id = params.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("params.id must be a non-empty task ID")
    task = _tasks.get(task_id)
    if task is None:
        return jsonrpc_error(
            request_id,
            -32001,
            "Task not found",
            {"taskId": task_id},
        )
    return {"jsonrpc": "2.0", "id": request_id, "result": task}


def handle_jsonrpc(payload: Any) -> dict[str, Any]:
    validated = _validate_envelope(payload)
    if isinstance(validated, dict):
        return validated
    request_id, method, params = validated
    try:
        if method == "message/send":
            return _send_message(request_id, params)
        if method == "tasks/get":
            return _get_task(request_id, params)
        return jsonrpc_error(request_id, -32601, "Method not found")
    except (ValueError, TypeError) as error:
        return jsonrpc_error(
            request_id,
            -32602,
            "Invalid params",
            {"reason": str(error)},
        )


@app.get("/health", operation_id="health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "simulation": True,
        "simulatedBackend": SIMULATED_BACKEND,
        "a2aProtocolVersion": A2A_PROTOCOL_VERSION,
    }


@app.get(
    "/v1/capabilities",
    operation_id="listCapabilities",
    dependencies=[Depends(require_gateway)],
)
def list_capabilities() -> dict[str, Any]:
    return {
        "capabilities": [
            "document-processing",
            "data-analysis",
            "workflow-automation",
        ],
        "serviceLevels": list(RATE_BY_SERVICE_LEVEL),
        "termMonths": list(DISCOUNT_BY_TERM),
        "simulation": True,
        "simulatedBackend": SIMULATED_BACKEND,
    }


@app.post(
    "/v1/quotes",
    response_model=QuoteResponse,
    operation_id="createQuote",
    dependencies=[Depends(require_gateway)],
)
def quote(request: QuoteRequest) -> QuoteResponse:
    return create_quote(request)


@app.get(
    "/.well-known/agent-card.json",
    dependencies=[Depends(require_gateway)],
)
def agent_card(request: Request) -> dict[str, Any]:
    card = build_agent_card(public_base_url(request))
    validate_agent_card(card, A2A_RUNTIME_PATH)
    return card


@app.post(
    A2A_RUNTIME_PATH,
    dependencies=[Depends(require_gateway)],
)
async def a2a_jsonrpc(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "application/json":
        return JSONResponse(
            status_code=415,
            content=jsonrpc_error(None, -32600, "Only application/json is supported"),
        )
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(content=jsonrpc_error(None, -32700, "Parse error"))
    return JSONResponse(content=handle_jsonrpc(payload))
