from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.actor_context import actor_from_mapping
from app.config import DATA_DIR
from app.session_transcript import append_transcript_event


DB_PATH = DATA_DIR / "conversations.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_conversation_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_merged INTEGER NOT NULL DEFAULT 0,
                source_conversation_ids TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                redacted_content TEXT NOT NULL DEFAULT '',
                answer_summary TEXT NOT NULL DEFAULT '',
                intent TEXT NOT NULL DEFAULT '',
                tool_calls TEXT NOT NULL DEFAULT '[]',
                citations TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
            );

            CREATE TABLE IF NOT EXISTS merges (
                merged_conversation_id TEXT PRIMARY KEY,
                source_conversation_ids TEXT NOT NULL,
                merge_summary TEXT NOT NULL,
                topics TEXT NOT NULL DEFAULT '[]',
                decisions TEXT NOT NULL DEFAULT '[]',
                open_questions TEXT NOT NULL DEFAULT '[]',
                source_turn_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_session
                ON conversations(session_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_turns_conversation
                ON turns(conversation_id, created_at);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}
        if "debug_payload" not in columns:
            conn.execute("ALTER TABLE turns ADD COLUMN debug_payload TEXT NOT NULL DEFAULT '{}'")
        conversation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        for column_name in ("tenant_id", "user_id", "workspace_id"):
            if column_name not in conversation_columns:
                conn.execute(f"ALTER TABLE conversations ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''")
        turn_columns = {row["name"] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}
        for column_name in ("tenant_id", "user_id", "workspace_id"):
            if column_name not in turn_columns:
                conn.execute(f"ALTER TABLE turns ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''")


def _row_to_conversation(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["is_merged"] = bool(item["is_merged"])
    item["source_conversation_ids"] = json.loads(item.get("source_conversation_ids") or "[]")
    return item


def _row_to_turn(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["tool_calls"] = json.loads(item.get("tool_calls") or "[]")
    item["citations"] = json.loads(item.get("citations") or "[]")
    item["debug_payload"] = json.loads(item.get("debug_payload") or "{}")
    return item


def create_conversation(
    session_id: str,
    title: str | None = None,
    *,
    is_merged: bool = False,
    source_conversation_ids: list[str] | None = None,
    conversation_id: str | None = None,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_conversation_store()
    now = _now()
    prefix = "merged" if is_merged else "conv"
    conv_id = conversation_id or _new_id(prefix)
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conv_id)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (
                conversation_id, session_id, title, summary, created_at, updated_at,
                is_merged, source_conversation_ids, tenant_id, user_id, workspace_id
            ) VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conv_id,
                session_id,
                title or ("Merged conversation" if is_merged else "New conversation"),
                now,
                now,
                1 if is_merged else 0,
                json.dumps(source_conversation_ids or [], ensure_ascii=False),
                actor.tenant_id,
                actor.user_id,
                actor.workspace_id,
            ),
        )
    return get_conversation(conv_id) or {}


def get_or_create_conversation(
    session_id: str,
    conversation_id: str | None = None,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if conversation_id:
        existing = get_conversation(conversation_id)
        if existing:
            return existing
    return create_conversation(session_id, actor_context=actor_context)


def list_conversations(session_id: str, actor_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    init_conversation_store()
    actor = actor_from_mapping(actor_context or {}, session_id=session_id) if actor_context else None
    clauses = ["session_id = ?"]
    params: list[Any] = [session_id]
    if actor and not actor.is_local_dev:
        clauses.extend(["tenant_id = ?", "user_id = ?", "workspace_id = ?"])
        params.extend([actor.tenant_id, actor.user_id, actor.workspace_id])
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM conversations
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC
            """,
            tuple(params),
        ).fetchall()
    return [_row_to_conversation(row) for row in rows]


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    init_conversation_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return _row_to_conversation(row) if row else None


def update_conversation_summary(conversation_id: str, summary: str, title: str | None = None) -> None:
    init_conversation_store()
    with _connect() as conn:
        if title:
            conn.execute(
                "UPDATE conversations SET summary = ?, title = ?, updated_at = ? WHERE conversation_id = ?",
                (summary, title, _now(), conversation_id),
            )
        else:
            conn.execute(
                "UPDATE conversations SET summary = ?, updated_at = ? WHERE conversation_id = ?",
                (summary, _now(), conversation_id),
            )


def delete_conversation(conversation_id: str) -> dict[str, Any] | None:
    init_conversation_store()
    conversation = get_conversation(conversation_id)
    if not conversation:
        return None

    with _connect() as conn:
        rows = conn.execute(
            "SELECT conversation_id, source_conversation_ids FROM conversations WHERE is_merged = 1"
        ).fetchall()
        for row in rows:
            source_ids = json.loads(row["source_conversation_ids"] or "[]")
            if conversation_id not in source_ids:
                continue
            updated_source_ids = [item for item in source_ids if item != conversation_id]
            conn.execute(
                "UPDATE conversations SET source_conversation_ids = ?, updated_at = ? WHERE conversation_id = ?",
                (json.dumps(updated_source_ids, ensure_ascii=False), _now(), row["conversation_id"]),
            )
            conn.execute(
                "UPDATE merges SET source_conversation_ids = ? WHERE merged_conversation_id = ?",
                (json.dumps(updated_source_ids, ensure_ascii=False), row["conversation_id"]),
            )

        conn.execute("DELETE FROM turns WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM merges WHERE merged_conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))

    return conversation


def append_turn(
    *,
    session_id: str,
    conversation_id: str,
    role: str,
    content: str,
    redacted_content: str = "",
    answer_summary: str = "",
    intent: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    debug_payload: dict[str, Any] | None = None,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_conversation_store()
    turn_id = _new_id("turn")
    now = _now()
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO turns (
                turn_id, conversation_id, session_id, role, content, redacted_content,
                answer_summary, intent, tool_calls, citations, debug_payload, created_at,
                tenant_id, user_id, workspace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                conversation_id,
                session_id,
                role,
                content,
                redacted_content,
                answer_summary,
                intent,
                json.dumps(tool_calls or [], ensure_ascii=False),
                json.dumps(citations or [], ensure_ascii=False),
                json.dumps(debug_payload or {}, ensure_ascii=False),
                now,
                actor.tenant_id,
                actor.user_id,
                actor.workspace_id,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (now, conversation_id),
        )
    turn = get_turn(turn_id) or {}
    try:
        append_transcript_event(
            {
                "turn_id": turn_id,
                "session_id": session_id,
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "redacted_content": redacted_content,
                "answer_summary": answer_summary,
                "intent": intent,
                "tool_calls": tool_calls or [],
                "citations": citations or [],
                "debug_payload": debug_payload or {},
                "tenant_id": actor.tenant_id,
                "user_id": actor.user_id,
                "workspace_id": actor.workspace_id,
                "created_at": now,
            }
        )
    except Exception:
        pass
    return turn


def append_exchange(
    *,
    session_id: str,
    conversation_id: str,
    question: str,
    answer: str,
    redacted_question: str = "",
    answer_summary: str = "",
    intent: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    debug_payload: dict[str, Any] | None = None,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user_turn = append_turn(
        session_id=session_id,
        conversation_id=conversation_id,
        role="user",
        content=question,
        redacted_content=redacted_question,
        intent=intent,
        actor_context=actor_context,
    )
    assistant_turn = append_turn(
        session_id=session_id,
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        redacted_content=answer,
        answer_summary=answer_summary,
        intent=intent,
        tool_calls=tool_calls,
        citations=citations,
        debug_payload=debug_payload,
        actor_context=actor_context,
    )
    return {"user_turn": user_turn, "assistant_turn": assistant_turn}


def get_turn(turn_id: str) -> dict[str, Any] | None:
    init_conversation_store()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
    return _row_to_turn(row) if row else None


def get_turns(conversation_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    init_conversation_store()
    query = "SELECT * FROM turns WHERE conversation_id = ? ORDER BY created_at ASC"
    params: tuple[Any, ...] = (conversation_id,)
    if limit is not None:
        query = "SELECT * FROM turns WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?"
        params = (conversation_id, limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    turns = [_row_to_turn(row) for row in rows]
    return list(reversed(turns)) if limit is not None else turns


def save_merge(
    *,
    merged_conversation_id: str,
    source_conversation_ids: list[str],
    merge_summary: str,
    topics: list[str],
    decisions: list[str],
    open_questions: list[str],
    source_turn_ids: list[str],
) -> None:
    init_conversation_store()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO merges (
                merged_conversation_id, source_conversation_ids, merge_summary,
                topics, decisions, open_questions, source_turn_ids, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merged_conversation_id,
                json.dumps(source_conversation_ids, ensure_ascii=False),
                merge_summary,
                json.dumps(topics, ensure_ascii=False),
                json.dumps(decisions, ensure_ascii=False),
                json.dumps(open_questions, ensure_ascii=False),
                json.dumps(source_turn_ids, ensure_ascii=False),
                _now(),
            ),
        )


def get_merge(merged_conversation_id: str) -> dict[str, Any] | None:
    init_conversation_store()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM merges WHERE merged_conversation_id = ?",
            (merged_conversation_id,),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    for key in ("source_conversation_ids", "topics", "decisions", "open_questions", "source_turn_ids"):
        item[key] = json.loads(item.get(key) or "[]")
    return item


def get_related_merged_conversations(session_id: str, conversation_id: str) -> list[dict[str, Any]]:
    conversations = list_conversations(session_id)
    return [
        item
        for item in conversations
        if item.get("is_merged") and conversation_id in item.get("source_conversation_ids", [])
    ]
