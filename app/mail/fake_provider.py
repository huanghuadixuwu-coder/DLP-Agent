from __future__ import annotations

from copy import deepcopy
from threading import Lock
from typing import Any
from uuid import uuid4

from app.mail.domain import MailAccount, MailDraft, MailMessage, MailThread, utc_now
from app.mail.fixtures import build_fixture_account, build_fixture_messages, build_fixture_threads
from app.mail.provider import MailProviderCapabilities, MailProviderResponse


FAILURE_MODES = {"healthy", "auth_expired", "timeout", "uncertain", "rate_limited", "unavailable"}


class FakeMailProvider:
    provider_name = "fake_mail_provider"

    def __init__(
        self,
        *,
        account: MailAccount | None = None,
        messages: list[MailMessage] | None = None,
        threads: list[MailThread] | None = None,
        capabilities: MailProviderCapabilities | None = None,
        failure_mode: str = "healthy",
    ) -> None:
        self.account = deepcopy(account or build_fixture_account())
        self.capabilities = capabilities or MailProviderCapabilities()
        self._messages = {item.message_id: deepcopy(item) for item in (messages or build_fixture_messages())}
        self._threads = {item.thread_id: deepcopy(item) for item in (threads or build_fixture_threads())}
        self._sent_by_key: dict[str, dict[str, Any]] = {}
        self._label_results_by_key: dict[str, dict[str, Any]] = {}
        self._uncertain_keys: set[str] = set()
        self._lock = Lock()
        self._failure_mode = "healthy"
        self._operation_count: dict[str, int] = {}
        self.set_failure_mode(failure_mode)

    @property
    def failure_mode(self) -> str:
        return self._failure_mode

    @property
    def sent_count(self) -> int:
        return len(self._sent_by_key)

    @property
    def sent_messages(self) -> list[dict[str, Any]]:
        return [deepcopy(item) for item in self._sent_by_key.values()]

    def set_failure_mode(self, mode: str) -> None:
        normalized = str(mode or "healthy").strip().lower()
        if normalized not in FAILURE_MODES:
            raise ValueError(f"Unsupported fake provider failure mode: {mode}")
        self._failure_mode = normalized
        self.account.status = "auth_expired" if normalized == "auth_expired" else "active"

    def _count(self, operation: str) -> None:
        self._operation_count[operation] = self._operation_count.get(operation, 0) + 1

    def _response(
        self,
        *,
        operation: str,
        ok: bool = True,
        status: str = "completed",
        data: dict[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
        retryable: bool = False,
        uncertain: bool = False,
    ) -> MailProviderResponse:
        return MailProviderResponse(
            ok=ok,
            operation=operation,
            status=status,
            provider=self.provider_name,
            data=deepcopy(data or {}),
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
            uncertain=uncertain,
        )

    def _scope_failure(self, operation: str, actor_context: dict[str, Any] | None) -> MailProviderResponse | None:
        actor = actor_context or {}
        tenant_id = str(actor.get("tenant_id") or self.account.tenant_id)
        workspace_id = str(actor.get("workspace_id") or self.account.workspace_id)
        if tenant_id != self.account.tenant_id or workspace_id != self.account.workspace_id:
            return self._response(
                operation=operation,
                ok=False,
                status="permission_denied",
                error_code="scope_mismatch",
                error_message="Actor tenant/workspace does not match the provider account scope.",
            )
        return None

    def _availability_failure(self, operation: str, actor_context: dict[str, Any] | None) -> MailProviderResponse | None:
        scope_failure = self._scope_failure(operation, actor_context)
        if scope_failure:
            return scope_failure
        if self.failure_mode == "auth_expired":
            return self._response(
                operation=operation,
                ok=False,
                status="auth_expired",
                error_code="auth_expired",
                error_message="Fake provider credentials have expired.",
            )
        if self.failure_mode == "unavailable":
            return self._response(
                operation=operation,
                ok=False,
                status="degraded",
                error_code="provider_unavailable",
                error_message="Fake provider is unavailable.",
                retryable=True,
            )
        if self.failure_mode == "rate_limited":
            return self._response(
                operation=operation,
                ok=False,
                status="rate_limited",
                error_code="provider_rate_limited",
                error_message="Fake provider rate limit reached.",
                retryable=True,
            )
        return None

    def search_messages(
        self,
        *,
        query: str = "",
        limit: int = 20,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        operation = "search_messages"
        self._count(operation)
        failure = self._availability_failure(operation, actor_context)
        if failure:
            return failure
        normalized = str(query or "").strip().lower()
        messages = list(self._messages.values())
        if normalized:
            messages = [
                item
                for item in messages
                if normalized in " ".join([item.sender, item.subject, item.body_text, item.snippet]).lower()
            ]
        messages = sorted(messages, key=lambda item: item.received_at or item.sent_at, reverse=True)[: max(1, int(limit))]
        return self._response(operation=operation, data={"messages": [item.to_dict() for item in messages]})

    def read_message(self, message_id: str, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        operation = "read_message"
        self._count(operation)
        failure = self._availability_failure(operation, actor_context)
        if failure:
            return failure
        message = self._messages.get(str(message_id or ""))
        if not message:
            return self._response(
                operation=operation,
                ok=False,
                status="not_found",
                error_code="message_not_found",
                error_message=f"Unknown message_id: {message_id}",
            )
        return self._response(operation=operation, data={"message": message.to_dict()})

    def list_threads(
        self,
        *,
        query: str = "",
        limit: int = 20,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        operation = "list_threads"
        self._count(operation)
        failure = self._availability_failure(operation, actor_context)
        if failure:
            return failure
        if not self.capabilities.threads:
            return self._response(
                operation=operation,
                ok=False,
                status="unsupported",
                error_code="thread_capability_unavailable",
                error_message="Provider-native thread listing is unavailable.",
            )
        normalized = str(query or "").strip().lower()
        threads = list(self._threads.values())
        if normalized:
            threads = [
                item
                for item in threads
                if normalized in " ".join([item.subject_normalized, *item.participants, *item.labels]).lower()
            ]
        threads = sorted(threads, key=lambda item: item.last_message_at, reverse=True)[: max(1, int(limit))]
        return self._response(operation=operation, data={"threads": [item.to_dict() for item in threads]})

    def read_thread(self, thread_id: str, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        operation = "read_thread"
        self._count(operation)
        failure = self._availability_failure(operation, actor_context)
        if failure:
            return failure
        thread = self._threads.get(str(thread_id or ""))
        if not thread:
            return self._response(
                operation=operation,
                ok=False,
                status="not_found",
                error_code="thread_not_found",
                error_message=f"Unknown thread_id: {thread_id}",
            )
        messages = [self._messages[item].to_dict() for item in thread.message_ids if item in self._messages]
        return self._response(operation=operation, data={"thread": thread.to_dict(), "messages": messages})

    def sync_mailbox(self, *, mailbox: str = "INBOX", actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        operation = "sync_mailbox"
        self._count(operation)
        failure = self._availability_failure(operation, actor_context)
        if failure:
            return failure
        return self._response(
            operation=operation,
            data={"mailbox": mailbox, "synced_at": utc_now(), "message_count": len(self._messages)},
        )

    def send_message(
        self,
        draft: MailDraft,
        *,
        idempotency_key: str,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        operation = "send_message"
        with self._lock:
            self._count(operation)
            failure = self._availability_failure(operation, actor_context)
            if failure:
                return failure
            existing = self._sent_by_key.get(idempotency_key)
            if existing:
                return self._response(
                    operation=operation,
                    status="deduplicated",
                    data={**existing, "deduplicated": True},
                )
            if self.failure_mode == "timeout":
                return self._response(
                    operation=operation,
                    ok=False,
                    status="delivery_deferred",
                    error_code="smtp_timeout",
                    error_message="Injected SMTP timeout.",
                    retryable=True,
                )
            sent = {
                "provider_operation_id": f"fake-send-{uuid4().hex[:12]}",
                "draft_id": draft.draft_id,
                "to": list(draft.to),
                "subject": draft.subject,
                "sent_at": utc_now(),
                "deduplicated": False,
            }
            self._sent_by_key[idempotency_key] = sent
            if self.failure_mode == "uncertain" and idempotency_key not in self._uncertain_keys:
                self._uncertain_keys.add(idempotency_key)
                return self._response(
                    operation=operation,
                    ok=False,
                    status="delivery_uncertain",
                    data=sent,
                    error_code="smtp_result_uncertain",
                    error_message="Provider accepted the payload but the acknowledgement was lost.",
                    retryable=True,
                    uncertain=True,
                )
            return self._response(operation=operation, status="sent", data=sent)

    def apply_label(
        self,
        *,
        message_id: str = "",
        thread_id: str = "",
        label: str,
        idempotency_key: str,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        operation = "apply_label"
        with self._lock:
            self._count(operation)
            failure = self._availability_failure(operation, actor_context)
            if failure:
                return failure
            if not self.capabilities.labels:
                return self._response(
                    operation=operation,
                    ok=False,
                    status="unsupported",
                    error_code="label_capability_unavailable",
                    error_message="Provider label writes are unavailable.",
                )
            existing = self._label_results_by_key.get(idempotency_key)
            if existing:
                return self._response(operation=operation, status="deduplicated", data={**existing, "deduplicated": True})
            targets: list[MailMessage] = []
            if message_id and message_id in self._messages:
                targets.append(self._messages[message_id])
            if thread_id and thread_id in self._threads:
                targets.extend(self._messages[item] for item in self._threads[thread_id].message_ids if item in self._messages)
            if not targets:
                return self._response(
                    operation=operation,
                    ok=False,
                    status="not_found",
                    error_code="label_target_not_found",
                    error_message="No message or thread matched the label request.",
                )
            for item in targets:
                if label not in item.labels:
                    item.labels.append(label)
            result = {"message_ids": [item.message_id for item in targets], "label": label, "deduplicated": False}
            self._label_results_by_key[idempotency_key] = result
            return self._response(operation=operation, data=result)

    def get_health(self) -> MailProviderResponse:
        status = "healthy" if self.failure_mode == "healthy" else "degraded"
        if self.failure_mode == "auth_expired":
            status = "auth_expired"
        return self._response(
            operation="get_health",
            ok=self.failure_mode == "healthy",
            status=status,
            data={
                "account_status": self.account.status,
                "failure_mode": self.failure_mode,
                "capabilities": self.capabilities.to_dict(),
                "operation_count": deepcopy(self._operation_count),
                "sent_count": self.sent_count,
            },
        )
