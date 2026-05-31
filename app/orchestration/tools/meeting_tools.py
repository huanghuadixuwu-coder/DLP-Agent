from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.orchestration.tool_discovery import register_tool
from app.orchestration.types import OrchestrationContext
from app.tencent_meeting_mcp_provider import TencentMeetingMcpProvider


def _meeting_provider_unavailable(action: str, context: OrchestrationContext) -> dict[str, Any]:
    settings = get_settings()
    configured = bool(
        (
            settings.tencent_meeting_enabled
            and settings.tencent_meeting_provider == "mcp_skill"
            and settings.tencent_meeting_mcp_url
            and settings.tencent_meeting_token
        )
        or (
            settings.tencent_meeting_enabled
            and settings.tencent_meeting_api_base_url
            and settings.tencent_meeting_app_id
            and settings.tencent_meeting_secret_id
            and settings.tencent_meeting_secret_key
        )
    )
    status = "provider_not_implemented" if configured else "provider_not_configured"
    return {
        "status": status,
        "error": status,
        "summary": f"Tencent Meeting provider is {status}.",
        "provider": "tencent_meeting",
        "action": action,
        "actor_context": dict(context.actor_context or {}),
        "constraints": [
            {
                "kind": "provider_boundary",
                "provider": "tencent_meeting",
                "requires_real_provider": True,
                "fake_success_allowed": False,
            }
        ],
    }


def _provider_result(action: str, context: OrchestrationContext, result: dict[str, Any]) -> dict[str, Any]:
    status = str(result.get("status") or ("completed" if result.get("ok") else "provider_error"))
    normalized = {
        **result,
        "status": status,
        "summary": str(result.get("message") or result.get("content_text") or f"Tencent Meeting MCP {action} returned {status}."),
        "provider": "tencent_meeting_mcp",
        "action": action,
        "actor_context": dict(context.actor_context or {}),
    }
    meeting_fields = _extract_meeting_fields(normalized)
    if meeting_fields:
        normalized.update(meeting_fields)
        subject = str(meeting_fields.get("subject") or "").strip()
        meeting_code = str(meeting_fields.get("meeting_code") or "").strip()
        normalized["summary"] = (
            f"Tencent Meeting {action} completed for {subject or 'meeting'}"
            + (f" ({meeting_code})." if meeting_code else ".")
        )
    return normalized


@register_tool(
    name="meeting_get_details",
    description="Read Tencent Meeting details by meeting id.",
    input_schema={"meeting_id": "Tencent Meeting id"},
    reads_from=["tencent_meeting_provider"],
    read_only=True,
    returns_observation_type="meeting_details",
    provider_constraints=["Requires configured Tencent Meeting provider; never fabricates meeting details."],
)
def meeting_get_details(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    provider = TencentMeetingMcpProvider()
    if provider.configured:
        return _provider_result("meeting_get_details", context, provider.get_meeting(payload))
    result = _meeting_provider_unavailable("meeting_get_details", context)
    result["meeting_id"] = str(payload.get("meeting_id") or "")
    result["meeting_code"] = str(payload.get("meeting_code") or "")
    return result


@register_tool(
    name="meeting_list_user_meetings",
    description="List current user's upcoming or in-progress Tencent Meetings.",
    input_schema={"page_size": "int", "page_token": "optional pagination token", "timezone": "IANA timezone"},
    reads_from=["tencent_meeting_provider"],
    read_only=True,
    returns_observation_type="meeting_list",
    provider_constraints=["Requires Tencent Meeting AI Skill token through TENCENT_MEETING_TOKEN."],
)
def meeting_list_user_meetings(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    provider = TencentMeetingMcpProvider()
    if provider.configured:
        return _provider_result("meeting_list_user_meetings", context, provider.get_user_meetings(payload))
    return _meeting_provider_unavailable("meeting_list_user_meetings", context)


@register_tool(
    name="meeting_create_tencent_meeting",
    description="Create a Tencent Meeting after explicit confirmation.",
    input_schema={"topic": "meeting topic", "start": "ISO datetime", "duration_minutes": "int", "attendees": "list[str]", "idempotency_key": "required for write"},
    read_only=False,
    mutating=True,
    side_effectful=True,
    requires_confirmation=True,
    writes_to=["tencent_meeting_provider"],
    returns_observation_type="meeting_write",
    provider_constraints=["Requires meeting.write permission, explicit confirmation, idempotency key, and configured Tencent Meeting provider."],
)
def meeting_create_tencent_meeting(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return {
            "status": "invalid_parameters",
            "error": "missing_idempotency_key",
            "summary": "meeting_create_tencent_meeting requires idempotency_key.",
            "provider": "tencent_meeting_mcp",
            "action": "meeting_create_tencent_meeting",
            "actor_context": dict(context.actor_context or {}),
        }
    normalized_payload = _normalize_meeting_time(payload)
    provider = TencentMeetingMcpProvider()
    if provider.configured:
        result = _provider_result("meeting_create_tencent_meeting", context, provider.schedule_meeting(normalized_payload))
    else:
        result = _meeting_provider_unavailable("meeting_create_tencent_meeting", context)
    result["idempotency_key"] = idempotency_key
    result["normalized_request"] = {
        "topic": str(normalized_payload.get("topic") or normalized_payload.get("subject") or ""),
        "start_time": str(normalized_payload.get("start_time") or ""),
        "end_time": str(normalized_payload.get("end_time") or ""),
        "duration_minutes": int(normalized_payload.get("duration_minutes") or 0),
        "timezone": str(normalized_payload.get("timezone") or normalized_payload.get("time_zone") or "Asia/Shanghai"),
    }
    result["side_effects"] = [{"kind": "meeting_create", "allowed": bool(result.get("ok")), "reason": result["status"]}]
    return result


@register_tool(
    name="meeting_cancel_tencent_meeting",
    description="Cancel a Tencent Meeting after explicit confirmation.",
    input_schema={"meeting_id": "Tencent Meeting id", "reason": "optional reason", "idempotency_key": "required for write"},
    read_only=False,
    mutating=True,
    side_effectful=True,
    requires_confirmation=True,
    writes_to=["tencent_meeting_provider"],
    returns_observation_type="meeting_write",
    provider_constraints=["Requires meeting.write permission, explicit confirmation, idempotency key, and configured Tencent Meeting provider."],
)
def meeting_cancel_tencent_meeting(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return {
            "status": "invalid_parameters",
            "error": "missing_idempotency_key",
            "summary": "meeting_cancel_tencent_meeting requires idempotency_key.",
            "provider": "tencent_meeting_mcp",
            "action": "meeting_cancel_tencent_meeting",
            "actor_context": dict(context.actor_context or {}),
        }
    provider = TencentMeetingMcpProvider()
    if provider.configured:
        result = _provider_result("meeting_cancel_tencent_meeting", context, provider.cancel_meeting(payload))
    else:
        result = _meeting_provider_unavailable("meeting_cancel_tencent_meeting", context)
    result["meeting_id"] = str(payload.get("meeting_id") or "")
    result["idempotency_key"] = idempotency_key
    result["side_effects"] = [{"kind": "meeting_cancel", "allowed": bool(result.get("ok")), "reason": result["status"]}]
    return result


@register_tool(
    name="meeting_attach_to_calendar_event",
    description="Attach an existing meeting link to a calendar event after explicit confirmation.",
    input_schema={"event_id": "calendar event id", "meeting_url": "meeting URL", "meeting_id": "optional meeting id", "idempotency_key": "required for write"},
    read_only=False,
    mutating=True,
    side_effectful=True,
    requires_confirmation=True,
    writes_to=["calendar_provider"],
    returns_observation_type="meeting_calendar_attachment",
    provider_constraints=["Requires calendar.write permission, explicit confirmation, idempotency key, and configured calendar provider."],
)
def meeting_attach_to_calendar_event(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    result = _meeting_provider_unavailable("meeting_attach_to_calendar_event", context)
    result["event_id"] = str(payload.get("event_id") or "")
    result["meeting_id"] = str(payload.get("meeting_id") or "")
    result["idempotency_key"] = str(payload.get("idempotency_key") or "")
    result["side_effects"] = [{"kind": "meeting_attach_to_calendar", "allowed": False, "reason": result["status"]}]
    return result


def _normalize_meeting_time(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    if normalized.get("start_time") or normalized.get("start"):
        return normalized
    natural_time = str(normalized.get("natural_time") or "").lower()
    if not natural_time:
        return normalized
    timezone_name = str(normalized.get("timezone") or normalized.get("time_zone") or "Asia/Shanghai")
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
        timezone_name = "Asia/Shanghai"
    now = datetime.now(tz)
    target = now
    if "后天" in natural_time:
        target = now + timedelta(days=2)
    elif "明天" in natural_time or "tomorrow" in natural_time:
        target = now + timedelta(days=1)
    elif "今天" in natural_time or "today" in natural_time:
        target = now
    elif "tonight" in natural_time or "今晚" in natural_time:
        target = now
    else:
        target = now + timedelta(days=1)

    if "上午" in natural_time or "morning" in natural_time:
        hour = 10
    elif "晚上" in natural_time or "今晚" in natural_time or "evening" in natural_time or "tonight" in natural_time:
        hour = 19
    else:
        hour = 14
    start = target.replace(hour=hour, minute=0, second=0, microsecond=0)
    if start <= now:
        start = start + timedelta(days=1)
    duration = max(5, min(int(normalized.get("duration_minutes") or 30), 480))
    end = start + timedelta(minutes=duration)
    normalized["start_time"] = start.isoformat()
    normalized["end_time"] = end.isoformat()
    normalized["time_zone"] = timezone_name
    normalized["duration_minutes"] = duration
    return normalized


def _extract_meeting_fields(result: dict[str, Any]) -> dict[str, Any]:
    content_text = str(result.get("content_text") or "").strip()
    candidates: list[dict[str, Any]] = []
    if content_text:
        parsed = _loads_json_dict(content_text)
        if parsed:
            candidates.append(parsed)
            body = parsed.get("body")
            if isinstance(body, str):
                body_payload = _loads_json_dict(body)
                if body_payload:
                    candidates.append(body_payload)
    raw_result = result.get("raw_result")
    if isinstance(raw_result, dict):
        candidates.append(raw_result)
    for candidate in candidates:
        meeting_info = _first_meeting_info(candidate)
        if not meeting_info:
            continue
        return {
            "meeting_id": str(meeting_info.get("meeting_id") or ""),
            "meeting_code": str(meeting_info.get("meeting_code") or ""),
            "meeting_url": str(meeting_info.get("join_url") or meeting_info.get("meeting_url") or ""),
            "join_url": str(meeting_info.get("join_url") or ""),
            "subject": str(meeting_info.get("subject") or ""),
            "start_time": str(meeting_info.get("start_time") or ""),
            "end_time": str(meeting_info.get("end_time") or ""),
        }
    return {}


def _loads_json_dict(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_meeting_info(payload: dict[str, Any]) -> dict[str, Any]:
    meetings = payload.get("meeting_info_list")
    if isinstance(meetings, list) and meetings and isinstance(meetings[0], dict):
        return dict(meetings[0])
    meeting = payload.get("meeting_info")
    if isinstance(meeting, dict):
        return dict(meeting)
    if any(key in payload for key in ("meeting_id", "join_url", "meeting_url")):
        return payload
    return {}
