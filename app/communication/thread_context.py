from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.communication.thread_store import get_active_communication_thread, get_communication_thread
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


def _message_summary(message: dict[str, Any]) -> str:
    return _compact(message.get("summary") or message.get("snippet") or message.get("body_preview"))


def _risk_rank(value: str) -> int:
    return {"": 0, "low": 1, "medium": 2, "high": 3}.get(str(value or "").lower(), 0)


def _highest_risk(messages: list[dict[str, Any]]) -> str:
    risk = ""
    for message in messages:
        candidate = _compact(message.get("risk_hint")).lower()
        if _risk_rank(candidate) > _risk_rank(risk):
            risk = candidate
    return risk


def _source_message_ids(messages: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for message in messages:
        message_id = _compact(message.get("provider_message_id") or message.get("message_id"))
        if message_id and message_id not in seen:
            ids.append(message_id)
            seen.add(message_id)
    return ids


def build_thread_ref_from_projection(
    thread: dict[str, Any],
    *,
    actor_context: dict[str, Any] | None = None,
) -> CommunicationThreadRef:
    source_message_ids = thread.get("source_message_ids") or []
    if isinstance(source_message_ids, str):
        source_message_ids = [source_message_ids]
    return CommunicationThreadRef(
        thread_id=_compact(thread.get("thread_id")),
        source=_compact(thread.get("source") or "mail"),
        subject=_compact(thread.get("subject")),
        participants=_split_addresses(thread.get("participants")),
        last_message_at=_compact(thread.get("last_message_at")),
        status=_compact(thread.get("status") or "open"),
        source_message_ids=[_compact(item) for item in list(source_message_ids) if _compact(item)],
        latest_summary=_compact(thread.get("latest_summary")),
        risk_hint=_compact(thread.get("risk_hint")),
        updated_at=_compact(thread.get("updated_at")),
        actor_context=dict(actor_context or thread.get("actor_context") or {}),
    )


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
    latest_summary = ""
    latest_timestamp = ""
    resolved_thread_id = _compact(thread_id)

    for message in messages:
        if not resolved_thread_id:
            resolved_thread_id = _compact(
                message.get("thread_id")
                or message.get("provider_thread_id")
                or message.get("provider_message_id")
                or message.get("message_id")
            )
        if not subject:
            subject = _compact(message.get("subject"))
        timestamp = _message_timestamp(message)
        if timestamp and timestamp > last_message_at:
            last_message_at = timestamp
        if timestamp and timestamp >= latest_timestamp:
            latest_timestamp = timestamp
            latest_summary = _message_summary(message)
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
        source_message_ids=_source_message_ids(messages),
        latest_summary=latest_summary,
        risk_hint=_highest_risk(messages),
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
    actor = dict(actor_context or {})
    thread_key = _compact(thread_id)
    if thread_key:
        thread_projection = get_communication_thread(thread_key, actor_context=actor)
        messages = list_thread_messages(thread_key, limit=messages_limit, actor_context=actor)
        if not messages:
            if thread_projection:
                return CommunicationThreadContext(
                    thread_ref=build_thread_ref_from_projection(thread_projection, actor_context=actor),
                    messages=[],
                    resolution="explicit_thread_projection",
                    confidence=0.75,
                    open_questions=["thread_messages_unavailable"],
                )
            return empty_thread_context(
                thread_id=thread_key,
                actor_context=actor,
                resolution="explicit_thread_not_found",
                confidence=0.1,
            )
        return CommunicationThreadContext(
            thread_ref=build_thread_ref_from_messages(messages, thread_id=thread_key, actor_context=actor),
            messages=messages,
            resolution="explicit_thread",
            confidence=0.95,
            open_questions=[],
        )

    active_thread = get_active_communication_thread(actor_context=actor)
    if active_thread:
        active_thread_id = _compact(active_thread.get("thread_id"))
        messages = list_thread_messages(active_thread_id, limit=messages_limit, actor_context=actor)
        if messages:
            return CommunicationThreadContext(
                thread_ref=build_thread_ref_from_messages(messages, thread_id=active_thread_id, actor_context=actor),
                messages=messages,
                resolution="active_thread",
                confidence=0.9,
                open_questions=[],
            )
        return CommunicationThreadContext(
            thread_ref=build_thread_ref_from_projection(active_thread, actor_context=actor),
            messages=[],
            resolution="active_thread_projection",
            confidence=0.7,
            open_questions=["active_thread_messages_unavailable"],
        )

    recent_threads = list_recent_inbound_threads(
        limit=recent_limit,
        messages_per_thread=recent_messages_per_thread,
        actor_context=actor,
    )
    if not recent_threads:
        return empty_thread_context(
            actor_context=actor,
            resolution="no_recent_thread",
            confidence=0.0,
        )

    recent = dict(recent_threads[0] or {})
    messages = [dict(item or {}) for item in list(recent.get("messages") or [])]
    fallback_thread_id = _compact(recent.get("thread_id") or recent.get("provider_thread_id"))
    if not messages and fallback_thread_id:
        messages = list_thread_messages(fallback_thread_id, limit=recent_messages_per_thread, actor_context=actor)
    if not messages:
        return empty_thread_context(
            thread_id=fallback_thread_id,
            actor_context=actor,
            resolution="recent_thread_without_messages",
            confidence=0.2,
        )

    return CommunicationThreadContext(
        thread_ref=build_thread_ref_from_messages(messages, thread_id=fallback_thread_id, actor_context=actor),
        messages=messages,
        resolution="recent_thread_fallback",
        confidence=0.55,
        open_questions=["confirm_active_communication_thread"],
    )
