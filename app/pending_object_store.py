from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.actor_context import actor_from_mapping
from app.config import get_settings


INIT_DDL_LOCK_KEY = 86420534
ACTIVE_STATUSES = {"active", "needs_clarification", "pending_confirmation"}
_INITIALIZED = False
_INIT_LOCK = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().postgres_dsn, row_factory=dict_row)


def _decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    for field in ("payload", "allowed_continuations", "source_observation_ids"):
        value = item.get(field)
        if isinstance(value, str):
            item[field] = json.loads(value or ("[]" if field != "payload" else "{}"))
        elif value is None:
            item[field] = [] if field != "payload" else {}
    item["salience"] = float(item.get("salience") or 0.0)
    return item


def init_pending_object_store() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        with _connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (INIT_DDL_LOCK_KEY,))
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_objects (
                    object_id TEXT PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    salience DOUBLE PRECISION NOT NULL DEFAULT 0,
                    allowed_continuations TEXT NOT NULL DEFAULT '[]',
                    source_observation_ids TEXT NOT NULL DEFAULT '[]',
                    payload TEXT NOT NULL DEFAULT '{}',
                    expires_at TEXT NOT NULL DEFAULT '',
                    consumed_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pending_objects_conversation
                    ON pending_objects(tenant_id, workspace_id, user_id, conversation_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_pending_objects_type_status
                    ON pending_objects(object_type, status, updated_at DESC);
                """
            )
            conn.commit()
        _INITIALIZED = True


def upsert_pending_object(
    *,
    object_type: str,
    session_id: str,
    conversation_id: str,
    payload: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
    object_id: str = "",
    status: str = "active",
    salience: float = 0.0,
    allowed_continuations: list[str] | tuple[str, ...] | None = None,
    source_observation_ids: list[str] | tuple[str, ...] | None = None,
    expires_at: str = "",
    supersede_same_type: bool = True,
) -> dict[str, Any]:
    init_pending_object_store()
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    normalized_type = str(object_type or "").strip()
    if not normalized_type:
        raise ValueError("object_type is required")
    oid = str(object_id or f"pending_{uuid.uuid4().hex[:16]}")
    now = _now()
    expiry = expires_at or _default_expiry()
    with _connect() as conn:
        if supersede_same_type:
            conn.execute(
                """
                UPDATE pending_objects
                SET status = 'superseded', updated_at = %s
                WHERE tenant_id = %s
                  AND workspace_id = %s
                  AND user_id = %s
                  AND conversation_id = %s
                  AND object_type = %s
                  AND object_id <> %s
                  AND status IN ('active', 'needs_clarification', 'pending_confirmation')
                """,
                (now, actor.tenant_id, actor.workspace_id, actor.user_id, conversation_id, normalized_type, oid),
            )
        conn.execute(
            """
            INSERT INTO pending_objects (
                object_id, object_type, tenant_id, user_id, workspace_id, session_id,
                conversation_id, status, salience, allowed_continuations,
                source_observation_ids, payload, expires_at, consumed_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s, %s)
            ON CONFLICT (object_id) DO UPDATE SET
                object_type = EXCLUDED.object_type,
                tenant_id = EXCLUDED.tenant_id,
                user_id = EXCLUDED.user_id,
                workspace_id = EXCLUDED.workspace_id,
                session_id = EXCLUDED.session_id,
                conversation_id = EXCLUDED.conversation_id,
                status = EXCLUDED.status,
                salience = EXCLUDED.salience,
                allowed_continuations = EXCLUDED.allowed_continuations,
                source_observation_ids = EXCLUDED.source_observation_ids,
                payload = EXCLUDED.payload,
                expires_at = EXCLUDED.expires_at,
                consumed_at = '',
                updated_at = EXCLUDED.updated_at
            """,
            (
                oid,
                normalized_type,
                actor.tenant_id,
                actor.user_id,
                actor.workspace_id,
                session_id,
                conversation_id,
                status,
                float(salience),
                json.dumps(list(allowed_continuations or []), ensure_ascii=False),
                json.dumps(list(source_observation_ids or []), ensure_ascii=False),
                json.dumps(dict(payload or {}), ensure_ascii=False),
                expiry,
                now,
                now,
            ),
        )
        conn.commit()
    return get_pending_object(oid, actor_context=actor.to_dict()) or {}


def get_pending_object(object_id: str, *, actor_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    init_pending_object_store()
    actor = actor_from_mapping(actor_context or {})
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM pending_objects
            WHERE object_id = %s
              AND tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
            """,
            (object_id, actor.tenant_id, actor.workspace_id, actor.user_id),
        ).fetchone()
    return _decode_row(row)


def get_latest_active_pending_object(
    *,
    conversation_id: str,
    object_type: str = "",
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    init_pending_object_store()
    actor = actor_from_mapping(actor_context or {}, conversation_id=conversation_id)
    clauses = [
        "tenant_id = %s",
        "workspace_id = %s",
        "user_id = %s",
        "conversation_id = %s",
        "status IN ('active', 'needs_clarification', 'pending_confirmation')",
        "(expires_at = '' OR expires_at > %s)",
    ]
    params: list[Any] = [actor.tenant_id, actor.workspace_id, actor.user_id, conversation_id, _now()]
    if object_type:
        clauses.append("object_type = %s")
        params.append(object_type)
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM pending_objects
            WHERE {' AND '.join(clauses)}
            ORDER BY salience DESC, updated_at DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    return _decode_row(row)


def list_active_pending_objects(
    *,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
    object_types: list[str] | tuple[str, ...] | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Return durable continuation objects for one actor-scoped conversation."""

    init_pending_object_store()
    actor = actor_from_mapping(actor_context or {}, conversation_id=conversation_id)
    clauses = [
        "tenant_id = %s",
        "workspace_id = %s",
        "user_id = %s",
        "conversation_id = %s",
        "status IN ('active', 'needs_clarification', 'pending_confirmation')",
        "(expires_at = '' OR expires_at > %s)",
    ]
    params: list[Any] = [actor.tenant_id, actor.workspace_id, actor.user_id, conversation_id, _now()]
    normalized_types = [str(item).strip() for item in list(object_types or []) if str(item).strip()]
    if normalized_types:
        placeholders = ", ".join(["%s"] * len(normalized_types))
        clauses.append(f"object_type IN ({placeholders})")
        params.extend(normalized_types)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM pending_objects
            WHERE {' AND '.join(clauses)}
            ORDER BY salience DESC, updated_at DESC
            LIMIT %s
            """,
            tuple([*params, max(1, min(int(limit), 200))]),
        ).fetchall()
    return [item for item in (_decode_row(row) for row in rows) if item]


def update_pending_object(
    object_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    status: str | None = None,
    allowed_continuations: list[str] | tuple[str, ...] | None = None,
    source_observation_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Patch registry metadata without widening the actor scope."""

    init_pending_object_store()
    actor = actor_from_mapping(actor_context or {})
    assignments = ["updated_at = %s"]
    params: list[Any] = [_now()]
    if payload is not None:
        assignments.append("payload = %s")
        params.append(json.dumps(dict(payload), ensure_ascii=False))
    if status is not None:
        assignments.append("status = %s")
        params.append(str(status))
    if allowed_continuations is not None:
        assignments.append("allowed_continuations = %s")
        params.append(json.dumps(list(allowed_continuations), ensure_ascii=False))
    if source_observation_ids is not None:
        assignments.append("source_observation_ids = %s")
        params.append(json.dumps(list(source_observation_ids), ensure_ascii=False))
    params.extend([object_id, actor.tenant_id, actor.workspace_id, actor.user_id])
    with _connect() as conn:
        conn.execute(
            f"""
            UPDATE pending_objects
            SET {', '.join(assignments)}
            WHERE object_id = %s
              AND tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
            """,
            tuple(params),
        )
        conn.commit()
    return get_pending_object(object_id, actor_context=actor.to_dict())


def consume_pending_object(
    object_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
    status: str = "consumed",
) -> dict[str, Any] | None:
    init_pending_object_store()
    actor = actor_from_mapping(actor_context or {})
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE pending_objects
            SET status = %s, consumed_at = %s, updated_at = %s
            WHERE object_id = %s
              AND tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
              AND status IN ('active', 'needs_clarification', 'pending_confirmation')
            """,
            (status, now, now, object_id, actor.tenant_id, actor.workspace_id, actor.user_id),
        )
        conn.commit()
    return get_pending_object(object_id, actor_context=actor.to_dict())
