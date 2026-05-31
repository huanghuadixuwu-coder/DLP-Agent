from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.orchestration.types import AgentSubtask, AgentTaskPlan


_MEETING_CUES = ("腾讯会议", "会议", "meeting", "wemeet", "tencent meeting")
_CALENDAR_CUES = ("日程", "日历", "空闲", "档期", "calendar", "schedule", "available")
_MAIL_CONTEXT_CUES = ("客户邮件", "邮件", "mail", "email", "customer email", "inbound")
_LIST_CUES = ("列出", "查看", "查询", "查一下", "我的会议", "会议列表", "list", "show", "upcoming")
_CREATE_CUES = ("创建", "新建", "安排", "预约", "发起", "约", "create", "schedule", "book")
_CANCEL_CUES = ("取消", "撤销", "cancel")
_AVAILABILITY_CUES = ("找空闲", "空闲时间", "find available", "available slot", "free time")
_TIME_MARKERS = (
    "今天",
    "明天",
    "后天",
    "上午",
    "下午",
    "晚上",
    "今晚",
    "周一",
    "周二",
    "周三",
    "周四",
    "周五",
    "周六",
    "周日",
    "today",
    "tomorrow",
    "morning",
    "afternoon",
    "evening",
    "tonight",
    "next monday",
    "next tuesday",
    "next wednesday",
    "next thursday",
    "next friday",
)


def plan_multi_agent_dag_request(
    *,
    message: str,
    actor_context: dict[str, Any] | None = None,
) -> AgentTaskPlan | None:
    """Return a high-confidence domain-agent DAG plan, or None for ReAct.

    The planner only selects structural steps and safety boundaries. It does
    not generate user-visible business text.
    """

    text = (message or "").strip()
    if not text:
        return None
    lowered = text.lower()
    has_meeting = _contains_any(text, lowered, _MEETING_CUES)
    has_calendar = _contains_any(text, lowered, _CALENDAR_CUES)
    if not (has_meeting or has_calendar):
        return None

    has_mail_context = _contains_any(text, lowered, _MAIL_CONTEXT_CUES)
    if has_meeting and has_mail_context and _contains_any(text, lowered, _CREATE_CUES):
        return _cross_domain_mail_meeting_plan(text, actor_context)

    if has_meeting and _contains_any(text, lowered, _LIST_CUES) and not _contains_any(text, lowered, _CREATE_CUES + _CANCEL_CUES):
        return AgentTaskPlan(
            original_message=text,
            subtasks=[
                AgentSubtask(
                    task_id="step_1",
                    agent="meeting",
                    capability="meeting_list_user_meetings",
                    action="meeting_list_user_meetings",
                    parameters={"page_size": 10, "timezone": "Asia/Shanghai"},
                    expected_observation_type="meeting_list",
                )
            ],
            aggregation_strategy="status_and_next_action",
            planner_reason="High-confidence Tencent Meeting read request routed to domain-agent DAG.",
            confidence=0.88,
            planner_type="multi_agent_dag_heuristic",
        )

    if has_calendar and not has_meeting and _contains_any(text, lowered, _LIST_CUES):
        return AgentTaskPlan(
            original_message=text,
            subtasks=[
                AgentSubtask(
                    task_id="step_1",
                    agent="calendar",
                    capability="calendar_list_events",
                    action="calendar_list_events",
                    parameters={"start": "", "end": "", "timezone": "Asia/Shanghai"},
                    expected_observation_type="calendar_events",
                )
            ],
            aggregation_strategy="status_and_next_action",
            planner_reason="High-confidence calendar read request routed to domain-agent DAG.",
            confidence=0.82,
            planner_type="multi_agent_dag_heuristic",
        )

    if has_meeting and _contains_any(text, lowered, _CREATE_CUES):
        idempotency_key = _idempotency_key(text, actor_context)
        return AgentTaskPlan(
            original_message=text,
            subtasks=[
                AgentSubtask(
                    task_id="step_1",
                    agent="meeting",
                    capability="meeting_create_tencent_meeting",
                    action="meeting_create_tencent_meeting",
                    parameters={
                        "topic": _extract_topic(text) or "meeting",
                        "natural_time": _extract_natural_time(text),
                        "timezone": "Asia/Shanghai",
                        "attendees": _extract_attendees(text),
                        "duration_minutes": _extract_duration_minutes(text),
                        "idempotency_key": idempotency_key,
                    },
                    risk="medium",
                    confirmation_required=True,
                    idempotency_key=idempotency_key,
                    expected_observation_type="meeting_write",
                    resource_scope={"provider": "tencent_meeting"},
                    mutating=True,
                    parallelizable=False,
                )
            ],
            aggregation_strategy="status_and_next_action",
            planner_reason="Tencent Meeting creation request routed to DAG confirmation boundary.",
            confidence=0.84,
            planner_type="multi_agent_dag_heuristic",
        )

    if has_calendar and _contains_any(text, lowered, _CREATE_CUES):
        idempotency_key = _idempotency_key(text, actor_context)
        return AgentTaskPlan(
            original_message=text,
            subtasks=[
                AgentSubtask(
                    task_id="step_1",
                    agent="calendar",
                    capability="calendar_create_event",
                    action="calendar_create_event",
                    parameters={
                        "title": _extract_topic(text) or "calendar event",
                        "natural_time": _extract_natural_time(text),
                        "timezone": "Asia/Shanghai",
                        "attendees": _extract_attendees(text),
                        "duration_minutes": _extract_duration_minutes(text),
                        "idempotency_key": idempotency_key,
                    },
                    risk="medium",
                    confirmation_required=True,
                    idempotency_key=idempotency_key,
                    expected_observation_type="calendar_event_write",
                    resource_scope={"provider": "calendar"},
                    mutating=True,
                    parallelizable=False,
                )
            ],
            aggregation_strategy="status_and_next_action",
            planner_reason="Calendar creation request routed to DAG confirmation boundary.",
            confidence=0.8,
            planner_type="multi_agent_dag_heuristic",
        )

    return None


def render_dag_plan(plan: AgentTaskPlan) -> dict[str, Any]:
    return {
        "original_message": plan.original_message,
        "aggregation_strategy": plan.aggregation_strategy,
        "planner_reason": plan.planner_reason,
        "confidence": plan.confidence,
        "planner_type": plan.planner_type,
        "subtasks": [asdict(item) for item in plan.subtasks],
    }


def _cross_domain_mail_meeting_plan(text: str, actor_context: dict[str, Any] | None) -> AgentTaskPlan:
    idempotency_key = _idempotency_key(text, actor_context)
    lowered = text.lower()
    asks_for_availability = _contains_any(text, lowered, _AVAILABILITY_CUES) or _contains_any(text, lowered, _CALENDAR_CUES)
    subtasks = [
        AgentSubtask(
            task_id="mail_context",
            agent="mail",
            capability="inbound_mail_summary",
            action="inbound_mail_summary",
            parameters={"since": "", "until": "", "limit": 5},
            expected_observation_type="mailbox_status",
        ),
        AgentSubtask(
            task_id="invitation_dlp",
            agent="dlp",
            capability="privacy_scan",
            action="privacy_scan",
            parameters={"message": text, "context_budget": 1200},
            dependencies=["mail_context"],
            expected_observation_type="privacy_scan",
        ),
    ]
    create_dependencies = ["invitation_dlp"]
    if asks_for_availability:
        subtasks.insert(
            1,
            AgentSubtask(
                task_id="availability",
                agent="calendar",
                capability="calendar_find_available_slots",
                action="calendar_find_available_slots",
                parameters={
                    "date": _extract_natural_time(text) or text,
                    "time_window": _extract_time_window(text),
                    "duration_minutes": _extract_duration_minutes(text),
                    "attendees": _extract_attendees(text),
                },
                expected_observation_type="calendar_availability",
            ),
        )
        create_dependencies.insert(0, "availability")
    subtasks.append(
        AgentSubtask(
            task_id="create_meeting",
            agent="meeting",
            capability="meeting_create_tencent_meeting",
            action="meeting_create_tencent_meeting",
            parameters={
                "topic": _extract_topic(text) or "customer follow-up",
                "natural_time": _extract_natural_time(text),
                "timezone": "Asia/Shanghai",
                "attendees": _extract_attendees(text),
                "duration_minutes": _extract_duration_minutes(text),
                "idempotency_key": idempotency_key,
            },
            dependencies=create_dependencies,
            risk="medium",
            confirmation_required=True,
            idempotency_key=idempotency_key,
            expected_observation_type="meeting_write",
            resource_scope={"provider": "tencent_meeting"},
            mutating=True,
            parallelizable=False,
        )
    )
    subtasks.append(
        AgentSubtask(
            task_id="invitation_draft",
            agent="mail",
            capability="mail_invitation_draft",
            action="mail_invitation_draft",
            parameters={"source_request": text, "recipient_hint": ""},
            dependencies=["create_meeting"],
            expected_observation_type="mail_invitation_draft",
        )
    )
    return AgentTaskPlan(
        original_message=text,
        subtasks=subtasks,
        aggregation_strategy="status_and_next_action",
        planner_reason="Cross-domain mail/calendar/meeting request routed to a governed DAG with DLP before side effects.",
        confidence=0.86 if asks_for_availability else 0.8,
        planner_type="multi_agent_dag_heuristic",
    )


def _contains_any(text: str, lowered: str, cues: tuple[str, ...]) -> bool:
    return any((cue in text) if _has_cjk(cue) else (cue in lowered) for cue in cues)


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _idempotency_key(message: str, actor_context: dict[str, Any] | None) -> str:
    actor = actor_context or {}
    base = "|".join(
        [
            str(actor.get("tenant_id") or "local-dev"),
            str(actor.get("user_id") or "local-user"),
            str(actor.get("conversation_id") or ""),
            message.strip(),
        ]
    )
    return f"dag-{uuid5(NAMESPACE_URL, base).hex[:24]}"


def _extract_topic(message: str) -> str:
    patterns = [
        r"(?:主题|标题)\s*(?:是|为|:|：)?\s*([^，。?.;；\n]+)",
        r"(?:topic|subject|title)\s*(?:is|:|-)?\s*([^，。?.;；\n]+)",
        r"(?:about)\s+([^，。?.;；\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return _strip_time_suffix(match.group(1).strip())
    return ""


def _extract_natural_time(message: str) -> str:
    lowered = message.lower()
    matched = [marker for marker in _TIME_MARKERS if marker.lower() in lowered]
    return " ".join(matched)


def _extract_attendees(message: str) -> list[str]:
    return sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)))


def _extract_time_window(message: str) -> str:
    lowered = message.lower()
    for marker in ("上午", "下午", "晚上", "morning", "afternoon", "evening"):
        if marker.lower() in lowered:
            return marker
    return ""


def _extract_duration_minutes(message: str) -> int:
    match = re.search(r"(\d{1,3})\s*(?:分钟|mins?|minutes?)", message, flags=re.IGNORECASE)
    if match:
        return max(5, min(int(match.group(1)), 480))
    return 30


def _strip_time_suffix(topic: str) -> str:
    cleaned = topic.strip(" -:：")
    lowered = cleaned.lower()
    cut_positions = [
        lowered.find(marker.lower())
        for marker in _TIME_MARKERS
        if marker.lower() in lowered
    ]
    cut_positions = [pos for pos in cut_positions if pos > 0]
    if cut_positions:
        cleaned = cleaned[: min(cut_positions)].strip(" -:：")
    return cleaned
