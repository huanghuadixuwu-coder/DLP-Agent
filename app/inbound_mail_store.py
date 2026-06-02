from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


INIT_DDL_LOCK_KEY = 86420532
_INITIALIZED = False
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


def _decode_message(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["is_seen"] = bool(item.get("is_seen"))
    item["labels"] = _decode_json(item.get("labels_json"), [])
    item["attachments"] = _decode_json(item.get("attachments_json"), [])
    item["headers_json"] = _decode_json(item.get("headers_json"), {})
    return item


def _decode_notification(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["payload_json"] = _decode_json(item.get("payload_json"), {})
    return item


def init_inbound_mail_store() -> None:
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
                CREATE TABLE IF NOT EXISTS inbound_mail_messages (
                    message_id TEXT PRIMARY KEY,
                    mailbox TEXT NOT NULL DEFAULT 'INBOX',
                    uid TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    provider_thread_id TEXT NOT NULL DEFAULT '',
                    sender TEXT NOT NULL DEFAULT '',
                    recipients TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL DEFAULT '',
                    snippet TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    body_text TEXT NOT NULL DEFAULT '',
                    body_html_sanitized TEXT NOT NULL DEFAULT '',
                    body_preview TEXT NOT NULL DEFAULT '',
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    headers_json TEXT NOT NULL DEFAULT '{}',
                    risk_hint TEXT NOT NULL DEFAULT '',
                    raw_size INTEGER NOT NULL DEFAULT 0,
                    is_seen BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS mail_sync_state (
                    mailbox TEXT PRIMARY KEY,
                    last_seen_uid TEXT NOT NULL DEFAULT '',
                    last_sync_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_outbox (
                    notification_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    delivered_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_inbound_mail_received
                    ON inbound_mail_messages(received_at DESC);
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_status
                    ON notification_outbox(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_type
                    ON notification_outbox(event_type, created_at);
                """
            )
            existing_columns = {
                row["column_name"]
                for row in conn.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'inbound_mail_messages'
                    """
                ).fetchall()
            }
            for column_name, column_sql in [
                ("thread_id", "TEXT NOT NULL DEFAULT ''"),
                ("provider_thread_id", "TEXT NOT NULL DEFAULT ''"),
                ("body_text", "TEXT NOT NULL DEFAULT ''"),
                ("body_html_sanitized", "TEXT NOT NULL DEFAULT ''"),
                ("body_preview", "TEXT NOT NULL DEFAULT ''"),
                ("labels_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("attachments_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("headers_json", "TEXT NOT NULL DEFAULT '{}'"),
            ]:
                if column_name not in existing_columns:
                    conn.execute(f"ALTER TABLE inbound_mail_messages ADD COLUMN {column_name} {column_sql}")
            conn.commit()
        _INITIALIZED = True


def upsert_inbound_message(message: dict[str, Any]) -> bool:
    init_inbound_mail_store()
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            """
            INSERT INTO inbound_mail_messages (
                message_id, mailbox, uid, thread_id, provider_thread_id, sender, recipients,
                subject, received_at, snippet, summary, body_text, body_html_sanitized,
                body_preview, labels_json, attachments_json, headers_json, risk_hint,
                raw_size, is_seen, created_at, updated_at
            ) VALUES (
                %(message_id)s, %(mailbox)s, %(uid)s, %(thread_id)s, %(provider_thread_id)s,
                %(sender)s, %(recipients)s, %(subject)s, %(received_at)s, %(snippet)s,
                %(summary)s, %(body_text)s, %(body_html_sanitized)s, %(body_preview)s,
                %(labels_json)s, %(attachments_json)s, %(headers_json)s, %(risk_hint)s,
                %(raw_size)s, %(is_seen)s, %(created_at)s, %(updated_at)s
            )
            ON CONFLICT (message_id) DO UPDATE SET
                mailbox = EXCLUDED.mailbox,
                uid = EXCLUDED.uid,
                thread_id = EXCLUDED.thread_id,
                provider_thread_id = EXCLUDED.provider_thread_id,
                sender = EXCLUDED.sender,
                recipients = EXCLUDED.recipients,
                subject = EXCLUDED.subject,
                received_at = EXCLUDED.received_at,
                snippet = EXCLUDED.snippet,
                summary = EXCLUDED.summary,
                body_text = EXCLUDED.body_text,
                body_html_sanitized = EXCLUDED.body_html_sanitized,
                body_preview = EXCLUDED.body_preview,
                labels_json = EXCLUDED.labels_json,
                attachments_json = EXCLUDED.attachments_json,
                headers_json = EXCLUDED.headers_json,
                risk_hint = EXCLUDED.risk_hint,
                raw_size = EXCLUDED.raw_size,
                is_seen = EXCLUDED.is_seen,
                updated_at = EXCLUDED.updated_at
            RETURNING (xmax = 0) AS inserted
            """,
            {
                "message_id": message["message_id"],
                "mailbox": message.get("mailbox", "INBOX"),
                "uid": message.get("uid", ""),
                "thread_id": message.get("thread_id", ""),
                "provider_thread_id": message.get("provider_thread_id", ""),
                "sender": message.get("sender", ""),
                "recipients": message.get("recipients", ""),
                "subject": message.get("subject", ""),
                "received_at": message.get("received_at", ""),
                "snippet": message.get("snippet", ""),
                "summary": message.get("summary", ""),
                "body_text": message.get("body_text", ""),
                "body_html_sanitized": message.get("body_html_sanitized", ""),
                "body_preview": message.get("body_preview", ""),
                "labels_json": json.dumps(message.get("labels", []), ensure_ascii=False),
                "attachments_json": json.dumps(message.get("attachments", []), ensure_ascii=False),
                "headers_json": json.dumps(message.get("headers_json", {}), ensure_ascii=False),
                "risk_hint": message.get("risk_hint", ""),
                "raw_size": int(message.get("raw_size", 0) or 0),
                "is_seen": bool(message.get("is_seen", False)),
                "created_at": now,
                "updated_at": now,
            },
        ).fetchone()
        conn.commit()
    return bool(row and row.get("inserted"))


def list_inbound_messages(
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    init_inbound_mail_store()
    clauses: list[str] = []
    params: list[Any] = []
    if since:
        clauses.append("received_at >= %s")
        params.append(since)
    if until:
        clauses.append("received_at <= %s")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM inbound_mail_messages
            {where}
            ORDER BY received_at DESC, created_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        ).fetchall()
    return [_decode_message(row) for row in rows if row]


def get_inbound_message(message_id: str) -> dict[str, Any] | None:
    init_inbound_mail_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM inbound_mail_messages WHERE message_id = %s",
            (message_id,),
        ).fetchone()
    return _decode_message(row)


def list_thread_messages(thread_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    init_inbound_mail_store()
    bounded_limit = max(1, min(int(limit or 20), 50))
    thread_key = str(thread_id or "").strip()
    if not thread_key:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM inbound_mail_messages
            WHERE thread_id = %s OR provider_thread_id = %s
            ORDER BY received_at ASC, created_at ASC
            LIMIT %s
            """,
            (thread_key, thread_key, bounded_limit),
        ).fetchall()
    return [_decode_message(row) for row in rows if row]


def list_recent_inbound_threads(*, limit: int = 3, messages_per_thread: int = 5) -> list[dict[str, Any]]:
    init_inbound_mail_store()
    bounded_limit = max(1, min(int(limit or 3), 10))
    bounded_messages = max(1, min(int(messages_per_thread or 5), 20))
    recent_messages = list_inbound_messages(limit=max(20, bounded_limit * bounded_messages * 2))
    threads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in recent_messages:
        thread_key = str(message.get("thread_id") or message.get("provider_thread_id") or message.get("message_id") or "").strip()
        if not thread_key or thread_key in seen:
            continue
        seen.add(thread_key)
        messages = list_thread_messages(thread_key, limit=bounded_messages)
        if not messages:
            messages = [message]
        latest = messages[-1] if messages else message
        first = messages[0] if messages else message
        threads.append(
            {
                "thread_id": str(first.get("thread_id") or thread_key),
                "provider_thread_id": str(first.get("provider_thread_id") or ""),
                "subject": str(first.get("subject") or latest.get("subject") or ""),
                "latest_received_at": str(latest.get("received_at") or message.get("received_at") or ""),
                "messages": messages,
            }
        )
        if len(threads) >= bounded_limit:
            break
    return threads


def inbound_summary(since: str, until: str) -> dict[str, Any]:
    messages = list_inbound_messages(since=since, until=until, limit=200)
    important = [
        item
        for item in messages
        if item.get("risk_hint") in {"medium", "high"} or any(
            token in (item.get("subject", "") + " " + item.get("snippet", "")).lower()
            for token in ("urgent", "asap", "approval", "contract", "invoice", "客户", "合同", "报价", "审批", "紧急")
        )
    ][:10]
    return {
        "since": since,
        "until": until,
        "total": len(messages),
        "unread": len([item for item in messages if not item.get("is_seen")]),
        "important_count": len(important),
        "important_messages": important,
        "recent_messages": messages[:10],
    }


def get_sync_state(mailbox: str) -> dict[str, Any]:
    init_inbound_mail_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM mail_sync_state WHERE mailbox = %s",
            (mailbox,),
        ).fetchone()
    return dict(row) if row else {"mailbox": mailbox, "last_seen_uid": "", "last_sync_at": "", "last_error": ""}


def update_sync_state(mailbox: str, *, last_seen_uid: str = "", last_error: str = "") -> dict[str, Any]:
    init_inbound_mail_store()
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO mail_sync_state (mailbox, last_seen_uid, last_sync_at, last_error, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (mailbox) DO UPDATE SET
                last_seen_uid = CASE
                    WHEN EXCLUDED.last_seen_uid <> '' THEN EXCLUDED.last_seen_uid
                    ELSE mail_sync_state.last_seen_uid
                END,
                last_sync_at = EXCLUDED.last_sync_at,
                last_error = EXCLUDED.last_error,
                updated_at = EXCLUDED.updated_at
            """,
            (mailbox, last_seen_uid, now, last_error, now),
        )
        conn.commit()
    return get_sync_state(mailbox)


def create_notification(
    event_type: str,
    title: str,
    body: str = "",
    payload: dict[str, Any] | None = None,
    *,
    status: str = "pending",
) -> dict[str, Any]:
    init_inbound_mail_store()
    now = _now()
    notification_id = _new_id("notify")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO notification_outbox (
                notification_id, event_type, title, body, payload_json, status, created_at, delivered_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, '')
            """,
            (
                notification_id,
                event_type,
                title,
                body,
                json.dumps(payload or {}, ensure_ascii=False),
                status,
                now,
            ),
        )
        conn.commit()
    return get_notification(notification_id) or {}


def get_notification(notification_id: str) -> dict[str, Any] | None:
    init_inbound_mail_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM notification_outbox WHERE notification_id = %s",
            (notification_id,),
        ).fetchone()
    return _decode_notification(row)


def list_notifications(
    *,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_inbound_mail_store()
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    if event_type:
        clauses.append("event_type = %s")
        params.append(event_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM notification_outbox
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
    return [_decode_notification(row) for row in rows if row]
