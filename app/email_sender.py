from __future__ import annotations

import re
import smtplib
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from typing import Any

from app.config import get_settings
from app.resilience import make_failure_observation, run_with_retry


def _mask_email(value: str) -> str:
    if "@" not in value:
        return value
    name, domain = value.split("@", 1)
    if len(name) <= 2:
        masked = name[:1] + "*"
    else:
        masked = name[:2] + "***" + name[-1:]
    return f"{masked}@{domain}"


def _smtp_provider_name(host: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (host or "").strip().lower()).strip("_")
    return normalized or "smtp"


def _normalize_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in attachments or []:
        content_bytes = item.get("content_bytes")
        if isinstance(content_bytes, (bytes, bytearray)) and content_bytes:
            normalized.append(
                {
                    "filename": str(item.get("filename") or "attachment.bin"),
                    "content_type": str(item.get("content_type") or "application/octet-stream"),
                    "content_bytes": bytes(content_bytes),
                }
            )
            continue
        content = str(item.get("content") or item.get("text") or "").strip()
        if content:
            normalized.append(
                {
                    "filename": str(item.get("filename") or "attachment.txt"),
                    "content_type": str(item.get("content_type") or "text/plain"),
                    "content": content,
                }
            )
    return normalized


def _split_content_type(content_type: str) -> tuple[str, str]:
    cleaned = (content_type or "text/plain").strip().lower()
    if "/" not in cleaned:
        return ("text", "plain")
    maintype, subtype = cleaned.split("/", 1)
    return (maintype or "text", subtype or "plain")


def send_email_smtp(
    to_email: str,
    subject: str,
    body: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    provider = _smtp_provider_name(settings.smtp_host)
    from_email = settings.smtp_from or settings.smtp_username
    normalized_attachments = _normalize_attachments(attachments)

    if not settings.email_send_enabled:
        return {
            "ok": False,
            "provider": provider,
            "to_email": to_email,
            "from_email_masked": _mask_email(from_email),
            "sent_at": "",
            "attachments_sent": 0,
            "error": "EMAIL_SEND_ENABLED is false. Set EMAIL_SEND_ENABLED=true after configuring SMTP credentials.",
        }
    if not settings.smtp_username or not settings.smtp_password or not from_email:
        return {
            "ok": False,
            "provider": provider,
            "to_email": to_email,
            "from_email_masked": _mask_email(from_email),
            "sent_at": "",
            "attachments_sent": 0,
            "error": "SMTP credentials are incomplete. Configure SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM.",
        }

    # Use classic SMTP policy so Chinese headers/body are MIME-encoded
    # instead of requiring SMTPUTF8 support from the server.
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body, charset="utf-8")
    for attachment in normalized_attachments:
        maintype, subtype = _split_content_type(attachment["content_type"])
        if "content_bytes" in attachment:
            message.add_attachment(
                attachment["content_bytes"],
                maintype=maintype,
                subtype=subtype,
                filename=attachment["filename"],
            )
        elif maintype == "text":
            message.add_attachment(
                attachment["content"],
                subtype=subtype,
                filename=attachment["filename"],
                charset="utf-8",
            )
        else:
            message.add_attachment(
                attachment["content"].encode("utf-8"),
                maintype=maintype,
                subtype=subtype,
                filename=attachment["filename"],
            )

    def _send_once() -> None:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(
                from_email,
                [to_email],
                message.as_bytes(),
            )

    ok, _, exc, retry_count = run_with_retry(
        _send_once,
        service="smtp",
        operation_name="send_email_smtp",
        attempts=2,
        retry_delay_seconds=0.5,
        circuit_threshold=3,
        cooldown_seconds=60.0,
    )
    if not ok:
        error = str(exc or "unknown SMTP send failure")
        return {
            "ok": False,
            "provider": provider,
            "to_email": to_email,
            "from_email_masked": _mask_email(from_email),
            "sent_at": "",
            "attachments_sent": len(normalized_attachments),
            "error": error,
            "resilience": {
                "retry_count": retry_count,
                "fallback_strategy": "delivery_deferred_or_send_failed_task_state",
            },
            "failure_observation": make_failure_observation(
                service="smtp",
                operation="send_email_smtp",
                error=error,
                fallback_strategy="delivery_deferred_or_send_failed_task_state",
                retry_count=retry_count,
            ),
        }

    return {
        "ok": True,
        "provider": provider,
        "to_email": to_email,
        "from_email_masked": _mask_email(from_email),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "attachments_sent": len(normalized_attachments),
        "error": "",
    }


def send_email_163(
    to_email: str,
    subject: str,
    body: str,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Backward-compatible alias kept for older workflow/tool names.
    return send_email_smtp(to_email=to_email, subject=subject, body=body, attachments=attachments)
