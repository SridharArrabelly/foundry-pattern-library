"""Remote-capable MCP change-control service for Pattern 13."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import tempfile

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from approval_store import ApprovalError, ApprovalStore


DB_PATH = os.environ.get(
    "APPROVAL_DB_PATH",
    str(Path(tempfile.gettempdir()) / "foundry-pattern-13.sqlite3"),
)
API_KEY = os.environ.get("MCP_API_KEY", "")
ALLOW_INSECURE_LOCAL = os.environ.get("MCP_ALLOW_INSECURE_LOCAL", "").lower() in {
    "1",
    "true",
    "yes",
}

store = ApprovalStore(DB_PATH)
mcp = FastMCP(
    "Foundry change control",
    instructions=(
        "get_change_request is read-only. schedule_change is consequential and must "
        "be protected by the Foundry MCP approval policy plus downstream authorization."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def get_change_request(change_request_id: str) -> str:
    """Read one reviewed change request. This tool never requires human approval."""
    return json.dumps(store.get_change_request(change_request_id), sort_keys=True)


@mcp.tool()
def schedule_change(change_request_id: str, scheduled_for: str, reason: str) -> str:
    """Schedule an approved change. Foundry must require approval for every call."""
    result = store.schedule_change(
        {
            "change_request_id": change_request_id,
            "scheduled_for": scheduled_for,
            "reason": reason,
        }
    )
    return json.dumps(result, sort_keys=True)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/decisions", methods=["POST"])
async def record_decision(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
        result = store.record_decision(payload)
        return JSONResponse(
            {
                "decision_id": result.decision_id,
                "approval_request_id": result.approval_request_id,
                "change_request_id": result.request_id,
                "approve": result.approve,
                "duplicate": result.duplicate,
            }
        )
    except (ApprovalError, ValueError, TypeError) as error:
        return JSONResponse({"error": str(error)}, status_code=409)


@mcp.custom_route("/audit/{request_id}", methods=["GET"])
async def audit(request: Request) -> JSONResponse:
    try:
        return JSONResponse(store.audit(request.path_params["request_id"]))
    except ApprovalError as error:
        return JSONResponse({"error": str(error)}, status_code=404)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if ALLOW_INSECURE_LOCAL and request.client and request.client.host in {
            "127.0.0.1",
            "::1",
        }:
            return await call_next(request)
        supplied = request.headers.get("x-mcp-api-key", "")
        if not API_KEY or not hmac.compare_digest(supplied, API_KEY):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app = mcp.streamable_http_app()
app.add_middleware(ApiKeyMiddleware)


def main() -> None:
    if not API_KEY and not ALLOW_INSECURE_LOCAL:
        raise SystemExit(
            "Set MCP_API_KEY. For loopback-only development, explicitly set "
            "MCP_ALLOW_INSECURE_LOCAL=true."
        )
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
