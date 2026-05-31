from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
import app.task_worker as task_worker
from app.models import DlpTaskCreateRequest
from app.resilience import make_failure_observation
from app.task_store import create_dlp_task, get_dlp_task


def _assert_dependency_failure(value: dict, label: str) -> None:
    assert dict(value).get("observation_type") == "dependency_failure", (label, value)
    assert dict(value).get("payload", {}).get("fallback_strategy"), (label, value)


def _meeting_provider_failure() -> dict:
    def _fake_dispatch(action, parameters, context, dependency_payloads, *, allow_side_effects=False):
        failure = make_failure_observation(
            service="tencent_meeting",
            operation=action,
            error="Injected provider timeout.",
            fallback_strategy="return_provider_error_observation",
            retry_count=2,
            actor_context=dict(context.actor_context or {}),
        )
        return {
            "ok": False,
            "action": action,
            "result": {
                "status": "provider_error",
                "error": "Injected provider timeout.",
                "failure_observation": failure,
            },
            "error": "Injected provider timeout.",
            "observation_type": "meeting_write",
        }

    task_worker.dispatch_tool_call = _fake_dispatch
    task = create_dlp_task(
        task_type="domain_meeting",
        session_id="session-failure-recovery",
        conversation_id="conversation-failure-recovery",
        message_raw="confirm create meeting",
        request_message="confirm create meeting",
        destination_email="",
        requested_action="meeting_create_tencent_meeting",
        status="queued",
        domain_action="meeting_create_tencent_meeting",
        domain_payload={
            "topic": "provider failure regression",
            "natural_time": "tomorrow afternoon",
            "duration_minutes": 30,
            "idempotency_key": "provider-failure-regression",
        },
        tenant_id="tenant-failure",
        user_id="user-failure",
        workspace_id="workspace-failure",
    )
    result = task_worker.process_domain_meeting_task.run(str(task["task_id"]))
    updated = get_dlp_task(str(task["task_id"]))
    assert result["status"] == "failed", result
    assert updated and updated["status"] == "failed", updated
    failure = dict(updated["domain_result"]["result"].get("failure_observation") or {})
    _assert_dependency_failure(failure, "meeting_provider_failure")
    return {"task_id": task["task_id"], "status": updated["status"], "error": updated["delivery_error"]}


def _smtp_failure() -> dict:
    task = create_dlp_task(
        task_type="dlp_outbound",
        session_id="session-failure-recovery",
        conversation_id="conversation-failure-recovery",
        message_raw="Public follow-up meeting invitation.",
        request_message="Public follow-up meeting invitation.",
        delivery_subject="Meeting invitation",
        delivery_body="Public meeting invitation body.",
        destination_email="alice@example.com",
        requested_action="send_meeting_invitation",
        status="queued_for_send",
        fault_injection={"force_smtp_fail": True},
        tenant_id="tenant-failure",
        user_id="user-failure",
        workspace_id="workspace-failure",
    )
    result = task_worker.send_dlp_email_task.run(str(task["task_id"]))
    updated = get_dlp_task(str(task["task_id"]))
    assert result["status"] == "send_failed", result
    assert updated and updated["status"] == "send_failed", updated
    recovery = dict(updated["domain_result"].get("recovery_observation") or {})
    _assert_dependency_failure(recovery, "smtp_failure")
    return {"task_id": task["task_id"], "status": updated["status"], "error_category": updated["last_error_category"]}


def _dlp_high_risk_recovery() -> dict:
    task = create_dlp_task(
        task_type="dlp_outbound",
        session_id="session-failure-recovery",
        conversation_id="conversation-failure-recovery",
        message_raw="Send this to alice@example.com: phone 13800000000 and API_KEY=sk-prod-secret-value.",
        request_message="Send high-risk content.",
        delivery_subject="Sensitive note",
        delivery_body="phone 13800000000 and API_KEY=sk-prod-secret-value",
        destination_email="alice@example.com",
        requested_action="send_message",
        status="queued",
        fault_injection={"force_rule_only_mode": True},
        tenant_id="tenant-failure",
        user_id="user-failure",
        workspace_id="workspace-failure",
    )
    result = task_worker.process_dlp_outbound_task.run(str(task["task_id"]))
    updated = get_dlp_task(str(task["task_id"]))
    assert result["status"] == "pending_approval", result
    assert updated and updated["status"] == "pending_approval", updated
    recovery = dict(updated["domain_result"].get("recovery_observation") or {})
    assert recovery.get("observation_type") == "governance_recovery", recovery
    assert recovery.get("status") == "blocked", recovery
    return {"task_id": task["task_id"], "status": updated["status"], "risk_level": updated["risk_level"]}


def _worker_unavailable_recovery() -> dict:
    main_module.enqueue_dlp_risk_task = lambda task_id: (_ for _ in ()).throw(RuntimeError("Injected Celery broker unavailable."))
    task = main_module._create_async_dlp_task(
        DlpTaskCreateRequest(
            session_id="session-failure-recovery",
            conversation_id="conversation-failure-recovery",
            message="Send public meeting invitation to alice@example.com.",
            request_message="Send public meeting invitation.",
            review_content="Public meeting invitation only.",
            resolved_outbound_content="Public meeting invitation only.",
            delivery_subject="Public meeting invitation",
            delivery_body="Public meeting invitation only.",
            destination_email="alice@example.com",
            requested_action="send_message",
            tenant_id="tenant-failure",
            user_id="user-failure",
            workspace_id="workspace-failure",
            roles=["admin", "user"],
        ),
        actor_context={
            "tenant_id": "tenant-failure",
            "user_id": "user-failure",
            "workspace_id": "workspace-failure",
            "roles": ["admin", "user"],
            "session_id": "session-failure-recovery",
            "conversation_id": "conversation-failure-recovery",
        },
    )
    assert task["status"] == "failed", task
    recovery = dict(task["domain_result"].get("recovery_observation") or {})
    _assert_dependency_failure(recovery, "worker_unavailable")
    return {"task_id": task["task_id"], "status": task["status"], "error_category": task["last_error_category"]}


def main() -> None:
    report = {
        "meeting_provider_failure": _meeting_provider_failure(),
        "smtp_failure": _smtp_failure(),
        "dlp_high_risk": _dlp_high_risk_recovery(),
        "worker_unavailable": _worker_unavailable_recovery(),
    }
    print(json.dumps({"ok": True, **report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
