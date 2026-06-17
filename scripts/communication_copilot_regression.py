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


def main() -> int:
    parser = argparse.ArgumentParser(description="Communication Copilot regression checks")
    parser.add_argument("--case", required=True, dest="case_name")
    args = parser.parse_args()

    if args.case_name != "contracts_import":
        print(f"unsupported case: {args.case_name}", file=sys.stderr)
        return 2

    try:
        result = run_contracts_import()
    except Exception as exc:
        print(json.dumps({"ok": False, "case": args.case_name, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
