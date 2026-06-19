from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.task_worker as task_worker
from app.communication.brief_store import get_latest_brief_for_thread, upsert_communication_brief
from app.communication.types import CommunicationBrief, CommunicationThreadRef
from app.task_store import create_dlp_task, get_dlp_task


def main() -> None:
    suffix = uuid4().hex[:8]
    idempotency_key = f"meeting-worker-regression-key-{suffix}"
    global_idempotency_key = f"meeting-worker-global-regression-key-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-meeting-worker-regression-{suffix}",
        "user_id": f"user-meeting-worker-regression-{suffix}",
        "workspace_id": "workspace-meeting-worker-regression",
        "session_id": f"session-meeting-worker-regression-{suffix}",
        "conversation_id": f"conversation-meeting-worker-regression-{suffix}",
    }
    thread_id = f"thread-meeting-worker-regression-{suffix}"
    brief_id = f"brief-meeting-worker-regression-{suffix}"
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Worker regression customer escalation",
        participants=["customer@example.com", "rep@example.com"],
        actor_context=actor_context,
    )
    stored_brief = upsert_communication_brief(
        CommunicationBrief(
            brief_id=brief_id,
            conversation_id=actor_context["conversation_id"],
            thread_ref=thread_ref,
            employee_goal="Escalate the customer thread to a meeting.",
            customer_context_summary="Customer asked for a meeting follow-up.",
            recommended_next_action="schedule_meeting",
            confidence=0.82,
            actor_context=actor_context,
        ),
        actor_context=actor_context,
        refresh_reason="meeting_worker_regression_seed",
    )
    assert stored_brief, stored_brief

    meeting_dispatches: list[str] = []

    def _fake_dispatch(action, parameters, context, dependency_payloads, *, allow_side_effects=False):
        if action == "mail_invitation_draft":
            from app.orchestration.tools.mail_workflow_tools import mail_invitation_draft

            assert allow_side_effects is False
            return {
                "ok": True,
                "action": action,
                "result": mail_invitation_draft(parameters, context, dependency_payloads),
                "error": "",
                "observation_type": "mail_invitation_draft",
                "side_effectful": False,
                "requires_confirmation": False,
            }
        assert action == "meeting_create_tencent_meeting", action
        assert allow_side_effects is True
        meeting_dispatches.append(str(parameters.get("idempotency_key") or ""))
        if parameters["idempotency_key"] == idempotency_key:
            assert parameters["thread_id"] == thread_id
            assert parameters["source_brief_id"] == brief_id
            assert parameters["brief_id"] == brief_id
            assert parameters["actor_context"]["tenant_id"] == actor_context["tenant_id"]
        elif parameters["idempotency_key"] == global_idempotency_key:
            assert parameters["global_mode"] is True
            assert parameters["server_global_mode"] is True
            assert parameters["global_mode_source"] == "agent_chat_context"
            assert not parameters.get("thread_id")
            assert not parameters.get("source_brief_id")
            assert parameters["actor_context"]["tenant_id"] == actor_context["tenant_id"]
        else:
            raise AssertionError(parameters)
        return {
            "ok": True,
            "action": action,
            "result": {
                "ok": True,
                "status": "completed",
                "summary": "Fake Tencent Meeting created by worker regression.",
                "provider": "tencent_meeting_mcp",
                "meeting_id": "fake-meeting-id",
                "meeting_url": "https://meeting.tencent.com/fake-meeting-id",
                "thread_id": f"fake-provider-thread-{suffix}",
                "source_brief_id": f"fake-provider-brief-{suffix}",
                "brief_id": f"fake-provider-brief-{suffix}",
                "idempotency_key": f"fake-provider-idempotency-{suffix}",
                "actor_context": {
                    "tenant_id": f"fake-provider-tenant-{suffix}",
                    "user_id": f"fake-provider-user-{suffix}",
                    "workspace_id": "fake-provider-workspace",
                },
                "normalized_request": {
                    "topic": "worker regression",
                    "start_time": "2026-05-25T14:00:00+08:00",
                    "end_time": "2026-05-25T14:30:00+08:00",
                    "timezone": "Asia/Shanghai",
                },
                "created_resource_ids": ["fake-meeting-id"],
            },
            "error": "",
            "observation_type": "meeting_write",
            "side_effectful": True,
            "requires_confirmation": True,
        }

    task_worker.dispatch_tool_call = _fake_dispatch
    task = create_dlp_task(
        task_type="domain_meeting",
        session_id=actor_context["session_id"],
        conversation_id=actor_context["conversation_id"],
        message_raw="确认创建腾讯会议。",
        request_message="确认创建腾讯会议。",
        destination_email="",
        requested_action="meeting_create_tencent_meeting",
        status="queued",
        domain_action="meeting_create_tencent_meeting",
        domain_payload={
            "topic": "worker regression",
            "natural_time": "tomorrow afternoon",
            "timezone": "Asia/Shanghai",
            "duration_minutes": 30,
            "idempotency_key": idempotency_key,
            "thread_id": thread_id,
            "source_brief_id": brief_id,
            "brief_id": brief_id,
            "actor_context": actor_context,
            "communication_role": "escalation_provider",
            "communication_input_kind": "meeting_escalation_candidate",
            "communication_closeout_owner": "mail_agent",
            "_dag_plan": {
                "subtasks": [
                    {
                        "task_id": "create_meeting",
                        "agent": "meeting",
                        "action": "meeting_create_tencent_meeting",
                        "capability": "meeting_create_tencent_meeting",
                        "parameters": {
                            "topic": "worker regression",
                            "natural_time": "tomorrow afternoon",
                            "duration_minutes": 30,
                            "idempotency_key": idempotency_key,
                            "thread_id": thread_id,
                            "source_brief_id": brief_id,
                            "brief_id": brief_id,
                            "actor_context": actor_context,
                        },
                    },
                    {
                        "task_id": "invitation_draft",
                        "agent": "mail",
                        "action": "mail_invitation_draft",
                        "capability": "mail_invitation_draft",
                        "parameters": {"source_request": "draft invitation after meeting creation"},
                        "dependencies": ["create_meeting"],
                    },
                ]
            },
        },
        idempotency_key=idempotency_key,
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
    )
    result = task_worker.process_domain_meeting_task.run(str(task["task_id"]))
    updated = get_dlp_task(str(task["task_id"]))
    assert result["ok"] is True, result
    assert result["status"] == "completed", result
    assert updated is not None
    assert updated["status"] == "completed", updated
    assert updated["domain_result"]["ok"] is True, updated
    assert updated["domain_result"]["communication_role"] == "escalation_provider", updated
    assert updated["domain_result"]["communication_input_kind"] == "meeting_result", updated
    assert updated["domain_result"]["communication_closeout_owner"] == "mail_agent", updated
    assert updated["domain_result"]["result"]["communication_role"] == "escalation_provider", updated
    assert updated["domain_result"]["result"]["communication_input_kind"] == "meeting_result", updated
    assert updated["domain_result"]["result"]["meeting_id"] == "fake-meeting-id", updated
    assert updated["domain_result"]["result"]["thread_id"] == thread_id, updated
    assert updated["domain_result"]["result"]["source_brief_id"] == brief_id, updated
    assert updated["domain_result"]["result"]["brief_id"] == brief_id, updated
    assert updated["domain_result"]["result"]["idempotency_key"] == idempotency_key, updated
    assert updated["domain_result"]["result"]["actor_context"]["tenant_id"] == actor_context["tenant_id"], updated
    assert updated["domain_result"]["brief_update"]["ok"] is True, updated
    assert updated["domain_result"]["brief_update"]["brief_id"] == brief_id, updated
    post_results = list(updated["domain_result"].get("post_confirm_results") or [])
    assert post_results and post_results[0]["action"] == "mail_invitation_draft", updated
    draft_state = post_results[0]["result"]["draft_state"]
    assert draft_state["draft_kind"] == "meeting_invitation", updated
    assert draft_state["meeting"]["meeting_id"] == "fake-meeting-id", updated
    assert draft_state["meeting"]["meeting_url"].startswith("https://meeting.tencent.com/"), updated
    latest_brief = get_latest_brief_for_thread(thread_id, actor_context=actor_context)
    latest_payload = dict((latest_brief or {}).get("brief") or {})
    assert latest_payload.get("recommended_next_action") == "draft_meeting_followup_via_mail_agent", latest_brief
    meeting_refs = [
        item for item in list(latest_payload.get("grounding_refs") or [])
        if isinstance(item, dict) and item.get("kind") == "meeting_result"
    ]
    assert meeting_refs and meeting_refs[-1]["meeting_id"] == "fake-meeting-id", latest_brief
    assert meeting_dispatches == [idempotency_key], meeting_dispatches

    malformed_key = f"meeting-worker-malformed-key-{suffix}"
    malformed_task = create_dlp_task(
        task_type="domain_meeting",
        session_id=f"session-meeting-worker-malformed-{suffix}",
        conversation_id=f"conversation-meeting-worker-malformed-{suffix}",
        message_raw="confirm malformed meeting",
        request_message="confirm malformed meeting",
        destination_email="",
        requested_action="meeting_create_tencent_meeting",
        status="queued",
        domain_action="meeting_create_tencent_meeting",
        domain_payload={
            "topic": "malformed worker regression",
            "natural_time": "tomorrow afternoon",
            "timezone": "Asia/Shanghai",
            "duration_minutes": 30,
            "idempotency_key": malformed_key,
            "global_mode": True,
            "server_global_mode": True,
            "global_mode_source": "agent_chat_context",
        },
        idempotency_key=malformed_key,
        tenant_id=f"tenant-meeting-worker-malformed-{suffix}",
        user_id=f"user-meeting-worker-malformed-{suffix}",
        workspace_id="workspace-meeting-worker-regression",
    )
    malformed_result = task_worker.process_domain_meeting_task.run(str(malformed_task["task_id"]))
    malformed_updated = get_dlp_task(str(malformed_task["task_id"]))
    assert malformed_result["ok"] is False, malformed_result
    assert malformed_result["status"] == "failed", malformed_result
    assert malformed_result["error"] == "meeting_escalation_context_required", malformed_result
    assert malformed_updated is not None, malformed_updated
    assert malformed_updated["status"] == "failed", malformed_updated
    malformed_domain_result = dict(malformed_updated.get("domain_result") or {})
    assert malformed_domain_result.get("status") == "blocked", malformed_updated
    assert malformed_domain_result.get("error") == "meeting_escalation_context_required", malformed_updated
    malformed_missing = set(malformed_domain_result.get("missing_fields") or [])
    assert {"thread_id", "source_brief_id", "actor_context"}.issubset(malformed_missing), malformed_updated
    assert meeting_dispatches == [idempotency_key], meeting_dispatches

    global_task = create_dlp_task(
        task_type="domain_meeting",
        session_id=f"session-meeting-worker-global-{suffix}",
        conversation_id=f"conversation-meeting-worker-global-{suffix}",
        message_raw="confirm trusted global meeting",
        request_message="confirm trusted global meeting",
        destination_email="",
        requested_action="meeting_create_tencent_meeting",
        status="queued",
        domain_action="meeting_create_tencent_meeting",
        domain_payload={
            "topic": "trusted global worker regression",
            "natural_time": "tomorrow afternoon",
            "timezone": "Asia/Shanghai",
            "duration_minutes": 30,
            "idempotency_key": global_idempotency_key,
            "global_mode": True,
            "server_global_mode": True,
            "global_mode_source": "agent_chat_context",
            "_server_global_authorized": True,
            "_server_global_authorized_by": "agent_chat_context",
            "actor_context": actor_context,
        },
        idempotency_key=global_idempotency_key,
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
    )
    global_result = task_worker.process_domain_meeting_task.run(str(global_task["task_id"]))
    global_updated = get_dlp_task(str(global_task["task_id"]))
    assert global_result["ok"] is True, global_result
    assert global_result["status"] == "completed", global_result
    assert global_updated is not None, global_updated
    assert global_updated["status"] == "completed", global_updated
    assert global_updated["domain_result"]["result"]["thread_id"] == "", global_updated
    assert global_updated["domain_result"]["result"]["source_brief_id"] == "", global_updated
    assert global_updated["domain_result"]["result"]["brief_id"] == "", global_updated
    assert global_updated["domain_result"]["result"]["idempotency_key"] == global_idempotency_key, global_updated
    assert global_updated["domain_result"]["result"]["actor_context"]["tenant_id"] == actor_context["tenant_id"], global_updated
    assert meeting_dispatches == [idempotency_key, global_idempotency_key], meeting_dispatches
    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task["task_id"],
                "malformed_task_id": malformed_task["task_id"],
                "global_task_id": global_task["task_id"],
                "worker_result": result,
                "malformed_worker_result": malformed_result,
                "global_worker_result": global_result,
                "final_status": updated["status"],
                "domain_result": updated["domain_result"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
