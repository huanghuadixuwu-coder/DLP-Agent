from __future__ import annotations

from dataclasses import asdict

from app.enterprise_rag.core.answer_composer import compose_enterprise_answer
from app.enterprise_rag.core.query_planner import build_retrieval_plan
from app.enterprise_rag.core.retrieval_orchestrator import retrieve_evidence
from app.hermes_memory import build_runtime_context_bundle


def answer_enterprise_question(
    question: str,
    *,
    source_types: list[str] | None = None,
    top_k: int = 8,
    session_id: str = "",
    conversation_id: str = "",
) -> dict:
    plan = build_retrieval_plan(question, source_types=source_types, top_k=top_k)
    context_bundle = (
        build_runtime_context_bundle(session_id=session_id, conversation_id=conversation_id, question=question)
        if session_id and conversation_id
        else {
            "context_text": "",
            "context_sources": [],
            "workspace_memory_hits": 0,
            "transcript_hits": 0,
            "user_model_used": False,
            "memory_retrieval_hits": 0,
            "workspace_memory": [],
            "transcript_entries": [],
            "legacy_memory": {},
        }
    )
    evidence = retrieve_evidence(plan)
    answer = compose_enterprise_answer(
        question,
        evidence,
        question_type=plan.question_type,
        context_text=str(context_bundle.get("context_text") or ""),
        context_sources=list(context_bundle.get("context_sources") or []),
        workspace_memory_hits=int(context_bundle.get("workspace_memory_hits", 0)),
        transcript_hits=int(context_bundle.get("transcript_hits", 0)),
        user_model_used=bool(context_bundle.get("user_model_used", False)),
    )
    return {
        "answer": answer.answer,
        "citations": [asdict(item) for item in answer.citations],
        "supporting_doc_ids": answer.supporting_doc_ids,
        "missing_evidence": answer.missing_evidence,
        "confidence": answer.confidence,
        "supporting_facts": answer.supporting_facts,
        "supporting_fact_details": [asdict(item) for item in answer.supporting_fact_details],
        "canonical_facts": [asdict(item) for item in answer.canonical_facts],
        "retrieval_stage_debug": answer.retrieval_stage_debug,
        "rerank_debug": answer.rerank_debug,
        "evidence_fact_hits": answer.evidence_fact_hits,
        "answer_debug": answer.answer_debug,
        "context_sources": answer.context_sources,
        "workspace_memory_hits": answer.workspace_memory_hits,
        "transcript_hits": answer.transcript_hits,
        "user_model_used": answer.user_model_used,
        "memory_context": context_bundle,
        "retrieval_plan": asdict(plan),
    }
