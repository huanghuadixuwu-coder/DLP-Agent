from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.continuation_state import ContinuationDecision, resolve_continuation as resolve_state_continuation
from app.pending_object_store import get_pending_object


app = main_module.app


ACTOR = {
    "tenant_id": "tenant-continuation-state",
    "user_id": "user-continuation-state",
    "workspace_id": "workspace-continuation-state",
    "roles": ["admin", "user", "viewer"],
}


def _post(client: TestClient, *, message: str, session_id: str, conversation_id: str) -> dict:
    response = client.post(
        "/agent/chat",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": message,
            **ACTOR,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def main() -> None:
    enqueued: list[str] = []

    def _fake_enqueue(task_id: str) -> str:
        enqueued.append(task_id)
        return task_id

    def _fake_final_renderer(**_: object) -> dict:
        return {
            "answer": "已记录当前状态。",
            "token_in": 1,
            "token_out": 1,
            "estimated_cost": 0.0,
            "used_fallback": False,
        }

    def _fake_mail_authoring(**kwargs: object) -> dict:
        mail_plan = dict(kwargs.get("mail_plan") or {})
        body = str(mail_plan.get("resolved_body") or "")
        if not body:
            selected = dict(mail_plan.get("selected_candidate") or {})
            body = str(selected.get("content") or "")
        missing = list(mail_plan.get("missing_fields") or [])
        return {
            "user_message": "请补充缺失信息。" if missing else "请确认是否发送这封邮件。",
            "clarification_question": "请补充收件人。" if "recipient" in missing else "",
            "body_for_sending": body,
            "token_in": 1,
            "token_out": 1,
            "estimated_cost": 0.0,
        }

    def _fake_source_resolver(
        *,
        message: str,
        candidates: list[dict],
        legacy_referential_request: bool = False,
        explicit_summary: bool = False,
    ) -> dict:
        if "该信息" in message:
            return {
                "observation_type": "mail_source_resolution",
                "status": "needs_clarification",
                "selected_candidate_ids": [],
                "source_mode": "none",
                "compose_mode": "recipient_ready_summary",
                "referential_request": True,
                "needs_clarification": True,
                "confidence": 0.52,
                "reason": "Multiple prior assistant answers are plausible.",
                "classifier_source": "test_ambiguous",
                "classifier_error": "",
            }
        if message.strip().lower() == "gcp":
            selected = next(
                item
                for item in candidates
                if "GCP" in str(item.get("content") or "")
            )
            return {
                "observation_type": "mail_source_resolution",
                "status": "resolved",
                "selected_candidate_ids": [str(selected.get("candidate_id") or "")],
                "source_mode": "prior_assistant_answer",
                "compose_mode": "recipient_ready_summary",
                "referential_request": True,
                "needs_clarification": False,
                "confidence": 0.91,
                "reason": "The clarification reply selects the GCP prior answer.",
                "classifier_source": "test_choice",
                "classifier_error": "",
            }
        inline = next((item for item in candidates if str(item.get("kind") or "") == "user_inline_text"), None)
        if inline:
            return {
                "observation_type": "mail_source_resolution",
                "status": "resolved",
                "selected_candidate_ids": [str(inline.get("candidate_id") or "")],
                "source_mode": "inline_body",
                "compose_mode": "direct_body",
                "referential_request": False,
                "needs_clarification": False,
                "confidence": 1.0,
                "reason": "Inline body candidate selected.",
                "classifier_source": "test_inline",
                "classifier_error": "",
            }
        return {
            "observation_type": "mail_source_resolution",
            "status": "needs_clarification",
            "selected_candidate_ids": [],
            "source_mode": "none",
            "compose_mode": "recipient_ready_summary",
            "referential_request": bool(legacy_referential_request or explicit_summary),
            "needs_clarification": True,
            "confidence": 0.0,
            "reason": "No deterministic test source.",
            "classifier_source": "test_fallback",
            "classifier_error": "",
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
            actor_context=actor_context or result.get("actor_context") or ACTOR,
        )
        return str(exchange["assistant_turn"]["turn_id"]), False

    def _resolve(message: str, pending_objects: list) -> ContinuationDecision:
        return resolve_state_continuation(
            message,
            pending_objects,
            semantic_classifier=lambda *_: ContinuationDecision(
                mode="new_task",
                continuation_type="new_task",
                confidence=0.98,
                reason="The isolated harness starts a new outbound source-resolution request.",
                source="test_semantic_classifier",
            ),
        )

    main_module.enqueue_meeting_task = _fake_enqueue
    main_module.render_final_answer = _fake_final_renderer
    main_module.render_mail_authoring = _fake_mail_authoring
    main_module.resolve_mail_source_request = _fake_source_resolver
    main_module.resolve_continuation = _resolve
    main_module._write_unified_conversation_memory = _write_test_turn
    main_module.list_recent_inbound_threads = lambda **_: []

    session_id = "session-continuation-state"
    with TestClient(app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, {**ACTOR, "session_id": session_id})
        conversation_id = str(conversation["conversation_id"])

        missing_recipient = _post(
            client,
            message="帮我发送文段为“测试”",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        missing_plan = dict((missing_recipient.get("task_plan") or {}).get("mail_plan") or {})
        missing_object = dict((missing_recipient.get("task_plan") or {}).get("pending_object") or {})
        assert missing_recipient["needs_clarification"], missing_recipient
        assert "recipient" in list(missing_plan.get("missing_fields") or []), missing_plan
        assert not list(missing_plan.get("resolved_recipients") or []), missing_plan
        assert missing_object.get("object_type") == "recipient_clarification", missing_object

        recipient_continuation = _post(
            client,
            message="1136732521@qq.com",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        recipient_pending = dict(recipient_continuation.get("pending_confirmation") or {})
        recipient_plan = dict(recipient_pending.get("mail_plan") or {})
        consumed_recipient_object = get_pending_object(str(missing_object.get("object_id") or ""), actor_context=ACTOR)
        recipient_confirmation_object = get_pending_object(
            str(recipient_pending.get("confirmation_id") or ""),
            actor_context=ACTOR,
        )
        assert recipient_continuation["termination_reason"] == "needs_confirmation", recipient_continuation
        assert recipient_plan.get("resolved_recipients") == ["1136732521@qq.com"], recipient_plan
        assert recipient_plan.get("resolved_body") == "测试", recipient_plan
        assert consumed_recipient_object and consumed_recipient_object.get("status") == "consumed", consumed_recipient_object
        assert recipient_confirmation_object and recipient_confirmation_object.get("status") == "pending_confirmation", recipient_confirmation_object

        conversation_body, _ = main_module._ensure_conversation(
            "session-continuation-body",
            None,
            {**ACTOR, "session_id": "session-continuation-body"},
        )
        body_conversation_id = str(conversation_body["conversation_id"])
        missing_body = _post(
            client,
            message="帮我发邮件给 1136732521@qq.com",
            session_id="session-continuation-body",
            conversation_id=body_conversation_id,
        )
        body_plan = dict((missing_body.get("task_plan") or {}).get("mail_plan") or {})
        body_object = dict((missing_body.get("task_plan") or {}).get("pending_object") or {})
        assert missing_body["needs_clarification"], missing_body
        assert body_object.get("object_type") == "body_clarification", body_object
        assert "content_or_attachment" in list(body_plan.get("missing_fields") or []), body_plan
        body_continuation = _post(
            client,
            message="正文为“测试正文”",
            session_id="session-continuation-body",
            conversation_id=body_conversation_id,
        )
        body_pending = dict(body_continuation.get("pending_confirmation") or {})
        body_resolved_plan = dict(body_pending.get("mail_plan") or {})
        consumed_body_object = get_pending_object(str(body_object.get("object_id") or ""), actor_context=ACTOR)
        assert body_continuation["termination_reason"] == "needs_confirmation", body_continuation
        assert body_resolved_plan.get("resolved_recipients") == ["1136732521@qq.com"], body_resolved_plan
        assert body_resolved_plan.get("resolved_body") == "测试正文", body_resolved_plan
        assert consumed_body_object and consumed_body_object.get("status") == "consumed", consumed_body_object

        inline = _post(
            client,
            message="帮我把如下文段发送到1136732521@qq.com 文段为“测试”",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        inline_pending = dict(inline.get("pending_confirmation") or {})
        inline_plan = dict(inline_pending.get("mail_plan") or {})
        selected = dict(inline_plan.get("selected_candidate") or {})
        assert inline["termination_reason"] == "needs_confirmation", inline
        assert selected.get("kind") == "user_inline_text", inline_plan
        assert inline_plan.get("resolved_recipients") == ["1136732521@qq.com"], inline_plan
        assert inline_plan.get("resolved_body") == "测试", inline_plan

        main_module.append_exchange(
            session_id=session_id,
            conversation_id=conversation_id,
            question="MedThink 的 EU 区域故障转移顺序是什么？",
            answer="MedThink failover answer: EU traffic can shift to US for a bounded emergency window.",
            redacted_question="MedThink 的 EU 区域故障转移顺序是什么？",
            answer_summary="MedThink failover answer: EU traffic can shift to US for a bounded emergency window.",
            intent="enterprise_rag_query",
            tool_calls=[],
            citations=[],
            debug_payload={},
            actor_context=ACTOR,
        )
        main_module.append_exchange(
            session_id=session_id,
            conversation_id=conversation_id,
            question="GCP Marketplace entitlement 延迟时怎么处理？",
            answer="GCP onboarding answer: treat entitlement delays as pending, avoid not-entitled errors, show syncing and retry/refresh guidance.",
            redacted_question="GCP Marketplace entitlement 延迟时怎么处理？",
            answer_summary="GCP onboarding answer: treat entitlement delays as pending, avoid not-entitled errors, show syncing and retry/refresh guidance.",
            intent="enterprise_rag_query",
            tool_calls=[],
            citations=[],
            debug_payload={},
            actor_context=ACTOR,
        )
        source_clarification = _post(
            client,
            message="请把该信息发送到 1136732521@qq.com。注意不要包含无关内容",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        source_plan = dict((source_clarification.get("task_plan") or {}).get("mail_plan") or {})
        source_pending_object = dict((source_clarification.get("task_plan") or {}).get("pending_object") or {})
        assert source_clarification["needs_clarification"], source_clarification
        assert dict(source_plan.get("source_resolution") or {}).get("needs_clarification"), source_plan
        assert source_pending_object.get("object_type") == "source_clarification", source_pending_object
        assert source_pending_object.get("status") == "needs_clarification", source_pending_object

        source_choice = _post(
            client,
            message="GCP",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        source_pending = dict(source_choice.get("pending_confirmation") or {})
        source_mail_plan = dict(source_pending.get("mail_plan") or {})
        chosen_candidate = dict(source_mail_plan.get("selected_candidate") or {})
        assert source_choice["termination_reason"] == "needs_confirmation", source_choice
        assert "GCP onboarding answer" in str(chosen_candidate.get("content") or ""), source_mail_plan
        assert "MedThink failover answer" not in str(chosen_candidate.get("content") or ""), source_mail_plan
        consumed_source_object = get_pending_object(str(source_pending_object.get("object_id") or ""), actor_context=ACTOR)
        assert consumed_source_object and consumed_source_object.get("status") == "consumed", consumed_source_object

        meeting_plan = _post(
            client,
            message="Create a Tencent Meeting. Topic: project sync tomorrow afternoon.",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        pending_domain = dict(meeting_plan.get("pending_confirmation") or {})
        assert pending_domain.get("tool_name") == "meeting_create_tencent_meeting", pending_domain
        domain_confirmation_id = str(pending_domain.get("idempotency_key") or "")
        domain_confirmation_object = get_pending_object(domain_confirmation_id, actor_context=ACTOR)
        assert domain_confirmation_object and domain_confirmation_object.get("status") == "pending_confirmation", domain_confirmation_object

        # Simulate a refresh or process-local turn-debug loss. Domain
        # confirmation must continue from PostgreSQL pending_objects alone.
        main_module.get_turns = lambda *_args, **_kwargs: []
        confirmed = _post(
            client,
            message="创建",
            session_id=session_id,
            conversation_id=conversation_id,
        )
        assert confirmed.get("task_id"), confirmed
        task = main_module.get_dlp_task(str(confirmed["task_id"]))
        assert task and task["task_type"] == "domain_meeting", task
        assert enqueued == [str(confirmed["task_id"])], enqueued
        consumed_domain_confirmation = get_pending_object(domain_confirmation_id, actor_context=ACTOR)
        assert consumed_domain_confirmation and consumed_domain_confirmation.get("status") == "consumed", consumed_domain_confirmation

        cancel_conversation, _ = main_module._ensure_conversation(
            "session-continuation-cancel",
            None,
            {**ACTOR, "session_id": "session-continuation-cancel"},
        )
        cancel_conversation_id = str(cancel_conversation["conversation_id"])
        cancellable_meeting = _post(
            client,
            message="Create a Tencent Meeting. Topic: cancelled sync tomorrow afternoon.",
            session_id="session-continuation-cancel",
            conversation_id=cancel_conversation_id,
        )
        cancellable_domain = dict(cancellable_meeting.get("pending_confirmation") or {})
        cancellable_confirmation_id = str(cancellable_domain.get("idempotency_key") or "")
        assert get_pending_object(cancellable_confirmation_id, actor_context=ACTOR), cancellable_domain
        cancelled = _post(
            client,
            message="取消",
            session_id="session-continuation-cancel",
            conversation_id=cancel_conversation_id,
        )
        cancelled_domain_confirmation = get_pending_object(cancellable_confirmation_id, actor_context=ACTOR)
        assert cancelled.get("termination_reason") == "pending_object_cancelled", cancelled
        assert cancelled_domain_confirmation and cancelled_domain_confirmation.get("status") == "cancelled", cancelled_domain_confirmation
        assert enqueued == [str(confirmed["task_id"])], enqueued

    print(
        json.dumps(
            {
                "ok": True,
                "conversation_id": conversation_id,
                "missing_recipient_fields": missing_plan.get("missing_fields"),
                "recipient_object_status": consumed_recipient_object.get("status") if consumed_recipient_object else "",
                "recipient_confirmation_status": recipient_confirmation_object.get("status") if recipient_confirmation_object else "",
                "body_object_status": consumed_body_object.get("status") if consumed_body_object else "",
                "inline_body": inline_plan.get("resolved_body"),
                "source_choice": chosen_candidate.get("content"),
                "source_object_status": consumed_source_object.get("status") if consumed_source_object else "",
                "meeting_task_id": confirmed.get("task_id"),
                "meeting_confirmation_status": consumed_domain_confirmation.get("status") if consumed_domain_confirmation else "",
                "domain_refresh_without_turn_debug": True,
                "cancelled_confirmation_status": cancelled_domain_confirmation.get("status") if cancelled_domain_confirmation else "",
                "enqueued": enqueued,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
