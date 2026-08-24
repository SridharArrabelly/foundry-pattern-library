"""Deterministic SQLite change-control state for Pattern 13.

SQLite is intentionally demo-only. Production systems should use their durable
change-management store and enforce the same authorization and idempotency rules.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator


SERVER_LABEL = "change_control"
READ_TOOL = "get_change_request"
WRITE_TOOL = "schedule_change"
DECISION_TTL_SECONDS = 300

DEMO_REQUESTS = (
    (
        "CRQ-1001",
        "Restart the inventory synchronization worker",
        "2026-08-25T02:00:00Z",
        "Apply the reviewed runtime configuration.",
    ),
    (
        "CRQ-1002",
        "Rotate the reporting service deployment",
        "2026-08-25T03:00:00Z",
        "Move the service to the approved image digest.",
    ),
    (
        "CRQ-1003",
        "Enable the reviewed order-validation rule",
        "2026-08-25T04:00:00Z",
        "Activate the approved rule during the maintenance window.",
    ),
)


class ApprovalError(RuntimeError):
    """Base fail-closed error for approval and scheduling failures."""


class MalformedApproval(ApprovalError):
    pass


class MismatchedApproval(ApprovalError):
    pass


class StaleApproval(ApprovalError):
    pass


class MissingApproval(ApprovalError):
    pass


class ReplayConflict(ApprovalError):
    pass


@dataclass(frozen=True)
class DecisionResult:
    decision_id: str
    approval_request_id: str
    request_id: str
    approve: bool
    duplicate: bool


def utc_now_epoch() -> int:
    return int(time.time())


def iso_time(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def normalize_schedule_arguments(value: dict[str, Any] | str) -> tuple[dict[str, str], str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise MalformedApproval("tool arguments are not valid JSON") from error
    if not isinstance(value, dict):
        raise MalformedApproval("tool arguments must be a JSON object")
    required = {"change_request_id", "scheduled_for", "reason"}
    if set(value) != required:
        raise MalformedApproval(
            f"tool arguments must contain exactly {sorted(required)}"
        )
    normalized: dict[str, str] = {}
    for key in sorted(required):
        item = value[key]
        if not isinstance(item, str) or not item.strip():
            raise MalformedApproval(f"{key} must be a non-empty string")
        normalized[key] = item.strip()
    if not normalized["scheduled_for"].endswith("Z"):
        raise MalformedApproval("scheduled_for must be an ISO-8601 UTC timestamp ending in Z")
    return normalized, canonical_json(normalized)


class ApprovalStore:
    def __init__(self, path: str | Path, *, now=utc_now_epoch):
        self.path = str(path)
        self.now = now
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS change_requests (
                    request_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    canonical_arguments TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('open', 'approved', 'rejected', 'scheduled')
                    ),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    approval_request_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    server_label TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    canonical_arguments TEXT NOT NULL,
                    approve INTEGER NOT NULL CHECK (approve IN (0, 1)),
                    reason TEXT,
                    decided_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES change_requests(request_id)
                );
                CREATE TABLE IF NOT EXISTS side_effects (
                    side_effect_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    decision_id TEXT NOT NULL UNIQUE,
                    canonical_arguments TEXT NOT NULL,
                    scheduled_at INTEGER NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES change_requests(request_id),
                    FOREIGN KEY(decision_id) REFERENCES decisions(decision_id)
                );
                """
            )
            now = self.now()
            for request_id, summary, scheduled_for, reason in DEMO_REQUESTS:
                arguments, canonical = normalize_schedule_arguments(
                    {
                        "change_request_id": request_id,
                        "scheduled_for": scheduled_for,
                        "reason": reason,
                    }
                )
                del arguments
                connection.execute(
                    """
                    INSERT OR IGNORE INTO change_requests
                        (request_id, summary, canonical_arguments, status, created_at, expires_at)
                    VALUES (?, ?, ?, 'open', ?, ?)
                    """,
                    (request_id, summary, canonical, now, now + 86_400),
                )

    def get_change_request(self, request_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError(f"unknown change request: {request_id}")
            arguments = json.loads(row["canonical_arguments"])
            side_effect = connection.execute(
                "SELECT side_effect_id FROM side_effects WHERE request_id = ?", (request_id,)
            ).fetchone()
            return {
                "change_request_id": row["request_id"],
                "summary": row["summary"],
                "status": row["status"],
                "expires_at": iso_time(row["expires_at"]),
                "schedule_tool": WRITE_TOOL,
                "schedule_arguments": arguments,
                "side_effect_id": side_effect["side_effect_id"] if side_effect else None,
            }

    def _validate_envelope(self, envelope: dict[str, Any]) -> tuple[dict[str, str], str]:
        required = {
            "type",
            "id",
            "approval_request_id",
            "approve",
            "reason",
            "server_label",
            "tool_name",
            "arguments",
        }
        if not isinstance(envelope, dict) or set(envelope) != required:
            raise MalformedApproval(
                f"approval envelope must contain exactly {sorted(required)}"
            )
        if envelope["type"] != "mcp_approval_response":
            raise MalformedApproval("approval response type must be mcp_approval_response")
        for field in ("id", "approval_request_id", "server_label", "tool_name"):
            if not isinstance(envelope[field], str) or not envelope[field].strip():
                raise MalformedApproval(f"{field} must be a non-empty string")
        if type(envelope["approve"]) is not bool:
            raise MalformedApproval("approve must be a boolean")
        if envelope["reason"] is not None and not isinstance(envelope["reason"], str):
            raise MalformedApproval("reason must be a string or null")
        if envelope["server_label"] != SERVER_LABEL:
            raise MismatchedApproval("approval server_label does not match this MCP server")
        if envelope["tool_name"] != WRITE_TOOL:
            raise MismatchedApproval("approval is not for schedule_change")
        return normalize_schedule_arguments(envelope["arguments"])

    def record_decision(self, envelope: dict[str, Any]) -> DecisionResult:
        arguments, canonical = self._validate_envelope(envelope)
        request_id = arguments["change_request_id"]
        now = self.now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise MismatchedApproval("approval references an unknown change request")
            if canonical != request["canonical_arguments"]:
                raise MismatchedApproval(
                    "approval arguments do not exactly match the reviewed change request"
                )
            if now > request["expires_at"]:
                raise StaleApproval("change request expired before the decision was recorded")

            by_decision = connection.execute(
                "SELECT * FROM decisions WHERE decision_id = ?", (envelope["id"],)
            ).fetchone()
            by_approval = connection.execute(
                "SELECT * FROM decisions WHERE approval_request_id = ?",
                (envelope["approval_request_id"],),
            ).fetchone()
            existing = by_decision or by_approval
            if existing is not None:
                same = (
                    existing["decision_id"] == envelope["id"]
                    and existing["approval_request_id"] == envelope["approval_request_id"]
                    and existing["request_id"] == request_id
                    and existing["canonical_arguments"] == canonical
                    and bool(existing["approve"]) is envelope["approve"]
                )
                if not same:
                    raise ReplayConflict(
                        "decision or approval-request ID was replayed with different content"
                    )
                return DecisionResult(
                    decision_id=existing["decision_id"],
                    approval_request_id=existing["approval_request_id"],
                    request_id=existing["request_id"],
                    approve=bool(existing["approve"]),
                    duplicate=True,
                )

            if request["status"] != "open":
                raise ReplayConflict(
                    f"change request is already {request['status']}; a new decision is not allowed"
                )
            decision_expires = min(request["expires_at"], now + DECISION_TTL_SECONDS)
            connection.execute(
                """
                INSERT INTO decisions (
                    decision_id, approval_request_id, request_id, server_label, tool_name,
                    canonical_arguments, approve, reason, decided_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope["id"],
                    envelope["approval_request_id"],
                    request_id,
                    envelope["server_label"],
                    envelope["tool_name"],
                    canonical,
                    int(envelope["approve"]),
                    envelope["reason"],
                    now,
                    decision_expires,
                ),
            )
            connection.execute(
                "UPDATE change_requests SET status = ? WHERE request_id = ?",
                ("approved" if envelope["approve"] else "rejected", request_id),
            )
            return DecisionResult(
                decision_id=envelope["id"],
                approval_request_id=envelope["approval_request_id"],
                request_id=request_id,
                approve=envelope["approve"],
                duplicate=False,
            )

    def schedule_change(self, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized, canonical = normalize_schedule_arguments(arguments)
        request_id = normalized["change_request_id"]
        now = self.now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None or canonical != request["canonical_arguments"]:
                raise MismatchedApproval(
                    "schedule arguments do not exactly match the reviewed change request"
                )

            existing = connection.execute(
                "SELECT * FROM side_effects WHERE request_id = ?", (request_id,)
            ).fetchone()
            if existing is not None:
                return {
                    "status": "already_scheduled",
                    "change_request_id": request_id,
                    "decision_id": existing["decision_id"],
                    "side_effect_id": existing["side_effect_id"],
                    "idempotent_replay": True,
                }

            decision = connection.execute(
                """
                SELECT * FROM decisions
                WHERE request_id = ? AND canonical_arguments = ? AND approve = 1
                ORDER BY decided_at DESC
                LIMIT 1
                """,
                (request_id, canonical),
            ).fetchone()
            if decision is None:
                raise MissingApproval(
                    "no recorded approval authorizes this exact schedule_change call"
                )
            if now > decision["expires_at"]:
                raise StaleApproval("approval expired before schedule_change executed")
            if request["status"] != "approved":
                raise ReplayConflict(
                    f"change request is {request['status']}, not approved for execution"
                )

            digest = hashlib.sha256(
                f"{request_id}:{decision['decision_id']}:{canonical}".encode("utf-8")
            ).hexdigest()[:20]
            side_effect_id = f"effect-{digest}"
            connection.execute(
                """
                INSERT INTO side_effects (
                    side_effect_id, request_id, decision_id, canonical_arguments, scheduled_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (side_effect_id, request_id, decision["decision_id"], canonical, now),
            )
            connection.execute(
                "UPDATE change_requests SET status = 'scheduled' WHERE request_id = ?",
                (request_id,),
            )
            return {
                "status": "scheduled",
                "change_request_id": request_id,
                "approval_request_id": decision["approval_request_id"],
                "decision_id": decision["decision_id"],
                "side_effect_id": side_effect_id,
                "idempotent_replay": False,
            }

    def audit(self, request_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            request = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise ApprovalError(f"unknown change request: {request_id}")
            decisions = connection.execute(
                """
                SELECT decision_id, approval_request_id, approve, tool_name, decided_at
                FROM decisions WHERE request_id = ? ORDER BY decided_at
                """,
                (request_id,),
            ).fetchall()
            effects = connection.execute(
                """
                SELECT side_effect_id, decision_id, scheduled_at
                FROM side_effects WHERE request_id = ? ORDER BY scheduled_at
                """,
                (request_id,),
            ).fetchall()
            return {
                "change_request_id": request_id,
                "status": request["status"],
                "decisions": [
                    {
                        "decision_id": row["decision_id"],
                        "approval_request_id": row["approval_request_id"],
                        "approve": bool(row["approve"]),
                        "tool_name": row["tool_name"],
                        "decided_at": iso_time(row["decided_at"]),
                    }
                    for row in decisions
                ],
                "side_effects": [
                    {
                        "side_effect_id": row["side_effect_id"],
                        "decision_id": row["decision_id"],
                        "scheduled_at": iso_time(row["scheduled_at"]),
                    }
                    for row in effects
                ],
            }
