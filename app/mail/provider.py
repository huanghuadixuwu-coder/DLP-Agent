from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.mail.domain import MailDraft


@dataclass(slots=True)
class MailProviderCapabilities:
    search: bool = True
    read: bool = True
    threads: bool = True
    labels: bool = True
    sync: bool = True
    send: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(slots=True)
class MailProviderResponse:
    ok: bool
    operation: str
    status: str
    provider: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    uncertain: bool = False
    recovery_observation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_observation(
        self,
        *,
        observation_type: str = "mail_provider_result",
        actor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = self.status or ("completed" if self.ok else "failed")
        success_statuses = {"completed", "sent", "deduplicated", "healthy", "degraded"}
        failure_statuses = {
            "auth_expired",
            "blocked",
            "delivery_deferred",
            "delivery_uncertain",
            "failed",
            "not_found",
            "permission_denied",
            "provider_not_configured",
            "rate_limited",
            "unsupported",
        }
        success = bool(self.ok or status in success_statuses) and status not in failure_statuses
        side_effects: list[dict[str, Any]] = []
        if self.operation in {"send_message", "apply_label"}:
            side_effects.append(
                {
                    "operation": self.operation,
                    "executed": bool(self.ok and status not in {"confirmation_required", "unsupported"}),
                    "uncertain": self.uncertain,
                    "provider": self.provider,
                }
            )
        constraints: list[dict[str, Any]] = []
        provider_write_supported = self.data.get("provider_write_supported")
        if provider_write_supported is False:
            constraints.append(
                {
                    "constraint": "provider_write_supported",
                    "value": False,
                    "operation": self.operation,
                }
            )
        if self.error_code:
            constraints.append(
                {
                    "constraint": "provider_error",
                    "error_code": self.error_code,
                    "retryable": self.retryable,
                    "uncertain": self.uncertain,
                }
            )
        recovery = self.recovery_observation or {}
        if not success and not recovery:
            recovery = {
                "observation_type": "dependency_failure",
                "status": status,
                "service": self.provider,
                "operation": self.operation,
                "error_code": self.error_code,
                "retryable": self.retryable,
                "fallback_strategy": "surface_provider_observation_to_renderer",
            }
        return {
            "observation_type": observation_type,
            "status": status,
            "source": self.provider,
            "source_tool": self.provider,
            "grounding_kind": "tool",
            "success": success,
            "summary": self.error_message or f"{self.provider}.{self.operation}: {status}",
            "provenance": {
                "provider": self.provider,
                "operation": self.operation,
                "error_code": self.error_code,
            },
            "confidence": 1.0 if success else 0.0,
            "missing_fields": [],
            "constraints": constraints,
            "side_effects": side_effects,
            "citations": [],
            "actor_context": actor_context or {},
            "payload": self.to_dict(),
            "recovery_observation": recovery,
        }


@runtime_checkable
class MailProvider(Protocol):
    provider_name: str
    capabilities: MailProviderCapabilities

    def search_messages(self, *, query: str = "", limit: int = 20, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        ...

    def read_message(self, message_id: str, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        ...

    def list_threads(self, *, query: str = "", limit: int = 20, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        ...

    def read_thread(self, thread_id: str, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        ...

    def sync_mailbox(self, *, mailbox: str = "INBOX", actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        ...

    def send_message(self, draft: MailDraft, *, idempotency_key: str, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        ...

    def apply_label(
        self,
        *,
        message_id: str = "",
        thread_id: str = "",
        label: str,
        idempotency_key: str,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        ...

    def get_health(self, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        ...
