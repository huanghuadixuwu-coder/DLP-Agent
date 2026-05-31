from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module

app = main_module.app


def _post(client: TestClient, *, message: str, session_id: str, conversation_id: str) -> dict:
    response = client.post(
        "/agent/chat",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": message,
            "tenant_id": "tenant-meeting-continuation",
            "user_id": "user-meeting-continuation",
            "workspace_id": "workspace-meeting-continuation",
            "roles": ["admin", "user", "viewer"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def main() -> None:
    session_id = "session-meeting-continuation"
    actor_context = {
        "tenant_id": "tenant-meeting-continuation",
        "user_id": "user-meeting-continuation",
        "workspace_id": "workspace-meeting-continuation",
        "roles": ["admin", "user", "viewer"],
        "session_id": session_id,
    }

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
        "answer": "Meeting result is available from the completed domain task.",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }
    main_module.render_mail_authoring = lambda **kwargs: {
        "user_message": "Please confirm sending the meeting invitation.",
        "body_for_sending": "Here are the meeting details from the completed meeting task.",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }

    with TestClient(app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, actor_context)
        conversation_id = str(conversation["conversation_id"])
        task = main_module.create_dlp_task(
            task_type="domain_meeting",
            session_id=session_id,
            conversation_id=conversation_id,
            message_raw="confirm create meeting",
            request_message="confirm create meeting",
            destination_email="",
            requested_action="meeting_create_tencent_meeting",
            status="completed",
            domain_action="meeting_create_tencent_meeting",
            domain_result={
                "ok": True,
                "status": "completed",
                "result": {
                    "subject": "Customer follow-up",
                    "meeting_id": "mtg-123",
                    "meeting_code": "123456789",
                    "meeting_url": "https://meeting.tencent.com/example",
                    "start_time": "2026-05-26T14:00:00+08:00",
                    "end_time": "2026-05-26T14:30:00+08:00",
                    "provider": "tencent_meeting_mcp",
                },
                "post_confirm_results": [
                    {
                        "ok": True,
                        "action": "mail_invitation_draft",
                        "result": {
                            "draft_state": {
                                "draft_kind": "meeting_invitation",
                                "status": "needs_clarification",
                                "subject": "Customer follow-up - meeting invitation",
                                "meeting": {
                                    "topic": "Customer follow-up",
                                    "meeting_id": "mtg-123",
                                    "meeting_url": "https://meeting.tencent.com/example",
                                    "start_time": "2026-05-26T14:00:00+08:00",
                                    "end_time": "2026-05-26T14:30:00+08:00",
                                },
                                "missing_fields": ["recipient"],
                            }
                        },
                    }
                ],
            },
            tenant_id=actor_context["tenant_id"],
            user_id=actor_context["user_id"],
            workspace_id=actor_context["workspace_id"],
        )

        result_response = _post(
            client,
            message="show meeting result",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert result_response["intent"] == "meeting_result", result_response
        assert any(
            obs.get("observation_type") == "meeting_result"
            for obs in result_response.get("tool_observations", [])
        ), result_response.get("tool_observations")

        invitation_response = _post(
            client,
            message="send to alice@example.com",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        pending = dict(invitation_response.get("pending_confirmation") or {})
        mail_plan = dict(pending.get("mail_plan") or {})
        assert invitation_response["termination_reason"] == "needs_confirmation", invitation_response
        assert pending.get("action_name") == "send_mail_plan", pending
        assert mail_plan.get("mail_action_type") == "send_meeting_invitation", mail_plan
        assert mail_plan.get("resolved_recipients") == ["alice@example.com"], mail_plan
        assert dict(mail_plan.get("meeting_result") or {}).get("meeting_id") == "mtg-123", mail_plan
        assert str(mail_plan.get("resolved_body") or ""), mail_plan

        confirm_response = _post(
            client,
            message="confirm",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert confirm_response["task_id"], confirm_response
        assert confirm_response["task_status"] in {"queued", "needs_approval", "rejected"}, confirm_response
        assert confirm_response["mode_used"] == "task_queue", confirm_response
        confirmed_task = main_module.get_dlp_task(str(confirm_response["task_id"]))
        assert confirmed_task and confirmed_task["task_type"] == "dlp_outbound", confirmed_task

    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task["task_id"],
                "result_intent": result_response["intent"],
                "pending_action": pending.get("action_name"),
                "recipient": mail_plan.get("resolved_recipients", [""])[0],
                "confirm_task_id": confirm_response["task_id"],
                "confirm_task_status": confirm_response["task_status"],
                "confirm_task_type": confirmed_task["task_type"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
