from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.orchestration.tool_discovery import register_tool
from app.orchestration.types import OrchestrationContext


def _calendar_provider_unavailable(action: str, context: OrchestrationContext) -> dict[str, Any]:
    settings = get_settings()
    configured = bool(settings.calendar_provider_enabled and settings.calendar_api_base_url and settings.calendar_api_key)
    status = "provider_not_implemented" if configured else "provider_not_configured"
    return {
        "status": status,
        "error": status,
        "summary": f"Calendar provider is {status}.",
        "provider": settings.calendar_provider,
        "action": action,
        "actor_context": dict(context.actor_context or {}),
        "constraints": [
            {
                "kind": "provider_boundary",
                "provider": settings.calendar_provider,
                "requires_real_provider": True,
                "fake_success_allowed": False,
            }
        ],
    }


@register_tool(
    name="calendar_list_events",
    description="List calendar events for the actor in a bounded time window.",
    input_schema={"start": "ISO datetime", "end": "ISO datetime", "calendar_id": "optional calendar id"},
    reads_from=["calendar_provider"],
    read_only=True,
    returns_observation_type="calendar_events",
    provider_constraints=["Requires a configured enterprise calendar provider; never fabricates events."],
)
def calendar_list_events(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    result = _calendar_provider_unavailable("calendar_list_events", context)
    result["requested_window"] = {"start": payload.get("start"), "end": payload.get("end")}
    return result


@register_tool(
    name="calendar_find_available_slots",
    description="Find available meeting slots from calendar availability constraints.",
    input_schema={"date": "date or natural date", "time_window": "time window", "duration_minutes": "int", "attendees": "list[str]"},
    reads_from=["calendar_provider"],
    read_only=True,
    returns_observation_type="calendar_availability",
    provider_constraints=["Requires a configured enterprise calendar provider; never fabricates availability."],
)
def calendar_find_available_slots(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    result = _calendar_provider_unavailable("calendar_find_available_slots", context)
    result["request"] = {
        "date": payload.get("date"),
        "time_window": payload.get("time_window"),
        "duration_minutes": payload.get("duration_minutes"),
        "attendees": list(payload.get("attendees") or []),
    }
    result["available_slots"] = []
    return result


@register_tool(
    name="calendar_create_event",
    description="Create a calendar event after explicit confirmation.",
    input_schema={"title": "event title", "start": "ISO datetime", "end": "ISO datetime", "attendees": "list[str]", "meeting_url": "optional URL", "idempotency_key": "required for write"},
    read_only=False,
    mutating=True,
    side_effectful=True,
    requires_confirmation=True,
    writes_to=["calendar_provider"],
    returns_observation_type="calendar_event_write",
    provider_constraints=["Requires calendar.write permission, explicit confirmation, idempotency key, and configured provider."],
)
def calendar_create_event(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    result = _calendar_provider_unavailable("calendar_create_event", context)
    result["idempotency_key"] = str(payload.get("idempotency_key") or "")
    result["side_effects"] = [{"kind": "calendar_event_create", "allowed": False, "reason": result["status"]}]
    return result


@register_tool(
    name="calendar_update_event",
    description="Update a calendar event after explicit confirmation.",
    input_schema={"event_id": "calendar event id", "patch": "dict", "idempotency_key": "required for write"},
    read_only=False,
    mutating=True,
    side_effectful=True,
    requires_confirmation=True,
    writes_to=["calendar_provider"],
    returns_observation_type="calendar_event_write",
    provider_constraints=["Requires calendar.write permission, explicit confirmation, idempotency key, and configured provider."],
)
def calendar_update_event(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    result = _calendar_provider_unavailable("calendar_update_event", context)
    result["event_id"] = str(payload.get("event_id") or "")
    result["idempotency_key"] = str(payload.get("idempotency_key") or "")
    result["side_effects"] = [{"kind": "calendar_event_update", "allowed": False, "reason": result["status"]}]
    return result


@register_tool(
    name="calendar_cancel_event",
    description="Cancel a calendar event after explicit confirmation.",
    input_schema={"event_id": "calendar event id", "reason": "optional cancel reason", "idempotency_key": "required for write"},
    read_only=False,
    mutating=True,
    side_effectful=True,
    requires_confirmation=True,
    writes_to=["calendar_provider"],
    returns_observation_type="calendar_event_write",
    provider_constraints=["Requires calendar.write permission, explicit confirmation, idempotency key, and configured provider."],
)
def calendar_cancel_event(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    result = _calendar_provider_unavailable("calendar_cancel_event", context)
    result["event_id"] = str(payload.get("event_id") or "")
    result["idempotency_key"] = str(payload.get("idempotency_key") or "")
    result["side_effects"] = [{"kind": "calendar_event_cancel", "allowed": False, "reason": result["status"]}]
    return result
