from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from typing import Any

from langchain_core.documents import Document

from app.conversation_store import get_related_merged_conversations, get_turns
from app.actor_context import actor_from_mapping
from app.vectorstore import get_conversation_memory_vectorstore, upsert_conversation_memory_documents


FOLLOW_UP_HINTS = ("之前", "上次", "刚才", "我们讨论过", "合并", "历史", "前面", "remember", "previous", "earlier")


@dataclass(slots=True)
class MemoryRetrievalPlan:
    is_follow_up: bool
    memory_strategy: str
    turn_top_k: int = 3
    merged_top_k: int = 2
    entity_anchors: list[str] = field(default_factory=list)
    expansion_reason: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_text(text: str, limit: int = 1200) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit] + ("..." if len(cleaned) > limit else "")


def infer_title(text: str, fallback: str = "新对话") -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return fallback
    return cleaned[:28] + ("..." if len(cleaned) > 28 else "")


def build_turn_summary_document(
    *,
    session_id: str,
    conversation_id: str,
    user_turn_id: str,
    assistant_turn_id: str,
    question: str,
    safe_question: str,
    answer: str,
    answer_summary: str,
    intent: str,
    citations: list[dict[str, Any]],
    upload_context: dict[str, Any] | None = None,
    dynamic_memory: dict[str, Any] | None = None,
    actor_context: dict[str, Any] | None = None,
) -> Document:
    source_turn_ids = f"{user_turn_id},{assistant_turn_id}"
    citation_terms = []
    for citation in citations[:4]:
        chunk_id = citation.get("chunk_id", "")
        source_type = citation.get("source_type", "")
        if chunk_id or source_type:
            citation_terms.append(f"{source_type}:{chunk_id}")

    upload_context = dict(upload_context or {})
    upload_lines: list[str] = []
    if upload_context.get("content_available"):
        upload_lines.append(
            "upload_context: "
            f"kind={upload_context.get('kind', 'unknown')} "
            f"file={upload_context.get('filename', '')} "
            f"parse_status={upload_context.get('parse_status', 'unknown')}"
        )
        if upload_context.get("summary"):
            upload_lines.append(f"upload_summary: {upload_context.get('summary', '')}")
        snippets = [str(item).strip() for item in upload_context.get("key_snippets") or [] if str(item).strip()]
        if snippets:
            upload_lines.append(f"upload_snippets: {' | '.join(snippets[:3])}")

    dynamic_memory = dict(dynamic_memory or {})
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id)
    dynamic_lines: list[str] = []
    if dynamic_memory:
        for key in ("summary", "intent", "risk_level", "user_goal", "outcome", "failure_reason", "memory_scope"):
            value = str(dynamic_memory.get(key) or "").strip()
            if value:
                dynamic_lines.append(f"{key}: {compact_text(value, 360)}")
        for key in ("entities", "files_uploaded", "key_files", "recipients", "task_ids"):
            values = [str(item).strip() for item in dynamic_memory.get(key) or [] if str(item).strip()]
            if values:
                dynamic_lines.append(f"{key}: {', '.join(values[:6])}")
    memory_scope = str(dynamic_memory.get("memory_scope") or "session") if dynamic_memory else "session"
    memory_intent = str(dynamic_memory.get("intent") or intent or "conversation") if dynamic_memory else (intent or "conversation")
    memory_risk_level = str(dynamic_memory.get("risk_level") or "low") if dynamic_memory else "low"
    memory_entities = ",".join(str(item) for item in list(dynamic_memory.get("entities") or [])[:12]) if dynamic_memory else ""
    memory_files = ",".join(str(item) for item in list(dynamic_memory.get("files_uploaded") or [])[:12]) if dynamic_memory else ""

    page_content = "\n".join(
        [
            f"question: {safe_question or question}",
            f"answer_summary: {answer_summary or compact_text(answer)}",
            f"intent: {intent}",
            f"source_turn_ids: {source_turn_ids}",
            f"citations: {', '.join(citation_terms) if citation_terms else 'none'}",
            *upload_lines,
            *dynamic_lines,
        ]
    )
    return Document(
        page_content=page_content,
        metadata={
            "chunk_id": f"conversation-{conversation_id}-{assistant_turn_id}",
            "domain": "conversation",
            "source_type": "turn_summary",
            "session_id": session_id,
            "conversation_id": conversation_id,
            "merged_conversation_id": "",
            "source_conversation_ids": conversation_id,
            "source_turn_ids": source_turn_ids,
            "topic": intent or "conversation",
            "memory_scope": memory_scope,
            "memory_intent": memory_intent,
            "memory_risk_level": memory_risk_level,
            "memory_entities": memory_entities,
            "files_uploaded": memory_files,
            "created_at": _now(),
            "is_runtime_memory": "true",
            "tenant_id": actor.tenant_id,
            "user_id": actor.user_id,
            "workspace_id": actor.workspace_id,
        },
    )


def build_merged_summary_document(
    *,
    session_id: str,
    merged_conversation_id: str,
    source_conversation_ids: list[str],
    source_turn_ids: list[str],
    summary: str,
    topics: list[str],
    actor_context: dict[str, Any] | None = None,
) -> Document:
    actor = actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=merged_conversation_id)
    return Document(
        page_content="\n".join(
            [
                f"merged_summary: {summary}",
                f"topics: {', '.join(topics) if topics else 'conversation_merge'}",
                f"source_conversations: {', '.join(source_conversation_ids)}",
                f"source_turn_ids: {', '.join(source_turn_ids)}",
            ]
        ),
        metadata={
            "chunk_id": f"conversation-{merged_conversation_id}-merged-summary",
            "domain": "conversation",
            "source_type": "merged_summary",
            "session_id": session_id,
            "conversation_id": merged_conversation_id,
            "merged_conversation_id": merged_conversation_id,
            "source_conversation_ids": ",".join(source_conversation_ids),
            "source_turn_ids": ",".join(source_turn_ids),
            "topic": ",".join(topics) if topics else "conversation_merge",
            "created_at": _now(),
            "is_runtime_memory": "true",
            "tenant_id": actor.tenant_id,
            "user_id": actor.user_id,
            "workspace_id": actor.workspace_id,
        },
    )


def write_turn_summary(**kwargs: Any) -> bool:
    doc = build_turn_summary_document(**kwargs)
    return upsert_conversation_memory_documents([doc]) == 1


def write_merged_summary(**kwargs: Any) -> bool:
    doc = build_merged_summary_document(**kwargs)
    return upsert_conversation_memory_documents([doc]) == 1


def is_memory_follow_up(question: str) -> bool:
    lowered = question.lower()
    return any(hint in question for hint in FOLLOW_UP_HINTS) or any(hint in lowered for hint in FOLLOW_UP_HINTS)


def extract_memory_entity_anchors(question: str) -> list[str]:
    text = question or ""
    anchors: list[str] = []
    anchors.extend(re.findall(r"[\w./-]+\.(?:pdf|docx?|xlsx?|csv|md|py|json|toml|yaml|yml)", text, flags=re.IGNORECASE))
    anchors.extend(re.findall(r"(?:task|ticket|issue|pr|doc|file)[-_:# ]+[A-Za-z0-9._-]+", text, flags=re.IGNORECASE))
    for quoted in re.findall(r"[\"'“”‘’`]([^\"'“”‘’`]{2,40})[\"'“”‘’`]", text):
        anchors.append(quoted.strip())
    for token in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text):
        anchors.append(token.strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for anchor in anchors:
        normalized = " ".join(anchor.split())
        key = normalized.lower()
        if normalized and key not in seen:
            deduped.append(normalized)
            seen.add(key)
    return deduped[:8]


def build_memory_retrieval_plan(question: str) -> MemoryRetrievalPlan:
    follow_up = is_memory_follow_up(question)
    anchors = extract_memory_entity_anchors(question)
    if follow_up and anchors:
        return MemoryRetrievalPlan(
            is_follow_up=True,
            memory_strategy="follow_up_with_entity_anchors",
            turn_top_k=4,
            merged_top_k=2,
            entity_anchors=anchors,
        )
    if follow_up:
        return MemoryRetrievalPlan(
            is_follow_up=True,
            memory_strategy="follow_up_recent_first",
            turn_top_k=4,
            merged_top_k=2,
            entity_anchors=anchors,
        )
    if anchors:
        return MemoryRetrievalPlan(
            is_follow_up=False,
            memory_strategy="entity_anchor_lookup",
            turn_top_k=3,
            merged_top_k=2,
            entity_anchors=anchors,
        )
    return MemoryRetrievalPlan(
        is_follow_up=False,
        memory_strategy="standard_recent_and_summary",
        turn_top_k=3,
        merged_top_k=2,
        entity_anchors=[],
    )


def _filter_by_source_conversation(docs: list[Document], conversation_id: str) -> list[Document]:
    filtered = []
    for doc in docs:
        source_ids = str(doc.metadata.get("source_conversation_ids", "")).split(",")
        if conversation_id in source_ids or str(doc.metadata.get("conversation_id", "")) == conversation_id:
            filtered.append(doc)
    return filtered


def _actor_filter_clauses(actor_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not actor_context:
        return []
    actor = actor_from_mapping(actor_context)
    if actor.is_local_dev:
        return []
    return [
        {"tenant_id": {"$eq": actor.tenant_id}},
        {"user_id": {"$eq": actor.user_id}},
        {"workspace_id": {"$eq": actor.workspace_id}},
    ]


def retrieve_turn_memory(
    session_id: str,
    conversation_id: str,
    question: str,
    top_k: int = 3,
    actor_context: dict[str, Any] | None = None,
) -> list[Document]:
    vectorstore = get_conversation_memory_vectorstore()
    filt = {
        "$and": [
            {"domain": {"$eq": "conversation"}},
            {"source_type": {"$eq": "turn_summary"}},
            {"session_id": {"$eq": session_id}},
            {"conversation_id": {"$eq": conversation_id}},
            *_actor_filter_clauses(actor_context),
        ]
    }
    return vectorstore.similarity_search(question, k=top_k, filter=filt)


def retrieve_merged_memory(
    session_id: str,
    conversation_id: str,
    question: str,
    top_k: int = 2,
    actor_context: dict[str, Any] | None = None,
) -> list[Document]:
    related = get_related_merged_conversations(session_id, conversation_id)
    related_ids = {item["conversation_id"] for item in related}
    if not related_ids:
        return []

    vectorstore = get_conversation_memory_vectorstore()
    filt = {
        "$and": [
            {"domain": {"$eq": "conversation"}},
            {"source_type": {"$eq": "merged_summary"}},
            {"session_id": {"$eq": session_id}},
            *_actor_filter_clauses(actor_context),
        ]
    }
    docs = vectorstore.similarity_search(question, k=max(top_k * 3, 6), filter=filt)
    return [doc for doc in docs if str(doc.metadata.get("merged_conversation_id", "")) in related_ids][:top_k]


def recent_turns_context(conversation_id: str, limit: int = 6) -> str:
    turns = get_turns(conversation_id, limit=limit)
    if not turns:
        return ""
    parts = []
    for turn in turns:
        content = turn.get("redacted_content") or turn.get("content", "")
        parts.append(f"{turn.get('role')}: {compact_text(content, 500)}")
    return "\n".join(parts)


def docs_context(docs: list[Document], label: str) -> str:
    if not docs:
        return ""
    parts = [f"{label}:"]
    for index, doc in enumerate(docs, start=1):
        parts.append(
            "\n".join(
                [
                    f"[{index}] chunk={doc.metadata.get('chunk_id', '')}",
                    f"source_turn_ids={doc.metadata.get('source_turn_ids', '')}",
                    doc.page_content,
                ]
            )
        )
    return "\n\n".join(parts)


def build_memory_context(
    *,
    session_id: str,
    conversation_id: str,
    question: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recent_context = recent_turns_context(conversation_id, limit=6)
    turn_docs: list[Document] = []
    merged_docs: list[Document] = []
    plan = build_memory_retrieval_plan(question)
    retrieval_query = question
    if plan.entity_anchors:
        retrieval_query = f"{question}\nanchors: {', '.join(plan.entity_anchors)}"

    try:
        turn_docs = retrieve_turn_memory(session_id, conversation_id, retrieval_query, top_k=plan.turn_top_k, actor_context=actor_context)
        merged_docs = retrieve_merged_memory(session_id, conversation_id, retrieval_query, top_k=plan.merged_top_k, actor_context=actor_context)
        if plan.is_follow_up and not merged_docs:
            plan.merged_top_k = 4
            plan.expansion_reason = "follow_up_summary_not_found"
            merged_docs = retrieve_merged_memory(session_id, conversation_id, retrieval_query, top_k=plan.merged_top_k, actor_context=actor_context)
    except Exception:
        turn_docs = []
        merged_docs = []
        plan.expansion_reason = "memory_retrieval_error"

    context_sections = []
    if recent_context:
        context_sections.append(f"Recent turns:\n{recent_context}")
    turn_context = docs_context(turn_docs, "Retrieved turn summaries")
    if turn_context:
        context_sections.append(turn_context)
    merged_context = docs_context(merged_docs, "Retrieved merged summaries")
    if merged_context:
        context_sections.append(merged_context)

    return {
        "memory_context": "\n\n".join(context_sections),
        "memory_hits": len(turn_docs),
        "merged_memory_hits": len(merged_docs),
        "turn_memory": [
            {
                "chunk_id": doc.metadata.get("chunk_id", ""),
                "source_turn_ids": doc.metadata.get("source_turn_ids", ""),
                "snippet": doc.page_content[:320],
            }
            for doc in turn_docs
        ],
        "merged_memory": [
            {
                "chunk_id": doc.metadata.get("chunk_id", ""),
                "merged_conversation_id": doc.metadata.get("merged_conversation_id", ""),
                "source_conversation_ids": doc.metadata.get("source_conversation_ids", ""),
                "source_turn_ids": doc.metadata.get("source_turn_ids", ""),
                "snippet": doc.page_content[:320],
            }
            for doc in merged_docs
        ],
        "memory_retrieval_plan": plan.to_dict(),
        "is_follow_up": plan.is_follow_up,
        "memory_strategy": plan.memory_strategy,
        "entity_anchors": list(plan.entity_anchors),
        "turn_top_k": plan.turn_top_k,
        "merged_top_k": plan.merged_top_k,
        "expansion_reason": plan.expansion_reason,
        "actor_context": actor_from_mapping(actor_context or {}, session_id=session_id, conversation_id=conversation_id).to_dict(),
    }
