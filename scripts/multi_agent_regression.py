from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.actor_context import ActorContext
from app.orchestration.dag_executor import execute_dag_plan, validate_dag_plan
from app.orchestration.domain_agents import build_domain_agent_catalog_dict
from app.orchestration.multi_agent_planner import plan_multi_agent_dag_request
from app.orchestration.registry import build_tool_registry
from app.orchestration.trace_evaluator import evaluate_agent_trace
from app.orchestration.types import AgentSubtask, AgentTaskPlan, OrchestrationContext


def _actor(conversation_id: str = "conversation-a") -> dict:
    return ActorContext(
        tenant_id="tenant-a",
        user_id="user-a",
        workspace_id="workspace-a",
        roles=("admin", "user", "viewer"),
        session_id="session-a",
        conversation_id=conversation_id,
    ).to_dict()


def _context() -> OrchestrationContext:
    return OrchestrationContext(
        session_id="session-a",
        conversation_id="conversation-a",
        message="安排一次客户跟进会议。",
        safe_message="安排一次客户跟进会议。",
        actor_context=_actor(),
    )


def main() -> None:
    registry = build_tool_registry()
    required_tools = {
        "calendar_list_events",
        "calendar_find_available_slots",
        "calendar_create_event",
        "meeting_create_tencent_meeting",
        "meeting_get_details",
        "meeting_list_user_meetings",
        "meeting_cancel_tencent_meeting",
        "meeting_attach_to_calendar_event",
        "inbound_mail_summary",
        "mail_invitation_draft",
        "privacy_scan",
    }
    missing = sorted(required_tools - set(registry))
    assert not missing, f"missing multi-agent tools: {missing}"

    catalog = build_domain_agent_catalog_dict()
    for agent in ("mail", "calendar", "meeting", "dlp", "enterprise_rag", "memory"):
        assert agent in catalog, f"missing domain agent: {agent}"

    valid_plan = AgentTaskPlan(
        original_message="找明天下午的空闲时间，然后创建腾讯会议。",
        subtasks=[
            AgentSubtask(
                task_id="step_1",
                agent="calendar",
                capability="calendar_find_available_slots",
                action="calendar_find_available_slots",
                parameters={"date": "tomorrow", "time_window": "afternoon", "duration_minutes": 30},
            ),
            AgentSubtask(
                task_id="step_2",
                agent="meeting",
                capability="meeting_create_tencent_meeting",
                action="meeting_create_tencent_meeting",
                parameters={"topic": "customer follow-up", "idempotency_key": "demo-key"},
                dependencies=["step_1"],
                confirmation_required=True,
                idempotency_key="demo-key",
            ),
        ],
    )
    assert validate_dag_plan(valid_plan.subtasks) == []

    cycle_plan = [
        AgentSubtask(task_id="a", capability="calendar_list_events", action="calendar_list_events", dependencies=["b"]),
        AgentSubtask(task_id="b", capability="calendar_find_available_slots", action="calendar_find_available_slots", dependencies=["a"]),
    ]
    cycle_errors = validate_dag_plan(cycle_plan)
    assert any(item["reason"] == "cycle_detected" for item in cycle_errors), cycle_errors

    read_plan = AgentTaskPlan(
        original_message="查日程和会议详情",
        subtasks=[
            AgentSubtask(
                task_id="cal",
                agent="calendar",
                capability="calendar_list_events",
                action="calendar_list_events",
                parameters={"start": "2026-05-24T09:00:00+08:00", "end": "2026-05-24T18:00:00+08:00"},
            ),
            AgentSubtask(
                task_id="meet",
                agent="meeting",
                capability="meeting_list_user_meetings",
                action="meeting_list_user_meetings",
                parameters={"page_size": 5},
            ),
        ],
    )
    read_result = execute_dag_plan(read_plan, _context())
    assert len(read_result["observations"]) == 2
    by_source = {item["source"]: item for item in read_result["observations"]}
    assert by_source["calendar_list_events"]["payload"].get("status") == "provider_not_configured", read_result
    assert by_source["meeting_list_user_meetings"]["status"] in {"completed", "unavailable", "provider_error"}, read_result

    write_plan = AgentTaskPlan(
        original_message="创建腾讯会议",
        subtasks=[
            AgentSubtask(
                task_id="create_meeting",
                agent="meeting",
                capability="meeting_create_tencent_meeting",
                action="meeting_create_tencent_meeting",
                parameters={"topic": "customer follow-up", "idempotency_key": "meeting-key"},
                confirmation_required=True,
                idempotency_key="meeting-key",
            )
        ],
    )
    write_result = execute_dag_plan(write_plan, _context())
    assert write_result["observations"][0]["observation_type"] == "confirmation_required", write_result
    assert write_result["observations"][0]["status"] == "needs_confirmation", write_result

    blocked_plan = AgentTaskPlan(
        original_message="依赖失败后不能继续",
        subtasks=[
            AgentSubtask(task_id="find", agent="calendar", capability="calendar_find_available_slots", action="calendar_find_available_slots", parameters={}),
            AgentSubtask(task_id="create", agent="meeting", capability="meeting_create_tencent_meeting", action="meeting_create_tencent_meeting", parameters={"idempotency_key": "blocked"}, dependencies=["find"]),
        ],
    )
    blocked_result = execute_dag_plan(blocked_plan, _context())
    assert any(item["observation_type"] == "dependency_blocked" for item in blocked_result["observations"]), blocked_result
    trace = evaluate_agent_trace({"tool_observations": blocked_result["observations"], "tool_calls": [], "actor_context": _context().actor_context})
    assert any(issue["code"] == "multi_agent_dependency_blocked" for issue in trace["issues"]), trace

    cross_plan = plan_multi_agent_dag_request(
        message="请总结今天客户邮件，找明天下午空闲时间，并创建一个腾讯会议。",
        actor_context=_actor("conversation-cross"),
    )
    assert cross_plan is not None
    cross_steps = {item.task_id: item for item in cross_plan.subtasks}
    assert {"mail_context", "availability", "invitation_dlp", "create_meeting", "invitation_draft"} <= set(cross_steps), cross_steps
    assert cross_steps["invitation_dlp"].dependencies == ["mail_context"], cross_steps["invitation_dlp"]
    assert sorted(cross_steps["create_meeting"].dependencies) == ["availability", "invitation_dlp"], cross_steps["create_meeting"]
    assert cross_steps["create_meeting"].confirmation_required is True
    assert cross_steps["invitation_draft"].dependencies == ["create_meeting"], cross_steps["invitation_draft"]

    print(
        json.dumps(
            {
                "ok": True,
                "domain_agents": sorted(catalog),
                "tools": sorted(required_tools),
                "cross_domain_steps": sorted(cross_steps),
                "trace_score": trace["score"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
