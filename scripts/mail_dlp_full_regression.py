from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

import httpx


BASE_URL = os.getenv("API_BASE_URL") or os.getenv("SECURE_MAIL_AGENT_BASE_URL") or "http://localhost:8000"
RECIPIENT = os.getenv("REGRESSION_RECIPIENT") or os.getenv("SMTP_USER") or "17388861183@163.com"
TENANT_ID = os.getenv("REGRESSION_TENANT_ID") or "regression-tenant"
USER_ID = os.getenv("REGRESSION_USER_ID") or "regression-user"
WORKSPACE_ID = os.getenv("REGRESSION_WORKSPACE_ID") or "regression-workspace"
ROLES = ["admin", "mail_sender", "approver", "user", "viewer"]
TIMEOUT = httpx.Timeout(240.0, connect=30.0)
TASK_TERMINAL_STATUSES = {"sent", "rejected", "send_failed", "failed", "delivery_deferred"}


def _headers() -> dict[str, str]:
    return {
        "X-Tenant-Id": TENANT_ID,
        "X-User-Id": USER_ID,
        "X-Workspace-Id": WORKSPACE_ID,
        "X-Roles": ",".join(ROLES),
    }


def _actor_payload() -> dict[str, Any]:
    return {
        "tenant_id": TENANT_ID,
        "user_id": USER_ID,
        "workspace_id": WORKSPACE_ID,
        "roles": ROLES,
    }


def _request(method: str, path: str, **kwargs: Any) -> Any:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(_headers())
    last_error: Exception | None = None
    for attempt in range(12):
        try:
            with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=headers) as client:
                response = client.request(method, path, **kwargs)
                if response.status_code >= 400:
                    raise RuntimeError(f"{method} {path} failed: {response.status_code} {response.text}")
                return response.json()
        except httpx.ConnectError as exc:
            last_error = exc
            time.sleep(min(1 + attempt * 0.5, 5))
    raise RuntimeError(f"{method} {path} failed after API startup retries: {last_error}")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _poll_task(task_id: str, *, expected: set[str] | None = None, timeout_seconds: int = 180) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_task: dict[str, Any] = {}
    while time.time() < deadline:
        task = _request("GET", f"/tasks/{task_id}")
        last_task = task if isinstance(task, dict) else {}
        status = str(last_task.get("status", ""))
        if expected and status in expected:
            return last_task
        if not expected and status in TASK_TERMINAL_STATUSES:
            return last_task
        if status in TASK_TERMINAL_STATUSES and expected:
            return last_task
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for task {task_id}; last={last_task}")


def _chat(session_id: str, conversation_id: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "message": message,
        "mode": "auto",
        "show_steps": False,
        **_actor_payload(),
        **extra,
    }
    return _request("POST", "/agent/chat", json=payload)


def _create_conversation(session_id: str) -> str:
    conversation = _request(
        "POST",
        "/conversations",
        json={
            "session_id": session_id,
            "title": "Mail DLP full regression",
            **_actor_payload(),
        },
    )
    return str(conversation["conversation_id"])


def _run_inbound_checks(report: dict[str, Any]) -> None:
    sync_result = _request("POST", "/mail/inbound/sync", json={})
    _assert(bool(sync_result.get("ok")), f"Inbound sync failed: {sync_result}")
    digest_result = _request("POST", "/mail/inbound/digest", json={})
    summary = _request("GET", "/mail/inbound/summary")
    summary_text = json.dumps(summary, ensure_ascii=False).lower()
    _assert("<style" not in summary_text and "@media" not in summary_text, "Inbound summary contains CSS/template noise.")
    report["checks"]["inbound"] = {
        "sync_ok": sync_result.get("ok"),
        "synced": sync_result.get("synced", sync_result.get("saved", 0)),
        "digest_keys": sorted(list(digest_result.keys()))[:8] if isinstance(digest_result, dict) else [],
        "summary_total": summary.get("total", 0),
        "important_count": summary.get("important_count", 0),
    }


def _run_mail_draft_patch_confirm(session_id: str, conversation_id: str, report: dict[str, Any]) -> None:
    draft = _chat(
        session_id,
        conversation_id,
        (
            "Polish this email body professionally. body: Public regression note: "
            "the maintenance window completed successfully and the customer report will be shared tomorrow."
        ),
    )
    _assert(not draft.get("task_id"), f"Draft-only mail path unexpectedly created task: {draft}")
    _assert(
        str(draft.get("final_answer_source"))
        in {"mail_draft", "mail_draft_renderer", "mail_draft_renderer_fallback", "mail_authoring_renderer", "mail_authoring_renderer_fallback"},
        f"Unexpected draft source: {draft.get('final_answer_source')}",
    )

    confirmation = _chat(
        session_id,
        conversation_id,
        f"Please send this uploaded public update to {RECIPIENT}. Keep it brief and professional.",
        uploaded_filename="public-update.txt",
        uploaded_content_type="text/plain",
        uploaded_text=(
            "Public customer update. Maintenance completed successfully. "
            "No customer data, credentials, secrets, or personal information are included. "
            "The final report will be shared tomorrow."
        ),
    )
    _assert(str(confirmation.get("termination_reason")) == "needs_confirmation", f"Mail send did not require confirmation: {confirmation}")
    _assert(bool(confirmation.get("pending_confirmation")), f"Missing pending confirmation: {confirmation}")
    _assert(str(confirmation.get("final_answer_source")) in {"mail_confirmation", "mail_confirmation_renderer", "mail_confirmation_renderer_fallback"}, f"Unexpected confirmation source: {confirmation.get('final_answer_source')}")

    patch = _chat(
        session_id,
        conversation_id,
        "body: Public regression update v2: maintenance finished at 10:30 UTC and the report will be shared tomorrow. Keep it brief.",
    )
    _assert(str(patch.get("termination_reason")) == "needs_confirmation", f"Patch did not preserve confirmation state: {patch}")
    _assert(str(patch.get("final_answer_source")) in {"mail_patch_confirmation", "mail_patch_renderer", "mail_patch_renderer_fallback"}, f"Unexpected patch source: {patch.get('final_answer_source')}")
    patch_payload = dict(patch.get("confirmation_payload") or {})
    patch_kind = str((patch.get("task_plan") or {}).get("patch_kind") or patch_payload.get("patch_kind") or "")
    _assert(patch_kind in {"replace_pending_draft_body", "edit_pending_draft"}, f"Patch kind missing/unexpected: {patch}")

    confirmed = _chat(session_id, conversation_id, "confirm send")
    task_id = str(confirmed.get("task_id") or "")
    _assert(task_id, f"Confirm did not create DLP task: {confirmed}")
    task = _poll_task(task_id, expected={"sent", "pending_approval", "send_failed", "delivery_deferred"}, timeout_seconds=240)
    initial_status = str(task.get("status"))
    if initial_status == "pending_approval":
        _request("POST", f"/tasks/{task_id}/approve", json={"actor": "codex_regression", "reason": "mail chain regression", **_actor_payload()})
        task = _poll_task(task_id, expected={"sent", "send_failed", "delivery_deferred"}, timeout_seconds=240)
    _assert(str(task.get("status")) == "sent", f"Confirmed mail task did not send successfully: {task}")
    report["checks"]["mail_draft_patch_confirm_send"] = {
        "draft_source": draft.get("final_answer_source"),
        "confirmation_source": confirmation.get("final_answer_source"),
        "patch_source": patch.get("final_answer_source"),
        "patch_kind": patch_kind,
        "task_id": task_id,
        "status": task.get("status"),
        "delivery_status": task.get("delivery_status"),
        "smtp_provider": task.get("smtp_provider"),
    }


def _replay_scenario(
    scenario_id: str,
    *,
    session_id: str,
    conversation_id: str,
    fault_injection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "destination_email": RECIPIENT,
        "fault_injection": fault_injection,
        **_actor_payload(),
    }
    return _request("POST", f"/labs/dlp/scenarios/{scenario_id}/replay", json=payload)


def _run_dlp_approval_reject_failure(session_id: str, conversation_id: str, report: dict[str, Any]) -> None:
    approval_seed = _replay_scenario(
        "customer_contacts_pending_approval",
        session_id=session_id,
        conversation_id=conversation_id,
    )
    approval_task_id = str(approval_seed["task_id"])
    pending = _poll_task(approval_task_id, expected={"pending_approval"}, timeout_seconds=180)
    _assert(str(pending.get("status")) == "pending_approval", f"Approval scenario did not reach pending_approval: {pending}")
    approved = _request(
        "POST",
        f"/tasks/{approval_task_id}/approve",
        json={"actor": "codex_regression", "reason": "approval regression", **_actor_payload()},
    )
    _assert(str(approved.get("status")) == "approved", f"Approve endpoint did not mark task approved: {approved}")
    approved_final = _poll_task(approval_task_id, expected={"sent", "send_failed", "delivery_deferred"}, timeout_seconds=240)
    _assert(str(approved_final.get("status")) == "sent", f"Approved task did not send successfully: {approved_final}")

    reject_seed = _replay_scenario(
        "api_secret_pending_approval",
        session_id=session_id,
        conversation_id=conversation_id,
    )
    reject_task_id = str(reject_seed["task_id"])
    reject_pending = _poll_task(reject_task_id, expected={"pending_approval"}, timeout_seconds=180)
    _assert(str(reject_pending.get("status")) == "pending_approval", f"Reject scenario did not reach pending_approval: {reject_pending}")
    rejected = _request(
        "POST",
        f"/tasks/{reject_task_id}/reject",
        json={"actor": "codex_regression", "reason": "regression reject path", **_actor_payload()},
    )
    _assert(str(rejected.get("status")) == "rejected", f"Reject endpoint failed: {rejected}")

    failure_seed = _replay_scenario(
        "smtp_failure_after_low_risk_review",
        session_id=session_id,
        conversation_id=conversation_id,
    )
    failure_task_id = str(failure_seed["task_id"])
    failure = _poll_task(failure_task_id, expected={"send_failed", "pending_approval"}, timeout_seconds=240)
    forced_failure_required_approval = str(failure.get("status")) == "pending_approval"
    if forced_failure_required_approval:
        raise RuntimeError(f"Forced SMTP failure scenario unexpectedly required approval: {failure}")
    _assert(str(failure.get("status")) == "send_failed", f"Forced SMTP failure scenario did not fail as expected: {failure}")

    report["checks"]["dlp_approval_reject_failure"] = {
        "approved_task_id": approval_task_id,
        "approved_final_status": approved_final.get("status"),
        "approved_delivery_status": approved_final.get("delivery_status"),
        "rejected_task_id": reject_task_id,
        "rejected_status": rejected.get("status"),
        "forced_failure_task_id": failure_task_id,
        "forced_failure_status": failure.get("status"),
        "forced_failure_required_approval": forced_failure_required_approval,
        "forced_failure_delivery_error": failure.get("delivery_error"),
    }


def main() -> int:
    session_id = f"mail_dlp_regression_{uuid.uuid4().hex[:8]}"
    conversation_id = _create_conversation(session_id)
    report: dict[str, Any] = {
        "base_url": BASE_URL,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "tenant_id": TENANT_ID,
        "user_id": USER_ID,
        "workspace_id": WORKSPACE_ID,
        "recipient": RECIPIENT,
        "checks": {},
    }

    _run_inbound_checks(report)
    _run_mail_draft_patch_confirm(session_id, conversation_id, report)
    _run_dlp_approval_reject_failure(session_id, conversation_id, report)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MAIL_DLP_REGRESSION_FAILED: {exc}", file=sys.stderr)
        raise
