from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_communication_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


@dataclass(slots=True)
class CommunicationThreadRef:
    thread_id: str = field(default_factory=lambda: new_communication_id("comm_thread"))
    source: str = ""
    subject: str = ""
    participants: list[str] = field(default_factory=list)
    last_message_at: str = ""
    actor_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CommunicationBrief:
    brief_id: str = field(default_factory=lambda: new_communication_id("comm_brief"))
    conversation_id: str = ""
    thread_ref: CommunicationThreadRef = field(default_factory=CommunicationThreadRef)
    employee_goal: str = ""
    customer_context_summary: str = ""
    grounding_refs: list[dict[str, Any]] = field(default_factory=list)
    must_include: list[str] = field(default_factory=list)
    must_avoid: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    recommended_next_action: str = ""
    source_observation_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = field(default_factory=utc_now)
    actor_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MeetingEscalationCandidate:
    candidate_id: str = field(default_factory=lambda: new_communication_id("meeting_candidate"))
    source_brief_id: str = ""
    topic: str = ""
    attendees: list[str] = field(default_factory=list)
    time_window: str = ""
    reason: str = ""
    confirmation_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
