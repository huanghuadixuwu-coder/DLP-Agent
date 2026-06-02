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


PRIOR_ANSWER = (
    "根据当前可确认的信息，根据提供的资料，MedThink 的 EU 区域故障转移方案如下："
    "先将 EU 流量切换到 EU 热备；如果 EU 热备不可用，再临时切换到美国区域。"
    "RPO 目标小于 5 分钟，RTO 目标为 15 分钟。"
    "切换到美国区域只能作为短期措施，最长不得超过 30 分钟。"
)
UNWANTED_META_TEXT = ("根据当前可确认的信息", "根据提供的资料")


def main() -> None:
    suffix = uuid4().hex[:10]
    session_id = f"session-mail-reference-{suffix}"
    actor_context = {
        "tenant_id": "tenant-mail-reference",
        "user_id": "user-mail-reference",
        "workspace_id": "workspace-mail-reference",
        "roles": ["admin", "user", "viewer"],
        "session_id": session_id,
    }
    with TestClient(main_module.app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, actor_context)
        conversation_id = str(conversation["conversation_id"])
        main_module.append_exchange(
            session_id=session_id,
            conversation_id=conversation_id,
            question="MedThink 的 EU 区域故障转移方案是什么？",
            answer=PRIOR_ANSWER,
            answer_summary=PRIOR_ANSWER,
            intent="enterprise_rag",
            actor_context=actor_context,
        )
        response = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "conversation_id": conversation_id,
                "message": "劳烦将该信息转交至1136732521@qq.com。注意不要包含无关内容",
                "tenant_id": actor_context["tenant_id"],
                "user_id": actor_context["user_id"],
                "workspace_id": actor_context["workspace_id"],
                "roles": actor_context["roles"],
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()

    pending = dict(result.get("pending_confirmation") or {})
    mail_plan = dict(pending.get("mail_plan") or {})
    resolved_body = str(mail_plan.get("resolved_body") or "").strip()
    review_content = str(mail_plan.get("review_content") or "").strip()
    assert result.get("termination_reason") == "needs_confirmation", result
    assert mail_plan.get("compose_mode") == "recipient_ready_summary", mail_plan
    assert mail_plan.get("authoring_status") == "completed", mail_plan
    assert resolved_body, mail_plan
    assert review_content == resolved_body, mail_plan
    assert resolved_body != PRIOR_ANSWER, mail_plan
    assert not any(item in resolved_body for item in UNWANTED_META_TEXT), resolved_body
    assert not any(item in review_content for item in UNWANTED_META_TEXT), review_content
    assert "MedThink" in resolved_body, resolved_body
    assert "30" in resolved_body, resolved_body
    assert list(mail_plan.get("source_artifacts") or []), mail_plan
    assert list(mail_plan.get("provenance_refs") or []), mail_plan
    assert {
        "kind": "reference_source",
        "role": "assistant_last_answer",
        "policy": "recipient_ready_summary",
    } in list(mail_plan.get("body_sources") or []), mail_plan

    print(
        json.dumps(
            {
                "ok": True,
                "conversation_id": conversation_id,
                "compose_mode": mail_plan.get("compose_mode"),
                "authoring_status": mail_plan.get("authoring_status"),
                "resolved_body": resolved_body,
                "review_content": review_content,
                "source_artifact_count": len(list(mail_plan.get("source_artifacts") or [])),
                "token_in": result.get("token_in"),
                "token_out": result.get("token_out"),
                "latency_ms": result.get("latency_ms"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
