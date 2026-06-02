from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.continuation_state import ContinuationDecision, resolve_continuation as resolve_state_continuation
from app.mail.draft_store import get_latest_active_mail_draft, get_mail_draft


app = main_module.app


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _post(client: TestClient, *, session_id: str, conversation_id: str, message: str, actor: dict) -> dict:
    response = client.post(
        "/agent/chat",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": message,
            **actor,
        },
    )
    _assert(response.status_code == 200, response.text)
    return response.json()


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    session_id = f"session-mail-persistent-{suffix}"
    actor = {
        "tenant_id": f"tenant-mail-persistent-{suffix}",
        "user_id": f"user-mail-persistent-{suffix}",
        "workspace_id": f"workspace-mail-persistent-{suffix}",
        "roles": ["admin", "mail_sender", "user", "viewer"],
    }
    enqueued: list[str] = []

    main_module.enqueue_dlp_risk_task = lambda task_id: enqueued.append(task_id) or task_id

    def _resolve(message: str, pending_objects: list) -> ContinuationDecision:
        def _semantic_classifier(text: str, candidates: list) -> ContinuationDecision | None:
            target = next((item for item in candidates if item.object_type == "mail_draft"), None)
            email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
            if target is None or email is None:
                return None
            return ContinuationDecision(
                mode="continue_existing",
                continuation_type="patch",
                object_id=target.object_id,
                object_type=target.object_type,
                confidence=0.99,
                reason="The isolated persistence harness updates the active draft recipient.",
                source="test_semantic_classifier",
                parameters={"recipient": email.group(0)},
            )

        return resolve_state_continuation(message, pending_objects, semantic_classifier=_semantic_classifier)

    def _render_mail_authoring(*, render_mode: str, **_: object) -> dict:
        body = "Patched public body after refresh." if render_mode == "patch" else "Original public body."
        return {
            "user_message": f"renderer:{render_mode}",
            "body_for_sending": body,
            "token_in": 1,
            "token_out": 1,
            "estimated_cost": 0.0,
        }

    main_module.render_mail_authoring = _render_mail_authoring
    main_module.resolve_continuation = _resolve

    with TestClient(app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, {**actor, "session_id": session_id})
        conversation_id = str(conversation["conversation_id"])

        draft_response = _post(
            client,
            session_id=session_id,
            conversation_id=conversation_id,
            message="帮我把如下文段发送到 first@example.com 文段是：“Original public body.”",
            actor=actor,
        )
        pending = dict(draft_response.get("pending_confirmation") or {})
        plan = dict(pending.get("mail_plan") or {})
        draft_id = str(plan.get("draft_id") or "")
        _assert(bool(draft_id), f"persistent draft id missing: {draft_response}")
        _assert(str(pending.get("persistence_source") or "") == "mail_drafts", f"persistence source missing: {pending}")
        stored = get_mail_draft(draft_id, actor_context=actor)
        _assert(stored and stored["status"] == "pending_confirmation", f"draft was not persisted: {stored}")
        _assert(bool(stored.get("confirmation_key")), f"confirmation key missing: {stored}")
        foreign_actor = {**actor, "user_id": f"foreign-user-{suffix}"}
        _assert(
            get_mail_draft(draft_id, actor_context=foreign_actor) is None,
            "persistent draft leaked across user scope",
        )

        # Simulate a page refresh or process-local debug loss. The next actions
        # must recover solely from PostgreSQL mail_drafts state.
        main_module.get_turns = lambda *_args, **_kwargs: []

        patch_response = _post(
            client,
            session_id=session_id,
            conversation_id=conversation_id,
            message="收件人改成 refreshed@example.com",
            actor=actor,
        )
        patched_plan = dict((patch_response.get("pending_confirmation") or {}).get("mail_plan") or {})
        _assert(patched_plan.get("resolved_recipients") == ["refreshed@example.com"], f"persistent patch failed: {patch_response}")
        patched = get_mail_draft(draft_id, actor_context=actor)
        _assert(patched and patched["version"] > stored["version"], f"draft version did not advance: {(stored, patched)}")
        _assert(patched["mail_plan"]["resolved_body"] == "Patched public body after refresh.", f"patched body missing: {patched}")

        first_confirm = _post(
            client,
            session_id=session_id,
            conversation_id=conversation_id,
            message="确认发送",
            actor=actor,
        )
        task_id = str(first_confirm.get("task_id") or "")
        _assert(bool(task_id), f"first confirmation did not create task: {first_confirm}")
        bound = get_mail_draft(draft_id, actor_context=actor)
        _assert(bound and bound["status"] == "queued_dlp", f"draft did not advance to queued_dlp: {bound}")
        _assert(str(bound.get("dlp_task_id") or "") == task_id, f"draft task binding mismatch: {bound}")

        second_confirm = _post(
            client,
            session_id=session_id,
            conversation_id=conversation_id,
            message="确认发送",
            actor=actor,
        )
        _assert(str(second_confirm.get("task_id") or "") == task_id, f"duplicate confirmation created a new task: {second_confirm}")
        _assert(enqueued == [task_id], f"duplicate confirmation enqueued twice: {enqueued}")
        _assert(get_latest_active_mail_draft(conversation_id, actor_context=actor) is None, "queued draft should not remain editable")

    print(
        json.dumps(
            {
                "ok": True,
                "conversation_id": conversation_id,
                "draft_id": draft_id,
                "draft_version": bound["version"],
                "task_id": task_id,
                "duplicate_task_id": second_confirm.get("task_id"),
                "enqueue_count": len(enqueued),
                "turn_debug_removed": True,
                "cross_user_isolated": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
