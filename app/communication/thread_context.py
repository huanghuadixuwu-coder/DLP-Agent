from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.communication.types import CommunicationThreadRef
from app.inbound_mail_store import list_recent_inbound_threads, list_thread_messages


@dataclass(slots=True)
class CommunicationThreadContext:
    thread_ref: CommunicationThreadRef
    messages: list[dict[str, Any]] = field(default_factory=list)
    resolution: str = "empty"
    confidence: float = 0.0
    open_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_ref": self.thread_ref.to_dict(),
            "messages": list(self.messages),
            "resolution": self.resolution,
            "confidence": self.confidence,
            "open_questions": list(self.open_questions),
        }


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _split_addresses(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = value
    else:
        text = _compact(value)
        for separator in (";", "|"):
            text = text.replace(separator, ",")
        candidates = text.split(",")
    return [_compact(item) for item in candidates if _compact(item)]


def _message_timestamp(message: dict[str, Any]) -> str:
    return _compact(message.get("received_at") or message.get("created_at") or message.get("updated_at"))


def build_thread_ref_from_messages(
    messages: list[dict[str, Any]],
    *,
    thread_id: str = "",
    actor_context: dict[str, Any] | None = None,
    source: str = "mail",
) -> CommunicationThreadRef:
    participants: list[str] = []
    seen_participants: set[str] = set()
    subject = ""
    last_message_at = ""
    resolved_thread_id = _compact(thread_id)

    for message in messages:
        if not resolved_thread_id:
            resolved_thread_id = _compact(
                message.get("thread_id") or message.get("provider_thread_id") or message.get("message_id")
            )
        if not subject:
            subject = _compact(message.get("subject"))
        timestamp = _message_timestamp(message)
        if timestamp and timestamp > last_message_at:
            last_message_at = timestamp
        for address in [message.get("sender"), *_split_addresses(message.get("recipients"))]:
            normalized = _compact(address)
            key = normalized.lower()
            if normalized and key not in seen_participants:
                participants.append(normalized)
                seen_participants.add(key)

    return CommunicationThreadRef(
        thread_id=resolved_thread_id,
        source=source,
        subject=subject,
        participants=participants,
        last_message_at=last_message_at,
        actor_context=dict(actor_context or {}),
    )


def empty_thread_context(
    *,
    thread_id: str = "",
    actor_context: dict[str, Any] | None = None,
    resolution: str = "empty",
    confidence: float = 0.0,
    open_questions: list[str] | None = None,
) -> CommunicationThreadContext:
    return CommunicationThreadContext(
        thread_ref=CommunicationThreadRef(
            thread_id=_compact(thread_id),
            source="mail",
            actor_context=dict(actor_context or {}),
        ),
        messages=[],
        resolution=resolution,
        confidence=float(confidence),
        open_questions=list(open_questions or ["communication_thread_required"]),
    )


def resolve_communication_thread_context(
    *,
    thread_id: str = "",
    actor_context: dict[str, Any] | None = None,
    messages_limit: int = 20,
    recent_limit: int = 1,
    recent_messages_per_thread: int = 5,
) -> CommunicationThreadContext:
    thread_key = _compact(thread_id)
    if thread_key:
        messages = list_thread_messages(thread_key, limit=messages_limit)
        if not messages:
            return empty_thread_context(
                thread_id=thread_key,
                actor_context=actor_context,
                resolution="explicit_thread_not_found",
                confidence=0.1,
            )
        return CommunicationThreadContext(
            thread_ref=build_thread_ref_from_messages(messages, thread_id=thread_key, actor_context=actor_context),
            messages=messages,
            resolution="explicit_thread",
            confidence=0.95,
            open_questions=[],
        )

    recent_threads = list_recent_inbound_threads(limit=recent_limit, messages_per_thread=recent_messages_per_thread)
    if not recent_threads:
        return empty_thread_context(
            actor_context=actor_context,
            resolution="no_recent_thread",
            confidence=0.0,
        )

    recent = dict(recent_threads[0] or {})
    messages = [dict(item or {}) for item in list(recent.get("messages") or [])]
    fallback_thread_id = _compact(recent.get("thread_id") or recent.get("provider_thread_id"))
    if not messages and fallback_thread_id:
        messages = list_thread_messages(fallback_thread_id, limit=recent_messages_per_thread)
    if not messages:
        return empty_thread_context(
            thread_id=fallback_thread_id,
            actor_context=actor_context,
            resolution="recent_thread_without_messages",
            confidence=0.2,
        )

    return CommunicationThreadContext(
        thread_ref=build_thread_ref_from_messages(messages, thread_id=fallback_thread_id, actor_context=actor_context),
        messages=messages,
        resolution="recent_thread_fallback",
        confidence=0.55,
        open_questions=["confirm_active_communication_thread"],
    )
