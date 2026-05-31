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
            "tenant_id": "tenant-mail-inline",
            "user_id": "user-mail-inline",
            "workspace_id": "workspace-mail-inline",
            "roles": ["admin", "user", "viewer"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def main() -> None:
    session_id = "session-mail-inline"
    actor_context = {
        "tenant_id": "tenant-mail-inline",
        "user_id": "user-mail-inline",
        "workspace_id": "workspace-mail-inline",
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
    main_module.render_mail_authoring = lambda **kwargs: {
        "user_message": "请确认是否发送这封邮件。",
        "body_for_sending": "测试",
        "token_in": 11,
        "token_out": 5,
        "estimated_cost": 0.0016,
    }

    with TestClient(app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, actor_context)
        conversation_id = str(conversation["conversation_id"])
        main_module.append_exchange(
            session_id=session_id,
            conversation_id=conversation_id,
            question="你的功能是什么",
            answer="你好！我是安全外发与邮件协作 Agent。我可以帮你总结上传文档、查看收件早报、回答企业知识问题。",
            answer_summary="你好！我是安全外发与邮件协作 Agent。我可以帮你总结上传文档、查看收件早报、回答企业知识问题。",
            intent="smalltalk",
            actor_context=actor_context,
        )

        response = _post(
            client,
            message="帮我把如下文段发送到1136732521@qq.com 文段是：“测试”",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        pending = dict(response.get("pending_confirmation") or {})
        mail_plan = dict(pending.get("mail_plan") or {})
        selected = dict(mail_plan.get("selected_candidate") or {})
        assert response["termination_reason"] == "needs_confirmation", response
        assert pending.get("action_name") == "send_mail_plan", pending
        assert selected.get("kind") == "user_inline_text", selected
        assert mail_plan.get("resolved_body") == "测试", mail_plan
        assert mail_plan.get("review_content") == "测试", mail_plan
        assert "安全外发与邮件协作 Agent" not in str(mail_plan.get("review_content") or ""), mail_plan
        assert "assistant_last_answer" not in list(mail_plan.get("source_refs") or []), mail_plan
        assert float(response.get("latency_ms") or 0.0) > 0.0, response
        assert int(response.get("token_in") or 0) == 11, response
        assert int(response.get("token_out") or 0) == 5, response

        confirm_response = _post(
            client,
            message="确认",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert confirm_response["task_id"], confirm_response
        task = main_module.get_dlp_task(str(confirm_response["task_id"]))
        assert task and task["task_type"] == "dlp_outbound", task
        assert str(task.get("message_raw") or "").strip() == "测试", task

    print(
        json.dumps(
            {
                "ok": True,
                "conversation_id": conversation_id,
                "selected_kind": selected.get("kind"),
                "review_content": mail_plan.get("review_content"),
                "confirm_task_id": confirm_response["task_id"],
                "confirm_task_type": task["task_type"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
