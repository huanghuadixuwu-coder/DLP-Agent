from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module


GCP_ANSWER = (
    "当 GCP Marketplace 订阅 entitlement 延迟时，应将其视为中间状态而非错误。"
    "界面应提示订阅仍在同步中，请几分钟后重试，并提供刷新按钮。"
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    suffix = uuid4().hex[:8]
    session_id = f"mail-confirm-render-{suffix}"
    actor = {
        "tenant_id": f"tenant-mail-confirm-{suffix}",
        "user_id": f"user-mail-confirm-{suffix}",
        "workspace_id": f"workspace-mail-confirm-{suffix}",
        "roles": ["admin", "mail_sender", "approver", "user", "viewer"],
    }

    captured: dict[str, object] = {}

    def _fake_mail_authoring(*, render_mode: str, mail_plan: dict, **_: object) -> dict:
        if render_mode == "confirmation":
            return {
                "user_message": "请确认发送基于 GCP 处理方案整理的邮件。",
                "clarification_question": "",
                "body_for_sending": GCP_ANSWER,
                "token_in": 5,
                "token_out": 5,
                "estimated_cost": 0.001,
            }
        return {
            "user_message": "",
            "clarification_question": "",
            "body_for_sending": str(mail_plan.get("resolved_body") or GCP_ANSWER),
            "token_in": 3,
            "token_out": 3,
            "estimated_cost": 0.001,
        }

    def _fake_final_answer(*, question: str, current_goal: str, observations: list[dict], **_: object) -> dict:
        captured["question"] = question
        captured["current_goal"] = current_goal
        captured["observation_types"] = [str(item.get("observation_type") or "") for item in observations]
        captured["first_payload_body"] = str((observations[0].get("payload") or {}).get("body") or "")
        return {
            "answer": "已基于选中的 GCP 处理方案创建治理任务，正在进行 DLP 风险判断。",
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
            "used_fallback": False,
        }

    main_module.render_mail_authoring = _fake_mail_authoring
    main_module.render_final_answer = _fake_final_answer

    with TestClient(main_module.app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, actor)
        conversation_id = str(conversation["conversation_id"])
        exchange = main_module.append_exchange(
            session_id=session_id,
            conversation_id=conversation_id,
            question="GCP Marketplace onboarding 中，订阅 entitlement 延迟时应如何处理？",
            answer=GCP_ANSWER,
            answer_summary=GCP_ANSWER,
            intent="enterprise_rag",
            actor_context=actor,
        )
        main_module._persist_answer_artifact_object(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=str(exchange["assistant_turn"]["turn_id"]),
            answer_summary=GCP_ANSWER,
            intent="enterprise_rag",
            citations=[{"title": "GCP Marketplace onboarding"}],
            actor_context=actor,
        )

        confirmation = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "conversation_id": conversation_id,
                "message": "把 GCP Marketplace 订阅延迟的处理方案发送到1136732521@qq.com",
                **actor,
            },
        )
        _assert(confirmation.status_code == 200, confirmation.text)
        confirmation_payload = confirmation.json()
        _assert(confirmation_payload.get("termination_reason") == "needs_confirmation", f"confirmation missing: {confirmation_payload}")

        confirmed = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "conversation_id": conversation_id,
                "message": "确认",
                **actor,
            },
        )
        _assert(confirmed.status_code == 200, confirmed.text)
        confirmed_payload = confirmed.json()
        _assert(confirmed_payload.get("final_answer_source") == "mail_task_created_renderer", f"wrong final answer source: {confirmed_payload}")
        _assert(captured.get("current_goal") == "action_or_draft", f"renderer goal drifted: {captured}")
        observation_types = list(captured.get("observation_types") or [])
        _assert(observation_types[:2] == ["governed_mail_task_created", "task_status_result"], f"post-confirm observations drifted: {captured}")
        _assert(
            "GCP Marketplace" in str(captured.get("first_payload_body") or ""),
            f"selected mail body was not preserved into renderer observations: {captured}",
        )
        trace_eval = dict((confirmed_payload.get("task_plan") or {}).get("agent_trace_evaluation") or {})
        trace_codes = [str(item.get("code") or "") for item in list(trace_eval.get("issues") or [])]
        _assert(
            "mail_confirmation_renderer_lost_source_artifact" not in trace_codes,
            f"trace evaluator still flags lost source artifact: {trace_eval}",
        )

    print(
        json.dumps(
            {
                "ok": True,
                "final_answer_source": "mail_task_created_renderer",
                "observation_types": captured.get("observation_types"),
                "first_payload_body": captured.get("first_payload_body"),
                "trace_codes": trace_codes,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
