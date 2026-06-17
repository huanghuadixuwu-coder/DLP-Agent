from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.communication.types import (
    CommunicationBrief,
    CommunicationThreadRef,
    MeetingEscalationCandidate,
)
from app.orchestration.types import TypedObservation


DEFAULT_SOURCE = "communication_copilot"


def build_communication_thread_observation(
    thread_ref: CommunicationThreadRef,
    *,
    status: str = "completed",
    source: str = DEFAULT_SOURCE,
    confidence: float = 1.0,
    provenance: dict[str, Any] | None = None,
) -> TypedObservation:
    payload = asdict(thread_ref)
    return TypedObservation(
        observation_type="communication_thread",
        status=status,
        source=source,
        grounding_kind="tool",
        summary="Communication thread reference captured.",
        payload=payload,
        provenance=provenance or {"source": source},
        confidence=float(confidence),
        actor_context=dict(thread_ref.actor_context),
    )


def build_communication_brief_observation(
    brief: CommunicationBrief,
    *,
    status: str = "completed",
    source: str = DEFAULT_SOURCE,
    provenance: dict[str, Any] | None = None,
) -> TypedObservation:
    payload = asdict(brief)
    return TypedObservation(
        observation_type="communication_brief",
        status=status,
        source=source,
        grounding_kind="tool",
        summary="Communication brief captured.",
        payload=payload,
        provenance=provenance or {"source": source},
        confidence=float(brief.confidence),
        missing_fields=list(brief.open_questions),
        actor_context=dict(brief.actor_context),
    )


def build_meeting_escalation_candidate_observation(
    candidate: MeetingEscalationCandidate,
    *,
    status: str = "completed",
    source: str = DEFAULT_SOURCE,
    confidence: float = 1.0,
    actor_context: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> TypedObservation:
    payload = asdict(candidate)
    side_effects = []
    if candidate.confirmation_required:
        side_effects.append({"kind": "confirmation_required", "allowed": False})
    return TypedObservation(
        observation_type="meeting_escalation_candidate",
        status=status,
        source=source,
        grounding_kind="tool",
        summary="Meeting escalation candidate captured.",
        payload=payload,
        provenance=provenance or {"source": source},
        confidence=float(confidence),
        side_effects=side_effects,
        actor_context=dict(actor_context or {}),
    )
