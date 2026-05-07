from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


TERMINAL_TASK_STATUSES = {"sent", "rejected", "send_failed", "failed"}
RECOVERABLE_TASK_STATUSES = {"needs_clarification", "input_invalid", "delivery_deferred"}
JSON_TASK_FIELDS = {
    "risk_reasons",
    "redactions",
    "retrieved_evidence",
    "fault_injection",
    "expected_outcome",
    "missing_fields",
}
LIST_JSON_FIELDS = {"risk_reasons", "redactions", "retrieved_evidence", "missing_fields"}
BOOLEAN_TASK_FIELDS = {"approval_required", "manual_handover_required", "lab_run"}
TASK_COLUMN_MIGRATIONS = [
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
                    session_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
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

                CREATE INDEX IF NOT EXISTS idx_dlp_tasks_status ON dlp_tasks(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_tasks_session ON dlp_tasks(session_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_tasks_risk ON dlp_tasks(risk_level, updated_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_task_events_task ON dlp_task_events(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_dlp_task_approvals_task ON dlp_task_approvals(task_id, created_at);
                """
            )
            for column_name, column_definition in TASK_COLUMN_MIGRATIONS:
                conn.execute(f"ALTER TABLE dlp_tasks ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
            conn.execute("ALTER TABLE dlp_tasks ALTER COLUMN destination_email SET DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dlp_tasks_scenario ON dlp_tasks(scenario_id, updated_at)")
            conn.commit()

        _TASK_STORE_INITIALIZED = True


def create_dlp_task(
    *,
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
) -> dict[str, Any]:
    init_task_store()
    now = _now()
    task_id = _new_id("task")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO dlp_tasks (
                task_id, session_id, conversation_id, priority, status,
                destination_email, requested_action, message_raw, request_message,
                delivery_subject, delivery_body, delivery_plan_kind, resolved_source_kind, attachment_strategy, attachment_content,
                attachment_filename, attachment_content_type, attachment_blob_id, source_filename,
                source_content_type, source_parse_status, source_parse_error, entry_issue_type,
                missing_fields, clarification_question, lab_run, scenario_id, scenario_name,
                fault_injection, expected_outcome, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                session_id,
                conversation_id,
                priority,
                status,
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
            "destination_email": destination_email,
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


def list_dlp_tasks(
    *,
    session_id: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
) -> list[dict[str, Any]]:
    init_task_store()
    clauses: list[str] = []
    params: list[Any] = []
    if session_id:
        clauses.append("session_id = %s")
        params.append(session_id)
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
