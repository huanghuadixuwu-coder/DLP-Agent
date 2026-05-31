from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.actor_context import actor_from_mapping
from app.config import get_settings


INIT_DDL_LOCK_KEY = 86420533
ACTIVE_DRAFT_STATUSES = {"draft", "draft_ready", "needs_clarification", "pending_confirmation", "patch"}
_INITIALIZED = False
_INIT_LOCK = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().postgres_dsn, row_factory=dict_row)


def _decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    value = item.get("mail_plan")
    if isinstance(value, str):
        item["mail_plan"] = json.loads(value or "{}")
    elif value is None:
        item["mail_plan"] = {}
    item["version"] = int(item.get("version") or 1)
    return item


def init_mail_draft_store() -> None:
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
                CREATE TABLE IF NOT EXISTS mail_drafts (
                    draft_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    version INTEGER NOT NULL DEFAULT 1,
                    mail_plan TEXT NOT NULL DEFAULT '{}',
                    confirmation_id TEXT NOT NULL DEFAULT '',
                    confirmation_key TEXT NOT NULL DEFAULT '',
                    dlp_task_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_mail_drafts_conversation
                    ON mail_drafts(tenant_id, workspace_id, user_id, conversation_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_mail_drafts_status
                    ON mail_drafts(status, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_drafts_confirmation_key
                    ON mail_drafts(confirmation_key)
                    WHERE confirmation_key <> '';
                """
            )
            conn.commit()
        _INITIALIZED = True


def build_mail_confirmation_key(mail_plan: dict[str, Any], actor_context: dict[str, Any]) -> str:
    actor = actor_from_mapping(actor_context or {})
    binding = {
        "tenant_id": actor.tenant_id,
        "workspace_id": actor.workspace_id,
        "user_id": actor.user_id,
        "draft_id": str(mail_plan.get("draft_id") or ""),
        "mail_action_type": str(mail_plan.get("mail_action_type") or ""),
        "recipients": sorted(str(item).strip().lower() for item in list(mail_plan.get("resolved_recipients") or []) if str(item).strip()),
        "subject": str(mail_plan.get("resolved_subject") or ""),
        "body": str(mail_plan.get("resolved_body") or ""),
        "attachments": list(mail_plan.get("resolved_attachments") or []),
        "source_policy": dict(mail_plan.get("source_policy") or {}),
    }
    digest = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"mail-confirm:{digest}"


def upsert_mail_draft(
    *,
    session_id: str,
    conversation_id: str,
    mail_plan: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    init_mail_draft_store()
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    plan = dict(mail_plan or {})
    draft_id = str(plan.get("draft_id") or uuid.uuid4().hex)
    plan["draft_id"] = draft_id
    plan["conversation_id"] = conversation_id
    draft_status = str(status or plan.get("status") or "draft")
    plan["status"] = draft_status
    confirmation_key = build_mail_confirmation_key(plan, actor.to_dict()) if draft_status == "pending_confirmation" else ""
    confirmation_id = str(plan.get("confirmation_id") or "")
    if draft_status == "pending_confirmation":
        confirmation_id = confirmation_id or f"confirmation_{uuid.uuid4().hex[:16]}"
        plan["confirmation_id"] = confirmation_id
        plan["idempotency_key"] = confirmation_key
    else:
        plan.pop("confirmation_id", None)
        plan.pop("idempotency_key", None)
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mail_drafts
            SET status = 'superseded', updated_at = %s
            WHERE tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
              AND conversation_id = %s
              AND draft_id <> %s
              AND status IN ('draft', 'draft_ready', 'needs_clarification', 'pending_confirmation', 'patch')
            """,
            (now, actor.tenant_id, actor.workspace_id, actor.user_id, conversation_id, draft_id),
        )
        conn.execute(
            """
            INSERT INTO mail_drafts (
                draft_id, tenant_id, user_id, workspace_id, session_id, conversation_id,
                status, version, mail_plan, confirmation_id, confirmation_key, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s)
            ON CONFLICT (draft_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                user_id = EXCLUDED.user_id,
                workspace_id = EXCLUDED.workspace_id,
                session_id = EXCLUDED.session_id,
                conversation_id = EXCLUDED.conversation_id,
                status = EXCLUDED.status,
                version = mail_drafts.version + 1,
                mail_plan = EXCLUDED.mail_plan,
                confirmation_id = EXCLUDED.confirmation_id,
                confirmation_key = EXCLUDED.confirmation_key,
                dlp_task_id = CASE
                    WHEN mail_drafts.confirmation_key = EXCLUDED.confirmation_key THEN mail_drafts.dlp_task_id
                    ELSE ''
                END,
                updated_at = EXCLUDED.updated_at
            """,
            (
                draft_id,
                actor.tenant_id,
                actor.user_id,
                actor.workspace_id,
                session_id,
                conversation_id,
                draft_status,
                json.dumps(plan, ensure_ascii=False),
                confirmation_id,
                confirmation_key,
                now,
                now,
            ),
        )
        conn.commit()
    return get_mail_draft(draft_id, actor_context=actor.to_dict()) or {}


def get_mail_draft(draft_id: str, *, actor_context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    init_mail_draft_store()
    actor = actor_from_mapping(actor_context or {})
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM mail_drafts
            WHERE draft_id = %s
              AND tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
            """,
            (draft_id, actor.tenant_id, actor.workspace_id, actor.user_id),
        ).fetchone()
    return _decode_row(row)


def get_latest_active_mail_draft(
    conversation_id: str,
    *,
    actor_context: dict[str, Any] | None = None,
    pending_confirmation_only: bool = False,
) -> dict[str, Any] | None:
    init_mail_draft_store()
    actor = actor_from_mapping(actor_context or {}, conversation_id=conversation_id)
    statuses = ["pending_confirmation", "queued_dlp"] if pending_confirmation_only else sorted(ACTIVE_DRAFT_STATUSES)
    placeholders = ", ".join(["%s"] * len(statuses))
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM mail_drafts
            WHERE tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
              AND conversation_id = %s
              AND status IN ({placeholders})
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            [actor.tenant_id, actor.workspace_id, actor.user_id, conversation_id, *statuses],
        ).fetchone()
    return _decode_row(row)


def bind_mail_draft_task(
    draft_id: str,
    *,
    actor_context: dict[str, Any],
    confirmation_key: str,
    task_id: str,
) -> dict[str, Any] | None:
    init_mail_draft_store()
    actor = actor_from_mapping(actor_context or {})
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mail_drafts
            SET status = 'queued_dlp', dlp_task_id = %s, updated_at = %s
            WHERE draft_id = %s
              AND tenant_id = %s
              AND workspace_id = %s
              AND user_id = %s
              AND confirmation_key = %s
              AND status IN ('pending_confirmation', 'queued_dlp')
            """,
            (task_id, now, draft_id, actor.tenant_id, actor.workspace_id, actor.user_id, confirmation_key),
        )
        conn.commit()
    return get_mail_draft(draft_id, actor_context=actor.to_dict())


def build_persisted_confirmation_payload(draft: dict[str, Any]) -> dict[str, Any]:
    plan = dict(draft.get("mail_plan") or {})
    plan["draft_id"] = str(draft.get("draft_id") or plan.get("draft_id") or "")
    plan["status"] = str(draft.get("status") or plan.get("status") or "")
    plan["draft_version"] = int(draft.get("version") or 1)
    plan["confirmation_id"] = str(draft.get("confirmation_id") or plan.get("confirmation_id") or "")
    plan["idempotency_key"] = str(draft.get("confirmation_key") or plan.get("idempotency_key") or "")
    return {
        "action_name": "send_mail_plan",
        "title": "请确认邮件发送计划",
        "message": "",
        "mail_plan": plan,
        "draft_id": plan["draft_id"],
        "confirmation_id": plan["confirmation_id"],
        "idempotency_key": plan["idempotency_key"],
        "persistence_source": "mail_drafts",
    }
