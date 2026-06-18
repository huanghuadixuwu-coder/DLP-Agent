from __future__ import annotations

from typing import Any

from app.communication.observations import build_communication_brief_observation
from app.communication.thread_context import (
    CommunicationThreadContext,
    resolve_communication_thread_context,
)
from app.communication.types import CommunicationBrief, new_communication_id
from app.orchestration.types import TypedObservation


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, TypedObservation):
        return {
            "observation_type": value.observation_type,
            "status": value.status,
            "source": value.source,
            "payload": dict(value.payload or {}),
            "confidence": value.confidence,
            "actor_context": dict(value.actor_context or {}),
            "citations": list(value.citations or []),
        }
    return dict(value or {}) if isinstance(value, dict) else {}


def _bounded_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _dedupe_strings(values: list[Any], *, limit: int = 8) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _bounded_text(value)
        key = text.lower()
        if text and key not in seen:
            results.append(text)
            seen.add(key)
        if len(results) >= limit:
            break
    return results


def _grounding_payload(grounding_observation: Any) -> dict[str, Any]:
    observation = _as_dict(grounding_observation)
    payload = dict(observation.get("payload") or {})
    if not payload and any(key in observation for key in ("evidence_manifest", "selected_evidence", "answer_state")):
        payload = observation
    if not payload and isinstance(grounding_observation, dict):
        payload = dict(grounding_observation.get("enterprise_answer_observation") or {})
    return payload


def extract_grounding_refs(grounding_observation: Any) -> list[dict[str, Any]]:
    payload = _grounding_payload(grounding_observation)
    refs: list[dict[str, Any]] = []
    for item in list(payload.get("evidence_manifest") or payload.get("selected_evidence") or payload.get("citations_brief") or []):
        ref = dict(item or {})
        refs.append(
            {
                "doc_id": str(ref.get("doc_id") or ""),
                "chunk_id": str(ref.get("chunk_id") or ""),
                "source_type": str(ref.get("source_type") or ""),
                "title": _bounded_text(ref.get("title"), 120),
                "score": float(ref.get("score") or 0.0),
            }
        )
    return refs[:8]


def _must_include_from_grounding(grounding_observation: Any) -> list[str]:
    payload = _grounding_payload(grounding_observation)
    facts = []
    for item in list(payload.get("canonical_facts") or [])[:6]:
        fact = dict(item or {})
        facts.append(fact.get("normalized_fact") or fact.get("fact_id") or "")
    return _dedupe_strings(facts, limit=6)


def _grounding_confidence(grounding_observation: Any) -> float:
    observation = _as_dict(grounding_observation)
    payload = _grounding_payload(grounding_observation)
    answer_state = dict(payload.get("answer_state") or {})
    candidates = [
        answer_state.get("confidence"),
        payload.get("confidence"),
        observation.get("confidence"),
    ]
    for value in candidates:
        if value is not None:
            return max(0.0, min(float(value or 0.0), 1.0))
    return 0.0


def _memory_must_include(memory_context: dict[str, Any] | None) -> list[str]:
    memory = dict(memory_context or {})
    refs = []
    for key in ("turn_memory", "merged_memory"):
        for item in list(memory.get(key) or [])[:2]:
            refs.append((dict(item or {}).get("chunk_id") or dict(item or {}).get("source_turn_ids") or ""))
    return _dedupe_strings(refs, limit=4)


def _summarize_thread_context(thread_context: CommunicationThreadContext) -> str:
    ref = thread_context.thread_ref
    parts = [
        f"thread_resolution={thread_context.resolution}",
        f"message_count={len(thread_context.messages)}",
    ]
    if ref.subject:
        parts.append(f"subject={_bounded_text(ref.subject, 120)}")
    if ref.participants:
        parts.append(f"participant_count={len(ref.participants)}")
    if ref.last_message_at:
        parts.append(f"last_message_at={ref.last_message_at}")
    return "; ".join(parts)


def _recommended_next_action(
    *,
    thread_context: CommunicationThreadContext,
    grounding_refs: list[dict[str, Any]],
) -> str:
    if not thread_context.messages:
        return "request_thread_selection"
    if thread_context.open_questions:
        return "confirm_thread_before_draft"
    if not grounding_refs:
        return "gather_grounding_before_draft"
    return "draft_with_grounding"


def assemble_communication_brief(
    *,
    brief_id: str = "",
    employee_goal: str,
    conversation_id: str = "",
    thread_context: CommunicationThreadContext | None = None,
    thread_id: str = "",
    grounding_refs: list[dict[str, Any]] | None = None,
    grounding_observation: Any = None,
    memory_context: dict[str, Any] | None = None,
    actor_context: dict[str, Any] | None = None,
    source_observation_ids: list[str] | None = None,
) -> CommunicationBrief:
    actor = dict(actor_context or {})
    resolved_thread = thread_context or resolve_communication_thread_context(thread_id=thread_id, actor_context=actor)
    resolved_grounding_refs = list(grounding_refs or extract_grounding_refs(grounding_observation))
    must_include = _dedupe_strings(
        [
            *_must_include_from_grounding(grounding_observation),
            *_memory_must_include(memory_context),
        ],
        limit=10,
    )
    must_avoid = ["unsupported_final_answer_copy", "unverified_customer_specific_claims"]
    open_questions = _dedupe_strings(
        [
            *resolved_thread.open_questions,
            *(["grounding_required"] if not resolved_grounding_refs else []),
        ],
        limit=8,
    )
    confidence_inputs = [resolved_thread.confidence]
    if resolved_grounding_refs:
        confidence_inputs.append(_grounding_confidence(grounding_observation) or 0.5)
    if memory_context and int(dict(memory_context).get("memory_hits") or 0) > 0:
        confidence_inputs.append(0.65)
    confidence = round(sum(confidence_inputs) / len(confidence_inputs), 2) if confidence_inputs else 0.0
    if open_questions:
        confidence = min(confidence, 0.72)

    return CommunicationBrief(
        brief_id=brief_id or new_communication_id("comm_brief"),
        conversation_id=conversation_id,
        thread_ref=resolved_thread.thread_ref,
        employee_goal=_bounded_text(employee_goal, 500),
        customer_context_summary=_summarize_thread_context(resolved_thread),
        grounding_refs=resolved_grounding_refs,
        must_include=must_include,
        must_avoid=must_avoid,
        open_questions=open_questions,
        recommended_next_action=_recommended_next_action(
            thread_context=resolved_thread,
            grounding_refs=resolved_grounding_refs,
        ),
        source_observation_ids=list(source_observation_ids or []),
        confidence=confidence,
        actor_context=actor,
    )


def assemble_communication_brief_observation(
    persist_snapshot: bool = False,
    refresh_reason: str = "assembly",
    **kwargs: Any,
) -> tuple[CommunicationBrief, TypedObservation]:
    brief = assemble_communication_brief(**kwargs)
    persistence_metadata: dict[str, Any] = {
        "brief_persistence_source": "assembled",
        "brief_version": 1,
        "thread_id": brief.thread_ref.thread_id,
        "refresh_reason": "",
    }
    if persist_snapshot and brief.thread_ref.thread_id:
        from app.communication.brief_store import upsert_communication_brief

        stored = upsert_communication_brief(
            brief,
            actor_context=brief.actor_context,
            refresh_reason=refresh_reason,
        )
        persistence_metadata = {
            "brief_persistence_source": str(stored.get("persistence_source") or "communication_briefs"),
            "brief_version": int(stored.get("version") or 1),
            "thread_id": str(stored.get("thread_id") or brief.thread_ref.thread_id),
            "refresh_reason": str(stored.get("refresh_reason") or refresh_reason),
        }
    return brief, build_communication_brief_observation(brief, persistence_metadata=persistence_metadata)
