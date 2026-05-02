from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.documents import Document

from app.conversation_store import get_related_merged_conversations, get_turns
from app.vectorstore import get_vectorstore, upsert_documents


FOLLOW_UP_HINTS = ("之前", "上次", "刚才", "我们讨论过", "合并", "历史", "前面", "remember", "previous", "earlier")


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
) -> Document:
    source_turn_ids = f"{user_turn_id},{assistant_turn_id}"
    citation_terms = []
    for citation in citations[:4]:
        chunk_id = citation.get("chunk_id", "")
        source_type = citation.get("source_type", "")
        if chunk_id or source_type:
            citation_terms.append(f"{source_type}:{chunk_id}")

    page_content = "\n".join(
        [
            f"question: {safe_question or question}",
            f"answer_summary: {answer_summary or compact_text(answer)}",
            f"intent: {intent}",
            f"source_turn_ids: {source_turn_ids}",
            f"citations: {', '.join(citation_terms) if citation_terms else 'none'}",
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
            "created_at": _now(),
            "is_runtime_memory": "true",
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
) -> Document:
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
        },
    )


def write_turn_summary(**kwargs: Any) -> bool:
    doc = build_turn_summary_document(**kwargs)
    return upsert_documents([doc]) == 1


def write_merged_summary(**kwargs: Any) -> bool:
    doc = build_merged_summary_document(**kwargs)
    return upsert_documents([doc]) == 1


def is_memory_follow_up(question: str) -> bool:
    lowered = question.lower()
    return any(hint in question for hint in FOLLOW_UP_HINTS) or any(hint in lowered for hint in FOLLOW_UP_HINTS)


def _filter_by_source_conversation(docs: list[Document], conversation_id: str) -> list[Document]:
    filtered = []
    for doc in docs:
        source_ids = str(doc.metadata.get("source_conversation_ids", "")).split(",")
        if conversation_id in source_ids or str(doc.metadata.get("conversation_id", "")) == conversation_id:
            filtered.append(doc)
    return filtered


def retrieve_turn_memory(session_id: str, conversation_id: str, question: str, top_k: int = 3) -> list[Document]:
    vectorstore = get_vectorstore()
    filt = {
        "$and": [
            {"domain": {"$eq": "conversation"}},
            {"source_type": {"$eq": "turn_summary"}},
            {"session_id": {"$eq": session_id}},
            {"conversation_id": {"$eq": conversation_id}},
        ]
    }
    return vectorstore.similarity_search(question, k=top_k, filter=filt)


def retrieve_merged_memory(session_id: str, conversation_id: str, question: str, top_k: int = 2) -> list[Document]:
    related = get_related_merged_conversations(session_id, conversation_id)
    related_ids = {item["conversation_id"] for item in related}
    if not related_ids:
        return []

    vectorstore = get_vectorstore()
    filt = {
        "$and": [
            {"domain": {"$eq": "conversation"}},
            {"source_type": {"$eq": "merged_summary"}},
            {"session_id": {"$eq": session_id}},
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
) -> dict[str, Any]:
    recent_context = recent_turns_context(conversation_id, limit=6)
    turn_docs: list[Document] = []
    merged_docs: list[Document] = []

    try:
        turn_top_k = 5 if is_memory_follow_up(question) else 3
        merged_top_k = 3 if is_memory_follow_up(question) else 2
        turn_docs = retrieve_turn_memory(session_id, conversation_id, question, top_k=turn_top_k)
        merged_docs = retrieve_merged_memory(session_id, conversation_id, question, top_k=merged_top_k)
    except Exception:
        turn_docs = []
        merged_docs = []

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
    }
