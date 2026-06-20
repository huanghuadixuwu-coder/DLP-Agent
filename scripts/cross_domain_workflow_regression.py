from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
import app.task_worker as task_worker
from app.auth_store import create_login_code, verify_login_code
from app.communication.brief_store import get_latest_brief_for_thread, upsert_communication_brief
from app.communication.thread_store import set_active_communication_thread, upsert_thread_projection
from app.communication.types import CommunicationBrief, CommunicationThreadRef

app = main_module.app


def _post(
    client: TestClient,
    *,
    message: str,
    session_id: str,
    conversation_id: str,
    actor_context: dict,
    headers: dict,
) -> dict:
    response = client.post(
        "/agent/chat",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": message,
            "tenant_id": actor_context["tenant_id"],
            "user_id": actor_context["user_id"],
            "workspace_id": actor_context["workspace_id"],
            "roles": list(actor_context.get("roles") or ["admin", "user", "viewer"]),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _install_test_renderers() -> None:
    def _write_test_turn(result: dict, conversation_id: str, actor_context: dict | None = None) -> tuple[str, bool]:
        answer = str(result.get("answer") or "test answer")
        exchange = main_module.append_exchange(
            session_id=str(result["session_id"]),
            conversation_id=conversation_id,
            question=str(result.get("display_message") or result.get("message") or ""),
            answer=answer,
            redacted_question=str(result.get("display_message") or result.get("safe_message") or result.get("message") or ""),
            answer_summary=answer,
            intent=str(result.get("intent") or ""),
            tool_calls=list(result.get("tool_calls") or []),
            citations=list(result.get("citations") or []),
            debug_payload=main_module._build_debug_snapshot(result, conversation_id),
            actor_context=actor_context or result.get("actor_context") or {},
        )
        return str(exchange["assistant_turn"]["turn_id"]), False

    main_module._write_unified_conversation_memory = _write_test_turn
    main_module.render_final_answer = lambda **kwargs: {
        "answer": "Structured observations are ready for the next governed step.",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }
    main_module.render_mail_authoring = lambda **kwargs: {
        "user_message": "Please confirm sending the meeting invitation.",
        "body_for_sending": "Customer email summary: the customer asked for a follow-up meeting. Meeting details are included from the completed Tencent Meeting task.",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }


def main() -> None:
    _install_test_renderers()
    enqueued_meetings: list[str] = []
    enqueued_dlp: list[str] = []
    enqueued_email: list[str] = []

    main_module.enqueue_meeting_task = lambda task_id: enqueued_meetings.append(task_id) or task_id
    main_module.enqueue_dlp_risk_task = lambda task_id: enqueued_dlp.append(task_id) or task_id
    main_module.enqueue_email_send_task = lambda task_id: enqueued_email.append(task_id) or task_id
    task_worker.enqueue_email_send_task = lambda task_id: enqueued_email.append(task_id) or task_id
    task_worker.call_mcp_tool = lambda tool_name, args: {
        "ok": True,
        "result": {
            "ok": True,
            "provider": "smtp_regression",
            "to_email": str(args.get("to_email") or ""),
            "sent_at": "2026-05-25T00:00:00+00:00",
            "attachments_sent": 0,
            "error": "",
        },
    }

    def _fake_dispatch(action, parameters, context, dependency_payloads, *, allow_side_effects=False):
        if action == "mail_invitation_draft":
            from app.orchestration.tools.mail_workflow_tools import mail_invitation_draft

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
        assert parameters["thread_id"].startswith("thread-cross-domain-"), parameters
        assert parameters["source_brief_id"].startswith("brief-cross-domain-"), parameters
        assert parameters["actor_context"]["tenant_id"] == actor_context["tenant_id"], parameters
        return {
            "ok": True,
            "action": action,
            "result": {
                "ok": True,
                "status": "completed",
                "summary": "Regression Tencent Meeting created.",
                "provider": "tencent_meeting_mcp_regression",
                "meeting_id": "cross-domain-meeting-id",
                "meeting_code": "123456789",
                "meeting_url": "https://meeting.tencent.com/cross-domain-regression",
                "normalized_request": {
                    "topic": "customer follow-up",
                    "start_time": "2026-05-26T14:00:00+08:00",
                    "end_time": "2026-05-26T14:30:00+08:00",
                    "timezone": "Asia/Shanghai",
                },
                "created_resource_ids": ["cross-domain-meeting-id"],
            },
            "error": "",
            "observation_type": "meeting_write",
            "side_effectful": True,
            "requires_confirmation": True,
        }

    task_worker.dispatch_tool_call = _fake_dispatch

    session_id = "session-cross-domain-workflow"
    login = create_login_code("cross-domain@example.com")
    auth = verify_login_code("cross-domain@example.com", str(login["code"]))
    auth_actor = dict(auth["user"])
    actor_context = {
        "tenant_id": str(auth_actor["tenant_id"]),
        "user_id": str(auth_actor["user_id"]),
        "workspace_id": str(auth_actor["workspace_id"]),
        "roles": list(auth_actor.get("roles") or ["admin", "user", "viewer"]),
        "session_id": session_id,
    }
    headers = {"x-auth-session": str(auth["session_token"])}
    with TestClient(app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, actor_context)
        conversation_id = str(conversation["conversation_id"])
        actor_context["conversation_id"] = conversation_id
        thread_id = f"thread-cross-domain-{conversation_id}"
        brief_id = f"brief-cross-domain-{conversation_id}"
        thread_ref = CommunicationThreadRef(
            thread_id=thread_id,
            source="mail",
            subject="Customer follow-up meeting",
            participants=["customer@example.com", "rep@example.com"],
            latest_summary="Customer asked for a follow-up meeting.",
            actor_context=actor_context,
        )
        upsert_thread_projection(thread_ref, actor_context=actor_context)
        assert set_active_communication_thread(thread_id, actor_context=actor_context), thread_id
        upsert_communication_brief(
            CommunicationBrief(
                brief_id=brief_id,
                conversation_id=conversation_id,
                thread_ref=thread_ref,
                employee_goal="Schedule a customer follow-up meeting and prepare the invitation.",
                customer_context_summary="Customer asked for a follow-up meeting.",
                recommended_next_action="schedule_meeting",
                confidence=0.84,
                actor_context=actor_context,
            ),
            actor_context=actor_context,
            refresh_reason="cross_domain_meeting_escalation_seed",
        )

        plan_response = _post(
            client,
            message="Summarize the latest customer email and create a Tencent Meeting about customer follow-up tomorrow afternoon.",
            session_id=session_id,
            conversation_id=conversation_id,
            actor_context=actor_context,
            headers=headers,
        )
        pending_domain = dict(plan_response.get("pending_confirmation") or {})
        assert pending_domain.get("tool_name") == "meeting_create_tencent_meeting", pending_domain
        pending_tool_input = dict(pending_domain.get("tool_input") or {})
        assert pending_tool_input.get("thread_id") == thread_id, pending_domain
        assert pending_tool_input.get("source_brief_id") == brief_id, pending_domain
        assert any(call.get("tool_name") == "inbound_mail_summary" for call in plan_response.get("tool_calls", [])), plan_response.get("tool_calls")
        assert any(call.get("tool_name") == "privacy_scan" for call in plan_response.get("tool_calls", [])), plan_response.get("tool_calls")

        confirm_meeting = _post(
            client,
            message="confirm",
            session_id=session_id,
            conversation_id=conversation_id,
            actor_context=actor_context,
            headers=headers,
        )
        meeting_task_id = str(confirm_meeting.get("task_id") or "")
        assert meeting_task_id and confirm_meeting.get("task_status") == "queued", confirm_meeting
        assert enqueued_meetings == [meeting_task_id], enqueued_meetings

        meeting_worker_result = task_worker.process_domain_meeting_task.run(meeting_task_id)
        assert meeting_worker_result["status"] == "completed", meeting_worker_result
        assert meeting_worker_result["result"]["communication_role"] == "escalation_provider", meeting_worker_result
        assert meeting_worker_result["result"]["communication_input_kind"] == "meeting_result", meeting_worker_result
        assert meeting_worker_result["result"]["thread_id"] == thread_id, meeting_worker_result
        latest_brief = get_latest_brief_for_thread(thread_id, actor_context=actor_context)
        assert dict((latest_brief or {}).get("brief") or {}).get("recommended_next_action") == "draft_meeting_followup_via_mail_agent", latest_brief

        result_response = _post(
            client,
            message="show meeting result",
            session_id=session_id,
            conversation_id=conversation_id,
            actor_context=actor_context,
            headers=headers,
        )
        assert result_response["intent"] == "meeting_result", result_response
        result_observation = next(
            dict(item)
            for item in list(result_response.get("tool_observations") or [])
            if dict(item).get("observation_type") == "meeting_result"
        )
        result_payload = dict(result_observation.get("payload") or {})
        assert result_payload.get("communication_role") == "escalation_provider", result_payload
        assert result_payload.get("communication_input_kind") == "meeting_result", result_payload
        assert result_payload.get("communication_closeout_owner") == "mail_agent", result_payload
        assert result_payload.get("thread_id") == thread_id, result_payload
        assert result_payload.get("source_brief_id") == brief_id, result_payload

        invitation_response = _post(
            client,
            message="send to alice@example.com",
            session_id=session_id,
            conversation_id=conversation_id,
            actor_context=actor_context,
            headers=headers,
        )
        pending_mail = dict(invitation_response.get("pending_confirmation") or {})
        mail_plan = dict(pending_mail.get("mail_plan") or {})
        assert pending_mail.get("action_name") == "send_mail_plan", pending_mail
        assert mail_plan.get("resolved_recipients") == ["alice@example.com"], mail_plan
        assert mail_plan.get("mail_action_type") == "send_meeting_invitation", mail_plan
        assert mail_plan.get("communication_role") == "escalation_provider", mail_plan
        assert mail_plan.get("communication_input_kind") == "meeting_result", mail_plan
        assert mail_plan.get("communication_closeout_owner") == "mail_agent", mail_plan
        assert mail_plan.get("source_brief_id") == brief_id, mail_plan
        assert dict(mail_plan.get("thread_ref") or {}).get("thread_id") == thread_id, mail_plan

        confirm_mail = _post(
            client,
            message="confirm",
            session_id=session_id,
            conversation_id=conversation_id,
            actor_context=actor_context,
            headers=headers,
        )
        dlp_task_id = str(confirm_mail.get("task_id") or "")
        assert dlp_task_id, confirm_mail
        assert dlp_task_id in enqueued_dlp, (dlp_task_id, enqueued_dlp)
        assert confirm_mail.get("task_status") == "queued", confirm_mail

        dlp_result = task_worker.process_dlp_outbound_task.run(dlp_task_id)
        if dlp_result["status"] == "pending_approval":
            approved = main_module.approve_task(dlp_task_id, "cross_domain_regression")
            assert approved and approved["status"] == "approved", approved
        elif dlp_result["status"] == "sender_review_required":
            assert enqueued_email == [], enqueued_email
            sender_confirmed = client.post(
                f"/tasks/{dlp_task_id}/sender-safety-confirm",
                json={
                    "actor": "cross_domain_regression_sender",
                    "tenant_id": actor_context["tenant_id"],
                    "user_id": actor_context["user_id"],
                    "workspace_id": actor_context["workspace_id"],
                    "roles": list(actor_context.get("roles") or []),
                },
                headers=headers,
            )
            assert sender_confirmed.status_code == 200, sender_confirmed.text
            confirmed_task = main_module.get_dlp_task(dlp_task_id)
            assert confirmed_task and confirmed_task["status"] == "queued_for_send", confirmed_task
            assert confirmed_task["delivery_status"] == "queued_for_send", confirmed_task
            assert enqueued_email == [dlp_task_id], enqueued_email
        else:
            assert dlp_result["status"] == "queued_for_send", dlp_result
            assert enqueued_email == [dlp_task_id], enqueued_email

        send_result = task_worker.send_dlp_email_task.run(dlp_task_id)
        final_task = main_module.get_dlp_task(dlp_task_id)
        assert send_result["status"] == "sent", send_result
        assert final_task and final_task["status"] == "sent", final_task

    print(
        json.dumps(
            {
                "ok": True,
                "conversation_id": conversation_id,
                "meeting_task_id": meeting_task_id,
                "dlp_task_id": dlp_task_id,
                "dlp_status_before_send": dlp_result["status"],
                "final_delivery_status": final_task.get("delivery_status"),
                "smtp_provider": final_task.get("smtp_provider"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
