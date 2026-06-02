from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.mail.draft_store import get_latest_active_mail_draft
from app.orchestration.observations import make_typed_observation


def main() -> None:
    suffix = uuid4().hex[:10]
    session_id = f"session-mail-authoring-failure-{suffix}"
    conversation_id = f"conv_mail_authoring_failure_{suffix}"
    actor_context = {
        "tenant_id": "tenant-mail-authoring-failure",
        "user_id": "user-mail-authoring-failure",
        "workspace_id": "workspace-mail-authoring-failure",
        "roles": ["admin", "user", "viewer"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    failure = make_typed_observation(
        observation_type="dependency_failure",
        source="llm",
        status="degraded",
        grounding_kind="guardrail",
        summary="llm.mail_authoring failed; fallback=return_mail_authoring_recovery_observation.",
        payload={
            "service": "llm",
            "operation": "mail_authoring",
            "error": "rate_limit:1302",
            "fallback_strategy": "return_mail_authoring_recovery_observation",
            "retry_count": 1,
            "retryable": True,
        },
        confidence=0.95,
        actor_context=actor_context,
    )

    original = main_module.render_mail_authoring
    main_module.render_mail_authoring = lambda **_: {
        "user_message": "",
        "body_for_sending": "",
        "clarification_question": "",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
        "used_fallback": True,
        "failure_observation": failure,
    }
    try:
        response = main_module._build_mail_confirmation_response(
            session_id=session_id,
            conversation_id=conversation_id,
            message="将该信息发送到 alice@example.com",
            display_message="将该信息发送到 alice@example.com",
            mail_plan={
                "draft_id": f"draft_{suffix}",
                "conversation_id": conversation_id,
                "status": "pending_confirmation",
                "draft_state": "confirm",
                "mail_action_type": "send_message",
                "resolved_recipients": ["alice@example.com"],
                "resolved_subject": "故障恢复回归",
                "resolved_body": "",
                "resolved_attachments": [],
                "missing_fields": [],
                "requires_confirmation": True,
                "compose_mode": "recipient_ready_summary",
                "reference_sources": [
                    {
                        "role": "assistant_last_answer",
                        "policy": "recipient_ready_summary",
                        "content": "用于 renderer 的企业知识回答。",
                    }
                ],
            },
            actor_context=actor_context,
        )
    finally:
        main_module.render_mail_authoring = original

    payload = response.model_dump()
    persisted = get_latest_active_mail_draft(conversation_id, actor_context=actor_context)
    persisted_plan = dict((persisted or {}).get("mail_plan") or {})
    assert payload.get("termination_reason") == "dependency_failure", payload
    assert payload.get("final_answer_source") == "mail_authoring_recovery", payload
    assert not payload.get("pending_confirmation"), payload
    assert payload.get("task_id") is None, payload
    assert "recipient_ready_body" not in list(persisted_plan.get("missing_fields") or []), persisted_plan
    assert persisted_plan.get("authoring_status") == "failed", persisted_plan
    assert (persisted or {}).get("status") == "authoring_failed", persisted
    assert any(
        item.get("observation_type") == "dependency_failure"
        for item in list(payload.get("tool_observations") or [])
    ), payload

    print(
        json.dumps(
            {
                "ok": True,
                "termination_reason": payload.get("termination_reason"),
                "final_answer_source": payload.get("final_answer_source"),
                "draft_status": (persisted or {}).get("status"),
                "missing_fields": persisted_plan.get("missing_fields"),
                "task_id": payload.get("task_id"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
