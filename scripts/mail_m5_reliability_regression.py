from __future__ import annotations

import json
import sys
from uuid import uuid4
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
import app.task_worker as task_worker
from app.mail.fake_provider import FakeMailProvider
from app.mail.fixtures import fixture_actor_context
from app.mail.harness import MailHarness
from app.models import DlpTaskCreateRequest
from app.task_store import (
    create_dlp_task,
    create_mail_dlq_entry,
    get_dlp_task,
    get_mail_dlq_entry_for_task,
    get_task_events,
    replay_mail_dlq_entry,
)

RUN_ID = uuid4().hex[:8]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _provider_timeout_recovery() -> dict:
    actor = fixture_actor_context()
    provider = FakeMailProvider(failure_mode="timeout")
    harness = MailHarness(provider)
    draft = harness.build_send_draft(
        actor_context=actor,
        conversation_id=str(actor["conversation_id"]),
        to=["timeout@example.com"],
        subject="M5 timeout",
        body_text="Public timeout body.",
    )
    draft_id = str(draft["draft_ref"])
    harness.request_send_confirmation(draft_id, actor_context=actor)
    first = harness.submit_dlp_result(draft_id, actor_context=actor, risk="low", confirmed=True)
    _assert(first["status"] == "delivery_deferred", f"timeout should defer: {first}")
    provider.set_failure_mode("healthy")
    replay = harness.retry_delivery(draft_id, actor_context=actor)
    _assert(replay["status"] == "sent", f"timeout replay should send: {replay}")
    return {"draft_id": draft_id, "first_status": first["status"], "replay_status": replay["status"]}


def _duplicate_confirmation_idempotency() -> dict:
    key = f"m5-duplicate-confirmation-key-{RUN_ID}"
    first = create_dlp_task(
        session_id="session-m5",
        conversation_id="conversation-m5",
        message_raw="public",
        request_message="public",
        delivery_subject="M5 duplicate",
        delivery_body="public",
        destination_email="duplicate@example.com",
        status="queued_for_send",
        idempotency_key=key,
        tenant_id="tenant-m5",
        user_id="user-m5",
        workspace_id="workspace-m5",
    )
    second = create_dlp_task(
        session_id="session-m5",
        conversation_id="conversation-m5",
        message_raw="public second",
        request_message="public second",
        delivery_subject="M5 duplicate second",
        delivery_body="public second",
        destination_email="duplicate@example.com",
        status="queued_for_send",
        idempotency_key=key,
        tenant_id="tenant-m5",
        user_id="user-m5",
        workspace_id="workspace-m5",
    )
    _assert(first["task_id"] == second["task_id"], f"idempotency mismatch: {first} {second}")
    return {"task_id": first["task_id"], "idempotency_key": key}


def _worker_unavailable_observation() -> dict:
    original = main_module.enqueue_dlp_risk_task
    try:
        main_module.enqueue_dlp_risk_task = lambda task_id: (_ for _ in ()).throw(RuntimeError("M5 injected broker unavailable"))
        task = main_module._create_async_dlp_task(
            DlpTaskCreateRequest(
                session_id="session-m5",
                conversation_id="conversation-m5",
                message="Send public note to worker@example.com.",
                request_message="Send public note.",
                review_content="Public note.",
                resolved_outbound_content="Public note.",
                delivery_subject="M5 worker unavailable",
                delivery_body="Public note.",
                destination_email="worker@example.com",
                requested_action="send_message",
                tenant_id="tenant-m5",
                user_id="user-m5",
                workspace_id="workspace-m5",
                roles=["admin", "user"],
            ),
            actor_context={
                "tenant_id": "tenant-m5",
                "user_id": "user-m5",
                "workspace_id": "workspace-m5",
                "roles": ["admin", "user"],
                "session_id": "session-m5",
                "conversation_id": "conversation-m5",
            },
        )
    finally:
        main_module.enqueue_dlp_risk_task = original
    recovery = dict(task.get("domain_result", {}).get("recovery_observation") or {})
    _assert(task["status"] == "failed", f"worker unavailable status mismatch: {task}")
    _assert(recovery.get("observation_type") == "dependency_failure", f"missing recovery observation: {recovery}")
    return {"task_id": task["task_id"], "status": task["status"], "recovery_type": recovery.get("observation_type")}


def _retry_exhaustion_enters_dlq() -> dict:
    task = create_dlp_task(
        session_id="session-m5",
        conversation_id="conversation-m5",
        message_raw="Public retry exhaustion.",
        request_message="Public retry exhaustion.",
        delivery_subject="M5 retry exhaustion",
        delivery_body="Public retry exhaustion.",
        destination_email="retry-exhaustion@example.com",
        status="sending",
        tenant_id="tenant-m5",
        user_id="user-m5",
        workspace_id="workspace-m5",
    )
    finalized = task_worker._finalize_worker_failure(task, RuntimeError("SMTP timeout after retries exhausted"))
    updated = get_dlp_task(str(task["task_id"]))
    dlq = get_mail_dlq_entry_for_task(str(task["task_id"]), operation="send_email_smtp")
    _assert(finalized and updated and updated["status"] == "dead_letter", f"retry exhaustion should be dead_letter: {updated}")
    _assert(dlq and dlq["safe_replay_allowed"] is True, f"retry exhaustion DLQ missing/safety mismatch: {dlq}")
    return {"task_id": task["task_id"], "status": updated["status"], "dlq_id": dlq["dlq_id"]}


def _smtp_uncertain_blocks_replay() -> dict:
    task = create_dlp_task(
        session_id="session-m5",
        conversation_id="conversation-m5",
        message_raw="Public uncertain send.",
        request_message="Public uncertain send.",
        delivery_subject="M5 uncertain",
        delivery_body="Public uncertain send.",
        destination_email="uncertain@example.com",
        status="queued_for_send",
        fault_injection={"force_smtp_uncertain": True},
        tenant_id="tenant-m5",
        user_id="user-m5",
        workspace_id="workspace-m5",
    )
    result = task_worker.send_dlp_email_task.run(str(task["task_id"]))
    updated = get_dlp_task(str(task["task_id"]))
    dlq = get_mail_dlq_entry_for_task(str(task["task_id"]), operation="send_email_smtp")
    _assert(result["status"] == "delivery_uncertain", f"uncertain result mismatch: {result}")
    _assert(updated and updated["status"] == "dead_letter", f"uncertain task should enter DLQ: {updated}")
    _assert(dlq and dlq["safe_replay_allowed"] is False, f"uncertain DLQ must block auto replay: {dlq}")
    replay = replay_mail_dlq_entry(str(dlq["dlq_id"]), actor="m5-regression")
    _assert(replay["status"] == "blocked", f"uncertain DLQ replay should block: {replay}")
    return {"task_id": task["task_id"], "dlq_id": dlq["dlq_id"], "replay_status": replay["status"]}


def _safe_dlq_replay_checkpoint() -> dict:
    task = create_dlp_task(
        session_id="session-m5",
        conversation_id="conversation-m5",
        message_raw="Public safe replay.",
        request_message="Public safe replay.",
        delivery_subject="M5 safe replay",
        delivery_body="Public safe replay.",
        destination_email="safe-replay@example.com",
        status="dead_letter",
        idempotency_key=f"m5-safe-replay-key-{RUN_ID}",
        tenant_id="tenant-m5",
        user_id="user-m5",
        workspace_id="workspace-m5",
    )
    dlq = create_mail_dlq_entry(
        task_id=str(task["task_id"]),
        operation="send_email_smtp",
        payload_digest=f"m5-safe-replay-digest-{RUN_ID}",
        last_error="temporary smtp timeout",
        attempt_count=3,
        safe_replay_allowed=True,
        recovery_hint="retry_send_with_same_idempotency_key",
        actor_context={"tenant_id": "tenant-m5", "user_id": "user-m5", "workspace_id": "workspace-m5"},
    )
    replay = replay_mail_dlq_entry(str(dlq["dlq_id"]), actor="m5-regression")
    updated = get_dlp_task(str(task["task_id"]))
    events = [item["event_type"] for item in get_task_events(str(task["task_id"]))]
    _assert(replay["ok"] is True and updated and updated["status"] == "queued_for_send", f"safe replay failed: {replay} {updated}")
    _assert("dead_letter_replay_queued" in events, f"replay event missing: {events}")
    return {"task_id": task["task_id"], "dlq_id": dlq["dlq_id"], "status": updated["status"]}


def main() -> None:
    report = {
        "provider_timeout": _provider_timeout_recovery(),
        "duplicate_confirmation": _duplicate_confirmation_idempotency(),
        "worker_unavailable": _worker_unavailable_observation(),
        "retry_exhaustion": _retry_exhaustion_enters_dlq(),
        "smtp_uncertain": _smtp_uncertain_blocks_replay(),
        "safe_replay": _safe_dlq_replay_checkpoint(),
    }
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
