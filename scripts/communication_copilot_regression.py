from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_true(value: Any, label: str) -> None:
    if not value:
        raise AssertionError(f"{label}: expected truthy value, got {value!r}")


def run_contracts_import() -> dict[str, Any]:
    from app.communication.observations import (
        build_communication_brief_observation,
        build_communication_thread_observation,
        build_meeting_escalation_candidate_observation,
    )
    from app.communication.types import (
        CommunicationBrief,
        CommunicationThreadRef,
        MeetingEscalationCandidate,
    )
    from app.orchestration.types import TypedObservation

    actor_context = {"tenant_id": "tenant-1", "user_id": "user-1"}
    thread_ref = CommunicationThreadRef(
        thread_id="thread_123",
        source="mail",
        subject="Renewal planning",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-17T08:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id="brief_123",
        conversation_id="conversation_123",
        thread_ref=thread_ref,
        employee_goal="Prepare renewal reply",
        customer_context_summary="Customer asked for renewal timing.",
        grounding_refs=[{"kind": "mail_message", "id": "message_123"}],
        must_include=["pricing timeline"],
        must_avoid=["unsupported discounts"],
        open_questions=["Confirm procurement owner"],
        recommended_next_action="draft_reply",
        source_observation_ids=["obs_thread_123"],
        confidence=0.82,
        created_at="2026-06-17T08:01:00+00:00",
        actor_context=actor_context,
    )
    candidate = MeetingEscalationCandidate(
        candidate_id="candidate_123",
        source_brief_id=brief.brief_id,
        topic="Renewal timeline alignment",
        attendees=["customer@example.com", "rep@example.com"],
        time_window="next week",
        reason="Open procurement question blocks draft confidence.",
        confirmation_required=True,
    )

    thread_observation = build_communication_thread_observation(thread_ref)
    brief_observation = build_communication_brief_observation(brief)
    candidate_observation = build_meeting_escalation_candidate_observation(
        candidate,
        actor_context=actor_context,
        confidence=0.7,
    )

    for observation in (thread_observation, brief_observation, candidate_observation):
        if not isinstance(observation, TypedObservation):
            raise AssertionError("builder did not return TypedObservation")
        _assert_equal(observation.status, "completed", "status")
        _assert_equal(observation.source, "communication_copilot", "source")
        _assert_equal(observation.grounding_kind, "tool", "grounding_kind")

    _assert_equal(thread_observation.observation_type, "communication_thread", "thread observation_type")
    _assert_equal(thread_observation.payload, asdict(thread_ref), "thread payload")
    _assert_equal(thread_observation.confidence, 1.0, "thread confidence")
    _assert_equal(thread_observation.actor_context, actor_context, "thread actor_context")

    _assert_equal(brief_observation.observation_type, "communication_brief", "brief observation_type")
    _assert_equal(brief_observation.payload, asdict(brief), "brief payload")
    _assert_equal(brief_observation.payload["thread_ref"]["thread_id"], thread_ref.thread_id, "brief thread_ref")
    _assert_equal(brief_observation.confidence, brief.confidence, "brief confidence")
    _assert_equal(brief_observation.actor_context, actor_context, "brief actor_context")

    _assert_equal(
        candidate_observation.observation_type,
        "meeting_escalation_candidate",
        "candidate observation_type",
    )
    _assert_equal(candidate_observation.payload, asdict(candidate), "candidate payload")
    _assert_equal(candidate_observation.confidence, 0.7, "candidate confidence")
    _assert_equal(candidate_observation.actor_context, actor_context, "candidate actor_context")
    _assert_equal(
        candidate_observation.side_effects,
        [{"kind": "confirmation_required", "allowed": False}],
        "candidate side_effects",
    )

    return {
        "ok": True,
        "case": "contracts_import",
        "observation_types": [
            thread_observation.observation_type,
            brief_observation.observation_type,
            candidate_observation.observation_type,
        ],
    }


def run_brief_assembly() -> dict[str, Any]:
    import app.communication.thread_context as thread_context_module
    from app.communication.brief_service import assemble_communication_brief_observation
    from app.orchestration.types import TypedObservation

    actor_context = {"tenant_id": "tenant-1", "user_id": "employee-1", "workspace_id": "workspace-1"}
    messages = [
        {
            "message_id": "mail_1",
            "thread_id": "thread_renewal",
            "provider_thread_id": "provider_thread_renewal",
            "sender": "customer@example.com",
            "recipients": "rep@example.com",
            "subject": "Renewal planning",
            "received_at": "2026-06-17T08:00:00+00:00",
            "summary": "Customer asked whether renewal pricing can be confirmed this week.",
        },
        {
            "message_id": "mail_2",
            "thread_id": "thread_renewal",
            "provider_thread_id": "provider_thread_renewal",
            "sender": "rep@example.com",
            "recipients": "customer@example.com",
            "subject": "Renewal planning",
            "received_at": "2026-06-17T08:05:00+00:00",
            "summary": "Rep acknowledged the renewal timing question.",
        },
    ]

    original_list_thread_messages = thread_context_module.list_thread_messages
    original_list_recent_inbound_threads = thread_context_module.list_recent_inbound_threads
    thread_context_module.list_thread_messages = lambda thread_id, **_: messages if thread_id == "thread_renewal" else []
    thread_context_module.list_recent_inbound_threads = lambda **_: []
    try:
        grounding_observation = {
            "observation_type": "enterprise_answer_observation",
            "payload": {
                "answer_state": {"confidence": 0.84, "answerable": True},
                "evidence_manifest": [
                    {
                        "doc_id": "doc_pricing",
                        "chunk_id": "chunk_pricing_1",
                        "source_type": "policy",
                        "title": "Renewal pricing policy",
                        "score": 0.91,
                    }
                ],
                "canonical_facts": [
                    {
                        "fact_id": "fact_renewal_notice",
                        "normalized_fact": "Renewal pricing requires current policy grounding.",
                    }
                ],
                "answer": "Hi customer, here is the final email body that must not leak into the brief.",
            },
            "confidence": 0.84,
        }
        memory_context = {
            "memory_hits": 1,
            "turn_memory": [{"chunk_id": "conversation-conv_1-turn_1", "source_turn_ids": "turn_1,turn_2"}],
            "merged_memory": [],
        }

        brief, observation = assemble_communication_brief_observation(
            employee_goal="Prepare renewal reply",
            conversation_id="conversation_123",
            thread_id="thread_renewal",
            grounding_observation=grounding_observation,
            memory_context=memory_context,
            actor_context=actor_context,
            source_observation_ids=["obs_thread_renewal", "obs_grounding_pricing"],
        )
    finally:
        thread_context_module.list_thread_messages = original_list_thread_messages
        thread_context_module.list_recent_inbound_threads = original_list_recent_inbound_threads

    if not isinstance(observation, TypedObservation):
        raise AssertionError("brief assembly did not return TypedObservation")

    payload = observation.payload
    _assert_equal(brief.thread_ref.thread_id, "thread_renewal", "brief thread_ref.thread_id")
    _assert_equal(brief.thread_ref.subject, "Renewal planning", "brief thread_ref.subject")
    _assert_equal(
        brief.thread_ref.participants,
        ["customer@example.com", "rep@example.com"],
        "brief thread_ref.participants",
    )
    _assert_equal(brief.thread_ref.last_message_at, "2026-06-17T08:05:00+00:00", "brief last_message_at")
    _assert_equal(brief.grounding_refs[0]["chunk_id"], "chunk_pricing_1", "brief grounding_refs")
    _assert_equal(brief.source_observation_ids, ["obs_thread_renewal", "obs_grounding_pricing"], "source_observation_ids")
    _assert_equal(brief.actor_context, actor_context, "brief actor_context")
    _assert_true(brief.confidence >= 0.8, "brief confidence")
    _assert_equal(brief.open_questions, [], "explicit grounded open_questions")
    _assert_equal(brief.recommended_next_action, "draft_with_grounding", "recommended_next_action")
    _assert_equal(observation.observation_type, "communication_brief", "observation_type")
    _assert_equal(payload["thread_ref"]["thread_id"], brief.thread_ref.thread_id, "payload thread_ref")
    _assert_equal(payload["grounding_refs"][0]["doc_id"], "doc_pricing", "payload grounding_refs")
    _assert_equal(observation.actor_context, actor_context, "observation actor_context")
    _assert_equal(observation.missing_fields, [], "observation missing_fields")
    if "answer" in payload:
        raise AssertionError("brief payload leaked an answer field")
    if "final email body" in json.dumps(payload, ensure_ascii=False):
        raise AssertionError("brief payload leaked direct final answer wording")

    thread_context_module.list_thread_messages = lambda *_, **__: []
    thread_context_module.list_recent_inbound_threads = lambda **_: []
    try:
        empty_brief, _ = assemble_communication_brief_observation(
            employee_goal="Prepare renewal reply",
            actor_context=actor_context,
            source_observation_ids=[],
        )
    finally:
        thread_context_module.list_thread_messages = original_list_thread_messages
        thread_context_module.list_recent_inbound_threads = original_list_recent_inbound_threads

    _assert_equal(empty_brief.thread_ref.thread_id, "", "empty thread_ref.thread_id")
    _assert_true("communication_thread_required" in empty_brief.open_questions, "empty thread open question")
    _assert_true("grounding_required" in empty_brief.open_questions, "empty grounding open question")
    _assert_equal(empty_brief.recommended_next_action, "request_thread_selection", "empty recommended_next_action")
    _assert_true(empty_brief.confidence <= 0.3, "empty brief confidence")

    return {
        "ok": True,
        "case": "brief_assembly",
        "brief_id": brief.brief_id,
        "thread_id": brief.thread_ref.thread_id,
        "grounding_refs": len(brief.grounding_refs),
        "empty_open_questions": empty_brief.open_questions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Communication Copilot regression checks")
    parser.add_argument("--case", required=True, dest="case_name")
    args = parser.parse_args()

    cases = {
        "contracts_import": run_contracts_import,
        "brief_assembly": run_brief_assembly,
    }
    if args.case_name not in cases:
        print(f"unsupported case: {args.case_name}", file=sys.stderr)
        return 2

    try:
        result = cases[args.case_name]()
    except Exception as exc:
        print(json.dumps({"ok": False, "case": args.case_name, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
