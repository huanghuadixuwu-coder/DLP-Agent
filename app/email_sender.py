from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from typing import Any

from app.config import get_settings


def _mask_email(value: str) -> str:
    if "@" not in value:
        return value
    name, domain = value.split("@", 1)
    if len(name) <= 2:
        masked = name[:1] + "*"
    else:
        masked = name[:2] + "***" + name[-1:]
    return f"{masked}@{domain}"


def send_email_163(to_email: str, subject: str, body: str) -> dict[str, Any]:
    settings = get_settings()
    provider = "163_smtp"
    from_email = settings.smtp_from or settings.smtp_username

    if not settings.email_send_enabled:
        return {
            "ok": False,
            "provider": provider,
            "to_email": to_email,
            "from_email_masked": _mask_email(from_email),
            "sent_at": "",
            "error": "EMAIL_SEND_ENABLED is false. Set EMAIL_SEND_ENABLED=true after configuring SMTP credentials.",
        }
    if not settings.smtp_username or not settings.smtp_password or not from_email:
        return {
            "ok": False,
            "provider": provider,
            "to_email": to_email,
            "from_email_masked": _mask_email(from_email),
            "sent_at": "",
            "error": "SMTP credentials are incomplete. Configure SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM.",
        }

    # Use classic SMTP policy so Chinese headers/body are MIME-encoded
    # instead of requiring SMTPUTF8 support from the server.
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body, charset="utf-8")

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.sendmail(
                from_email,
                [to_email],
                message.as_bytes(),
            )
    except Exception as exc:
        return {
            "ok": False,
            "provider": provider,
            "to_email": to_email,
            "from_email_masked": _mask_email(from_email),
            "sent_at": "",
            "error": str(exc),
        }

    return {
        "ok": True,
        "provider": provider,
        "to_email": to_email,
        "from_email_masked": _mask_email(from_email),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "error": "",
    }
