from __future__ import annotations

from typing import Any

from langchain_core.documents import Document

from app.vectorstore import get_vectorstore


FOLLOW_UP_TOKENS = [
    "刚才",
    "刚刚",
    "上面",
    "前面",
    "这个",
    "那个",
    "它",
    "这些",
    "那些",
    "你说的",
    "所说的",
    "边界情况指什么",
]


def classify_query(question: str) -> str:
    lowered = question.lower()
    if any(token in lowered for token in ["复杂度", "time complexity", "space complexity"]):
        return "complexity"
    if any(token in lowered for token in ["为什么", "why", "数据结构", "hash", "stack", "dp", "树"]):
        return "data_structure_reason"
    if any(token in lowered for token in ["哪一行", "loop", "循环", "这段代码", "局部"]):
        return "code_detail"
    if any(token in lowered for token in ["分步骤", "逐步", "完整讲解", "step by step"]):
        return "step_by_step"
    return "idea"


def is_follow_up_question(question: str) -> bool:
    lowered = question.lower()
    return any(token in question for token in FOLLOW_UP_TOKENS) or any(token in lowered for token in ["that", "those", "earlier", "previous"])


def retrieval_filter(problem_id: str, query_type: str) -> dict[str, Any] | None:
    source_type_map = {
        "complexity": ["editor_note", "code_solution", "algo_background"],
        "data_structure_reason": ["editor_note", "algo_background", "code_solution"],
        "code_detail": ["code_solution", "editor_note"],
        "step_by_step": ["problem_statement", "editor_note", "code_solution", "algo_background"],
        "idea": ["problem_statement", "editor_note", "code_solution"],
    }
    allowed = source_type_map.get(query_type, source_type_map["idea"])
    return {
        "$and": [
            {
                "$or": [
                    {"problem_id": {"$eq": problem_id}},
                    {"problem_id": {"$eq": "shared"}},
                ]
            },
            {"source_type": {"$in": allowed}},
        ]
    }


def runtime_memory_filter(session_id: str, problem_id: str) -> dict[str, Any]:
    return {
        "$and": [
            {"problem_id": {"$eq": problem_id}},
            {"session_id": {"$eq": session_id}},
            {"source_type": {"$eq": "conversation_summary"}},
        ]
    }


def retrieve_documents(problem_id: str, question: str, query_type: str, top_k: int = 5) -> list[Document]:
    vectorstore = get_vectorstore()
    filt = retrieval_filter(problem_id, query_type)
    return vectorstore.similarity_search(question, k=top_k, filter=filt)


def retrieve_conversation_summaries(session_id: str, problem_id: str, question: str, top_k: int = 2) -> list[Document]:
    vectorstore = get_vectorstore()
    filt = runtime_memory_filter(session_id, problem_id)
    return vectorstore.similarity_search(question, k=top_k, filter=filt)


def build_context_block(documents: list[Document]) -> str:
    parts: list[str] = []
    for index, doc in enumerate(documents, start=1):
        source_type = doc.metadata.get("source_type", "unknown")
        title = doc.metadata.get("problem_title", "unknown")
        chunk_id = doc.metadata.get("chunk_id", "unknown")
        parts.append(f"[{index}] type={source_type} title={title} chunk={chunk_id}\n{doc.page_content}")
    return "\n\n".join(parts)


def build_recent_turns_block(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return ""
    parts: list[str] = []
    for turn in turns:
        parts.append(
            "\n".join(
                [
                    f"[turn {turn.get('turn_index', '?')}] question: {turn.get('question', '')}",
                    f"answer_summary: {turn.get('final_answer') or turn.get('answer', '')}",
                    f"query_type: {turn.get('query_type', '')}",
                ]
            )
        )
    return "\n\n".join(parts)


def merge_context_sources(recent_turns_block: str, problem_context_block: str, memory_context_block: str) -> str:
    sections: list[str] = []
    if recent_turns_block:
        sections.append(f"Recent conversation memory:\n{recent_turns_block}")
    if problem_context_block:
        sections.append(f"Problem knowledge:\n{problem_context_block}")
    if memory_context_block:
        sections.append(f"Retrieved conversation summaries:\n{memory_context_block}")
    return "\n\n".join(sections)
