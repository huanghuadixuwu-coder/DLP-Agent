from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.actor_context import ActorContext, actor_from_mapping
from app.communication.types import CommunicationThreadRef
from app.config import get_settings
from app.inbound_mail_store import list_recent_inbound_threads, list_thread_messages


INIT_DDL_LOCK_KEY = 86420535
_INITIALIZED = False
_INIT_LOCK = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().postgres_dsn, row_factory=dict_row)


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _decode_json(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value or json.dumps(default))
        except json.JSONDecodeError:
            return default
    return default if value is None else value


def _split_addresses(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = value
    else:
        text = _compact(value)
        for separator in (";", "|"):
            text = text.replace(separator, ",")
        candidates = text.split(",")
    return [_compact(item) for item in candidates if _compact(item)]


def _message_timestamp(message: dict[str, Any]) -> str:
    return _compact(message.get("received_at") or message.get("created_at") or message.get("updated_at"))


def _message_summary(message: dict[str, Any]) -> str:
    return _compact(message.get("summary") or message.get("snippet"))


def _risk_rank(value: str) -> int:
    return {"": 0, "low": 1, "medium": 2, "high": 3}.get(str(value or "").lower(), 0)


def _highest_risk(messages: list[dict[str, Any]], fallback: str = "") -> str:
    risk = _compact(fallback).lower()
    for message in messages:
        candidate = _compact(message.get("risk_hint")).lower()
        if _risk_rank(candidate) > _risk_rank(risk):
            risk = candidate
    return risk


def _participants_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    participants: list[str] = []
    seen: set[str] = set()
    for message in messages:
        for address in [message.get("sender"), *_split_addresses(message.get("recipients"))]:
            normalized = _compact(address)
            key = normalized.lower()
            if normalized and key not in seen:
                participants.append(normalized)
                seen.add(key)
    return participants


def _source_message_ids(messages: list[dict[str, Any]], fallback: Any = None) -> list[str]:
    if isinstance(fallback, str):
        values = [fallback]
    else:
        values = list(fallback or [])
    if not values:
        values = [message.get("provider_message_id") or message.get("message_id") for message in messages]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _compact(value)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _latest_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    if not messages:
        return {}
    return sorted(messages, key=_message_timestamp)[-1]


def _decode_thread(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["participants"] = list(_decode_json(item.pop("participants_json", "[]"), []))
    item["source_message_ids"] = list(_decode_json(item.pop("source_message_ids_json", "[]"), []))
    item["actor_context"] = dict(_decode_json(item.pop("actor_context_json", "{}"), {}))
    return item


def _actor_params(actor: ActorContext) -> tuple[str, str, str]:
    return actor.tenant_id, actor.workspace_id, actor.user_id


def init_communication_thread_store() -> None:
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
                CREATE TABLE IF NOT EXISTS communication_threads (
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'mail',
                    subject TEXT NOT NULL DEFAULT '',
                    participants_json TEXT NOT NULL DEFAULT '[]',
                    last_message_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
                    latest_summary TEXT NOT NULL DEFAULT '',
                    risk_hint TEXT NOT NULL DEFAULT '',
                    actor_context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, workspace_id, user_id, thread_id)
                );

                CREATE TABLE IF NOT EXISTS communication_active_threads (
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    active_thread_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, workspace_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_communication_threads_actor_updated
                    ON communication_threads(tenant_id, workspace_id, user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_communication_threads_actor_last_message
                    ON communication_threads(tenant_id, workspace_id, user_id, last_message_at DESC);
                """
            )
            conn.commit()
        _INITIALIZED = True


def upsert_thread_projection(
    thread: dict[str, Any] | CommunicationThreadRef,
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_communication_thread_store()
    data = thread.to_dict() if isinstance(thread, CommunicationThreadRef) else dict(thread or {})
    messages = [dict(item or {}) for item in list(data.get("messages") or []) if isinstance(item, dict)]
    actor = actor_from_mapping(actor_context or data.get("actor_context") or {})
    latest = _latest_message(messages)
    first = messages[0] if messages else {}
    thread_id = _compact(
        first.get("thread_id")
        or data.get("thread_id")
        or data.get("provider_thread_id")
        or first.get("provider_thread_id")
        or first.get("provider_message_id")
        or first.get("message_id")
    )
    if not thread_id:
        raise ValueError("thread_id is required for communication thread projection")

    source = _compact(data.get("source") or "mail")
    subject = _compact(data.get("subject") or first.get("subject") or latest.get("subject"))
    participants = _split_addresses(data.get("participants")) or _participants_from_messages(messages)
    last_message_at = _compact(data.get("last_message_at") or data.get("latest_received_at") or _message_timestamp(latest))
    status = _compact(data.get("status") or "open")
    source_message_ids = _source_message_ids(messages, data.get("source_message_ids"))
    latest_summary = _compact(data.get("latest_summary") or _message_summary(latest))
    risk_hint = _highest_risk(messages, _compact(data.get("risk_hint")))
    now = _now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO communication_threads (
                tenant_id, workspace_id, user_id, thread_id, source, subject,
                participants_json, last_message_at, status, source_message_ids_json,
                latest_summary, risk_hint, actor_context_json, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, workspace_id, user_id, thread_id) DO UPDATE SET
                source = EXCLUDED.source,
                subject = EXCLUDED.subject,
                participants_json = EXCLUDED.participants_json,
                last_message_at = EXCLUDED.last_message_at,
                status = EXCLUDED.status,
                source_message_ids_json = EXCLUDED.source_message_ids_json,
                latest_summary = EXCLUDED.latest_summary,
                risk_hint = EXCLUDED.risk_hint,
                actor_context_json = EXCLUDED.actor_context_json,
                updated_at = EXCLUDED.updated_at
            """,
            (
                actor.tenant_id,
                actor.workspace_id,
                actor.user_id,
                thread_id,
                source,
                subject,
                json.dumps(participants, ensure_ascii=False),
                last_message_at,
                status,
                json.dumps(source_message_ids, ensure_ascii=False),
                latest_summary,
                risk_hint,
                json.dumps(actor.to_dict(), ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    return get_communication_thread(thread_id, actor_context=actor.to_dict(), refresh=False) or {}


def _refresh_recent_thread_projections(
    *,
    actor: ActorContext,
    limit: int,
    messages_per_thread: int,
) -> None:
    for thread in list_recent_inbound_threads(
        limit=max(1, min(int(limit or 20), 100)),
        messages_per_thread=max(1, min(int(messages_per_thread or 5), 20)),
        actor_context=actor.to_dict(),
    ):
        upsert_thread_projection(thread, actor_context=actor.to_dict())


def _refresh_thread_projection(thread_id: str, *, actor: ActorContext, limit: int = 20) -> str:
    messages = list_thread_messages(thread_id, limit=limit, actor_context=actor.to_dict())
    if messages:
        first = messages[0]
        canonical_thread_id = _compact(first.get("thread_id") or thread_id)
        upsert_thread_projection({"thread_id": canonical_thread_id, "messages": messages}, actor_context=actor.to_dict())
        alias_thread_id = _compact(thread_id)
        if alias_thread_id and alias_thread_id != canonical_thread_id:
            with _connect() as conn:
                conn.execute(
                    """
                    DELETE FROM communication_threads
                    WHERE tenant_id = %s
                      AND workspace_id = %s
                      AND user_id = %s
                      AND thread_id = %s
                    """,
                    (*_actor_params(actor), alias_thread_id),
                )
                conn.commit()
        return canonical_thread_id
    return ""


def list_communication_threads(
    *,
    actor_context: dict[str, Any] | None = None,
    limit: int = 20,
    refresh: bool = True,
    messages_per_thread: int = 5,
) -> list[dict[str, Any]]:
    init_communication_thread_store()
    actor = actor_from_mapping(actor_context or {})
    bounded_limit = max(1, min(int(limit or 20), 100))
    if refresh:
        _refresh_recent_thread_projections(actor=actor, limit=bounded_limit, messages_per_thread=messages_per_thread)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM communication_threads
            WHERE tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
            ORDER BY
                CASE WHEN last_message_at <> '' THEN last_message_at ELSE updated_at END DESC,
                updated_at DESC
            LIMIT %s
            """,
            (*_actor_params(actor), bounded_limit),
        ).fetchall()
    return [item for row in rows if (item := _decode_thread(row))]


def get_communication_thread(
    thread_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
    refresh: bool = True,
) -> dict[str, Any] | None:
    init_communication_thread_store()
    thread_key = _compact(thread_id)
    if not thread_key:
        return None
    actor = actor_from_mapping(actor_context or {})
    lookup_key = thread_key
    if refresh:
        lookup_key = _refresh_thread_projection(thread_key, actor=actor) or thread_key
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM communication_threads
            WHERE tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
              AND thread_id = %s
            """,
            (*_actor_params(actor), lookup_key),
        ).fetchone()
    return _decode_thread(row)


def set_active_communication_thread(
    thread_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    init_communication_thread_store()
    thread = get_communication_thread(thread_id, actor_context=actor_context, refresh=True)
    if not thread:
        return None
    actor = actor_from_mapping(actor_context or {})
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO communication_active_threads (
                tenant_id, workspace_id, user_id, active_thread_id, updated_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workspace_id, user_id) DO UPDATE SET
                active_thread_id = EXCLUDED.active_thread_id,
                updated_at = EXCLUDED.updated_at
            """,
            (*_actor_params(actor), thread["thread_id"], now),
        )
        conn.commit()
    return get_active_communication_thread(actor_context=actor.to_dict())


def get_active_communication_thread(
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    init_communication_thread_store()
    actor = actor_from_mapping(actor_context or {})
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT threads.*
            FROM communication_active_threads active
            JOIN communication_threads threads
              ON threads.tenant_id = active.tenant_id
             AND threads.workspace_id = active.workspace_id
             AND threads.user_id = active.user_id
             AND threads.thread_id = active.active_thread_id
            WHERE active.tenant_id = %s
              AND active.workspace_id = %s
              AND active.user_id = %s
            """,
            _actor_params(actor),
        ).fetchone()
    return _decode_thread(row)
