from __future__ import annotations

from typing import Any

from app.communication.observations import build_communication_brief_observation
from app.communication.thread_context import (
    CommunicationThreadContext,
    resolve_communication_thread_context,
)
from app.communication.types import CommunicationBrief, CommunicationThreadRef, new_communication_id
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
    answer_state = dict(payload.get("answer_state") or {})
    missing_aspects = _dedupe_strings(
        [
            *list(payload.get("missing_aspects") or []),
            *list(answer_state.get("missing_aspects") or []),
            str(answer_state.get("fallback_reason") or "") if answer_state.get("missing_evidence") else "",
        ],
        limit=6,
    )
    refs: list[dict[str, Any]] = []
    for item in list(payload.get("evidence_manifest") or payload.get("selected_evidence") or payload.get("citations_brief") or []):
        ref = dict(item or {})
        doc_id = str(ref.get("doc_id") or "")
        chunk_id = str(ref.get("chunk_id") or "")
        citation_id = str(ref.get("citation_id") or ref.get("id") or "")
        if not citation_id and (doc_id or chunk_id):
            citation_id = f"{doc_id}:{chunk_id}" if chunk_id else doc_id
        refs.append(
            {
                "citation_id": citation_id,
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "source_type": str(ref.get("source_type") or ""),
                "title": _bounded_text(ref.get("title"), 120),
                "score": float(ref.get("score") or 0.0),
                "missing_aspects": missing_aspects,
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


def record_meeting_result_on_brief(
    *,
    source_brief_id: str,
    thread_id: str,
    meeting_result: dict[str, Any],
    task_id: str = "",
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a completed meeting result to the thread brief as next-action state."""

    brief_key = str(source_brief_id or "").strip()
    thread_key = str(thread_id or "").strip()
    if not brief_key or not thread_key:
        return {}

    from app.communication.brief_store import get_communication_brief, get_latest_brief_for_thread, upsert_communication_brief

    actor = dict(actor_context or {})
    stored = get_communication_brief(brief_key, actor_context=actor) or get_latest_brief_for_thread(thread_key, actor_context=actor)
    brief_payload = dict((stored or {}).get("brief") or {})
    if not brief_payload:
        return {}

    thread_ref_payload = dict(brief_payload.get("thread_ref") or {})
    thread_ref_payload["thread_id"] = str(thread_ref_payload.get("thread_id") or thread_key)
    if actor and not thread_ref_payload.get("actor_context"):
        thread_ref_payload["actor_context"] = actor
    thread_ref = CommunicationThreadRef(
        **{
            key: value
            for key, value in thread_ref_payload.items()
            if key in CommunicationThreadRef.__dataclass_fields__
        }
    )

    meeting_ref = _meeting_result_brief_ref(meeting_result, task_id=task_id)
    grounding_refs = _append_unique_meeting_ref(list(brief_payload.get("grounding_refs") or []), meeting_ref)
    source_observation_ids = _dedupe_strings(
        [
            *list(brief_payload.get("source_observation_ids") or []),
            f"meeting_task:{task_id}" if task_id else "",
        ],
        limit=12,
    )
    meeting_url = str(meeting_ref.get("meeting_url") or "").strip()
    meeting_code = str(meeting_ref.get("meeting_code") or "").strip()
    must_include = _dedupe_strings(
        [
            *list(brief_payload.get("must_include") or []),
            f"meeting_url={meeting_url}" if meeting_url else "",
            f"meeting_code={meeting_code}" if meeting_code else "",
        ],
        limit=12,
    )

    brief = CommunicationBrief(
        brief_id=str(brief_payload.get("brief_id") or brief_key),
        conversation_id=str(brief_payload.get("conversation_id") or ""),
        thread_ref=thread_ref,
        employee_goal=str(brief_payload.get("employee_goal") or ""),
        customer_context_summary=str(brief_payload.get("customer_context_summary") or ""),
        grounding_refs=grounding_refs,
        must_include=must_include,
        must_avoid=list(brief_payload.get("must_avoid") or []),
        open_questions=list(brief_payload.get("open_questions") or []),
        recommended_next_action="draft_meeting_followup_via_mail_agent",
        source_observation_ids=source_observation_ids,
        confidence=max(float(brief_payload.get("confidence") or 0.0), 0.88),
        created_at=str(brief_payload.get("created_at") or ""),
        actor_context=actor or dict(brief_payload.get("actor_context") or {}),
    )
    return upsert_communication_brief(
        brief,
        actor_context=actor or brief.actor_context,
        refresh_reason="meeting_result_completed",
    )


def _meeting_result_brief_ref(meeting_result: dict[str, Any], *, task_id: str = "") -> dict[str, Any]:
    result = dict(meeting_result or {})
    normalized = dict(result.get("normalized_request") or {})
    return {
        "kind": "meeting_result",
        "source_type": "meeting_result",
        "communication_role": "escalation_provider",
        "communication_input_kind": "meeting_result",
        "communication_closeout_owner": "mail_agent",
        "task_id": str(task_id or result.get("task_id") or ""),
        "meeting_id": str(result.get("meeting_id") or ""),
        "meeting_code": str(result.get("meeting_code") or ""),
        "meeting_url": str(result.get("meeting_url") or result.get("join_url") or ""),
        "topic": str(result.get("subject") or normalized.get("topic") or ""),
        "start_time": str(result.get("start_time") or normalized.get("start_time") or ""),
        "end_time": str(result.get("end_time") or normalized.get("end_time") or ""),
        "provider": str(result.get("provider") or ""),
        "summary": _bounded_text(result.get("summary") or result.get("message") or result.get("content_text"), 240),
    }


def _append_unique_meeting_ref(existing_refs: list[Any], meeting_ref: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [dict(item or {}) for item in existing_refs if isinstance(item, dict)]
    key = str(meeting_ref.get("task_id") or meeting_ref.get("meeting_id") or meeting_ref.get("meeting_url") or "")
    filtered: list[dict[str, Any]] = []
    for item in refs:
        item_key = str(item.get("task_id") or item.get("meeting_id") or item.get("meeting_url") or "")
        if key and item_key == key:
            continue
        filtered.append(item)
    return [meeting_ref, *filtered][:12]
