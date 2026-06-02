from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.models import UnifiedAgentRequest


TARGET_ANSWER = (
    "GCP Marketplace onboarding 中，订阅 entitlement 延迟时，应将其视为中间状态而不是错误。"
    "界面应提示用户订阅仍在同步中，请几分钟后重试，并提供刷新按钮。"
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    suffix = uuid4().hex[:8]
    session_id = f"artifact-registry-{suffix}"
    actor = {
        "tenant_id": f"tenant-artifact-{suffix}",
        "user_id": f"user-artifact-{suffix}",
        "workspace_id": f"workspace-artifact-{suffix}",
        "roles": ["admin", "mail_sender", "user", "viewer"],
    }
    conversation, _ = main_module._ensure_conversation(session_id, None, actor)
    conversation_id = str(conversation["conversation_id"])

    target_exchange = main_module.append_exchange(
        session_id=session_id,
        conversation_id=conversation_id,
        question="GCP Marketplace onboarding 中，订阅 entitlement 延迟时应如何处理？",
        answer=TARGET_ANSWER,
        answer_summary=TARGET_ANSWER,
        intent="enterprise_rag",
        actor_context=actor,
    )
    target_turn_id = str(target_exchange["assistant_turn"]["turn_id"])
    main_module._persist_answer_artifact_object(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=target_turn_id,
        answer_summary=TARGET_ANSWER,
        intent="enterprise_rag",
        citations=[{"title": "GCP Marketplace onboarding"}],
        actor_context=actor,
    )

    for index in range(12):
        filler = f"这是第 {index + 1} 条后续无关回答，用于把目标答案挤出最近 turns 扫描窗口。"
        main_module.append_exchange(
            session_id=session_id,
            conversation_id=conversation_id,
            question=f"filler question {index + 1}",
            answer=filler,
            answer_summary=filler,
            intent="enterprise_rag",
            actor_context=actor,
        )

    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message="把 GCP Marketplace 订阅延迟的处理方案发送到 first@example.com",
        tenant_id=actor["tenant_id"],
        user_id=actor["user_id"],
        workspace_id=actor["workspace_id"],
        roles=actor["roles"],
    )
    candidates = main_module._collect_outbound_candidates(
        payload,
        conversation_id,
        {},
        actor_context=actor,
    )
    target = next(
        (
            item
            for item in candidates
            if str(item.get("candidate_id") or "") == f"assistant-turn:{target_turn_id}"
        ),
        None,
    )
    _assert(target is not None, f"answer artifact candidate missing from registry-backed candidates: {candidates}")
    _assert(
        "GCP Marketplace" in str(target.get("content") or ""),
        f"answer artifact content drifted: {target}",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "candidate_id": str(target.get("candidate_id") or ""),
                "label": str(target.get("label") or ""),
                "content_preview": str(target.get("content") or "")[:120],
                "candidate_count": len(candidates),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
