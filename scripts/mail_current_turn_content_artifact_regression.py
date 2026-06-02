from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.mail.content_parser import parse_message_content_candidates


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    session_id = f"session-current-turn-content-{suffix}"
    actor = {
        "tenant_id": f"tenant-current-turn-content-{suffix}",
        "user_id": f"user-current-turn-content-{suffix}",
        "workspace_id": f"workspace-current-turn-content-{suffix}",
        "roles": ["admin", "mail_sender", "user", "viewer"],
    }

    parsed = parse_message_content_candidates("帮我把文段为“测试”的内容发送到 first@example.com")
    _assert(parsed and parsed[0]["source_type"] == "user_inline_text", f"inline parser missed quoted text: {parsed}")
    _assert(parsed[0]["content"] == "测试", f"inline parser extracted wrong content: {parsed}")
    unquoted = parse_message_content_candidates("帮我把文段为 测试的内容发送到 first@example.com")
    _assert(unquoted and unquoted[0]["content"] == "测试", f"inline parser leaked destination into body: {unquoted}")

    def _fake_mail_authoring(*, render_mode: str, mail_plan: dict, **_: object) -> dict:
        return {
            "user_message": f"renderer:{render_mode}",
            "clarification_question": "",
            "body_for_sending": str(mail_plan.get("resolved_body") or "测试"),
            "token_in": 7,
            "token_out": 3,
            "estimated_cost": 0.001,
        }

    main_module.render_mail_authoring = _fake_mail_authoring

    with TestClient(main_module.app) as client:
        response = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "conversation_id": "",
                "message": "帮我把文段为“测试”的内容发送到 first@example.com",
                **actor,
            },
        )
        _assert(response.status_code == 200, response.text)
        payload = response.json()
        pending = dict(payload.get("pending_confirmation") or {})
        mail_plan = dict(pending.get("mail_plan") or {})
        selected = dict(mail_plan.get("selected_candidate") or {})
        _assert(payload.get("termination_reason") == "needs_confirmation", f"mail did not reach confirmation: {payload}")
        _assert(selected.get("kind") == "user_inline_text", f"wrong selected source: {mail_plan}")
        _assert(selected.get("content") == "测试", f"wrong selected content: {mail_plan}")
        _assert(mail_plan.get("resolved_body") == "测试", f"wrong resolved body: {mail_plan}")
        _assert("content_or_attachment" not in list(mail_plan.get("missing_fields") or []), f"body still missing: {mail_plan}")
        _assert(mail_plan.get("review_content") == "测试", f"review content drifted: {mail_plan}")

    print(
        json.dumps(
            {
                "ok": True,
                "selected_kind": selected.get("kind"),
                "resolved_body": mail_plan.get("resolved_body"),
                "review_content": mail_plan.get("review_content"),
                "termination_reason": payload.get("termination_reason"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
