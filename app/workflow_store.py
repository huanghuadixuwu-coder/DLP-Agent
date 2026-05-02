from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import DATA_DIR
from app.mcp_client import call_mcp_tool


DB_PATH = DATA_DIR / "workflows.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_workflow_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sensitive_workflows (
                workflow_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '',
                workflow_type TEXT NOT NULL DEFAULT 'dlp_outbound_approval',
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                redacted_text TEXT NOT NULL,
                business_context TEXT NOT NULL DEFAULT '',
                recipient_type TEXT NOT NULL DEFAULT '',
                destination_email TEXT NOT NULL DEFAULT '17388861183@163.com',
                source_filename TEXT NOT NULL DEFAULT '',
                source_content_type TEXT NOT NULL DEFAULT '',
                source_text_preview TEXT NOT NULL DEFAULT '',
                requested_action TEXT NOT NULL DEFAULT 'summarize_and_send',
                risk_level TEXT NOT NULL,
                risk_reasons TEXT NOT NULL DEFAULT '[]',
                redactions TEXT NOT NULL DEFAULT '[]',
                proposed_action TEXT NOT NULL DEFAULT '',
                final_result TEXT NOT NULL DEFAULT '',
                draft_summary TEXT NOT NULL DEFAULT '',
                simulated_delivery_result TEXT NOT NULL DEFAULT '',
                delivery_status TEXT NOT NULL DEFAULT 'not_sent',
                delivery_result TEXT NOT NULL DEFAULT '',
                delivery_error TEXT NOT NULL DEFAULT '',
                sent_at TEXT NOT NULL DEFAULT '',
                smtp_provider TEXT NOT NULL DEFAULT '',
                approval_required INTEGER NOT NULL DEFAULT 0,
                approved_by TEXT NOT NULL DEFAULT '',
                rejected_by TEXT NOT NULL DEFAULT '',
                rejection_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workflow_audit_events (
                audit_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES sensitive_workflows(workflow_id)
            );

            CREATE INDEX IF NOT EXISTS idx_sensitive_workflows_session
                ON sensitive_workflows(session_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_sensitive_workflows_status
                ON sensitive_workflows(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_workflow_audit_workflow
                ON workflow_audit_events(workflow_id, created_at);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sensitive_workflows)").fetchall()}
        migrations = {
            "workflow_type": "TEXT NOT NULL DEFAULT 'dlp_outbound_approval'",
            "destination_email": "TEXT NOT NULL DEFAULT '17388861183@163.com'",
            "source_filename": "TEXT NOT NULL DEFAULT ''",
            "source_content_type": "TEXT NOT NULL DEFAULT ''",
            "source_text_preview": "TEXT NOT NULL DEFAULT ''",
            "requested_action": "TEXT NOT NULL DEFAULT 'summarize_and_send'",
            "draft_summary": "TEXT NOT NULL DEFAULT ''",
            "simulated_delivery_result": "TEXT NOT NULL DEFAULT ''",
            "delivery_status": "TEXT NOT NULL DEFAULT 'not_sent'",
            "delivery_result": "TEXT NOT NULL DEFAULT ''",
            "delivery_error": "TEXT NOT NULL DEFAULT ''",
            "sent_at": "TEXT NOT NULL DEFAULT ''",
            "smtp_provider": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE sensitive_workflows ADD COLUMN {column} {definition}")


def _decode_workflow(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["approval_required"] = bool(item["approval_required"])
    item["risk_reasons"] = json.loads(item.get("risk_reasons") or "[]")
    item["redactions"] = json.loads(item.get("redactions") or "[]")
    return item


def _decode_audit(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["details"] = json.loads(item.get("details") or "{}")
    return item


def add_audit_event(workflow_id: str, event_type: str, actor: str = "", details: dict[str, Any] | None = None) -> dict:
    init_workflow_store()
    audit_id = _new_id("audit")
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO workflow_audit_events (audit_id, workflow_id, event_type, actor, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (audit_id, workflow_id, event_type, actor, json.dumps(details or {}, ensure_ascii=False), now),
        )
    return get_audit_events(workflow_id)[-1]


def create_sensitive_workflow(
    *,
    session_id: str,
    conversation_id: str | None,
    message: str,
    redacted_text: str,
    business_context: str,
    recipient_type: str,
    destination_email: str,
    source_filename: str,
    source_content_type: str,
    source_text_preview: str,
    requested_action: str,
    risk_level: str,
    risk_reasons: list[str],
    redactions: list[dict[str, Any]],
    proposed_action: str,
    final_result: str,
    draft_summary: str,
    simulated_delivery_result: str,
    approval_required: bool,
    status: str,
) -> dict[str, Any]:
    init_workflow_store()
    workflow_id = _new_id("wf")
    now = _now()
    initial_delivery_status = "pending_approval" if approval_required else "not_sent"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sensitive_workflows (
                workflow_id, session_id, conversation_id, workflow_type, status, message, redacted_text,
                business_context, recipient_type, destination_email, source_filename,
                source_content_type, source_text_preview, requested_action, risk_level,
                risk_reasons, redactions, proposed_action, final_result, draft_summary,
                simulated_delivery_result, delivery_status, delivery_result, delivery_error,
                sent_at, smtp_provider, approval_required, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                session_id,
                conversation_id or "",
                "dlp_outbound_approval",
                status,
                message,
                redacted_text,
                business_context,
                recipient_type,
                destination_email,
                source_filename,
                source_content_type,
                source_text_preview,
                requested_action,
                risk_level,
                json.dumps(risk_reasons, ensure_ascii=False),
                json.dumps(redactions, ensure_ascii=False),
                proposed_action,
                final_result,
                draft_summary,
                simulated_delivery_result,
                initial_delivery_status,
                "",
                "",
                "",
                "",
                1 if approval_required else 0,
                now,
                now,
            ),
        )
    add_audit_event(
        workflow_id,
        "created",
        "agent",
        {"status": status, "risk_level": risk_level, "approval_required": approval_required},
    )
    return get_sensitive_workflow(workflow_id) or {}


def get_sensitive_workflow(workflow_id: str) -> dict[str, Any] | None:
    init_workflow_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sensitive_workflows WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
    return _decode_workflow(row) if row else None


def list_sensitive_workflows(session_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    init_workflow_store()
    clauses: list[str] = []
    params: list[Any] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM sensitive_workflows {where} ORDER BY updated_at DESC",
            tuple(params),
        ).fetchall()
    return [_decode_workflow(row) for row in rows]


def get_audit_events(workflow_id: str) -> list[dict[str, Any]]:
    init_workflow_store()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_audit_events WHERE workflow_id = ? ORDER BY created_at ASC",
            (workflow_id,),
        ).fetchall()
    return [_decode_audit(row) for row in rows]


def approve_sensitive_workflow(workflow_id: str, actor: str) -> dict[str, Any] | None:
    workflow = get_sensitive_workflow(workflow_id)
    if not workflow:
        return None
    if workflow["status"] != "pending_approval":
        return workflow

    draft_summary = workflow.get("draft_summary") or _build_draft_summary(workflow.get("redacted_text", ""))
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE sensitive_workflows
            SET status = 'approved', approved_by = ?, draft_summary = ?,
                delivery_status = 'not_sent', updated_at = ?
            WHERE workflow_id = ?
            """,
            (actor, draft_summary, now, workflow_id),
        )
    add_audit_event(workflow_id, "approved", actor, {"draft_summary": draft_summary})
    return send_sensitive_workflow_email(workflow_id, actor)


def send_sensitive_workflow_email(workflow_id: str, actor: str = "agent") -> dict[str, Any] | None:
    workflow = get_sensitive_workflow(workflow_id)
    if not workflow:
        return None
    if workflow["status"] in {"rejected", "sent"}:
        return workflow

    draft_summary = workflow.get("draft_summary") or _build_draft_summary(workflow.get("redacted_text", ""))
    destination_email = workflow.get("destination_email") or "17388861183@163.com"
    subject = f"DLP Agent outbound summary - {workflow_id}"
    body = "\n".join(
        [
            "这是一封由 DLP Agent 发送的脱敏摘要邮件。",
            "",
            f"Workflow ID: {workflow_id}",
            f"Risk Level: {workflow.get('risk_level', '')}",
            f"Source File: {workflow.get('source_filename', '') or 'text'}",
            "",
            "摘要内容:",
            draft_summary,
            "",
            "安全说明: 本邮件只包含脱敏摘要，不包含原始敏感文本。",
        ]
    )
    tool_result = call_mcp_tool(
        "send_email_163",
        {"to_email": destination_email, "subject": subject, "body": body},
    )
    result_payload = tool_result.get("result") if tool_result.get("ok") else {"ok": False, "error": tool_result.get("error", "")}
    success = bool(result_payload.get("ok"))
    sent_at = str(result_payload.get("sent_at") or "")
    provider = str(result_payload.get("provider") or "163_smtp")
    error = str(result_payload.get("error") or "")
    delivery_result = (
        f"已真实发送到 {destination_email}。发送内容仅使用脱敏摘要，未包含原始敏感字段。"
        if success
        else ""
    )
    final_result = delivery_result if success else f"邮件发送失败: {error}"
    status = "sent" if success else "send_failed"
    now = _now()

    with _connect() as conn:
        conn.execute(
            """
            UPDATE sensitive_workflows
            SET status = ?, draft_summary = ?, delivery_status = ?, delivery_result = ?,
                delivery_error = ?, sent_at = ?, smtp_provider = ?, final_result = ?,
                simulated_delivery_result = '', updated_at = ?
            WHERE workflow_id = ?
            """,
            (
                status,
                draft_summary,
                status,
                delivery_result,
                error,
                sent_at,
                provider,
                final_result,
                now,
                workflow_id,
            ),
        )
    add_audit_event(
        workflow_id,
        "email_sent" if success else "email_send_failed",
        actor,
        {
            "to_email": destination_email,
            "provider": provider,
            "sent_at": sent_at,
            "error": error,
        },
    )
    return get_sensitive_workflow(workflow_id)


def reject_sensitive_workflow(workflow_id: str, actor: str, reason: str = "") -> dict[str, Any] | None:
    workflow = get_sensitive_workflow(workflow_id)
    if not workflow:
        return None
    if workflow["status"] not in {"pending_approval", "approved", "send_failed"}:
        return workflow

    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE sensitive_workflows
            SET status = 'rejected', rejected_by = ?, rejection_reason = ?,
                simulated_delivery_result = '', delivery_status = 'rejected',
                delivery_result = '', delivery_error = '', final_result = ?, updated_at = ?
            WHERE workflow_id = ?
            """,
            (actor, reason, "已驳回。流程终止，不执行外发。", now, workflow_id),
        )
    add_audit_event(workflow_id, "rejected", actor, {"reason": reason})
    return get_sensitive_workflow(workflow_id)


def _build_draft_summary(text: str, limit: int = 420) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "未提取到可总结内容。"
    if len(cleaned) <= limit:
        return f"摘要草稿：{cleaned}"
    return f"摘要草稿：{cleaned[:limit]}..."
