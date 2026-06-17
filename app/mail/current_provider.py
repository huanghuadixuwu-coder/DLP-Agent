from __future__ import annotations

import hashlib
import re
from typing import Any

from app.config import get_settings
from app.email_sender import send_email_smtp
from app.inbound_mail import list_inbound_mail_messages, latest_sync_state, sync_inbound_mail
from app.inbound_mail_store import get_inbound_message
from app.mail.domain import MailAttachment, MailDraft, MailMessage, MailThread
from app.mail.provider import MailProviderCapabilities, MailProviderResponse


def _split_recipients(value: str) -> list[str]:
    return [item.strip().lower() for item in re.split(r"[,;]\s*", value or "") if item.strip()]


def _normalize_subject(value: str) -> str:
    cleaned = re.sub(r"^(re|fw|fwd)\s*:\s*", "", value or "", flags=re.IGNORECASE).strip().lower()
    return cleaned or "(no subject)"


def _thread_id_for_message(message: dict[str, Any]) -> str:
    seed = "|".join(
        [
            _normalize_subject(str(message.get("subject") or "")),
            str(message.get("sender") or "").strip().lower(),
        ]
    )
    return "local-thread-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _message_from_row(row: dict[str, Any]) -> MailMessage:
    body = str(row.get("body_text") or row.get("summary") or row.get("snippet") or "")
    raw_attachments = row.get("attachments") or []
    attachments = [MailAttachment(**item) for item in raw_attachments if isinstance(item, dict)]
    labels = list(row.get("labels") or [])
    if not labels:
        labels = ["seen"] if row.get("is_seen") else ["unread"]
    return MailMessage(
        message_id=str(row.get("message_id") or ""),
        provider_message_id=str(row.get("provider_message_id") or row.get("uid") or row.get("message_id") or ""),
        thread_id=str(row.get("thread_id") or _thread_id_for_message(row)),
        provider_thread_id=str(row.get("provider_thread_id") or ""),
        mailbox=str(row.get("mailbox") or "INBOX"),
        labels=labels,
        sender=str(row.get("sender") or ""),
        to=_split_recipients(str(row.get("recipients") or "")),
        subject=str(row.get("subject") or ""),
        received_at=str(row.get("received_at") or ""),
        snippet=str(row.get("snippet") or ""),
        body_text=body,
        body_html_sanitized=str(row.get("body_html_sanitized") or ""),
        body_preview=str(row.get("body_preview") or body[:240]),
        attachments=attachments,
        headers_json=dict(row.get("headers_json") or {}),
        risk_hint=str(row.get("risk_hint") or "low"),
        is_seen=bool(row.get("is_seen")),
        source_policy={"provider": "current_imap_smtp", "normalized_from": "local_inbound_store"},
    )


class CurrentImapSmtpMailProvider:
    provider_name = "current_imap_smtp"

    def __init__(self, *, allow_external_send: bool = False, allow_external_sync: bool = False) -> None:
        self.allow_external_send = allow_external_send
        self.allow_external_sync = allow_external_sync
        self.capabilities = MailProviderCapabilities(
            search=True,
            read=True,
            threads=True,
            labels=False,
            sync=True,
            send=True,
        )

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
        recovery_observation: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        return MailProviderResponse(
            ok=ok,
            operation=operation,
            status=status,
            provider=self.provider_name,
            data=data or {},
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
            uncertain=uncertain,
            recovery_observation=recovery_observation or {},
        )

    def search_messages(
        self,
        *,
        query: str = "",
        limit: int = 20,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        operation = "search_messages"
        try:
            rows = list_inbound_mail_messages(
                limit=max(1, min(200, int(limit or 20) * 4)),
                actor_context=actor_context,
            )
            needle = str(query or "").strip().lower()
            if needle:
                rows = [
                    row
                    for row in rows
                    if needle
                    in " ".join(
                        [
                            str(row.get("sender") or ""),
                            str(row.get("recipients") or ""),
                            str(row.get("subject") or ""),
                            str(row.get("snippet") or ""),
                            str(row.get("summary") or ""),
                        ]
                    ).lower()
                ]
            messages = [_message_from_row(row).to_dict() for row in rows[: max(1, int(limit or 20))]]
            return self._response(operation=operation, data={"messages": messages, "source": "local_inbound_store"})
        except Exception as exc:
            return self._response(
                operation=operation,
                ok=False,
                status="failed",
                error_code="local_inbound_store_error",
                error_message=str(exc),
                retryable=True,
            )

    def read_message(self, message_id: str, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        operation = "read_message"
        try:
            row = get_inbound_message(str(message_id or ""), actor_context=actor_context)
            if not row:
                return self._response(
                    operation=operation,
                    ok=False,
                    status="not_found",
                    error_code="message_not_found",
                    error_message=f"Unknown message_id: {message_id}",
                )
            return self._response(operation=operation, data={"message": _message_from_row(row).to_dict()})
        except Exception as exc:
            return self._response(
                operation=operation,
                ok=False,
                status="failed",
                error_code="local_inbound_store_error",
                error_message=str(exc),
                retryable=True,
            )

    def list_threads(
        self,
        *,
        query: str = "",
        limit: int = 20,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        search = self.search_messages(query=query, limit=max(20, int(limit or 20) * 4), actor_context=actor_context)
        if not search.ok:
            return self._response(
                operation="list_threads",
                ok=False,
                status=search.status,
                data=search.data,
                error_code=search.error_code,
                error_message=search.error_message,
                retryable=search.retryable,
            )
        grouped: dict[str, dict[str, Any]] = {}
        for message in search.data.get("messages", []):
            thread_id = str(message.get("thread_id") or "")
            if not thread_id:
                continue
            bucket = grouped.setdefault(
                thread_id,
                {
                    "thread_id": thread_id,
                    "subject_normalized": _normalize_subject(str(message.get("subject") or "")),
                    "participants": [],
                    "message_ids": [],
                    "last_message_at": str(message.get("received_at") or message.get("sent_at") or ""),
                    "unread_count": 0,
                    "labels": [],
                },
            )
            sender = str(message.get("sender") or "").strip().lower()
            if sender and sender not in bucket["participants"]:
                bucket["participants"].append(sender)
            bucket["message_ids"].append(str(message.get("message_id") or ""))
            if "unread" in (message.get("labels") or []):
                bucket["unread_count"] += 1
            for label in message.get("labels") or []:
                if label not in bucket["labels"]:
                    bucket["labels"].append(label)
            if str(message.get("received_at") or "") > str(bucket.get("last_message_at") or ""):
                bucket["last_message_at"] = str(message.get("received_at") or "")
        threads = [MailThread(**item).to_dict() for item in grouped.values()]
        threads = sorted(threads, key=lambda item: item.get("last_message_at") or "", reverse=True)[: max(1, int(limit or 20))]
        return self._response(operation="list_threads", data={"threads": threads, "source": "local_inbound_store"})

    def read_thread(self, thread_id: str, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        threads = self.list_threads(limit=100, actor_context=actor_context)
        if not threads.ok:
            return self._response(
                operation="read_thread",
                ok=False,
                status=threads.status,
                data=threads.data,
                error_code=threads.error_code,
                error_message=threads.error_message,
                retryable=threads.retryable,
            )
        match = next((item for item in threads.data.get("threads", []) if item.get("thread_id") == thread_id), None)
        if not match:
            return self._response(
                operation="read_thread",
                ok=False,
                status="not_found",
                error_code="thread_not_found",
                error_message=f"Unknown thread_id: {thread_id}",
            )
        messages: list[dict[str, Any]] = []
        for message_id in match.get("message_ids") or []:
            response = self.read_message(str(message_id), actor_context=actor_context)
            if response.ok and response.data.get("message"):
                messages.append(response.data["message"])
        return self._response(operation="read_thread", data={"thread": match, "messages": messages})

    def sync_mailbox(self, *, mailbox: str = "INBOX", actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        operation = "sync_mailbox"
        if not self.allow_external_sync:
            return self._response(
                operation=operation,
                ok=False,
                status="provider_not_configured",
                data={
                    "mailbox": mailbox,
                    "external_sync_allowed": False,
                    "local_sync_state": latest_sync_state(actor_context=actor_context),
                },
                error_code="external_sync_disabled_for_contract",
                error_message="Current provider sync is available but disabled for contract/regression execution.",
            )
        try:
            result = sync_inbound_mail(limit=50, actor_context=actor_context)
            if not result.get("enabled"):
                return self._response(
                    operation=operation,
                    ok=False,
                    status="provider_not_configured",
                    data=result,
                    error_code="imap_not_configured",
                    error_message=str(result.get("state", {}).get("last_error") or "IMAP is not configured."),
                )
            if result.get("ok") is False:
                return self._response(
                    operation=operation,
                    ok=False,
                    status="failed",
                    data=result,
                    error_code="imap_sync_failed",
                    error_message=str(result.get("error") or "IMAP sync failed."),
                    retryable=True,
                )
            return self._response(operation=operation, data=result)
        except Exception as exc:
            return self._response(
                operation=operation,
                ok=False,
                status="failed",
                error_code="imap_sync_error",
                error_message=str(exc),
                retryable=True,
            )

    def send_message(
        self,
        draft: MailDraft,
        *,
        idempotency_key: str,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        operation = "send_message"
        if not self.allow_external_send:
            return self._response(
                operation=operation,
                ok=False,
                status="confirmation_required",
                data={
                    "draft_id": draft.draft_id,
                    "idempotency_key": idempotency_key,
                    "external_send_allowed": False,
                    "provider_write_supported": True,
                },
                error_code="external_send_disabled_for_contract",
                error_message="Current provider send is available but disabled for contract/regression execution.",
            )
        recipients = list(draft.to or [])
        if not recipients:
            return self._response(
                operation=operation,
                ok=False,
                status="failed",
                error_code="recipient_missing",
                error_message="Cannot send a draft without recipients.",
            )
        delivery_results = [
            send_email_smtp(
                to_email=recipient,
                subject=draft.subject,
                body=draft.body_text or draft.body_html,
                attachments=[item.to_dict() for item in draft.attachments],
            )
            for recipient in recipients
        ]
        failures = [item for item in delivery_results if not item.get("ok")]
        if failures:
            first = failures[0]
            return self._response(
                operation=operation,
                ok=False,
                status="delivery_deferred",
                data={"delivery_results": delivery_results, "draft_id": draft.draft_id},
                error_code="smtp_send_failed",
                error_message=str(first.get("error") or "SMTP send failed."),
                retryable=True,
                recovery_observation=first.get("failure_observation") or {},
            )
        return self._response(
            operation=operation,
            status="sent",
            data={"delivery_results": delivery_results, "draft_id": draft.draft_id, "recipient_count": len(recipients)},
        )

    def apply_label(
        self,
        *,
        message_id: str = "",
        thread_id: str = "",
        label: str,
        idempotency_key: str,
        actor_context: dict[str, Any] | None = None,
    ) -> MailProviderResponse:
        return self._response(
            operation="apply_label",
            ok=False,
            status="unsupported",
            data={
                "message_id": message_id,
                "thread_id": thread_id,
                "label": label,
                "idempotency_key": idempotency_key,
                "provider_write_supported": False,
            },
            error_code="label_capability_unavailable",
            error_message="Current IMAP/SMTP adapter does not support provider-native label writes.",
        )

    def get_health(self, *, actor_context: dict[str, Any] | None = None) -> MailProviderResponse:
        settings = get_settings()
        smtp_configured = bool(settings.smtp_username and settings.smtp_password and (settings.smtp_from or settings.smtp_username))
        imap_configured = bool(settings.imap_enabled and settings.imap_username and settings.imap_password)
        state = latest_sync_state(actor_context=actor_context)
        status = "healthy" if (smtp_configured or imap_configured) else "provider_not_configured"
        return self._response(
            operation="get_health",
            ok=bool(smtp_configured or imap_configured),
            status=status,
            data={
                "capabilities": self.capabilities.to_dict(),
                "smtp_configured": smtp_configured,
                "imap_configured": imap_configured,
                "email_send_enabled": bool(settings.email_send_enabled),
                "allow_external_send": self.allow_external_send,
                "allow_external_sync": self.allow_external_sync,
                "sync_state": state,
            },
        )
