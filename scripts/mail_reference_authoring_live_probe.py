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


def main() -> None:
    suffix = uuid4().hex[:10]
    session_id = f"session-mail-reference-live-{suffix}"
    actor_context = {
        "tenant_id": "tenant-mail-reference-live",
        "user_id": "user-mail-reference-live",
        "workspace_id": "workspace-mail-reference-live",
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

    termination_reason = str(result.get("termination_reason") or "")
    observations = list(result.get("tool_observations") or [])
    if termination_reason == "needs_confirmation":
        mail_plan = dict((result.get("pending_confirmation") or {}).get("mail_plan") or {})
        assert mail_plan.get("authoring_status") == "completed", mail_plan
        assert str(mail_plan.get("resolved_body") or "").strip(), mail_plan
        probe_mode = "provider_available"
    else:
        assert termination_reason == "dependency_failure", result
        mail_plan = dict((result.get("task_plan") or {}).get("mail_plan") or {})
        assert mail_plan.get("authoring_status") == "failed", mail_plan
        assert "recipient_ready_body" not in list(mail_plan.get("missing_fields") or []), mail_plan
        assert any(item.get("observation_type") == "dependency_failure" for item in observations), observations
        assert result.get("task_id") is None, result
        assert not result.get("pending_confirmation"), result
        probe_mode = "provider_degraded_safe_recovery"

    print(
        json.dumps(
            {
                "ok": True,
                "probe_mode": probe_mode,
                "termination_reason": termination_reason,
                "authoring_status": mail_plan.get("authoring_status"),
                "missing_fields": mail_plan.get("missing_fields"),
                "task_id": result.get("task_id"),
                "latency_ms": result.get("latency_ms"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
