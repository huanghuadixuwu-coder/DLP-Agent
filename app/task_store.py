from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


TERMINAL_TASK_STATUSES = {"sent", "rejected", "send_failed", "failed", "completed", "dead_letter"}
RECOVERABLE_TASK_STATUSES = {"needs_clarification", "input_invalid", "delivery_deferred", "dead_letter"}
JSON_TASK_FIELDS = {
    "risk_reasons",
    "redactions",
    "retrieved_evidence",
    "fault_injection",
    "expected_outcome",
    "missing_fields",
    "domain_payload",
    "domain_result",
}
LIST_JSON_FIELDS = {"risk_reasons", "redactions", "retrieved_evidence", "missing_fields"}
BOOLEAN_TASK_FIELDS = {"approval_required", "manual_handover_required", "lab_run"}
TASK_COLUMN_MIGRATIONS = [
    ("task_type", "TEXT NOT NULL DEFAULT 'dlp_outbound'"),
    ("user_id", "TEXT NOT NULL DEFAULT ''"),
    ("workspace_id", "TEXT NOT NULL DEFAULT ''"),
    ("domain_action", "TEXT NOT NULL DEFAULT ''"),
    ("domain_payload", "TEXT NOT NULL DEFAULT '{}'"),
    ("domain_result", "TEXT NOT NULL DEFAULT '{}'"),
    ("mail_draft_id", "TEXT NOT NULL DEFAULT ''"),
    ("idempotency_key", "TEXT NOT NULL DEFAULT ''"),
    ("request_message", "TEXT NOT NULL DEFAULT ''"),
    ("delivery_subject", "TEXT NOT NULL DEFAULT ''"),
    ("delivery_body", "TEXT NOT NULL DEFAULT ''"),
    ("delivery_plan_kind", "TEXT NOT NULL DEFAULT ''"),
    ("resolved_source_kind", "TEXT NOT NULL DEFAULT ''"),
    ("attachment_strategy", "TEXT NOT NULL DEFAULT 'none'"),
    ("attachment_content", "TEXT NOT NULL DEFAULT ''"),
    ("attachment_filename", "TEXT NOT NULL DEFAULT ''"),
    ("attachment_content_type", "TEXT NOT NULL DEFAULT ''"),
    ("attachment_blob_id", "TEXT NOT NULL DEFAULT ''"),
    ("degradation_mode", "TEXT NOT NULL DEFAULT ''"),
    ("fallback_reason", "TEXT NOT NULL DEFAULT ''"),
    ("manual_handover_required", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("next_recommended_action", "TEXT NOT NULL DEFAULT ''"),
    ("last_error_category", "TEXT NOT NULL DEFAULT ''"),
    ("entry_issue_type", "TEXT NOT NULL DEFAULT ''"),
    ("clarification_question", "TEXT NOT NULL DEFAULT ''"),
    ("retrieval_status", "TEXT NOT NULL DEFAULT ''"),
    ("summary_status", "TEXT NOT NULL DEFAULT ''"),
    ("lab_run", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("scenario_id", "TEXT NOT NULL DEFAULT ''"),
    ("scenario_name", "TEXT NOT NULL DEFAULT ''"),
    ("source_parse_status", "TEXT NOT NULL DEFAULT 'not_provided'"),
    ("source_parse_error", "TEXT NOT NULL DEFAULT ''"),
    ("missing_fields", "TEXT NOT NULL DEFAULT '[]'"),
    ("retrieved_evidence", "TEXT NOT NULL DEFAULT '[]'"),
    ("fault_injection", "TEXT NOT NULL DEFAULT '{}'"),
    ("expected_outcome", "TEXT NOT NULL DEFAULT '{}'"),
]
INIT_DDL_LOCK_KEY = 86420531
_TASK_STORE_INITIALIZED = False
_INIT_LOCK = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _connect() -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(settings.postgres_dsn, row_factory=dict_row)


def _decode_json(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value or json.dumps(default))
        except json.JSONDecodeError:
            return default
    return default if value is None else value


def _decode_task(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    for key in BOOLEAN_TASK_FIELDS:
        item[key] = bool(item.get(key))
    for key in JSON_TASK_FIELDS:
        value = item.get(key)
        if isinstance(value, str):
            default_value = "[]" if key in LIST_JSON_FIELDS else "{}"
            item[key] = json.loads(value or default_value)
        elif value is None:
            item[key] = [] if key in LIST_JSON_FIELDS else {}
    return item


def _decode_event(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    value = item.get("details_json")
    if isinstance(value, str):
        item["details_json"] = json.loads(value or "{}")
    elif value is None:
        item["details_json"] = {}
    return item


def _decode_dlq(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    for key in ("actor_context", "payload_snapshot", "replay_result"):
        item[key] = _decode_json(item.get(key), {})
    item["safe_replay_allowed"] = bool(item.get("safe_replay_allowed"))
    item["replay_count"] = int(item.get("replay_count") or 0)
    item["attempt_count"] = int(item.get("attempt_count") or 0)
    return item


def init_task_store() -> None:
    global _TASK_STORE_INITIALIZED
    if _TASK_STORE_INITIALIZED:
        return

    with _INIT_LOCK:
        if _TASK_STORE_INITIALIZED:
            return

        with _connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (INIT_DDL_LOCK_KEY,))
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dlp_tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL DEFAULT 'dlp_outbound',
                    tenant_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    workspace_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    domain_action TEXT NOT NULL DEFAULT '',
                    domain_payload TEXT NOT NULL DEFAULT '{}',
                    domain_result TEXT NOT NULL DEFAULT '{}',
                    mail_draft_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    risk_level TEXT NOT NULL DEFAULT '',
                    approval_required BOOLEAN NOT NULL DEFAULT FALSE,
                    destination_email TEXT NOT NULL DEFAULT '',
                    requested_action TEXT NOT NULL DEFAULT 'summarize_and_send',
                    message_raw TEXT NOT NULL,
                    request_message TEXT NOT NULL DEFAULT '',
                    delivery_subject TEXT NOT NULL DEFAULT '',
                    delivery_body TEXT NOT NULL DEFAULT '',
                    delivery_plan_kind TEXT NOT NULL DEFAULT '',
                    resolved_source_kind TEXT NOT NULL DEFAULT '',
                    attachment_strategy TEXT NOT NULL DEFAULT 'none',
                    attachment_content TEXT NOT NULL DEFAULT '',
                    attachment_filename TEXT NOT NULL DEFAULT '',
                    attachment_content_type TEXT NOT NULL DEFAULT '',
                    attachment_blob_id TEXT NOT NULL DEFAULT '',
                    message_redacted TEXT NOT NULL DEFAULT '',
                    source_filename TEXT NOT NULL DEFAULT '',
                    source_content_type TEXT NOT NULL DEFAULT '',
                    source_parse_status TEXT NOT NULL DEFAULT 'not_provided',
                    source_parse_error TEXT NOT NULL DEFAULT '',
                    draft_summary TEXT NOT NULL DEFAULT '',
                    risk_reasons TEXT NOT NULL DEFAULT '[]',
                    redactions TEXT NOT NULL DEFAULT '[]',
                    retrieved_evidence TEXT NOT NULL DEFAULT '[]',
                    delivery_status TEXT NOT NULL DEFAULT 'not_sent',
                    delivery_result TEXT NOT NULL DEFAULT '',
                    delivery_error TEXT NOT NULL DEFAULT '',
                    smtp_provider TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL DEFAULT '',
                    final_result TEXT NOT NULL DEFAULT '',
                    degradation_mode TEXT NOT NULL DEFAULT '',
                    fallback_reason TEXT NOT NULL DEFAULT '',
                    manual_handover_required BOOLEAN NOT NULL DEFAULT FALSE,
                    next_recommended_action TEXT NOT NULL DEFAULT '',
                    last_error_category TEXT NOT NULL DEFAULT '',
                    entry_issue_type TEXT NOT NULL DEFAULT '',
                    missing_fields TEXT NOT NULL DEFAULT '[]',
                    clarification_question TEXT NOT NULL DEFAULT '',
                    retrieval_status TEXT NOT NULL DEFAULT '',
                    summary_status TEXT NOT NULL DEFAULT '',
                    lab_run BOOLEAN NOT NULL DEFAULT FALSE,
                    scenario_id TEXT NOT NULL DEFAULT '',
                    scenario_name TEXT NOT NULL DEFAULT '',
                    fault_injection TEXT NOT NULL DEFAULT '{}',
                    expected_outcome TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dlp_task_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES dlp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS dlp_task_approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES dlp_tasks(task_id)
                );

                CREATE TABLE IF NOT EXISTS mail_dead_letter_queue (
                    dlq_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_digest TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    safe_replay_allowed BOOLEAN NOT NULL DEFAULT FALSE,
                    recovery_hint TEXT NOT NULL DEFAULT '',
                    actor_context TEXT NOT NULL DEFAULT '{}',
                    payload_snapshot TEXT NOT NULL DEFAULT '{}',
                    replay_count INTEGER NOT NULL DEFAULT 0,
                    replay_status TEXT NOT NULL DEFAULT 'pending',
                    replay_result TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dlp_tasks_status ON dlp_tasks(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_tasks_session ON dlp_tasks(session_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_tasks_risk ON dlp_tasks(risk_level, updated_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_task_events_task ON dlp_task_events(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_task_approvals_task ON dlp_task_approvals(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_mail_dlq_task ON mail_dead_letter_queue(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_mail_dlq_status ON mail_dead_letter_queue(replay_status, updated_at);
                """
            )
            for column_name, column_definition in TASK_COLUMN_MIGRATIONS:
                conn.execute(f"ALTER TABLE dlp_tasks ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
            conn.execute("ALTER TABLE dlp_tasks ALTER COLUMN destination_email SET DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dlp_tasks_scenario ON dlp_tasks(scenario_id, updated_at)")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_dlp_tasks_idempotency_key
                ON dlp_tasks(idempotency_key)
                WHERE idempotency_key <> ''
                """
            )
            conn.commit()

        _TASK_STORE_INITIALIZED = True


def create_dlp_task(
    *,
    task_type: str = "dlp_outbound",
    session_id: str,
    conversation_id: str,
    message_raw: str,
    request_message: str = "",
    delivery_subject: str = "",
    delivery_body: str = "",
    delivery_plan_kind: str = "",
    resolved_source_kind: str = "",
    attachment_strategy: str = "none",
    attachment_content: str = "",
    attachment_filename: str = "",
    attachment_content_type: str = "",
    attachment_blob_id: str = "",
    destination_email: str,
    source_filename: str = "",
    source_content_type: str = "",
    source_parse_status: str = "not_provided",
    source_parse_error: str = "",
    requested_action: str = "summarize_and_send",
    priority: int = 0,
    status: str = "queued",
    entry_issue_type: str = "",
    missing_fields: list[str] | None = None,
    clarification_question: str = "",
    lab_run: bool = False,
    scenario_id: str = "",
    scenario_name: str = "",
    fault_injection: dict[str, Any] | None = None,
    expected_outcome: dict[str, Any] | None = None,
    domain_action: str = "",
    domain_payload: dict[str, Any] | None = None,
    domain_result: dict[str, Any] | None = None,
    mail_draft_id: str = "",
    idempotency_key: str = "",
    tenant_id: str = "",
    user_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    init_task_store()
    now = _now()
    task_id = _new_id("task")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO dlp_tasks (
                task_id, task_type, tenant_id, user_id, workspace_id, session_id, conversation_id, priority, status,
                domain_action, domain_payload, domain_result, mail_draft_id, idempotency_key,
                destination_email, requested_action, message_raw, request_message,
                delivery_subject, delivery_body, delivery_plan_kind, resolved_source_kind, attachment_strategy, attachment_content,
                attachment_filename, attachment_content_type, attachment_blob_id, source_filename,
                source_content_type, source_parse_status, source_parse_error, entry_issue_type,
                missing_fields, clarification_question, lab_run, scenario_id, scenario_name,
                fault_injection, expected_outcome, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) WHERE idempotency_key <> '' DO NOTHING
            """,
            (
                task_id,
                task_type,
                tenant_id,
                user_id,
                workspace_id,
                session_id,
                conversation_id,
                priority,
                status,
                domain_action,
                json.dumps(domain_payload or {}, ensure_ascii=False),
                json.dumps(domain_result or {}, ensure_ascii=False),
                mail_draft_id,
                idempotency_key,
                destination_email,
                requested_action,
                message_raw,
                request_message,
                delivery_subject,
                delivery_body,
                delivery_plan_kind,
                resolved_source_kind,
                attachment_strategy,
                attachment_content,
                attachment_filename,
                attachment_content_type,
                attachment_blob_id,
                source_filename,
                source_content_type,
                source_parse_status,
                source_parse_error,
                entry_issue_type,
                json.dumps(missing_fields or [], ensure_ascii=False),
                clarification_question,
                lab_run,
                scenario_id,
                scenario_name,
                json.dumps(fault_injection or {}, ensure_ascii=False),
                json.dumps(expected_outcome or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    if idempotency_key:
        existing = get_dlp_task_by_idempotency_key(idempotency_key)
        if existing and str(existing.get("task_id") or "") != task_id:
            return existing
    add_task_event(
        task_id,
        status,
        "api",
        {
            "message": (
                "Task queued for DLP processing."
                if status == "queued"
                else "Task created in a governance state and is waiting for more input."
            ),
            "status": status,
            "task_type": task_type,
            "domain_action": domain_action,
            "domain_payload": domain_payload or {},
            "mail_draft_id": mail_draft_id,
            "idempotency_key": idempotency_key,
            "destination_email": destination_email,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "request_message": request_message,
            "delivery_subject": delivery_subject,
            "delivery_body": delivery_body,
            "delivery_plan_kind": delivery_plan_kind,
            "resolved_source_kind": resolved_source_kind,
            "attachment_strategy": attachment_strategy,
            "attachment_filename": attachment_filename,
            "attachment_content_type": attachment_content_type,
            "attachment_blob_id": attachment_blob_id,
            "source_filename": source_filename,
            "scenario_id": scenario_id,
            "lab_run": lab_run,
            "entry_issue_type": entry_issue_type,
            "missing_fields": missing_fields or [],
        },
    )
    return get_dlp_task(task_id) or {}


def get_dlp_task(task_id: str) -> dict[str, Any] | None:
    init_task_store()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM dlp_tasks WHERE task_id = %s", (task_id,)).fetchone()
    return _decode_task(row)


def get_dlp_task_by_idempotency_key(idempotency_key: str) -> dict[str, Any] | None:
    if not str(idempotency_key or "").strip():
        return None
    init_task_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM dlp_tasks WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
    return _decode_task(row)


def list_dlp_tasks(
    *,
    session_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
) -> list[dict[str, Any]]:
    init_task_store()
    clauses: list[str] = []
    params: list[Any] = []
    if session_id:
        clauses.append("session_id = %s")
        params.append(session_id)
    if tenant_id:
        clauses.append("tenant_id = %s")
        params.append(tenant_id)
    if user_id:
        clauses.append("user_id = %s")
        params.append(user_id)
    if workspace_id:
        clauses.append("workspace_id = %s")
        params.append(workspace_id)
    if status:
        clauses.append("status = %s")
        params.append(status)
    if risk_level:
        clauses.append("risk_level = %s")
        params.append(risk_level)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM dlp_tasks {where} ORDER BY updated_at DESC",
            params,
        ).fetchall()
    return [_decode_task(row) for row in rows]


def get_latest_recoverable_task(session_id: str, conversation_id: str) -> dict[str, Any] | None:
    init_task_store()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM dlp_tasks
            WHERE session_id = %s
              AND conversation_id = %s
              AND status IN (%s, %s, %s)
              AND lab_run = FALSE
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (session_id, conversation_id, "needs_clarification", "input_invalid", "delivery_deferred"),
        ).fetchone()
    return _decode_task(row)


def add_task_event(task_id: str, event_type: str, actor: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    init_task_store()
    event_id = _new_id("event")
    now = _now()
    payload = json.dumps(details or {}, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO dlp_task_events (event_id, task_id, event_type, actor, details_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (event_id, task_id, event_type, actor, payload, now),
        )
        conn.commit()
    return get_task_events(task_id)[-1]


def get_task_events(task_id: str) -> list[dict[str, Any]]:
    init_task_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dlp_task_events WHERE task_id = %s ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    return [_decode_event(row) for row in rows]


def create_mail_dlq_entry(
    *,
    task_id: str,
    operation: str,
    payload_digest: str = "",
    last_error: str = "",
    attempt_count: int = 0,
    safe_replay_allowed: bool = False,
    recovery_hint: str = "",
    actor_context: dict[str, Any] | None = None,
    payload_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_task_store()
    existing = get_mail_dlq_entry_for_task(task_id, operation=operation, replay_status="pending")
    if existing:
        return existing
    dlq_id = _new_id("dlq")
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO mail_dead_letter_queue (
                dlq_id, task_id, operation, payload_digest, last_error, attempt_count,
                safe_replay_allowed, recovery_hint, actor_context, payload_snapshot,
                replay_count, replay_status, replay_result, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 'pending', '{}', %s, %s)
            """,
            (
                dlq_id,
                task_id,
                operation,
                payload_digest,
                last_error,
                int(attempt_count or 0),
                bool(safe_replay_allowed),
                recovery_hint,
                json.dumps(actor_context or {}, ensure_ascii=False),
                json.dumps(payload_snapshot or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    add_task_event(
        task_id,
        "dead_letter_created",
        "system",
        {
            "dlq_id": dlq_id,
            "operation": operation,
            "safe_replay_allowed": bool(safe_replay_allowed),
            "recovery_hint": recovery_hint,
            "last_error": last_error,
        },
    )
    return get_mail_dlq_entry(dlq_id) or {}


def get_mail_dlq_entry(dlq_id: str) -> dict[str, Any] | None:
    init_task_store()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM mail_dead_letter_queue WHERE dlq_id = %s", (dlq_id,)).fetchone()
    return _decode_dlq(row)


def get_mail_dlq_entry_for_task(task_id: str, *, operation: str = "", replay_status: str = "") -> dict[str, Any] | None:
    init_task_store()
    clauses = ["task_id = %s"]
    params: list[Any] = [task_id]
    if operation:
        clauses.append("operation = %s")
        params.append(operation)
    if replay_status:
        clauses.append("replay_status = %s")
        params.append(replay_status)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT * FROM mail_dead_letter_queue WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 1",
            params,
        ).fetchone()
    return _decode_dlq(row)


def list_mail_dlq_entries(*, replay_status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    init_task_store()
    clauses: list[str] = []
    params: list[Any] = []
    if replay_status:
        clauses.append("replay_status = %s")
        params.append(replay_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM mail_dead_letter_queue {where} ORDER BY updated_at DESC LIMIT %s",
            params,
        ).fetchall()
    return [_decode_dlq(row) for row in rows if row]


def mark_mail_dlq_replay(dlq_id: str, *, status: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    init_task_store()
    entry = get_mail_dlq_entry(dlq_id)
    if not entry:
        return None
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mail_dead_letter_queue
            SET replay_count = replay_count + 1,
                replay_status = %s,
                replay_result = %s,
                updated_at = %s
            WHERE dlq_id = %s
            """,
            (status, json.dumps(result or {}, ensure_ascii=False), now, dlq_id),
        )
        conn.commit()
    add_task_event(
        str(entry["task_id"]),
        "dead_letter_replay_marked",
        "system",
        {"dlq_id": dlq_id, "replay_status": status, "result": result or {}},
    )
    return get_mail_dlq_entry(dlq_id)


def replay_mail_dlq_entry(dlq_id: str, *, actor: str = "system") -> dict[str, Any]:
    entry = get_mail_dlq_entry(dlq_id)
    if not entry:
        return {"ok": False, "status": "not_found", "error": "Unknown dlq_id"}
    if not entry.get("safe_replay_allowed"):
        updated = mark_mail_dlq_replay(
            dlq_id,
            status="blocked",
            result={"ok": False, "error": "DLQ entry is not safe for automatic replay."},
        )
        return {"ok": False, "status": "blocked", "entry": updated}
    task = get_dlp_task(str(entry["task_id"]))
    if not task:
        updated = mark_mail_dlq_replay(
            dlq_id,
            status="blocked",
            result={"ok": False, "error": "Original task is missing."},
        )
        return {"ok": False, "status": "blocked", "entry": updated}
    if task.get("status") not in {"dead_letter", "send_failed", "delivery_deferred"}:
        updated = mark_mail_dlq_replay(
            dlq_id,
            status="blocked",
            result={"ok": False, "error": f"Task status is not replay-safe: {task.get('status')}"},
        )
        return {"ok": False, "status": "blocked", "entry": updated, "task": task}
    task = set_task_status(
        str(task["task_id"]),
        "queued_for_send",
        actor=actor,
        event_type="dead_letter_replay_queued",
        event_message="DLQ replay moved the task back to the send queue checkpoint.",
        extra_updates={
            "delivery_status": "queued_for_send",
            "delivery_error": "",
            "manual_handover_required": False,
            "next_recommended_action": "Enqueue email send worker with the existing idempotency key.",
        },
        details={"dlq_id": dlq_id, "operation": entry.get("operation"), "payload_digest": entry.get("payload_digest")},
    )
    updated = mark_mail_dlq_replay(
        dlq_id,
        status="replay_queued",
        result={"ok": True, "task_id": str((task or {}).get("task_id") or ""), "status": "queued_for_send"},
    )
    return {"ok": True, "status": "replay_queued", "entry": updated, "task": task}


def add_task_approval(task_id: str, action: str, actor: str, reason: str = "") -> dict[str, Any]:
    init_task_store()
    approval_id = _new_id("approval")
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO dlp_task_approvals (approval_id, task_id, action, actor, reason, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (approval_id, task_id, action, actor, reason, now),
        )
        conn.commit()
    return {
        "approval_id": approval_id,
        "task_id": task_id,
        "action": action,
        "actor": actor,
        "reason": reason,
        "created_at": now,
    }


def get_task_approvals(task_id: str) -> list[dict[str, Any]]:
    init_task_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dlp_task_approvals WHERE task_id = %s ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_dlp_task(task_id)
    init_task_store()
    fields["updated_at"] = _now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    values = []
    for key, value in fields.items():
        if key in JSON_TASK_FIELDS and not isinstance(value, str):
            values.append(json.dumps(value, ensure_ascii=False))
        else:
            values.append(value)
    values.append(task_id)
    with _connect() as conn:
        conn.execute(f"UPDATE dlp_tasks SET {assignments} WHERE task_id = %s", values)
        conn.commit()
    return get_dlp_task(task_id)


def set_task_status(
    task_id: str,
    status: str,
    *,
    actor: str,
    event_type: str,
    event_message: str,
    extra_updates: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    updates = {"status": status}
    if extra_updates:
        updates.update(extra_updates)
    task = update_task(task_id, **updates)
    add_task_event(
        task_id,
        event_type,
        actor,
        {"status": status, "message": event_message, **(details or {})},
    )
    return task


def transition_task_status(task_id: str, from_statuses: set[str], to_status: str, **updates: Any) -> dict[str, Any] | None:
    init_task_store()
    current = get_dlp_task(task_id)
    if not current or current["status"] not in from_statuses:
        return current
    return update_task(task_id, status=to_status, **updates)


def approve_task(task_id: str, actor: str) -> dict[str, Any] | None:
    task = get_dlp_task(task_id)
    if not task:
        return None
    if task["status"] == "sent":
        return task
    if task["status"] != "pending_approval":
        return task
    add_task_approval(task_id, "approve", actor)
    task = set_task_status(
        task_id,
        "approved",
        actor=actor,
        event_type="approved",
        event_message="Task approved for outbound delivery.",
        extra_updates={"delivery_status": "not_sent"},
    )
    return task


def reject_task(task_id: str, actor: str, reason: str) -> dict[str, Any] | None:
    task = get_dlp_task(task_id)
    if not task:
        return None
    if task["status"] in TERMINAL_TASK_STATUSES:
        return task
    add_task_approval(task_id, "reject", actor, reason)
    task = set_task_status(
        task_id,
        "rejected",
        actor=actor,
        event_type="rejected",
        event_message="Task rejected and terminated.",
        extra_updates={
            "delivery_status": "rejected",
            "delivery_error": "",
            "delivery_result": "",
            "final_result": f"Rejected by {actor}. {reason}".strip(),
        },
        details={"reason": reason},
    )
    return task


def get_task_stats() -> dict[str, Any]:
    init_task_store()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS total FROM dlp_tasks").fetchone()["total"]
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM dlp_tasks GROUP BY status"
        ).fetchall()
    by_status = {row["status"]: int(row["count"]) for row in rows}
    inflight = sum(
        count
        for status, count in by_status.items()
        if status not in TERMINAL_TASK_STATUSES
    )
    return {"total": int(total), "inflight": int(inflight), "by_status": by_status}


def get_sent_mail_stats(
    *,
    since: str | None = None,
    until: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    init_task_store()
    clauses = ["status = %s", "delivery_status = %s", "sent_at <> ''"]
    params: list[Any] = ["sent", "sent"]
    if session_id:
        clauses.append("session_id = %s")
        params.append(session_id)
    if since:
        clauses.append("sent_at >= %s")
        params.append(since)
    if until:
        clauses.append("sent_at <= %s")
        params.append(until)
    where = " AND ".join(clauses)
    with _connect() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS total FROM dlp_tasks WHERE {where}",
            params,
        ).fetchone()
        recent_rows = conn.execute(
            f"""
            SELECT task_id, destination_email, sent_at, draft_summary, final_result
            FROM dlp_tasks
            WHERE {where}
            ORDER BY sent_at DESC
            LIMIT 5
            """,
            params,
        ).fetchall()
    return {
        "total_sent": int((count_row or {}).get("total", 0)),
        "recent_sent": [dict(row) for row in recent_rows],
    }
