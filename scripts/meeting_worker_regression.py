from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.task_worker as task_worker
from app.task_store import create_dlp_task, get_dlp_task


def main() -> None:
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
        assert parameters["idempotency_key"] == "meeting-worker-regression-key"
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
        session_id="session-meeting-worker-regression",
        conversation_id="conversation-meeting-worker-regression",
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
            "idempotency_key": "meeting-worker-regression-key",
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
                            "idempotency_key": "meeting-worker-regression-key",
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
        tenant_id="tenant-meeting-worker-regression",
        user_id="user-meeting-worker-regression",
        workspace_id="workspace-meeting-worker-regression",
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
    post_results = list(updated["domain_result"].get("post_confirm_results") or [])
    assert post_results and post_results[0]["action"] == "mail_invitation_draft", updated
    draft_state = post_results[0]["result"]["draft_state"]
    assert draft_state["draft_kind"] == "meeting_invitation", updated
    assert draft_state["meeting"]["meeting_id"] == "fake-meeting-id", updated
    assert draft_state["meeting"]["meeting_url"].startswith("https://meeting.tencent.com/"), updated
    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task["task_id"],
                "worker_result": result,
                "final_status": updated["status"],
                "domain_result": updated["domain_result"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
