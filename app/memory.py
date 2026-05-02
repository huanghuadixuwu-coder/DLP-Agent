from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.documents import Document


def build_conversation_summary_document(
    *,
    session_id: str,
    problem_id: str,
    problem_title: str,
    question: str,
    final_answer: str,
    query_type: str,
    turn_index: int,
    citations: list[dict[str, str]],
) -> Document:
    key_terms = []
    for citation in citations[:3]:
        source_type = citation.get("source_type", "")
        chunk_id = citation.get("chunk_id", "")
        if source_type or chunk_id:
            key_terms.append(f"{source_type}:{chunk_id}")

    summary_text = "\n".join(
        [
            f"question: {question}",
            f"answer_summary: {final_answer[:1200]}",
            f"query_type: {query_type}",
            f"key_terms: {', '.join(key_terms) if key_terms else 'none'}",
        ]
    )

    created_at = datetime.now(timezone.utc).isoformat()
    return Document(
        page_content=summary_text,
        metadata={
            "problem_id": problem_id,
            "problem_title": problem_title,
            "topic": "conversation-memory",
            "difficulty": "runtime",
            "language": "text",
            "tags": "conversation-summary",
            "source_type": "conversation_summary",
            "chunk_level": "explanation",
            "chunk_id": f"{session_id}-{problem_id}-turn-{turn_index}",
            "session_id": session_id,
            "turn_index": str(turn_index),
            "question_type": query_type,
            "created_at": created_at,
            "is_runtime_memory": "true",
        },
    )
