from __future__ import annotations

from typing import Any

from app.mail.domain import MailDraft
from app.mail.provider import MailProvider, MailProviderResponse


PASS_STATUSES = {
    "auth_expired",
    "completed",
    "confirmation_required",
    "deduplicated",
    "degraded",
    "delivery_deferred",
    "delivery_uncertain",
    "failed",
    "healthy",
    "not_found",
    "permission_denied",
    "provider_not_configured",
    "rate_limited",
    "sent",
    "unsupported",
}


def _response_report(name: str, response: MailProviderResponse, *, actor_context: dict[str, Any]) -> dict[str, Any]:
    observation = response.to_observation(observation_type=f"mail_provider.{name}", actor_context=actor_context)
    return {
        "name": name,
        "ok": isinstance(response, MailProviderResponse)
        and bool(response.operation)
        and bool(response.provider)
        and response.status in PASS_STATUSES
        and observation.get("observation_type") == f"mail_provider.{name}",
        "status": response.status,
        "provider": response.provider,
        "operation": response.operation,
        "error_code": response.error_code,
        "retryable": response.retryable,
        "uncertain": response.uncertain,
        "observation_status": observation.get("status"),
        "observation_success": observation.get("success"),
        "recovery_observation": observation.get("recovery_observation") or {},
    }


def _first_message_id(search_response: MailProviderResponse) -> str:
    for item in search_response.data.get("messages") or []:
        message_id = str(item.get("message_id") or "")
        if message_id:
            return message_id
    return ""


def _first_thread_id(threads_response: MailProviderResponse) -> str:
    for item in threads_response.data.get("threads") or []:
        thread_id = str(item.get("thread_id") or "")
        if thread_id:
            return thread_id
    return ""


def run_mail_provider_contract(
    provider: MailProvider,
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = actor_context or {
        "tenant_id": "local-dev",
        "workspace_id": "default",
        "user_id": "contract-user",
        "roles": ["mail:read", "mail:send"],
    }
    checks: list[dict[str, Any]] = []

    health = provider.get_health()
    checks.append(_response_report("health", health, actor_context=actor))

    search = provider.search_messages(query="", limit=5, actor_context=actor)
    checks.append(_response_report("search_messages", search, actor_context=actor))

    message_id = _first_message_id(search)
    if message_id:
        read = provider.read_message(message_id, actor_context=actor)
    else:
        read = provider.read_message("__contract_missing_message__", actor_context=actor)
    checks.append(_response_report("read_message", read, actor_context=actor))

    threads = provider.list_threads(query="", limit=5, actor_context=actor)
    checks.append(_response_report("list_threads", threads, actor_context=actor))

    thread_id = _first_thread_id(threads)
    if thread_id:
        read_thread = provider.read_thread(thread_id, actor_context=actor)
    else:
        read_thread = provider.read_thread("__contract_missing_thread__", actor_context=actor)
    checks.append(_response_report("read_thread", read_thread, actor_context=actor))

    sync = provider.sync_mailbox(mailbox="INBOX", actor_context=actor)
    checks.append(_response_report("sync_mailbox", sync, actor_context=actor))

    draft = MailDraft(
        conversation_id="contract-conversation",
        to=["contract-recipient@example.com"],
        subject="Contract test",
        body_text="Provider contract test body.",
        actor_context=actor,
        idempotency_key="contract-send-key",
    )
    send = provider.send_message(draft, idempotency_key="contract-send-key", actor_context=actor)
    checks.append(_response_report("send_message", send, actor_context=actor))

    label = provider.apply_label(
        message_id=message_id or "__contract_missing_message__",
        label="contract-tested",
        idempotency_key="contract-label-key",
        actor_context=actor,
    )
    label_report = _response_report("apply_label", label, actor_context=actor)
    if label.status == "unsupported":
        label_report["provider_write_supported"] = label.data.get("provider_write_supported")
        label_report["ok"] = label_report["ok"] and label.data.get("provider_write_supported") is False
    checks.append(label_report)

    ok = all(item.get("ok") for item in checks)
    return {
        "ok": ok,
        "provider": provider.provider_name,
        "capabilities": provider.capabilities.to_dict(),
        "checks": checks,
    }
