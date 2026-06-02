from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.models import UnifiedAgentRequest


def main() -> None:
    session_id = "session-legacy-async-outbound-contract"
    actor_context = {
        "tenant_id": "tenant-legacy-async",
        "user_id": "user-legacy-async",
        "workspace_id": "workspace-legacy-async",
        "roles": ["admin", "user", "viewer"],
        "session_id": session_id,
    }

    original_renderer = main_module.render_mail_authoring
    main_module.render_mail_authoring = lambda **kwargs: {
        "user_message": "Please confirm sending this email.",
        "body_for_sending": "Legacy async path body authored by renderer.",
        "clarification_question": "",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
        "used_fallback": False,
    }
    try:
        with TestClient(main_module.app):
            conversation, _ = main_module._ensure_conversation(session_id, None, actor_context)
            conversation_id = str(conversation["conversation_id"])
            payload = UnifiedAgentRequest(
                session_id=session_id,
                conversation_id=conversation_id,
                message='Send this text to alice@example.com: "hello from legacy async path"',
                tenant_id=actor_context["tenant_id"],
                user_id=actor_context["user_id"],
                workspace_id=actor_context["workspace_id"],
                roles=actor_context["roles"],
            )
            response = main_module._handle_async_outbound_agent_request(
                payload,
                conversation_id,
                outbound_message="hello from legacy async path",
                display_message=payload.message,
                upload_context={},
                actor_context=actor_context,
            )
    finally:
        main_module.render_mail_authoring = original_renderer

    data = response.model_dump() if hasattr(response, "model_dump") else response.dict()
    pending = dict(data.get("pending_confirmation") or {})
    mail_plan = dict(pending.get("mail_plan") or {})
    assert data.get("termination_reason") == "needs_confirmation", data
    assert not data.get("task_id"), data
    assert pending.get("action_name") == "send_mail_plan", pending
    assert mail_plan.get("resolved_recipients") == ["alice@example.com"], mail_plan
    assert mail_plan.get("resolved_body") == "Legacy async path body authored by renderer.", mail_plan
    assert mail_plan.get("review_content") == "Legacy async path body authored by renderer.", mail_plan
    assert mail_plan.get("authoring_status") == "completed", mail_plan

    print(
        json.dumps(
            {
                "ok": True,
                "termination_reason": data.get("termination_reason"),
                "task_id": data.get("task_id"),
                "pending_action": pending.get("action_name"),
                "recipient": mail_plan.get("resolved_recipients", [""])[0],
                "resolved_body": mail_plan.get("resolved_body"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
