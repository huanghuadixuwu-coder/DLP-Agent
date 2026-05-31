from __future__ import annotations

from typing import Any

from app.orchestration.tool_discovery import register_tool
from app.orchestration.types import OrchestrationContext


@register_tool(
    name="mail_invitation_draft",
    description="Build a structured invitation draft state from meeting and mail observations without sending email.",
    input_schema={"recipient_hint": "optional recipient", "subject_hint": "optional subject", "source_request": "original user request"},
    read_only=True,
    writes_to=["draft_state"],
    returns_observation_type="mail_invitation_draft",
    provider_constraints=["Draft only; does not send mail and must be rendered by the final renderer."],
)
def mail_invitation_draft(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    meeting_payload = _find_meeting_payload(dependency_payloads)
    normalized_request = dict(meeting_payload.get("normalized_request") or {})
    content_text = str(meeting_payload.get("content_text") or meeting_payload.get("summary") or "")
    meeting_url = _extract_url(meeting_payload)
    meeting_id = str(meeting_payload.get("meeting_id") or meeting_payload.get("meeting_code") or "")
    topic = str(normalized_request.get("topic") or payload.get("subject_hint") or "会议邀请").strip()
    recipient_hint = str(payload.get("recipient_hint") or "").strip()
    missing_fields = []
    if not recipient_hint:
        missing_fields.append("recipient")
    if not meeting_url and not content_text:
        missing_fields.append("meeting_link_or_details")
    draft_state = {
        "draft_kind": "meeting_invitation",
        "status": "draft_ready" if not missing_fields else "needs_clarification",
        "recipient_hint": recipient_hint,
        "subject": f"{topic} - 会议邀请",
        "meeting": {
            "topic": topic,
            "meeting_id": meeting_id,
            "meeting_url": meeting_url,
            "start_time": str(normalized_request.get("start_time") or ""),
            "end_time": str(normalized_request.get("end_time") or ""),
            "timezone": str(normalized_request.get("timezone") or "Asia/Shanghai"),
            "provider_summary": content_text,
        },
        "body_constraints": {
            "source_policy": "meeting_result_only",
            "send_requires_dlp": True,
            "send_requires_confirmation": True,
            "do_not_send_from_this_tool": True,
        },
        "missing_fields": missing_fields,
        "source_request": str(payload.get("source_request") or context.display_message or context.message or ""),
    }
    return {
        "status": draft_state["status"],
        "summary": "Meeting invitation draft state prepared." if not missing_fields else "Meeting invitation draft needs more fields.",
        "draft_state": draft_state,
        "missing_fields": missing_fields,
        "actor_context": dict(context.actor_context or {}),
    }


def _find_meeting_payload(dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    for value in dependency_payloads.values():
        if not isinstance(value, dict):
            continue
        if value.get("provider") in {"tencent_meeting_mcp", "tencent_meeting"} or value.get("normalized_request"):
            return value
        nested = value.get("result")
        if isinstance(nested, dict) and (nested.get("provider") or nested.get("normalized_request")):
            return nested
    return {}


def _extract_url(payload: dict[str, Any]) -> str:
    for key in ("meeting_url", "join_url", "url"):
        value = str(payload.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    text = str(payload.get("content_text") or payload.get("summary") or "")
    for token in text.replace("\n", " ").split():
        cleaned = token.strip(".,;，。；()（）[]【】")
        if cleaned.startswith(("http://", "https://")):
            return cleaned
    return ""
