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
import secrets
import sqlite3
import time
from typing import Any, Callable, Iterator


SERVER_LABEL = "change_control"
READ_TOOL = "get_change_request"
WRITE_TOOL = "schedule_change"
DECISION_TTL_SECONDS = 300
SCHEMA_VERSION = 2

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
class PendingResult:
    pending_approval_id: str
    approval_request_id: str
    request_id: str
    duplicate: bool


@dataclass(frozen=True)
class DecisionResult:
    pending_approval_id: str
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
    required = {
        "change_request_id",
        "pending_approval_id",
        "scheduled_for",
        "reason",
    }
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
    if not normalized["pending_approval_id"].startswith("pending-"):
        raise MalformedApproval("pending_approval_id is not server-issued")
    if not normalized["scheduled_for"].endswith("Z"):
        raise MalformedApproval("scheduled_for must be an ISO-8601 UTC timestamp ending in Z")
    return normalized, canonical_json(normalized)


class ApprovalStore:
    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], int] = utc_now_epoch,
        nonce_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
    ):
        self.path = str(path)
        self.now = now
        self.nonce_factory = nonce_factory
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
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)"
            )
            version_row = connection.execute(
                "SELECT version FROM schema_meta LIMIT 1"
            ).fetchone()
            if version_row is None or version_row["version"] != SCHEMA_VERSION:
                connection.executescript(
                    """
                    DROP TABLE IF EXISTS side_effects;
                    DROP TABLE IF EXISTS decisions;
                    DROP TABLE IF EXISTS pending_approvals;
                    DROP TABLE IF EXISTS change_requests;
                    DELETE FROM schema_meta;
                    """
                )
                connection.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
                )
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
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    pending_approval_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    server_label TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    canonical_arguments TEXT NOT NULL,
                    approval_request_id TEXT UNIQUE,
                    status TEXT NOT NULL CHECK (
                        status IN ('issued', 'registered', 'approved', 'rejected', 'consumed')
                    ),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES change_requests(request_id)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    approval_request_id TEXT NOT NULL UNIQUE,
                    pending_approval_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    server_label TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    canonical_arguments TEXT NOT NULL,
                    approve INTEGER NOT NULL CHECK (approve IN (0, 1)),
                    reason TEXT,
                    decided_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES change_requests(request_id),
                    FOREIGN KEY(pending_approval_id)
                        REFERENCES pending_approvals(pending_approval_id)
                );
                CREATE TABLE IF NOT EXISTS side_effects (
                    side_effect_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    decision_id TEXT NOT NULL UNIQUE,
                    pending_approval_id TEXT NOT NULL UNIQUE,
                    canonical_arguments TEXT NOT NULL,
                    scheduled_at INTEGER NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES change_requests(request_id),
                    FOREIGN KEY(decision_id) REFERENCES decisions(decision_id),
                    FOREIGN KEY(pending_approval_id)
                        REFERENCES pending_approvals(pending_approval_id)
                );
                """
            )
            now = self.now()
            for request_id, summary, scheduled_for, reason in DEMO_REQUESTS:
                if connection.execute(
                    "SELECT 1 FROM change_requests WHERE request_id = ?", (request_id,)
                ).fetchone():
                    continue
                pending_id = f"pending-{self.nonce_factory()}"
                _, canonical = normalize_schedule_arguments(
                    {
                        "change_request_id": request_id,
                        "pending_approval_id": pending_id,
                        "scheduled_for": scheduled_for,
                        "reason": reason,
                    }
                )
                connection.execute(
                    """
                    INSERT INTO change_requests
                        (request_id, summary, canonical_arguments, status, created_at, expires_at)
                    VALUES (?, ?, ?, 'open', ?, ?)
                    """,
                    (request_id, summary, canonical, now, now + 86_400),
                )
                connection.execute(
                    """
                    INSERT INTO pending_approvals (
                        pending_approval_id, request_id, server_label, tool_name,
                        canonical_arguments, status, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, 'issued', ?, ?)
                    """,
                    (
                        pending_id,
                        request_id,
                        SERVER_LABEL,
                        WRITE_TOOL,
                        canonical,
                        now,
                        now + 86_400,
                    ),
                )

    def get_change_request(self, request_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError(f"unknown change request: {request_id}")
            pending = connection.execute(
                "SELECT status FROM pending_approvals WHERE request_id = ?", (request_id,)
            ).fetchone()
            side_effect = connection.execute(
                "SELECT side_effect_id FROM side_effects WHERE request_id = ?", (request_id,)
            ).fetchone()
            return {
                "change_request_id": row["request_id"],
                "summary": row["summary"],
                "status": row["status"],
                "expires_at": iso_time(row["expires_at"]),
                "schedule_tool": WRITE_TOOL,
                "schedule_arguments": json.loads(row["canonical_arguments"]),
                "pending_approval_status": pending["status"],
                "side_effect_id": side_effect["side_effect_id"] if side_effect else None,
            }

    def register_pending(self, payload: dict[str, Any]) -> PendingResult:
        required = {
            "pending_approval_id",
            "approval_request_id",
            "server_label",
            "tool_name",
            "arguments",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise MalformedApproval(
                f"pending registration must contain exactly {sorted(required)}"
            )
        for field in required - {"arguments"}:
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise MalformedApproval(f"{field} must be a non-empty string")
        arguments, canonical = normalize_schedule_arguments(payload["arguments"])
        if arguments["pending_approval_id"] != payload["pending_approval_id"]:
            raise MismatchedApproval("pending approval ID does not match tool arguments")
        if payload["server_label"] != SERVER_LABEL or payload["tool_name"] != WRITE_TOOL:
            raise MismatchedApproval("pending approval targets a different MCP tool")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self.now()
            pending = connection.execute(
                "SELECT * FROM pending_approvals WHERE pending_approval_id = ?",
                (payload["pending_approval_id"],),
            ).fetchone()
            if pending is None:
                raise MissingApproval("pending approval ID was not issued by this service")
            if now > pending["expires_at"]:
                raise StaleApproval("pending approval expired before registration")
            exact = (
                pending["server_label"] == payload["server_label"]
                and pending["tool_name"] == payload["tool_name"]
                and pending["canonical_arguments"] == canonical
            )
            if not exact:
                raise MismatchedApproval(
                    "pending approval is not bound to these exact tool arguments"
                )
            if pending["approval_request_id"] is not None:
                if pending["approval_request_id"] != payload["approval_request_id"]:
                    raise ReplayConflict(
                        "pending approval is already bound to another Foundry request"
                    )
                return PendingResult(
                    pending_approval_id=pending["pending_approval_id"],
                    approval_request_id=pending["approval_request_id"],
                    request_id=pending["request_id"],
                    duplicate=True,
                )
            if pending["status"] != "issued":
                raise ReplayConflict(
                    f"pending approval is {pending['status']}, not available for registration"
                )
            connection.execute(
                """
                UPDATE pending_approvals
                SET approval_request_id = ?, status = 'registered'
                WHERE pending_approval_id = ?
                """,
                (payload["approval_request_id"], payload["pending_approval_id"]),
            )
            return PendingResult(
                pending_approval_id=pending["pending_approval_id"],
                approval_request_id=payload["approval_request_id"],
                request_id=pending["request_id"],
                duplicate=False,
            )

    def _validate_envelope(self, envelope: dict[str, Any]) -> tuple[dict[str, str], str]:
        required = {
            "type",
            "id",
            "approval_request_id",
            "pending_approval_id",
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
        for field in (
            "id",
            "approval_request_id",
            "pending_approval_id",
            "server_label",
            "tool_name",
        ):
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
        arguments, canonical = normalize_schedule_arguments(envelope["arguments"])
        if arguments["pending_approval_id"] != envelope["pending_approval_id"]:
            raise MismatchedApproval("approval nonce does not match tool arguments")
        return arguments, canonical

    def record_decision(self, envelope: dict[str, Any]) -> DecisionResult:
        arguments, canonical = self._validate_envelope(envelope)
        request_id = arguments["change_request_id"]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self.now()
            pending = connection.execute(
                "SELECT * FROM pending_approvals WHERE pending_approval_id = ?",
                (envelope["pending_approval_id"],),
            ).fetchone()
            if pending is None:
                raise MissingApproval("approval nonce was not issued by this service")
            if (
                pending["request_id"] != request_id
                or pending["approval_request_id"] != envelope["approval_request_id"]
                or pending["server_label"] != envelope["server_label"]
                or pending["tool_name"] != envelope["tool_name"]
                or pending["canonical_arguments"] != canonical
            ):
                raise MismatchedApproval(
                    "decision does not match the registered pending approval"
                )
            if now > pending["expires_at"]:
                raise StaleApproval("pending approval expired before the decision")

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
                    and existing["pending_approval_id"] == envelope["pending_approval_id"]
                    and existing["request_id"] == request_id
                    and existing["canonical_arguments"] == canonical
                    and bool(existing["approve"]) is envelope["approve"]
                )
                if not same:
                    raise ReplayConflict(
                        "decision or approval-request ID was replayed with different content"
                    )
                return DecisionResult(
                    pending_approval_id=existing["pending_approval_id"],
                    decision_id=existing["decision_id"],
                    approval_request_id=existing["approval_request_id"],
                    request_id=existing["request_id"],
                    approve=bool(existing["approve"]),
                    duplicate=True,
                )

            if pending["status"] != "registered":
                raise ReplayConflict(
                    f"pending approval is {pending['status']}, not awaiting a decision"
                )
            request = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None or request["canonical_arguments"] != canonical:
                raise MismatchedApproval(
                    "approval arguments do not exactly match the reviewed change request"
                )
            if request["status"] != "open":
                raise ReplayConflict(
                    f"change request is already {request['status']}; a new decision is not allowed"
                )
            decision_expires = min(pending["expires_at"], now + DECISION_TTL_SECONDS)
            connection.execute(
                """
                INSERT INTO decisions (
                    decision_id, approval_request_id, pending_approval_id, request_id,
                    server_label, tool_name, canonical_arguments, approve, reason,
                    decided_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope["id"],
                    envelope["approval_request_id"],
                    envelope["pending_approval_id"],
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
            status = "approved" if envelope["approve"] else "rejected"
            connection.execute(
                "UPDATE pending_approvals SET status = ? WHERE pending_approval_id = ?",
                (status, envelope["pending_approval_id"]),
            )
            connection.execute(
                "UPDATE change_requests SET status = ? WHERE request_id = ?",
                (status, request_id),
            )
            return DecisionResult(
                pending_approval_id=envelope["pending_approval_id"],
                decision_id=envelope["id"],
                approval_request_id=envelope["approval_request_id"],
                request_id=request_id,
                approve=envelope["approve"],
                duplicate=False,
            )

    def schedule_change(self, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized, canonical = normalize_schedule_arguments(arguments)
        request_id = normalized["change_request_id"]
        pending_id = normalized["pending_approval_id"]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self.now()
            request = connection.execute(
                "SELECT * FROM change_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            pending = connection.execute(
                "SELECT * FROM pending_approvals WHERE pending_approval_id = ?",
                (pending_id,),
            ).fetchone()
            if (
                request is None
                or pending is None
                or pending["request_id"] != request_id
                or canonical != request["canonical_arguments"]
                or canonical != pending["canonical_arguments"]
            ):
                raise MismatchedApproval(
                    "schedule arguments do not exactly match the server-issued approval"
                )

            existing = connection.execute(
                "SELECT * FROM side_effects WHERE request_id = ?", (request_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["pending_approval_id"] != pending_id
                    or existing["canonical_arguments"] != canonical
                ):
                    raise ReplayConflict("an approval nonce cannot authorize another effect")
                return {
                    "status": "already_scheduled",
                    "change_request_id": request_id,
                    "decision_id": existing["decision_id"],
                    "side_effect_id": existing["side_effect_id"],
                    "idempotent_replay": True,
                }

            if pending["status"] != "approved":
                raise MissingApproval(
                    "no approved, registered server nonce authorizes this call"
                )
            decision = connection.execute(
                """
                SELECT * FROM decisions
                WHERE pending_approval_id = ? AND request_id = ?
                    AND canonical_arguments = ? AND approve = 1
                """,
                (pending_id, request_id, canonical),
            ).fetchone()
            if decision is None:
                raise MissingApproval(
                    "no operator decision authorizes this exact schedule_change call"
                )
            if now > decision["expires_at"]:
                raise StaleApproval("approval expired before schedule_change executed")
            if request["status"] != "approved":
                raise ReplayConflict(
                    f"change request is {request['status']}, not approved for execution"
                )

            digest = hashlib.sha256(
                f"{request_id}:{pending_id}:{decision['decision_id']}:{canonical}".encode()
            ).hexdigest()[:20]
            side_effect_id = f"effect-{digest}"
            connection.execute(
                """
                INSERT INTO side_effects (
                    side_effect_id, request_id, decision_id, pending_approval_id,
                    canonical_arguments, scheduled_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    side_effect_id,
                    request_id,
                    decision["decision_id"],
                    pending_id,
                    canonical,
                    now,
                ),
            )
            connection.execute(
                "UPDATE pending_approvals SET status = 'consumed' WHERE pending_approval_id = ?",
                (pending_id,),
            )
            connection.execute(
                "UPDATE change_requests SET status = 'scheduled' WHERE request_id = ?",
                (request_id,),
            )
            return {
                "status": "scheduled",
                "change_request_id": request_id,
                "pending_approval_id": pending_id,
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
            pending = connection.execute(
                """
                SELECT pending_approval_id, approval_request_id, status
                FROM pending_approvals WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            decisions = connection.execute(
                """
                SELECT decision_id, approval_request_id, pending_approval_id, approve,
                       tool_name, decided_at
                FROM decisions WHERE request_id = ? ORDER BY decided_at
                """,
                (request_id,),
            ).fetchall()
            effects = connection.execute(
                """
                SELECT side_effect_id, decision_id, pending_approval_id, scheduled_at
                FROM side_effects WHERE request_id = ? ORDER BY scheduled_at
                """,
                (request_id,),
            ).fetchall()
            return {
                "change_request_id": request_id,
                "status": request["status"],
                "pending_approval": {
                    "pending_approval_id": pending["pending_approval_id"],
                    "approval_request_id": pending["approval_request_id"],
                    "status": pending["status"],
                },
                "decisions": [
                    {
                        "decision_id": row["decision_id"],
                        "approval_request_id": row["approval_request_id"],
                        "pending_approval_id": row["pending_approval_id"],
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
                        "pending_approval_id": row["pending_approval_id"],
                        "scheduled_at": iso_time(row["scheduled_at"]),
                    }
                    for row in effects
                ],
            }
