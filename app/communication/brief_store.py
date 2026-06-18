from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.actor_context import ActorContext, actor_from_mapping
from app.communication.types import CommunicationBrief, CommunicationThreadRef
from app.config import get_settings


INIT_DDL_LOCK_KEY = 86420536
_INITIALIZED = False
_INIT_LOCK = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().postgres_dsn, row_factory=dict_row)


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _actor_params(actor: ActorContext) -> tuple[str, str, str]:
    return actor.tenant_id, actor.workspace_id, actor.user_id


def _decode_json(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value or json.dumps(default))
        except json.JSONDecodeError:
            return default
    return default if value is None else value


def _decode_json_dict(value: Any) -> dict[str, Any]:
    decoded = _decode_json(value, {})
    return dict(decoded) if isinstance(decoded, dict) else {}


def _decode_json_list(value: Any) -> list[Any]:
    decoded = _decode_json(value, [])
    return list(decoded) if isinstance(decoded, list) else []


def _brief_payload(brief: CommunicationBrief | dict[str, Any]) -> dict[str, Any]:
    if isinstance(brief, CommunicationBrief):
        return asdict(brief)
    return dict(brief or {})


def _brief_from_payload(payload: dict[str, Any]) -> CommunicationBrief:
    data = dict(payload or {})
    thread_ref = data.get("thread_ref")
    if isinstance(thread_ref, dict):
        data["thread_ref"] = CommunicationThreadRef(**thread_ref)
    elif not isinstance(thread_ref, CommunicationThreadRef):
        data["thread_ref"] = CommunicationThreadRef()
    allowed = set(CommunicationBrief.__dataclass_fields__)
    return CommunicationBrief(**{key: value for key, value in data.items() if key in allowed})


def _decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["version"] = int(item.get("version") or 1)
    item["brief"] = _decode_json_dict(item.pop("brief_json", "{}"))
    item["actor_context"] = _decode_json_dict(item.pop("actor_context_json", "{}"))
    item["grounding_refs"] = _decode_json_list(item.pop("grounding_refs_json", "[]"))
    item["source_observation_ids"] = _decode_json_list(item.pop("source_observation_ids_json", "[]"))
    return item


def init_communication_brief_store() -> None:
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
                CREATE TABLE IF NOT EXISTS communication_briefs (
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    brief_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    refresh_reason TEXT NOT NULL DEFAULT '',
                    persistence_source TEXT NOT NULL DEFAULT 'communication_briefs',
                    brief_json TEXT NOT NULL DEFAULT '{}',
                    grounding_refs_json TEXT NOT NULL DEFAULT '[]',
                    source_observation_ids_json TEXT NOT NULL DEFAULT '[]',
                    actor_context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    refreshed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, workspace_id, user_id, brief_id)
                );

                CREATE INDEX IF NOT EXISTS idx_communication_briefs_thread_latest
                    ON communication_briefs(tenant_id, workspace_id, user_id, thread_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_communication_briefs_conversation_latest
                    ON communication_briefs(tenant_id, workspace_id, user_id, conversation_id, updated_at DESC);
                """
            )
            conn.commit()
        _INITIALIZED = True


def upsert_communication_brief(
    brief: CommunicationBrief | dict[str, Any],
    *,
    actor_context: dict[str, Any] | None = None,
    refresh_reason: str = "",
    persistence_source: str = "communication_briefs",
) -> dict[str, Any]:
    init_communication_brief_store()
    payload = _brief_payload(brief)
    brief_obj = _brief_from_payload(payload)
    actor = actor_from_mapping(actor_context or brief_obj.actor_context or {})
    thread_id = _compact(brief_obj.thread_ref.thread_id)
    if not thread_id:
        raise ValueError("thread_id is required for communication brief persistence")
    now = _now()
    payload["actor_context"] = actor.to_dict()
    payload["thread_ref"] = brief_obj.thread_ref.to_dict()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO communication_briefs (
                tenant_id, workspace_id, user_id, thread_id, brief_id, conversation_id,
                version, refresh_reason, persistence_source, brief_json, grounding_refs_json,
                source_observation_ids_json, actor_context_json, created_at, refreshed_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, workspace_id, user_id, brief_id) DO UPDATE SET
                thread_id = EXCLUDED.thread_id,
                conversation_id = EXCLUDED.conversation_id,
                version = communication_briefs.version + 1,
                refresh_reason = EXCLUDED.refresh_reason,
                persistence_source = EXCLUDED.persistence_source,
                brief_json = EXCLUDED.brief_json,
                grounding_refs_json = EXCLUDED.grounding_refs_json,
                source_observation_ids_json = EXCLUDED.source_observation_ids_json,
                actor_context_json = EXCLUDED.actor_context_json,
                refreshed_at = EXCLUDED.refreshed_at,
                updated_at = EXCLUDED.updated_at
            """,
            (
                actor.tenant_id,
                actor.workspace_id,
                actor.user_id,
                thread_id,
                brief_obj.brief_id,
                brief_obj.conversation_id,
                _compact(refresh_reason),
                _compact(persistence_source) or "communication_briefs",
                json.dumps(payload, ensure_ascii=False),
                json.dumps(brief_obj.grounding_refs, ensure_ascii=False),
                json.dumps(brief_obj.source_observation_ids, ensure_ascii=False),
                json.dumps(actor.to_dict(), ensure_ascii=False),
                brief_obj.created_at or now,
                now,
                now,
            ),
        )
        conn.commit()
    return get_communication_brief(brief_obj.brief_id, actor_context=actor.to_dict()) or {}


def get_communication_brief(
    brief_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    init_communication_brief_store()
    brief_key = _compact(brief_id)
    if not brief_key:
        return None
    actor = actor_from_mapping(actor_context or {})
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM communication_briefs
            WHERE tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
              AND brief_id = %s
            """,
            (*_actor_params(actor), brief_key),
        ).fetchone()
    return _decode_row(row)


def get_latest_brief_for_thread(
    thread_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    init_communication_brief_store()
    thread_key = _compact(thread_id)
    if not thread_key:
        return None
    actor = actor_from_mapping(actor_context or {})
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM communication_briefs
            WHERE tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
              AND thread_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (*_actor_params(actor), thread_key),
        ).fetchone()
    return _decode_row(row)


def refresh_brief_for_thread(
    thread_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
    employee_goal: str,
    conversation_id: str = "",
    grounding_refs: list[dict[str, Any]] | None = None,
    source_observation_ids: list[str] | None = None,
    refresh_reason: str = "manual_refresh",
) -> dict[str, Any]:
    from app.communication.brief_service import assemble_communication_brief

    init_communication_brief_store()
    actor = actor_from_mapping(actor_context or {}, conversation_id=conversation_id)
    latest = get_latest_brief_for_thread(thread_id, actor_context=actor.to_dict())
    latest_brief = dict((latest or {}).get("brief") or {})
    resolved_grounding_refs = (
        list(latest_brief.get("grounding_refs") or [])
        if grounding_refs is None
        else list(grounding_refs)
    )
    resolved_source_observation_ids = (
        list(latest_brief.get("source_observation_ids") or [])
        if source_observation_ids is None
        else list(source_observation_ids)
    )
    brief = assemble_communication_brief(
        brief_id=str(latest_brief.get("brief_id") or ""),
        employee_goal=employee_goal,
        conversation_id=conversation_id or str(latest_brief.get("conversation_id") or ""),
        thread_id=thread_id,
        grounding_refs=resolved_grounding_refs,
        actor_context=actor.to_dict(),
        source_observation_ids=resolved_source_observation_ids,
    )
    return upsert_communication_brief(
        brief,
        actor_context=actor.to_dict(),
        refresh_reason=refresh_reason,
    )
