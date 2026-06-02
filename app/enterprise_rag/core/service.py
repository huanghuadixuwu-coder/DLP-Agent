from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any

from app.enterprise_rag.core.evidence_pack import build_evidence_pack
from app.enterprise_rag.core.answer_composer import compose_enterprise_answer
from app.enterprise_rag.core.query_planner import build_retrieval_plan
from app.enterprise_rag.core.retrieval_orchestrator import retrieve_evidence
from app.hermes_memory import build_runtime_context_bundle
from app.resilience import make_failure_observation


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 2)


def _dependency_failures(result: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    candidates = list(result.get("dependency_failures") or [])
    retrieval_debug = dict(result.get("retrieval_stage_debug") or {})
    candidates.extend(value for key, value in retrieval_debug.items() if key.endswith("failure_observation"))
    for item in candidates:
        observation = dict(item or {}) if isinstance(item, dict) else {}
        if observation.get("observation_type") != "dependency_failure":
            continue
        payload = dict(observation.get("payload") or {})
        dedupe_key = (
            str(payload.get("service") or observation.get("source") or ""),
            str(payload.get("operation") or ""),
            str(payload.get("fallback_strategy") or ""),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        failures.append(observation)
    return failures[:6]


def build_enterprise_answer_observation(result: dict[str, Any]) -> dict[str, Any]:
    answer_debug = dict(result.get("answer_debug") or {})
    retrieval_debug = dict(result.get("retrieval_stage_debug") or {})
    canonical_facts = []
    for item in list(result.get("canonical_facts") or [])[:8]:
        fact = dict(item or {})
        canonical_facts.append(
            {
                "fact_id": str(fact.get("fact_id") or ""),
                "fact_type": str(fact.get("fact_type") or ""),
                "normalized_fact": str(fact.get("normalized_fact") or ""),
                "priority": str(fact.get("priority") or ""),
                "score": float(fact.get("score") or 0.0),
                "source_fact_ids": list(fact.get("source_fact_ids") or []),
            }
        )
    citations_brief = []
    for item in list(result.get("citations") or [])[:3]:
        citation = dict(item or {})
        citations_brief.append(
            {
                "doc_id": str(citation.get("doc_id") or ""),
                "chunk_id": str(citation.get("chunk_id") or ""),
                "source_type": str(citation.get("source_type") or ""),
                "title": str(citation.get("title") or ""),
                "snippet": str(citation.get("snippet") or "")[:320],
                "score": float(citation.get("score") or 0.0),
            }
        )
    return {
        "answer_state": {
            "answerable": bool(answer_debug.get("answerable", not result.get("missing_evidence", False))),
            "missing_evidence": bool(result.get("missing_evidence", False)),
            "confidence": float(result.get("confidence") or 0.0),
            "answer_intent": str(answer_debug.get("answer_intent") or ""),
            "question_focus": dict(answer_debug.get("question_focus") or {}),
            "fallback_reason": str(answer_debug.get("fallback_reason") or ""),
            "final_answer_source": str(answer_debug.get("final_answer_source") or ""),
        },
        "canonical_facts": canonical_facts,
        "citations_brief": citations_brief,
        "source_doc_ids": [str(item) for item in list(result.get("supporting_doc_ids") or [])[:8]],
        "context_sources": [str(item) for item in list(result.get("context_sources") or [])[:8]],
        "dependency_failures": _dependency_failures(result),
        "retrieval_summary": {
            "budget_profile": str(retrieval_debug.get("budget_profile") or ""),
            "expansion_triggered": bool(retrieval_debug.get("expansion_triggered", False)),
            "expansion_reason": str(retrieval_debug.get("expansion_reason") or "none"),
            "rerank_candidates_submitted": int(retrieval_debug.get("rerank_candidates_submitted", 0)),
            "rerank_candidate_limit": int(retrieval_debug.get("rerank_candidate_limit", 0)),
            "rerank_passage_chars": int(retrieval_debug.get("rerank_passage_chars", 0)),
            "first_pass_counts": dict(retrieval_debug.get("first_pass_counts") or {}),
            "second_pass_counts": dict(retrieval_debug.get("second_pass_counts") or {}),
            "stage_latencies_ms": dict(result.get("stage_latencies_ms") or {}),
        },
    }


def answer_enterprise_question(
    question: str,
    *,
    source_types: list[str] | None = None,
    top_k: int = 8,
    session_id: str = "",
    conversation_id: str = "",
    actor_context: dict | None = None,
    compose_answer: bool = True,
) -> dict:
    total_started = perf_counter()
    planning_started = perf_counter()
    plan = build_retrieval_plan(question, source_types=source_types, top_k=top_k)
    planning_ms = _elapsed_ms(planning_started)
    if actor_context:
        plan.tenant_id = str(actor_context.get("tenant_id") or "")
        plan.workspace_id = str(actor_context.get("workspace_id") or "")
    context_started = perf_counter()
    dependency_failures: list[dict[str, Any]] = []
    try:
        context_bundle = (
            build_runtime_context_bundle(session_id=session_id, conversation_id=conversation_id, question=question, actor_context=actor_context)
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
                "actor_context": dict(actor_context or {}),
            }
        )
    except Exception as exc:
        dependency_failures.append(
            make_failure_observation(
                service="memory_context",
                operation="build_runtime_context_bundle",
                error=str(exc),
                fallback_strategy="continue_without_runtime_context",
                actor_context=actor_context,
            )
        )
        context_bundle = {
            "context_text": "",
            "context_sources": [],
            "workspace_memory_hits": 0,
            "transcript_hits": 0,
            "user_model_used": False,
            "memory_retrieval_hits": 0,
            "workspace_memory": [],
            "transcript_entries": [],
            "legacy_memory": {},
            "actor_context": dict(actor_context or {}),
        }
    context_bundle_ms = _elapsed_ms(context_started)
    retrieval_started = perf_counter()
    try:
        evidence = retrieve_evidence(plan)
    except Exception as exc:
        failure_observation = make_failure_observation(
            service="enterprise_rag",
            operation="retrieve_evidence",
            error=str(exc),
            fallback_strategy="return_recovery_observation",
            actor_context=actor_context,
        )
        dependency_failures.append(failure_observation)
        evidence = build_evidence_pack(
            question,
            [],
            retrieval_stage_debug={
                "retrieval_failure_observation": failure_observation,
                "retrieval_error": str(exc),
                "budget_profile": plan.budget_profile,
                "expansion_triggered": False,
                "expansion_reason": "dependency_failure",
            },
        )
    retrieval_ms = _elapsed_ms(retrieval_started)
    answer_started = perf_counter()
    if compose_answer:
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
        answer_payload = {
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
        }
    else:
        answer_payload = {
            "answer": "",
            "citations": [asdict(item) for item in evidence.citations],
            "supporting_doc_ids": evidence.supporting_doc_ids,
            "missing_evidence": evidence.missing_evidence,
            "confidence": evidence.confidence,
            "supporting_facts": evidence.supporting_facts,
            "supporting_fact_details": [asdict(item) for item in evidence.supporting_fact_details],
            "canonical_facts": [asdict(item) for item in evidence.canonical_facts],
            "retrieval_stage_debug": evidence.retrieval_stage_debug,
            "rerank_debug": evidence.rerank_debug,
            "evidence_fact_hits": [],
            "answer_debug": {
                "answerable": not evidence.missing_evidence,
                "answer_intent": "",
                "question_focus": {},
                "fallback_reason": "none" if not evidence.missing_evidence else "insufficient_core_facts",
                "final_answer_source": "renderer_required",
            },
            "context_sources": list(context_bundle.get("context_sources") or []),
            "workspace_memory_hits": int(context_bundle.get("workspace_memory_hits", 0)),
            "transcript_hits": int(context_bundle.get("transcript_hits", 0)),
            "user_model_used": bool(context_bundle.get("user_model_used", False)),
        }
    answer_compose_ms = _elapsed_ms(answer_started)
    stage_latencies_ms = {
        "planning": planning_ms,
        "context_bundle": context_bundle_ms,
        "retrieval": retrieval_ms,
        "answer_compose": answer_compose_ms,
        "total": _elapsed_ms(total_started),
    }
    result = {
        **answer_payload,
        "memory_context": context_bundle,
        "retrieval_plan": asdict(plan),
        "stage_latencies_ms": stage_latencies_ms,
        "actor_context": dict(actor_context or {}),
        "answer_composition_skipped": not compose_answer,
        "dependency_failures": dependency_failures,
    }
    result["enterprise_answer_observation"] = build_enterprise_answer_observation(result)
    return result
