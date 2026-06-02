from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.task_store import get_dlp_task


UNIQUE_FACT = "EU主站点 → EU热备用站点 → 美国紧急故障转移"
PRIOR_ANSWER = (
    "根据查询结果，MedThink 的 EU 区域故障转移策略如下："
    f"故障转移顺序：{UNIQUE_FACT}。"
    "活跃数据集的目标 RPO 为 15 分钟，关键 API 的目标 RTO 为 30 分钟。"
    "跨区域故障转移到美国仅在声明的紧急窗口内允许，最长不超过 4 小时。"
    "如果仍未恢复，请联系值班支持 13800000000。"
)


def _wait_for_pending_approval(task_id: str, timeout_seconds: float = 45.0) -> dict:
    deadline = time.time() + timeout_seconds
    latest: dict = {}
    while time.time() < deadline:
        latest = get_dlp_task(task_id) or {}
        if str(latest.get("status") or "") in {"pending_approval", "sent", "send_failed", "failed"}:
            return latest
        time.sleep(0.5)
    raise AssertionError({"error": "task_poll_timeout", "task_id": task_id, "latest": latest})


def _normalized(text: str) -> str:
    return str(text or "").replace(" ", "").replace("\n", "")


def main() -> None:
    suffix = uuid4().hex[:10]
    session_id = f"session-mail-preview-{suffix}"
    actor_context = {
        "tenant_id": os.getenv("GOVERNANCE_TENANT_ID", "local-dev"),
        "user_id": "user-mail-preview",
        "workspace_id": os.getenv("GOVERNANCE_WORKSPACE_ID", "default"),
        "roles": ["admin", "mail_sender", "approver", "user", "viewer"],
        "session_id": session_id,
    }
    with TestClient(main_module.app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, actor_context)
        conversation_id = str(conversation["conversation_id"])
        main_module.append_exchange(
            session_id=session_id,
            conversation_id=conversation_id,
            question="MedThink 的 EU 区域故障转移顺序、RPO/RTO 和切换到美国的时限是什么？",
            answer=PRIOR_ANSWER,
            answer_summary=PRIOR_ANSWER,
            intent="enterprise_rag",
            actor_context=actor_context,
        )
        send_request = {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": "将该信息发送到1136732521@qq.com。注意不要包含无关内容",
            "tenant_id": actor_context["tenant_id"],
            "user_id": actor_context["user_id"],
            "workspace_id": actor_context["workspace_id"],
            "roles": actor_context["roles"],
        }
        confirmation = client.post("/agent/chat", json=send_request)
        assert confirmation.status_code == 200, confirmation.text
        confirmation_result = confirmation.json()
        assert confirmation_result.get("termination_reason") == "needs_confirmation", confirmation_result
        mail_plan = dict((confirmation_result.get("pending_confirmation") or {}).get("mail_plan") or {})
        resolved_body = str(mail_plan.get("resolved_body") or "")
        review_content = str(mail_plan.get("review_content") or "")
        assert mail_plan.get("authoring_status") == "completed", mail_plan
        assert review_content == resolved_body, mail_plan
        assert _normalized(resolved_body).count(_normalized(UNIQUE_FACT)) == 1, resolved_body

        confirmed = client.post(
            "/agent/chat",
            json={
                **send_request,
                "message": "确认发送",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_result = confirmed.json()
        task_id = str(confirmed_result.get("task_id") or "")
        assert task_id, confirmed_result

    task = _wait_for_pending_approval(task_id)
    message_raw = str(task.get("message_raw") or "")
    message_redacted = str(task.get("message_redacted") or "")
    assert task.get("status") == "pending_approval", task
    assert task.get("approval_required") is True, task
    assert message_raw == resolved_body, task
    assert _normalized(message_raw).count(_normalized(UNIQUE_FACT)) == 1, message_raw
    assert _normalized(message_redacted).count(_normalized(UNIQUE_FACT)) == 1, message_redacted
    assert PRIOR_ANSWER not in message_raw, message_raw

    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task_id,
                "status": task.get("status"),
                "approval_required": task.get("approval_required"),
                "raw_occurrences": _normalized(message_raw).count(_normalized(UNIQUE_FACT)),
                "redacted_occurrences": _normalized(message_redacted).count(_normalized(UNIQUE_FACT)),
                "message_raw": message_raw,
                "message_redacted": message_redacted,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
