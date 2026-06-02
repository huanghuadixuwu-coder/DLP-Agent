from __future__ import annotations

import base64
import json
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.continuation_state import ContinuationDecision, PendingObject, resolve_continuation as resolve_state_continuation
from app.models import UnifiedAgentRequest
from app.orchestration.trace_evaluator import evaluate_agent_trace
from app.pending_object_store import list_active_pending_objects


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    session_id = f"session-state-foundation-{suffix}"
    actor = {
        "tenant_id": f"tenant-state-foundation-{suffix}",
        "user_id": f"user-state-foundation-{suffix}",
        "workspace_id": f"workspace-state-foundation-{suffix}",
        "roles": ["admin", "mail_sender", "user", "viewer"],
    }
    enqueued: list[str] = []

    def _fake_enqueue(task_id: str) -> str:
        enqueued.append(task_id)
        return task_id

    def _fake_mail_authoring(*, render_mode: str, mail_plan: dict, **_: object) -> dict:
        constraints = dict(mail_plan.get("body_constraints") or {})
        body = str(constraints.get("body_override") or mail_plan.get("resolved_body") or "Original body.")
        if constraints.get("length") == "brief":
            body = "Brief body."
        return {
            "user_message": f"renderer:{render_mode}",
            "clarification_question": "",
            "body_for_sending": body,
            "token_in": 1,
            "token_out": 1,
            "estimated_cost": 0.0,
        }

    def _fake_final_renderer(**_: object) -> dict:
        return {
            "answer": "Please clarify whether to edit the active draft or start a new task.",
            "token_in": 1,
            "token_out": 1,
            "estimated_cost": 0.0,
            "used_fallback": False,
        }

    def _fake_memory_write(result: dict, conversation_id: str, actor_context: dict | None = None) -> tuple[str, bool]:
        exchange = main_module.append_exchange(
            session_id=str(result["session_id"]),
            conversation_id=conversation_id,
            question=str(result.get("display_message") or result.get("message") or ""),
            answer=str(result.get("answer") or ""),
            redacted_question=str(result.get("display_message") or result.get("message") or ""),
            answer_summary=str(result.get("answer") or ""),
            intent=str(result.get("intent") or ""),
            tool_calls=list(result.get("tool_calls") or []),
            citations=list(result.get("citations") or []),
            debug_payload=main_module._build_debug_snapshot(result, conversation_id),
            actor_context=actor_context or actor,
        )
        return str(exchange["assistant_turn"]["turn_id"]), False

    def _semantic_classifier(message: str, pending_objects: list) -> ContinuationDecision | None:
        target = next((item for item in pending_objects if item.object_type == "mail_draft"), None)
        if target and "语气" in message:
            return ContinuationDecision(
                mode="continue_existing",
                continuation_type="patch",
                object_id=target.object_id,
                object_type=target.object_type,
                confidence=0.97,
                reason="Test semantic classifier selected an existing mail draft patch.",
                source="test_semantic_classifier",
                parameters={"constraints": {"length": "brief"}},
            )
        return None

    def _resolve(message: str, pending_objects: list) -> ContinuationDecision:
        return resolve_state_continuation(message, pending_objects, semantic_classifier=_semantic_classifier)

    main_module.enqueue_dlp_risk_task = _fake_enqueue
    main_module.render_mail_authoring = _fake_mail_authoring
    main_module.render_final_answer = _fake_final_renderer
    main_module._write_unified_conversation_memory = _fake_memory_write
    main_module.resolve_continuation = _resolve

    new_task_decision = resolve_state_continuation(
        "Please send the selected information to second@example.com",
        [
            PendingObject(
                object_id=f"mail-draft:existing-{suffix}",
                object_type="mail_draft",
                status="active",
                allowed_continuations=("patch", "cancel"),
                salience=0.9,
            )
        ],
        semantic_classifier=lambda *_: ContinuationDecision(
            mode="new_task",
            continuation_type="new_task",
            confidence=0.96,
            reason="The user started a new outbound request.",
            source="test_semantic_classifier",
        ),
    )
    _assert(new_task_decision.mode == "new_task", f"new outbound request was swallowed as draft patch: {new_task_decision}")

    with TestClient(main_module.app) as client:
        conversation, _ = main_module._ensure_conversation(session_id, None, {**actor, "session_id": session_id})
        conversation_id = str(conversation["conversation_id"])
        actor_context = {**actor, "session_id": session_id, "conversation_id": conversation_id}
        initial = main_module._build_mail_confirmation_response(
            session_id=session_id,
            conversation_id=conversation_id,
            message="send original",
            display_message="send original",
            mail_plan={
                "draft_id": f"draft-{suffix}",
                "status": "pending_confirmation",
                "draft_state": "confirm",
                "mail_action_type": "send_message",
                "resolved_recipients": ["first@example.com"],
                "resolved_subject": "Original",
                "resolved_body": "Original body.",
                "review_content": "Original body.",
                "resolved_attachments": [],
                "requires_confirmation": True,
                "missing_fields": [],
                "body_constraints": {},
                "body_sources": [],
                "source_policy": {},
            },
            actor_context=actor_context,
        )
        _assert(initial.termination_reason == "needs_confirmation", f"initial confirmation failed: {initial}")
        main_module._persist_confirmation_object(
            session_id=session_id,
            conversation_id=conversation_id,
            confirmation_payload=dict(initial.confirmation_payload or initial.pending_confirmation or {}),
            actor_context=actor_context,
        )

        headers = {
            "X-Tenant-Id": actor["tenant_id"],
            "X-User-Id": actor["user_id"],
            "X-Workspace-Id": actor["workspace_id"],
            "X-Roles": ",".join(actor["roles"]),
        }
        snapshot = client.get(
            "/agent/pending-objects",
            params={"session_id": session_id, "conversation_id": conversation_id},
            headers=headers,
        )
        _assert(snapshot.status_code == 200, snapshot.text)
        snapshot_payload = snapshot.json()
        snapshot_types = {item["object_type"] for item in snapshot_payload["pending_objects"]}
        _assert("mail_draft" in snapshot_types, f"mail draft registry snapshot missing: {snapshot_payload}")
        _assert("mail_confirmation" in snapshot_types, f"mail confirmation registry snapshot missing: {snapshot_payload}")

        ambiguous = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "conversation_id": conversation_id,
                "message": "Please continue with the existing request.",
                **actor,
            },
        )
        _assert(ambiguous.status_code == 200, ambiguous.text)
        ambiguous_payload = ambiguous.json()
        _assert(
            ambiguous_payload.get("termination_reason") == "continuation_resolution_required",
            f"ambiguous continuation escaped into the planner: {ambiguous_payload}",
        )
        _assert(bool(ambiguous_payload.get("needs_clarification")), f"ambiguous continuation did not clarify: {ambiguous_payload}")

        main_module.get_turns = lambda *_args, **_kwargs: []
        patched = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "conversation_id": conversation_id,
                "message": "请将现有草稿的语气调整得更简练",
                **actor,
            },
        )
        _assert(patched.status_code == 200, patched.text)
        patched_payload = patched.json()
        patched_plan = dict((patched_payload.get("pending_confirmation") or {}).get("mail_plan") or {})
        continuation_state = dict((patched_payload.get("task_plan") or {}).get("continuation_state") or {})
        _assert(patched_payload.get("termination_reason") == "needs_confirmation", f"semantic patch failed: {patched_payload}")
        _assert(patched_plan.get("resolved_body") == "Brief body.", f"semantic patch body missing: {patched_plan}")
        _assert(dict(continuation_state.get("decision") or {}).get("continuation_type") == "patch", f"patch decision missing: {continuation_state}")

        confirmed = client.post(
            "/agent/chat",
            json={
                "session_id": session_id,
                "conversation_id": conversation_id,
                "message": "确认发送",
                **actor,
            },
        )
        _assert(confirmed.status_code == 200, confirmed.text)
        confirmed_payload = confirmed.json()
        task_id = str(confirmed_payload.get("task_id") or "")
        _assert(bool(task_id), f"confirmed task missing: {confirmed_payload}")
        _assert(enqueued == [task_id], f"unexpected queue calls: {enqueued}")

        upload_context, _ = main_module._resolve_upload_context(
            UnifiedAgentRequest(
                session_id=session_id,
                conversation_id=conversation_id,
                message="summarize upload",
                uploaded_filename="notes.md",
                uploaded_content_type="text/markdown",
                uploaded_text="# Notes",
                uploaded_file_base64=base64.b64encode(b"# Notes").decode("ascii"),
                **actor,
            ),
            conversation_id,
            actor_context,
        )
        _assert(bool(upload_context.get("upload_blob_id")), f"upload blob missing: {upload_context}")

        main_module._persist_answer_artifact_object(
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=f"turn-answer-{suffix}",
            answer_summary="Grounded enterprise answer.",
            intent="enterprise_rag_query",
            citations=[{"doc_id": "doc-1"}],
            actor_context=actor_context,
        )
        registry = list_active_pending_objects(conversation_id=conversation_id, actor_context=actor_context)
        registry_types = {item["object_type"] for item in registry}
        _assert({"dlp_task", "upload_artifact", "answer_artifact"}.issubset(registry_types), f"registry coverage incomplete: {registry_types}")

        foreign = {**actor_context, "user_id": f"foreign-{suffix}"}
        _assert(
            not list_active_pending_objects(conversation_id=conversation_id, actor_context=foreign),
            "pending registry leaked across actors",
        )

        trace = evaluate_agent_trace(
            {
                "task_plan": {
                    "continuation_state": {
                        "decision": {"mode": "new_task"},
                        "active_objects": [
                            {
                                "object_id": "confirmation-risk",
                                "object_type": "mail_confirmation",
                                "status": "pending_confirmation",
                            }
                        ],
                    }
                },
                "tool_calls": [
                    {
                        "tool_name": "send_email_smtp",
                        "success": True,
                        "status": "completed",
                    }
                ],
            },
            actor_context=actor_context,
        )
        trace_codes = {item["code"] for item in trace["issues"]}
        _assert("pending_confirmation_bypassed_by_new_side_effect" in trace_codes, f"trace guard missing: {trace}")

    print(
        json.dumps(
            {
                "ok": True,
                "conversation_id": conversation_id,
                "snapshot_types": sorted(snapshot_types),
                "registry_types": sorted(registry_types),
                "semantic_patch_body": patched_plan.get("resolved_body"),
                "task_id": task_id,
                "new_task_not_swallowed": True,
                "ambiguous_continuation_guarded": True,
                "cross_actor_isolated": True,
                "trace_guard": "pending_confirmation_bypassed_by_new_side_effect",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
