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

    def get_health(self) -> MailProviderResponse:
        ...
