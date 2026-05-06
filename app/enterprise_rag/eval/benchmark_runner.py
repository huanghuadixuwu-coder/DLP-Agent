from __future__ import annotations

from app.enterprise_rag.core.service import answer_enterprise_question
from app.enterprise_rag.eval.metrics import answer_fact_coverage, doc_recall, evidence_fact_coverage, supporting_fact_hits
from app.enterprise_rag.ingestion.question_loader import load_questions


def run_benchmark_sample(*, questions_path: str | None = None, limit: int = 20, top_k: int = 8) -> dict:
    cases = []
    for question in load_questions(local_path=questions_path, limit=limit):
        result = answer_enterprise_question(question.question, source_types=question.source_types, top_k=top_k)
        fact_hits = supporting_fact_hits(question.answer_facts, result.get("supporting_facts") or [])
        cases.append(
            {
                "question_id": question.question_id,
                "question_type": question.question_type,
                "question": question.question,
                "expected_doc_ids": question.expected_doc_ids,
                "actual_doc_ids": result["supporting_doc_ids"],
                "doc_recall": doc_recall(question.expected_doc_ids, result["supporting_doc_ids"]),
                "evidence_fact_coverage": evidence_fact_coverage(question.answer_facts, result.get("supporting_facts") or []),
                "answer_fact_coverage": answer_fact_coverage(question.answer_facts, result["answer"]),
                "missing_evidence": result["missing_evidence"],
                "supporting_fact_hits": fact_hits,
                "answer_missing_facts": [fact for fact in question.answer_facts if fact not in fact_hits and fact.lower() not in (result["answer"] or "").lower()],
                "top_chunk_ids": [item.get("chunk_id") for item in (result.get("citations") or [])],
                "supporting_facts": result.get("supporting_facts") or [],
                "canonical_facts": [item.get("normalized_fact") for item in (result.get("canonical_facts") or [])],
                "core_facts": [item.get("normalized_fact") for item in (((result.get("answer_debug") or {}).get("core_facts")) or [])],
                "secondary_facts": [item.get("normalized_fact") for item in (((result.get("answer_debug") or {}).get("secondary_facts")) or [])],
                "answer_intent": (result.get("answer_debug") or {}).get("answer_intent") or "",
                "classifier_source": (result.get("answer_debug") or {}).get("classifier_source") or "",
                "classifier_confidence": float((result.get("answer_debug") or {}).get("classifier_confidence") or 0.0),
                "classifier_reason": (result.get("answer_debug") or {}).get("classifier_reason") or "",
                "question_focus": (result.get("answer_debug") or {}).get("question_focus") or {},
                "answer_slots": (result.get("answer_debug") or {}).get("answer_slots") or {},
                "slot_coverage": (result.get("answer_debug") or {}).get("slot_coverage") or {},
                "fallback_reason": (result.get("answer_debug") or {}).get("fallback_reason") or "none",
                "answer_plan": (result.get("answer_debug") or {}).get("answer_plan") or {},
                "draft_answer": (result.get("answer_debug") or {}).get("draft_answer") or "",
                "rewritten_answer": (result.get("answer_debug") or {}).get("rewritten_answer") or "",
                "rewrite_applied": bool((result.get("answer_debug") or {}).get("rewrite_applied", False)),
                "polish_applied": bool((result.get("answer_debug") or {}).get("polish_applied", False)),
                "polish_rejected": bool((result.get("answer_debug") or {}).get("polish_rejected", False)),
                "polish_rejected_reason": (result.get("answer_debug") or {}).get("polish_rejected_reason") or "none",
                "final_answer_source": (result.get("answer_debug") or {}).get("final_answer_source") or "",
                "duplicate_visible_citations": int((result.get("answer_debug") or {}).get("duplicate_visible_citations", 0)),
                "deduped_citations": (result.get("answer_debug") or {}).get("deduped_citations") or [],
                "excluded_facts": [item.get("normalized_fact") for item in ((result.get("answer_debug") or {}).get("excluded_facts") or [])],
                "answer": result["answer"],
            }
        )
    avg_recall = sum(item["doc_recall"] for item in cases) / len(cases) if cases else 0.0
    avg_evidence_coverage = sum(item["evidence_fact_coverage"] for item in cases) / len(cases) if cases else 0.0
    avg_fact_coverage = sum(item["answer_fact_coverage"] for item in cases) / len(cases) if cases else 0.0
    return {
        "cases": cases,
        "average_doc_recall": avg_recall,
        "average_evidence_fact_coverage": avg_evidence_coverage,
        "average_answer_fact_coverage": avg_fact_coverage,
    }
