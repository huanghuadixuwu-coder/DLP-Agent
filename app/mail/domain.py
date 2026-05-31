from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_mail_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _compact_addresses(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        normalized = str(value or "").strip().lower()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


@dataclass(slots=True)
class MailAttachment:
    attachment_id: str = field(default_factory=lambda: new_mail_id("attachment"))
    filename: str = ""
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    provider_attachment_id: str = ""
    content_ref: str = ""
    inline: bool = False
    source_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MailAccount:
    account_id: str = field(default_factory=lambda: new_mail_id("account"))
    tenant_id: str = "local-dev"
    user_id: str = "local-user"
    workspace_id: str = "default"
    provider: str = "fake"
    email_address: str = ""
    display_name: str = ""
    capabilities: dict[str, bool] = field(default_factory=dict)
    auth_ref: str = ""
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MailMessage:
    message_id: str = field(default_factory=lambda: new_mail_id("message"))
    provider_message_id: str = ""
    thread_id: str = ""
    provider_thread_id: str = ""
    mailbox: str = "INBOX"
    labels: list[str] = field(default_factory=list)
    sender: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str = ""
    received_at: str = ""
    sent_at: str = ""
    snippet: str = ""
    body_text: str = ""
    body_html_sanitized: str = ""
    body_preview: str = ""
    attachments: list[MailAttachment] = field(default_factory=list)
    headers_json: dict[str, Any] = field(default_factory=dict)
    risk_hint: str = "low"
    is_seen: bool = False
    source_policy: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.to = _compact_addresses(self.to)
        self.cc = _compact_addresses(self.cc)
        self.bcc = _compact_addresses(self.bcc)
        if not self.body_preview:
            self.body_preview = self.body_text[:240]
        if not self.snippet:
            self.snippet = self.body_preview[:160]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MailThread:
    thread_id: str = field(default_factory=lambda: new_mail_id("thread"))
    provider_thread_id: str = ""
    subject_normalized: str = ""
    participants: list[str] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    last_message_at: str = ""
    unread_count: int = 0
    labels: list[str] = field(default_factory=list)
    summary_observation_ref: str = ""
    open_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MailDraft:
    draft_id: str = field(default_factory=lambda: new_mail_id("draft"))
    conversation_id: str = ""
    draft_kind: str = "new_message"
    status: str = "draft_building"
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    body_html: str = ""
    body_format: str = "plain"
    attachments: list[MailAttachment] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    body_sources: list[dict[str, Any]] = field(default_factory=list)
    source_policy: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    requires_confirmation: bool = True
    requires_dlp: bool = True
    created_from_observations: list[str] = field(default_factory=list)
    last_patch_request: dict[str, Any] = field(default_factory=dict)
    actor_context: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    version: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.to = _compact_addresses(self.to)
        self.cc = _compact_addresses(self.cc)
        self.bcc = _compact_addresses(self.bcc)
        self.refresh_missing_fields()

    def refresh_missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.to:
            missing.append("to")
        if not self.subject.strip():
            missing.append("subject")
        if not self.body_text.strip() and not self.body_html.strip():
            missing.append("body")
        self.missing_fields = missing
        if self.status in {"draft_building", "needs_clarification", "draft_ready"}:
            self.status = "needs_clarification" if missing else "draft_ready"
        return list(missing)

    def touch(self) -> None:
        self.version += 1
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MailOperationTask:
    operation_id: str = field(default_factory=lambda: new_mail_id("mailop"))
    task_id: str = ""
    task_type: str = "mail_send"
    draft_id: str = ""
    status: str = "queued_send"
    dlp_task_id: str = ""
    provider_operation_id: str = ""
    idempotency_key: str = ""
    attempt_count: int = 0
    last_error: str = ""
    recovery_hint: str = ""
    actor_context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MailObservation:
    observation_type: str
    status: str = "completed"
    source_tool: str = "mail_harness"
    summary: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    message_refs: list[str] = field(default_factory=list)
    thread_refs: list[str] = field(default_factory=list)
    draft_ref: str = ""
    task_ref: str = ""
    risk: str = ""
    missing_fields: list[str] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    actor_context: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source"] = self.source_tool
        result["grounding_kind"] = "tool"
        result["success"] = self.status not in {
            "blocked",
            "conflict",
            "degraded",
            "failed",
            "permission_denied",
            "rate_limited",
            "rejected",
        }
        return result
