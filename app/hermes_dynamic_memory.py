from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, get_settings
from app.conversation_memory import compact_text
from app.conversation_store import get_turns, update_conversation_summary
from app.actor_context import ActorContext, actor_from_mapping
from app.hermes_memory import search_workspace_memory_with_plan


EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TASK_ID_PATTERN = re.compile(r"\btask_[A-Za-z0-9_]+\b")
URL_PATTERN = re.compile(r"https?://[^\s)>\]]+")
FILE_PATTERN = re.compile(r"[\w\u4e00-\u9fff ._-]+\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|json|zip)", re.IGNORECASE)
HIGH_RISK_TERMS = (
    "approval",
    "approve",
    "bypass",
    "send by default",
    "mail default",
    "email default",
    "smtp",
    "password",
    "api key",
    "审批",
    "绕过",
    "默认发送",
    "默认发信",
    "邮件默认",
    "发信策略",
    "外发策略",
)
PREFERENCE_TERMS = ("prefer", "preference", "remember", "以后", "记住", "偏好", "我希望", "我不希望", "不要", "别再", "默认")
USER_MEMORY_REVIEW_STATUSES = {"active", "rejected", "pending", "expired"}
REFLECTION_REVIEW_STATUSES = {"approved", "rejected", "pending", "expired"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _db_path() -> Path:
    path = Path(getattr(get_settings(), "hermes_memory_db_path", str(DATA_DIR / "hermes_memory.db")))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_hermes_dynamic_memory_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hermes_turn_summaries (
                summary_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                workspace_id TEXT NOT NULL DEFAULT '',
                user_turn_id TEXT NOT NULL,
                assistant_turn_id TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                intent TEXT NOT NULL DEFAULT '',
                entities TEXT NOT NULL DEFAULT '[]',
                files_uploaded TEXT NOT NULL DEFAULT '[]',
                risk_level TEXT NOT NULL DEFAULT 'low',
                user_goal TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                key_files TEXT NOT NULL DEFAULT '[]',
                recipients TEXT NOT NULL DEFAULT '[]',
                task_ids TEXT NOT NULL DEFAULT '[]',
                failure_reason TEXT NOT NULL DEFAULT '',
                memory_scope TEXT NOT NULL DEFAULT 'session',
                provenance TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hermes_user_memory_facts (
                fact_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '',
                tenant_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                workspace_id TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                risk_level TEXT NOT NULL DEFAULT 'low',
                source_turn_ids TEXT NOT NULL DEFAULT '[]',
                reviewed_by TEXT NOT NULL DEFAULT '',
                review_reason TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hermes_reflection_candidates (
                candidate_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                workspace_id TEXT NOT NULL DEFAULT '',
                trigger_reason TEXT NOT NULL,
                durable_facts TEXT NOT NULL DEFAULT '[]',
                user_preferences TEXT NOT NULL DEFAULT '[]',
                process_improvements TEXT NOT NULL DEFAULT '[]',
                risk_level TEXT NOT NULL DEFAULT 'low',
                status TEXT NOT NULL DEFAULT 'pending',
                source_turn_ids TEXT NOT NULL DEFAULT '[]',
                reviewed_by TEXT NOT NULL DEFAULT '',
                review_reason TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hermes_memory_review_audit (
                audit_id TEXT PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                previous_status TEXT NOT NULL DEFAULT '',
                new_status TEXT NOT NULL DEFAULT '',
                reviewer TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                tenant_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                workspace_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hermes_transcript_compactions (
                compaction_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                workspace_id TEXT NOT NULL DEFAULT '',
                first_kept_turn_id TEXT NOT NULL DEFAULT '',
                source_turn_ids TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL,
                preserved_identifiers TEXT NOT NULL DEFAULT '{}',
                token_estimate INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_hermes_turn_summaries_conversation
                ON hermes_turn_summaries(session_id, conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_user_memory_session
                ON hermes_user_memory_facts(session_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_reflection_conversation
                ON hermes_reflection_candidates(session_id, conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_compactions_conversation
                ON hermes_transcript_compactions(session_id, conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_memory_review_audit_target
                ON hermes_memory_review_audit(target_type, target_id, created_at);
            """
        )
        actor_columns = {
            "tenant_id": "TEXT NOT NULL DEFAULT ''",
            "user_id": "TEXT NOT NULL DEFAULT ''",
            "workspace_id": "TEXT NOT NULL DEFAULT ''",
        }
        _ensure_columns(
            conn,
            "hermes_turn_summaries",
            {
                **actor_columns,
                "summary": "TEXT NOT NULL DEFAULT ''",
                "intent": "TEXT NOT NULL DEFAULT ''",
                "entities": "TEXT NOT NULL DEFAULT '[]'",
                "files_uploaded": "TEXT NOT NULL DEFAULT '[]'",
                "risk_level": "TEXT NOT NULL DEFAULT 'low'",
            },
        )
        for table in ("hermes_user_memory_facts", "hermes_reflection_candidates", "hermes_transcript_compactions"):
            _ensure_columns(conn, table, actor_columns)
        review_columns = {
            "reviewed_by": "TEXT NOT NULL DEFAULT ''",
            "review_reason": "TEXT NOT NULL DEFAULT ''",
            "reviewed_at": "TEXT NOT NULL DEFAULT ''",
            "expires_at": "TEXT NOT NULL DEFAULT ''",
        }
        _ensure_columns(conn, "hermes_user_memory_facts", review_columns)
        _ensure_columns(conn, "hermes_reflection_candidates", review_columns)
        _ensure_columns(conn, "hermes_memory_review_audit", actor_columns)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_hermes_turn_summaries_actor
                ON hermes_turn_summaries(tenant_id, user_id, workspace_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_user_memory_actor
                ON hermes_user_memory_facts(tenant_id, user_id, workspace_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_reflection_actor
                ON hermes_reflection_candidates(tenant_id, user_id, workspace_id, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_compactions_actor
                ON hermes_transcript_compactions(tenant_id, user_id, workspace_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_hermes_memory_review_audit_actor
                ON hermes_memory_review_audit(tenant_id, user_id, workspace_id, created_at);
            """
        )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def extract_memory_identifiers(text: str, upload_context: dict[str, Any] | None = None) -> dict[str, list[str]]:
    upload_context = dict(upload_context or {})
    filenames = set(FILE_PATTERN.findall(text or ""))
    if upload_context.get("filename"):
        filenames.add(str(upload_context["filename"]))
    return {
        "emails": sorted(set(EMAIL_PATTERN.findall(text or ""))),
        "task_ids": sorted(set(TASK_ID_PATTERN.findall(text or ""))),
        "urls": sorted(set(URL_PATTERN.findall(text or ""))),
        "files": sorted(item.strip() for item in filenames if item.strip()),
    }


def classify_memory_risk(text: str) -> str:
    lowered = (text or "").lower()
    if any(term in lowered or term in text for term in HIGH_RISK_TERMS):
        return "high"
    if EMAIL_PATTERN.search(text or ""):
        return "medium"
    return "low"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in (
        "key_files",
        "recipients",
        "task_ids",
        "provenance",
        "source_turn_ids",
        "durable_facts",
        "user_preferences",
        "process_improvements",
        "preserved_identifiers",
        "entities",
        "files_uploaded",
    ):
        if key in item:
            try:
                default = "{}" if key in {"provenance", "preserved_identifiers"} else "[]"
                item[key] = json.loads(item.get(key) or default)
            except json.JSONDecodeError:
                item[key] = {} if key in {"provenance", "preserved_identifiers"} else []
    return item


def _actor_filter(actor_context: dict[str, Any] | None, *, session_id: str = "", conversation_id: str = "") -> tuple[ActorContext, list[str], list[Any]]:
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    if actor.is_local_dev:
        return actor, [], []
    return actor, ["tenant_id = ?", "user_id = ?", "workspace_id = ?"], [actor.tenant_id, actor.user_id, actor.workspace_id]


def _row_matches_actor(row: sqlite3.Row | dict[str, Any], actor_context: dict[str, Any] | None) -> bool:
    actor, _, _ = _actor_filter(actor_context)
    if actor.is_local_dev:
        return True
    item = dict(row)
    return (
        str(item.get("tenant_id") or "") == actor.tenant_id
        and str(item.get("user_id") or "") == actor.user_id
        and str(item.get("workspace_id") or "") == actor.workspace_id
    )


def _write_memory_review_audit(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    target_id: str,
    previous_status: str,
    new_status: str,
    reviewer: str,
    reason: str,
    actor_context: dict[str, Any] | None,
    session_id: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    audit_id = _new_id("memaudit")
    created_at = _now()
    conn.execute(
        """
        INSERT INTO hermes_memory_review_audit (
            audit_id, target_type, target_id, previous_status, new_status,
            reviewer, reason, tenant_id, user_id, workspace_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            target_type,
            target_id,
            previous_status,
            new_status,
            reviewer,
            reason,
            actor.tenant_id,
            actor.user_id,
            actor.workspace_id,
            created_at,
        ),
    )
    return {
        "audit_id": audit_id,
        "target_type": target_type,
        "target_id": target_id,
        "previous_status": previous_status,
        "new_status": new_status,
        "reviewer": reviewer,
        "reason": reason,
        "tenant_id": actor.tenant_id,
        "user_id": actor.user_id,
        "workspace_id": actor.workspace_id,
        "created_at": created_at,
    }


def _infer_memory_intent(result: dict[str, Any], question: str, answer: str) -> str:
    explicit = str(result.get("intent") or "").strip()
    if explicit:
        return explicit
    text = f"{question}\n{answer}".lower()
    if any(marker in text for marker in ("enterprise", "rag", "knowledge base", "企业知识库")):
        return "enterprise_rag_query"
    if any(marker in text for marker in ("email", "mail", "smtp", "发邮件", "外发")):
        return "outbound_mail"
    if any(marker in text for marker in ("dlp", "privacy", "sensitive", "敏感", "隐私", "合规")):
        return "dlp_policy_query"
    if any(marker in text for marker in ("remember", "preference", "默认", "记住", "偏好")):
        return "memory_preference"
    return "general_qa"


def _extract_memory_entities(identifiers: dict[str, list[str]], text: str) -> list[str]:
    entities: list[str] = []
    for key, prefix in (("emails", "email"), ("files", "file"), ("task_ids", "task"), ("urls", "url")):
        entities.extend(f"{prefix}:{value}" for value in identifiers.get(key, [])[:8])
    for token in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}(?:\s+[A-Z][A-Za-z0-9_-]{2,})?\b", text or ""):
        if token.lower() not in {"the", "and", "api", "url"}:
            entities.append(f"name:{compact_text(token, 80)}")
    seen: set[str] = set()
    deduped: list[str] = []
    for entity in entities:
        if entity not in seen:
            deduped.append(entity)
            seen.add(entity)
    return deduped[:20]


def _build_structured_summary(question: str, answer: str, *, intent: str, risk_level: str) -> str:
    question_part = compact_text(question, 220)
    answer_part = compact_text(answer, 260)
    pieces = [f"intent={intent}", f"risk={risk_level}"]
    if question_part:
        pieces.append(f"user asked: {question_part}")
    if answer_part:
        pieces.append(f"assistant answered: {answer_part}")
    return compact_text("; ".join(pieces), 700)


def _validate_turn_memory_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": compact_text(str(schema.get("summary") or ""), 900),
        "intent": compact_text(str(schema.get("intent") or "general_qa"), 120),
        "entities": [str(item) for item in list(schema.get("entities") or [])[:20] if str(item).strip()],
        "files_uploaded": [str(item) for item in list(schema.get("files_uploaded") or [])[:12] if str(item).strip()],
        "risk_level": str(schema.get("risk_level") or "low") if str(schema.get("risk_level") or "low") in {"low", "medium", "high", "critical"} else "low",
    }


def _extract_preference_candidates(question: str, answer: str) -> list[str]:
    source = f"{question}\n{answer}"
    if not any(term in source.lower() or term in source for term in PREFERENCE_TERMS):
        return []
    candidates: list[str] = []
    for sentence in re.split(r"[。！？!?；;\n]+", source):
        cleaned = compact_text(sentence.strip(), 220)
        if cleaned and any(term in cleaned.lower() or term in cleaned for term in PREFERENCE_TERMS):
            candidates.append(cleaned)
    return candidates[:5]


def _extract_process_improvements(result: dict[str, Any]) -> list[str]:
    improvements: list[str] = []
    for failure in result.get("partial_failures") or []:
        tool_name = str(failure.get("tool_name") or "unknown_tool")
        error = compact_text(str(failure.get("error") or "unknown error"), 180)
        improvements.append(f"Tool failure to review: {tool_name}: {error}")
    termination = str(result.get("termination_reason") or "")
    if termination in {"budget_exhausted", "fatal_tool_failure", "needs_clarification"}:
        improvements.append(f"Conversation ended with {termination}; review whether routing or memory evidence was sufficient.")
    return improvements[:5]


def _active_tool_call_count(result: dict[str, Any]) -> int:
    return len([item for item in result.get("tool_calls") or [] if isinstance(item, dict)])


def write_dynamic_turn_memory(
    *,
    result: dict[str, Any],
    conversation_id: str,
    user_turn_id: str,
    assistant_turn_id: str,
    question: str,
    answer: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_hermes_dynamic_memory_store()
    session_id = str(result.get("session_id") or "")
    actor = actor_from_mapping(actor_context or result.get("actor_context") or {}, session_id=session_id, conversation_id=conversation_id)
    upload_context = dict(result.get("upload_context") or {})
    combined_text = "\n".join(
        [
            question,
            answer,
            str(upload_context.get("filename") or ""),
            _json_dumps(result.get("tool_observations") or []),
        ]
    )
    identifiers = extract_memory_identifiers(combined_text, upload_context=upload_context)
    failure_reason = _summarize_failure(result)
    user_goal = compact_text(question, 360)
    outcome = compact_text(answer, 520)
    risk_level = classify_memory_risk(combined_text)
    intent = _infer_memory_intent(result, question, answer)
    files_uploaded = identifiers["files"]
    structured_memory = _validate_turn_memory_schema(
        {
            "summary": _build_structured_summary(question, answer, intent=intent, risk_level=risk_level),
            "intent": intent,
            "entities": _extract_memory_entities(identifiers, combined_text),
            "files_uploaded": files_uploaded,
            "risk_level": risk_level,
        }
    )
    memory_scope = "session"
    if any(term in question for term in ("记住", "以后", "默认", "约定", "规则")) or any(term in question.lower() for term in ("remember", "preference", "default")):
        memory_scope = "candidate_long_term"
    if memory_scope == "candidate_long_term" and structured_memory["risk_level"] in {"medium", "high", "critical"}:
        memory_scope = "pending_long_term"

    summary_id = _new_id("memsum")
    now = _now()
    provenance = {
        "source": "turn_end",
        "intent": result.get("intent") or "",
        "termination_reason": result.get("termination_reason") or "",
        "final_answer_source": result.get("final_answer_source") or "",
        "memory_reads": result.get("memory_reads") or [],
        "tool_observation_count": len(result.get("tool_observations") or []),
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO hermes_turn_summaries (
                summary_id, session_id, conversation_id, tenant_id, user_id, workspace_id,
                user_turn_id, assistant_turn_id,
                summary, intent, entities, files_uploaded, risk_level,
                user_goal, outcome, key_files, recipients, task_ids, failure_reason,
                memory_scope, provenance, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary_id,
                session_id,
                conversation_id,
                actor.tenant_id,
                actor.user_id,
                actor.workspace_id,
                user_turn_id,
                assistant_turn_id,
                structured_memory["summary"],
                structured_memory["intent"],
                _json_dumps(structured_memory["entities"]),
                _json_dumps(structured_memory["files_uploaded"]),
                structured_memory["risk_level"],
                user_goal,
                outcome,
                _json_dumps(identifiers["files"]),
                _json_dumps(identifiers["emails"]),
                _json_dumps(identifiers["task_ids"]),
                failure_reason,
                memory_scope,
                _json_dumps(provenance),
                now,
            ),
        )

    preferences = _extract_preference_candidates(question, answer)
    preference_records = _write_user_preference_candidates(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context=actor.to_dict(),
        source_turn_ids=[user_turn_id, assistant_turn_id],
        preferences=preferences,
    )
    reflection = maybe_write_reflection_candidate(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context=actor.to_dict(),
        result=result,
        source_turn_ids=[user_turn_id, assistant_turn_id],
        durable_facts=_durable_fact_candidates(identifiers, question, answer),
        user_preferences=preferences,
        process_improvements=_extract_process_improvements(result),
    )
    compaction = maybe_compact_conversation(session_id=session_id, conversation_id=conversation_id, actor_context=actor.to_dict())
    return {
        "summary_id": summary_id,
        "memory_scope": memory_scope,
        "summary": structured_memory["summary"],
        "intent": structured_memory["intent"],
        "entities": structured_memory["entities"],
        "files_uploaded": structured_memory["files_uploaded"],
        "risk_level": structured_memory["risk_level"],
        "identifiers": identifiers,
        "preference_records": preference_records,
        "reflection_candidate": reflection,
        "compaction": compaction,
    }


def _summarize_failure(result: dict[str, Any]) -> str:
    partial_failures = result.get("partial_failures") or []
    if partial_failures:
        first = dict(partial_failures[0] or {})
        return compact_text(f"{first.get('tool_name', 'tool')}: {first.get('error', 'unknown error')}", 260)
    termination = str(result.get("termination_reason") or "")
    if termination in {"budget_exhausted", "fatal_tool_failure", "abort_with_reason"}:
        return termination
    return ""


def _durable_fact_candidates(identifiers: dict[str, list[str]], question: str, answer: str) -> list[str]:
    facts: list[str] = []
    if identifiers.get("task_ids"):
        facts.append("Related task ids: " + ", ".join(identifiers["task_ids"][:4]))
    if identifiers.get("files"):
        facts.append("Related files: " + ", ".join(identifiers["files"][:4]))
    if any(term in question for term in ("决定", "约定", "计划", "下一步")):
        facts.append(compact_text(f"User decision/context: {question}", 260))
    return facts[:5]


def _write_user_preference_candidates(
    *,
    session_id: str,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
    source_turn_ids: list[str],
    preferences: list[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    now = _now()
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    with _connect() as conn:
        for preference in preferences:
            risk = classify_memory_risk(preference)
            status = "pending" if risk in {"medium", "high"} else "active"
            fact_id = _new_id("umem")
            conn.execute(
                """
                INSERT INTO hermes_user_memory_facts (
                    fact_id, session_id, conversation_id, tenant_id, user_id, workspace_id,
                    category, content, status,
                    risk_level, source_turn_ids, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_id,
                    session_id,
                    conversation_id,
                    actor.tenant_id,
                    actor.user_id,
                    actor.workspace_id,
                    "user_preference",
                    preference,
                    status,
                    risk,
                    _json_dumps(source_turn_ids),
                    now,
                    now,
                ),
            )
            records.append({"fact_id": fact_id, "content": preference, "status": status, "risk_level": risk})
    return records


def maybe_write_reflection_candidate(
    *,
    session_id: str,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
    result: dict[str, Any],
    source_turn_ids: list[str],
    durable_facts: list[str],
    user_preferences: list[str],
    process_improvements: list[str],
) -> dict[str, Any]:
    init_hermes_dynamic_memory_store()
    tool_count = _active_tool_call_count(result)
    termination = str(result.get("termination_reason") or "")
    should_reflect = bool(durable_facts or user_preferences or process_improvements)
    should_reflect = should_reflect and (
        tool_count >= int(getattr(get_settings(), "hermes_reflection_tool_call_interval", 8))
        or termination in {"fatal_tool_failure", "budget_exhausted", "needs_clarification"}
        or bool(user_preferences)
        or bool(process_improvements)
    )
    if not should_reflect:
        return {}
    combined = "\n".join([*durable_facts, *user_preferences, *process_improvements])
    risk = classify_memory_risk(combined)
    candidate_id = _new_id("refl")
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO hermes_reflection_candidates (
                candidate_id, session_id, conversation_id, tenant_id, user_id, workspace_id,
                trigger_reason,
                durable_facts, user_preferences, process_improvements, risk_level,
                status, source_turn_ids, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                candidate_id,
                session_id,
                conversation_id,
                actor.tenant_id,
                actor.user_id,
                actor.workspace_id,
                _reflection_trigger_reason(result, tool_count),
                _json_dumps(durable_facts),
                _json_dumps(user_preferences),
                _json_dumps(process_improvements),
                risk,
                _json_dumps(source_turn_ids),
                "Controlled reflection candidate. It must not mutate tools, prompts, approval policy, or delivery policy automatically.",
                _now(),
            ),
        )
    return {
        "candidate_id": candidate_id,
        "risk_level": risk,
        "status": "pending",
        "durable_facts": durable_facts,
        "user_preferences": user_preferences,
        "process_improvements": process_improvements,
    }


def _reflection_trigger_reason(result: dict[str, Any], tool_count: int) -> str:
    termination = str(result.get("termination_reason") or "direct_answer")
    if termination in {"fatal_tool_failure", "budget_exhausted", "needs_clarification"}:
        return f"termination:{termination}"
    if result.get("partial_failures"):
        return "tool_failure"
    if tool_count:
        return f"tool_calls:{tool_count}"
    return "turn_end_candidate"


def maybe_compact_conversation(
    *,
    session_id: str,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_hermes_dynamic_memory_store()
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    turns = get_turns(conversation_id)
    threshold = int(getattr(get_settings(), "hermes_compaction_turn_threshold", 80))
    keep_turns = int(getattr(get_settings(), "hermes_compaction_keep_turns", 16))
    if len(turns) <= threshold or len(turns) <= keep_turns:
        return {}
    old_turns = turns[: -keep_turns]
    kept_turns = turns[-keep_turns:]
    if not old_turns or not kept_turns:
        return {}
    first_kept_turn_id = str(kept_turns[0].get("turn_id") or "")
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT compaction_id FROM hermes_transcript_compactions
            WHERE session_id = ? AND conversation_id = ? AND first_kept_turn_id = ?
            LIMIT 1
            """,
            (session_id, conversation_id, first_kept_turn_id),
        ).fetchone()
        if existing:
            return {"compaction_id": existing["compaction_id"], "status": "already_exists"}

    source_text = "\n".join(
        f"{turn.get('role')}: {turn.get('redacted_content') or turn.get('content') or ''}"
        for turn in old_turns
    )
    preserved = extract_memory_identifiers(source_text)
    summary = _compact_turns_deterministically(old_turns, preserved)
    compaction_id = _new_id("compact")
    source_turn_ids = [str(turn.get("turn_id") or "") for turn in old_turns if turn.get("turn_id")]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO hermes_transcript_compactions (
                compaction_id, session_id, conversation_id, tenant_id, user_id, workspace_id,
                first_kept_turn_id,
                source_turn_ids, summary, preserved_identifiers, token_estimate, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                compaction_id,
                session_id,
                conversation_id,
                actor.tenant_id,
                actor.user_id,
                actor.workspace_id,
                first_kept_turn_id,
                _json_dumps(source_turn_ids),
                summary,
                _json_dumps(preserved),
                max(1, len(source_text) // 4),
                _now(),
            ),
        )
    if summary:
        update_conversation_summary(conversation_id, compact_text(summary, 1200))
    return {
        "compaction_id": compaction_id,
        "status": "created",
        "source_turn_count": len(old_turns),
        "first_kept_turn_id": first_kept_turn_id,
        "preserved_identifiers": preserved,
    }


def _compact_turns_deterministically(turns: list[dict[str, Any]], preserved: dict[str, list[str]]) -> str:
    user_goals: list[str] = []
    outcomes: list[str] = []
    failures: list[str] = []
    for turn in turns:
        role = str(turn.get("role") or "")
        content = compact_text(str(turn.get("redacted_content") or turn.get("content") or ""), 220)
        if not content:
            continue
        if role == "user":
            user_goals.append(content)
        elif role == "assistant":
            outcomes.append(str(turn.get("answer_summary") or content))
            debug = dict(turn.get("debug_payload") or {})
            if debug.get("partial_failures"):
                failures.append(compact_text(_json_dumps(debug.get("partial_failures")), 220))
    pieces = [
        f"Compacted {len(turns)} older turns.",
        "Main user requests: " + " | ".join(user_goals[-6:]) if user_goals else "",
        "Assistant outcomes: " + " | ".join(outcomes[-6:]) if outcomes else "",
        "Failures or blockers: " + " | ".join(failures[-4:]) if failures else "",
        "Preserved identifiers: " + _json_dumps({key: value[:8] for key, value in preserved.items() if value}),
    ]
    return "\n".join(part for part in pieces if part).strip()


def get_recent_compactions(
    *,
    session_id: str,
    conversation_id: str,
    limit: int = 3,
    actor_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    init_hermes_dynamic_memory_store()
    _, actor_clauses, actor_params = _actor_filter(actor_context, session_id=session_id, conversation_id=conversation_id)
    clauses = ["session_id = ?", "conversation_id = ?", *actor_clauses]
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM hermes_transcript_compactions
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, conversation_id, *actor_params, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_structured_turn_summaries(
    *,
    session_id: str,
    conversation_id: str,
    limit: int = 6,
    actor_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    init_hermes_dynamic_memory_store()
    _, actor_clauses, actor_params = _actor_filter(actor_context, session_id=session_id, conversation_id=conversation_id)
    clauses = ["session_id = ?", "conversation_id = ?", *actor_clauses]
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM hermes_turn_summaries
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, conversation_id, *actor_params, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_user_memory_context(
    *,
    session_id: str,
    conversation_id: str = "",
    limit: int = 8,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_hermes_dynamic_memory_store()
    actor, actor_clauses, actor_params = _actor_filter(actor_context, session_id=session_id, conversation_id=conversation_id)
    fact_clauses = ["session_id = ?", "status = 'active'", *actor_clauses]
    pending_fact_clauses = ["session_id = ?", "status = 'pending'", *actor_clauses]
    reflection_clauses = ["session_id = ?", "conversation_id = ?", "status = 'approved'", *actor_clauses]
    pending_reflection_clauses = ["session_id = ?", "conversation_id = ?", "status = 'pending'", *actor_clauses]
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM hermes_user_memory_facts
            WHERE {' AND '.join(fact_clauses)}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (session_id, *actor_params, limit),
        ).fetchall()
        pending_fact_count = int(
            conn.execute(
                f"SELECT COUNT(*) AS count FROM hermes_user_memory_facts WHERE {' AND '.join(pending_fact_clauses)}",
                (session_id, *actor_params),
            ).fetchone()["count"]
        )
        reflection_rows = conn.execute(
            f"""
            SELECT * FROM hermes_reflection_candidates
            WHERE {' AND '.join(reflection_clauses)}
            ORDER BY created_at DESC
            LIMIT 4
            """,
            (session_id, conversation_id, *actor_params),
        ).fetchall()
        pending_reflection_count = int(
            conn.execute(
                f"SELECT COUNT(*) AS count FROM hermes_reflection_candidates WHERE {' AND '.join(pending_reflection_clauses)}",
                (session_id, conversation_id, *actor_params),
            ).fetchone()["count"]
        )
    facts = [_row_to_dict(row) for row in rows]
    reflections = [_row_to_dict(row) for row in reflection_rows]
    summary_lines: list[str] = []
    if facts:
        summary_lines.append("Active user memory:")
        summary_lines.extend(f"- {item['content']}" for item in facts[:5])
    if reflections:
        summary_lines.append("Approved reflection notes:")
        for item in reflections[:3]:
            durable = item.get("durable_facts") or []
            prefs = item.get("user_preferences") or []
            improvements = item.get("process_improvements") or []
            preview = compact_text("; ".join([*durable, *prefs, *improvements]), 220)
            if preview:
                summary_lines.append(f"- {preview} (risk={item.get('risk_level', 'low')})")
    return {
        "summary": "\n".join(summary_lines).strip(),
        "facts": facts,
        "reflection_candidates": reflections,
        "hits": len(facts) + len(reflections),
        "review_queue": {
            "pending_facts": pending_fact_count,
            "pending_reflections": pending_reflection_count,
            "policy": "pending candidates are review-only and are not exposed as behavioral memory",
        },
        "provenance": {
            "source": "hermes_dynamic_memory",
            "active_facts": len(facts),
            "approved_reflections": len(reflections),
            "pending_facts": pending_fact_count,
            "pending_reflections": pending_reflection_count,
            "memory_boundary": "review_pending_excluded_from_behavior",
            "actor_context": actor.to_dict(),
        },
    }


def list_memory_review_candidates(
    *,
    session_id: str = "",
    conversation_id: str = "",
    status: str = "pending",
    limit: int = 50,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_hermes_dynamic_memory_store()
    bounded_limit = max(1, min(int(limit or 50), 200))
    actor, actor_clauses, actor_params = _actor_filter(actor_context, session_id=session_id, conversation_id=conversation_id)
    fact_clauses = ["status = ?"]
    fact_params: list[Any] = [status]
    reflection_clauses = ["status = ?"]
    reflection_params: list[Any] = [status]
    if session_id:
        fact_clauses.append("session_id = ?")
        fact_params.append(session_id)
        reflection_clauses.append("session_id = ?")
        reflection_params.append(session_id)
    if conversation_id:
        fact_clauses.append("conversation_id = ?")
        fact_params.append(conversation_id)
        reflection_clauses.append("conversation_id = ?")
        reflection_params.append(conversation_id)
    fact_clauses.extend(actor_clauses)
    fact_params.extend(actor_params)
    reflection_clauses.extend(actor_clauses)
    reflection_params.extend(actor_params)
    with _connect() as conn:
        fact_rows = conn.execute(
            f"""
            SELECT * FROM hermes_user_memory_facts
            WHERE {' AND '.join(fact_clauses)}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*fact_params, bounded_limit),
        ).fetchall()
        reflection_rows = conn.execute(
            f"""
            SELECT * FROM hermes_reflection_candidates
            WHERE {' AND '.join(reflection_clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*reflection_params, bounded_limit),
        ).fetchall()
    facts = [_row_to_dict(row) for row in fact_rows]
    reflections = [_row_to_dict(row) for row in reflection_rows]
    return {
        "user_memory_facts": facts,
        "reflection_candidates": reflections,
        "counts": {
            "user_memory_facts": len(facts),
            "reflection_candidates": len(reflections),
        },
        "actor_context": actor.to_dict(),
    }


def review_user_memory_fact(
    *,
    fact_id: str,
    status: str,
    reviewer: str = "",
    reason: str = "",
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in USER_MEMORY_REVIEW_STATUSES:
        raise ValueError("status must be one of active, rejected, pending, expired")
    init_hermes_dynamic_memory_store()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM hermes_user_memory_facts WHERE fact_id = ?", (fact_id,)).fetchone()
        if not row:
            return {}
        if not _row_matches_actor(row, actor_context):
            return {}
        previous_status = str(row["status"] or "")
        reviewed_at = _now()
        conn.execute(
            """
            UPDATE hermes_user_memory_facts
            SET status = ?, reviewed_by = ?, review_reason = ?, reviewed_at = ?, updated_at = ?
            WHERE fact_id = ?
            """,
            (status, reviewer, reason, reviewed_at, reviewed_at, fact_id),
        )
        audit = _write_memory_review_audit(
            conn,
            target_type="user_memory_fact",
            target_id=fact_id,
            previous_status=previous_status,
            new_status=status,
            reviewer=reviewer,
            reason=reason,
            actor_context=actor_context,
            session_id=str(row["session_id"] or ""),
            conversation_id=str(row["conversation_id"] or ""),
        )
        updated = conn.execute("SELECT * FROM hermes_user_memory_facts WHERE fact_id = ?", (fact_id,)).fetchone()
    item = _row_to_dict(updated) if updated else {}
    item["review"] = {"reviewer": reviewer, "reason": reason, "audit": audit}
    return item


def review_reflection_candidate(
    *,
    candidate_id: str,
    status: str,
    reviewer: str = "",
    reason: str = "",
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in REFLECTION_REVIEW_STATUSES:
        raise ValueError("status must be one of approved, rejected, pending, expired")
    init_hermes_dynamic_memory_store()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM hermes_reflection_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        if not row:
            return {}
        if not _row_matches_actor(row, actor_context):
            return {}
        previous_status = str(row["status"] or "")
        existing_notes = str(row["notes"] or "")
        review_note = compact_text(f"reviewer={reviewer}; status={status}; reason={reason}", 500)
        notes = "\n".join(part for part in [existing_notes, review_note] if part).strip()
        reviewed_at = _now()
        conn.execute(
            """
            UPDATE hermes_reflection_candidates
            SET status = ?, notes = ?, reviewed_by = ?, review_reason = ?, reviewed_at = ?
            WHERE candidate_id = ?
            """,
            (status, notes, reviewer, reason, reviewed_at, candidate_id),
        )
        audit = _write_memory_review_audit(
            conn,
            target_type="reflection_candidate",
            target_id=candidate_id,
            previous_status=previous_status,
            new_status=status,
            reviewer=reviewer,
            reason=reason,
            actor_context=actor_context,
            session_id=str(row["session_id"] or ""),
            conversation_id=str(row["conversation_id"] or ""),
        )
        updated = conn.execute("SELECT * FROM hermes_reflection_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
    item = _row_to_dict(updated) if updated else {}
    item["review"] = {"reviewer": reviewer, "reason": reason, "audit": audit}
    return item


def list_memory_review_audit(
    *,
    target_type: str = "",
    target_id: str = "",
    limit: int = 50,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    init_hermes_dynamic_memory_store()
    bounded_limit = max(1, min(int(limit or 50), 200))
    actor, actor_clauses, actor_params = _actor_filter(actor_context)
    clauses: list[str] = [*actor_clauses]
    params: list[Any] = [*actor_params]
    if target_type:
        clauses.append("target_type = ?")
        params.append(target_type)
    if target_id:
        clauses.append("target_id = ?")
        params.append(target_id)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM hermes_memory_review_audit
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, bounded_limit),
        ).fetchall()
    return {
        "audit_events": [_row_to_dict(row) for row in rows],
        "counts": {"audit_events": len(rows)},
        "actor_context": actor.to_dict(),
    }


def get_workspace_memory_context(question: str, *, top_k: int = 6, actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = {}
    if actor_context:
        filters["actor_context"] = dict(actor_context)
    result = search_workspace_memory_with_plan(question, top_k=top_k, filters=filters)
    hits = list(result.get("hits") or [])
    summary = "\n".join(
        f"- {item.get('file_path', '')} :: {item.get('title', '')}: {item.get('snippet', '')}"
        for item in hits
    )
    return {
        "summary": compact_text(summary, 900),
        "hits": len(hits),
        "items": hits,
        "provenance": {
            "source": "workspace_memory",
            "top_k": top_k,
            "retrieval_plan": result.get("retrieval_plan") or {},
            "diagnostics": result.get("diagnostics") or {},
            "actor_context": dict(actor_context or {}),
        },
    }
