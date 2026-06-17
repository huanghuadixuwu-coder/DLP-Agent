from __future__ import annotations

import html
import hashlib
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.actor_context import actor_from_mapping
from app.config import get_settings
from app.graph import get_llm
from app.inbound_mail_store import (
    create_notification,
    get_inbound_message,
    get_sync_state,
    inbound_summary,
    list_inbound_messages,
    update_sync_state,
    upsert_inbound_message,
)
from app.privacy_lab import scan_sensitive_message


IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
IGNORED_HTML_TAGS = {"head", "meta", "noscript", "script", "style", "svg", "title"}
SYSTEM_MAIL_PATTERNS = [
    (
        "password_changed",
        ("修改了邮箱密码", "password", "密码已修改", "reset password", "change password"),
        "邮箱安全通知：检测到邮箱密码已修改，建议立即确认是否为本人操作。",
    ),
    (
        "login_alert",
        ("登录提醒", "login", "异地登录", "new sign-in", "登录保护"),
        "邮箱安全通知：检测到一次登录提醒，建议确认是否为本人或授权客户端操作。",
    ),
    (
        "verification_code",
        ("验证码", "verification code", "security code", "otp"),
        "系统通知：邮件中包含验证码或登录校验信息，请勿外发并注意账号安全。",
    ),
]


class _VisibleTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        normalized = tag.lower()
        if normalized in IGNORED_HTML_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth == 0 and normalized in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        normalized = tag.lower()
        if normalized in IGNORED_HTML_TAGS and self._ignored_depth > 0:
            self._ignored_depth -= 1
            return
        if self._ignored_depth == 0 and normalized in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._ignored_depth > 0:
            return
        if data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def default_digest_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    local = current.astimezone(timezone(timedelta(hours=8)))
    since_local = (local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return since_local.astimezone(timezone.utc), current


def _imap_date(value: datetime) -> str:
    return f"{value.day:02d}-{IMAP_MONTHS[value.month - 1]}-{value.year}"


def _decode_addresses(value: str) -> str:
    addresses = getaddresses([value or ""])
    return ", ".join([email for _, email in addresses if email] or [value or ""])


def _message_datetime(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _looks_like_css_noise(line: str) -> bool:
    cleaned = line.strip().lower()
    if not cleaned:
        return True
    if cleaned.startswith((".", "#", "@media")) and "{" in cleaned:
        return True
    if "{" in cleaned and "}" in cleaned and cleaned.count(":") >= 2:
        return True
    css_tokens = ("font-size", "padding", "margin", "border", "background", "display", "line-height", "!important")
    if ";" in cleaned and any(token in cleaned for token in css_tokens):
        return True
    punctuation_count = sum(1 for char in cleaned if char in "{};:<>")
    if punctuation_count >= max(6, len(cleaned) // 4):
        return True
    return False


def _normalize_mail_text(text: str) -> str:
    candidate = html.unescape(text or "").replace("\xa0", " ")
    candidate = candidate.replace("\r", "\n")
    candidate = re.sub(r"\n{3,}", "\n\n", candidate)
    lines = [line.strip() for line in candidate.splitlines()]
    useful_lines = [line for line in lines if line and not _looks_like_css_noise(line)]
    compact = "\n".join(useful_lines)
    compact = re.sub(r"[ \t]+", " ", compact)
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    return compact.strip()


def _extract_visible_html_text(raw_html: str) -> str:
    parser = _VisibleTextHTMLParser()
    parser.feed(raw_html or "")
    parser.close()
    return _normalize_mail_text(parser.text())


def _sanitize_html_fragment(raw_html: str, *, limit: int = 20000) -> str:
    cleaned = re.sub(r"(?is)<(script|style|iframe|object|embed|svg|meta|link|head).*?>.*?</\1>", "", raw_html or "")
    cleaned = re.sub(r"(?is)<(script|style|iframe|object|embed|svg|meta|link|head)[^>]*?/?>", "", cleaned)
    cleaned = re.sub(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", cleaned)
    cleaned = re.sub(r"\s+on[a-z]+\s*=\s*[^\s>]+", "", cleaned)
    cleaned = re.sub(r"(?i)(href|src)\s*=\s*(['\"])\s*javascript:.*?\2", r"\1=\"#\"", cleaned)
    cleaned = re.sub(r"(?i)(href|src)\s*=\s*javascript:[^\s>]+", r"\1=\"#\"", cleaned)
    return cleaned.strip()[:limit]


def _message_body_parts(message) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        content_disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in content_disposition:
            continue
        content_type = part.get_content_type()
        try:
            payload = part.get_content()
        except Exception:
            continue
        if content_type == "text/plain":
            plain_parts.append(str(payload))
        elif content_type == "text/html":
            html_parts.append(str(payload))
    plain_text = _normalize_mail_text("\n".join(plain_parts))
    raw_html = "\n".join(html_parts)
    sanitized_html = _sanitize_html_fragment(raw_html)
    if not plain_text and raw_html:
        plain_text = _extract_visible_html_text(raw_html)
    return plain_text, sanitized_html


def _best_body_text(message) -> str:
    plain_text, sanitized_html = _message_body_parts(message)
    if plain_text:
        return plain_text

    html_text = _extract_visible_html_text(sanitized_html)
    if html_text:
        return html_text

    try:
        payload = message.get_body(preferencelist=("plain", "html"))
        if payload:
            content = str(payload.get_content())
            if payload.get_content_type() == "text/html":
                return _extract_visible_html_text(content)
            return _normalize_mail_text(content)
    except Exception:
        pass
    return ""


def _text_from_message(message) -> str:
    return _best_body_text(message)


def _summarize_text(text: str, limit: int = 420) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    return cleaned[:limit] + ("..." if len(cleaned) > limit else "")


def _sentence_summary(text: str, limit: int = 220) -> str:
    normalized = _normalize_mail_text(text)
    if not normalized:
        return ""
    pieces = [
        piece.strip()
        for piece in re.split(r"[。！？!?；;]\s*|\n+", normalized)
        if piece.strip()
    ]
    chosen: list[str] = []
    current_length = 0
    for piece in pieces:
        if _looks_like_css_noise(piece):
            continue
        next_length = current_length + len(piece) + (1 if chosen else 0)
        if next_length > limit and chosen:
            break
        chosen.append(piece)
        current_length = next_length
        if len(chosen) >= 2:
            break
    return "；".join(chosen) if chosen else _summarize_text(normalized, limit)


def _system_mail_summary(subject: str, sender: str, text: str) -> str:
    combined = " ".join([subject or "", sender or "", text or ""]).lower()
    for _, tokens, template in SYSTEM_MAIL_PATTERNS:
        if any(token.lower() in combined for token in tokens):
            return template
    if "exmail.weixin.qq.com" in (sender or "").lower():
        sentence = _sentence_summary(text, limit=180)
        if sentence:
            return f"腾讯企业邮箱系统通知：{sentence}"
    return ""


def _generate_mail_summary(subject: str, sender: str, text: str) -> str:
    system_summary = _system_mail_summary(subject, sender, text)
    if system_summary:
        return system_summary
    summary = _sentence_summary(text, limit=220)
    return summary or _summarize_text(text, 220)


def _classify_risk_hint(text: str) -> str:
    result = scan_sensitive_message(text or "")
    return str(result.get("risk_level", "low"))


def _normalize_subject_for_thread(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = re.sub(r"^(\s*(re|fw|fwd|答复|回复)\s*[:：]\s*)+", "", cleaned, flags=re.IGNORECASE)
    return cleaned or "(no subject)"


def _extract_references(value: str) -> str:
    ids = [item.strip("<> \t") for item in re.split(r"\s+", value or "") if item.strip()]
    return ids[-1] if ids else ""


def _infer_thread_id(parsed, mailbox: str, uid: str, sender: str, recipients: str, subject: str) -> tuple[str, str]:
    provider_thread_id = _extract_references(str(parsed.get("References") or "")) or str(parsed.get("In-Reply-To") or "").strip("<> ")
    if provider_thread_id:
        seed = provider_thread_id
    else:
        participants = "|".join(sorted(_split_email_values(f"{sender},{recipients}"))[:8])
        seed = "|".join([mailbox, _normalize_subject_for_thread(subject), participants])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"thread_{digest}", provider_thread_id


def _split_email_values(value: str) -> list[str]:
    addresses = [email.strip().lower() for _, email in getaddresses([value or ""]) if email.strip()]
    if addresses:
        return addresses
    return [item.strip().lower() for item in re.split(r"[,;]\s*", value or "") if item.strip()]


def _labels_from_flags(flags_blob: str, mailbox: str) -> list[str]:
    labels = {str(mailbox or "INBOX").strip().lower()}
    if "\\Seen" in flags_blob:
        labels.add("seen")
    else:
        labels.add("unread")
    if "\\Flagged" in flags_blob:
        labels.add("flagged")
    if "\\Answered" in flags_blob:
        labels.add("answered")
    return sorted(label for label in labels if label)


def _attachment_metadata(message) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for index, part in enumerate(message.walk() if message.is_multipart() else []):
        content_disposition = str(part.get("Content-Disposition", "")).lower()
        filename = part.get_filename()
        if "attachment" not in content_disposition and not filename:
            continue
        payload = b""
        try:
            decoded = part.get_payload(decode=True)
            if isinstance(decoded, bytes):
                payload = decoded
        except Exception:
            payload = b""
        provider_attachment_id = str(part.get("Content-ID") or part.get("Content-Location") or f"part-{index}").strip("<> ")
        attachments.append(
            {
                "attachment_id": f"attachment_{hashlib.sha1((provider_attachment_id + str(filename or index)).encode('utf-8')).hexdigest()[:12]}",
                "filename": str(filename or f"attachment-{index}.bin"),
                "content_type": str(part.get_content_type() or "application/octet-stream"),
                "size_bytes": len(payload),
                "provider_attachment_id": provider_attachment_id,
                "content_ref": "",
                "inline": "inline" in content_disposition,
                "source_policy": {"body_source": "metadata_only", "attachment_source": "attachment_only"},
            }
        )
    return attachments


def _headers_snapshot(parsed) -> dict[str, Any]:
    return {
        "message_id": str(parsed.get("Message-ID") or "").strip(),
        "in_reply_to": str(parsed.get("In-Reply-To") or "").strip(),
        "references": str(parsed.get("References") or "").strip()[-1000:],
        "content_type": str(parsed.get_content_type() or ""),
    }


def _parse_fetched_message(mailbox: str, uid: str, response_parts: list[Any]) -> dict[str, Any] | None:
    raw_bytes = b""
    flags_blob = ""
    for part in response_parts:
        if isinstance(part, tuple):
            meta, payload = part
            flags_blob += str(meta)
            if isinstance(payload, bytes):
                raw_bytes = payload
        elif isinstance(part, bytes):
            flags_blob += part.decode(errors="ignore")
    if not raw_bytes:
        return None
    parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    text, sanitized_html = _message_body_parts(parsed)
    if not text:
        text = _text_from_message(parsed)
    sender = _decode_addresses(str(parsed.get("From") or ""))
    subject = str(parsed.get("Subject") or "").strip()
    snippet = _summarize_text(text, 800)
    message_id = str(parsed.get("Message-ID") or "").strip().strip("<>")
    if not message_id:
        message_id = f"{mailbox}:{uid}"
    recipients = _decode_addresses(str(parsed.get("To") or ""))
    thread_id, provider_thread_id = _infer_thread_id(parsed, mailbox, uid, sender, recipients, subject)
    body_preview = _summarize_text(text, 240)
    return {
        "message_id": message_id,
        "mailbox": mailbox,
        "uid": uid,
        "thread_id": thread_id,
        "provider_thread_id": provider_thread_id,
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "received_at": _message_datetime(str(parsed.get("Date") or "")),
        "snippet": snippet,
        "summary": _generate_mail_summary(subject, sender, text),
        "body_text": text,
        "body_html_sanitized": sanitized_html,
        "body_preview": body_preview,
        "labels": _labels_from_flags(flags_blob, mailbox),
        "attachments": _attachment_metadata(parsed),
        "headers_json": _headers_snapshot(parsed),
        "risk_hint": _classify_risk_hint(text),
        "raw_size": len(raw_bytes),
        "is_seen": "\\Seen" in flags_blob,
    }


def sync_inbound_mail(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    mailbox = settings.imap_mailbox or "INBOX"
    actor = actor_from_mapping(actor_context or {})
    if not settings.imap_enabled:
        state = update_sync_state(
            mailbox,
            last_error="IMAP is disabled. Set IMAP_ENABLED=true after configuring IMAP credentials.",
            actor_context=actor.to_dict(),
        )
        return {"enabled": False, "synced": 0, "new": 0, "mailbox": mailbox, "state": state}
    if not settings.imap_username or not settings.imap_password:
        state = update_sync_state(
            mailbox,
            last_error="IMAP credentials are incomplete. Configure IMAP_USERNAME and IMAP_PASSWORD.",
            actor_context=actor.to_dict(),
        )
        return {"enabled": False, "synced": 0, "new": 0, "mailbox": mailbox, "state": state}

    start, end = (since, until) if since and until else default_digest_window()
    synced = 0
    new_count = 0
    max_uid = ""
    try:
        with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=20) as imap:
            imap.login(settings.imap_username, settings.imap_password)
            select_status, select_data = imap.select(mailbox, readonly=True)
            if select_status != "OK":
                detail = ""
                if select_data:
                    try:
                        detail = " ".join(
                            item.decode(errors="ignore") if isinstance(item, bytes) else str(item)
                            for item in select_data
                            if item
                        ).strip()
                    except Exception:
                        detail = str(select_data)
                if "Unsafe Login" in detail:
                    raise RuntimeError(
                        "IMAP mailbox access was rejected as unsafe login by the mail provider. "
                        "Please confirm IMAP is enabled and approve third-party client access in mailbox security settings."
                    )
                raise RuntimeError(
                    f"IMAP mailbox select failed for {mailbox}: {detail or select_status}"
                )
            status, data = imap.uid("search", None, "SINCE", _imap_date(start))
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {status}")
            uids = (data[0] or b"").split()
            for uid_bytes in uids[-limit:]:
                uid = uid_bytes.decode()
                max_uid = uid
                status, fetched = imap.uid("fetch", uid, "(FLAGS RFC822)")
                if status != "OK":
                    continue
                parsed = _parse_fetched_message(mailbox, uid, fetched)
                if not parsed:
                    continue
                try:
                    received = datetime.fromisoformat(str(parsed["received_at"]).replace("Z", "+00:00"))
                    if received > end:
                        continue
                except Exception:
                    pass
                synced += 1
                if upsert_inbound_message(parsed, actor_context=actor.to_dict()):
                    new_count += 1
                    if actor.is_local_dev:
                        create_notification(
                            "new_mail_received",
                            f"New mail: {parsed['subject'] or '(no subject)'}",
                            parsed["summary"] or parsed["snippet"],
                            {
                                "message_id": parsed["message_id"],
                                "sender": parsed["sender"],
                                "received_at": parsed["received_at"],
                            },
                        )
                    else:
                        create_notification(
                            "new_mail_received",
                            "New mail received",
                            "Message details are available only in the actor-scoped inbox.",
                            {
                                "received_at": parsed["received_at"],
                                "details_redacted": True,
                                "redaction_reason": "actor_scoped_inbound_mail",
                            },
                        )
            state = update_sync_state(mailbox, last_seen_uid=max_uid, last_error="", actor_context=actor.to_dict())
    except Exception as exc:
        state = update_sync_state(mailbox, last_error=str(exc), actor_context=actor.to_dict())
        return {"enabled": True, "ok": False, "synced": synced, "new": new_count, "mailbox": mailbox, "error": str(exc), "state": state}

    return {"enabled": True, "ok": True, "synced": synced, "new": new_count, "mailbox": mailbox, "state": state}


def generate_daily_mail_digest(
    since: str | None = None,
    until: str | None = None,
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start, end = default_digest_window()
    since_value = since or start.isoformat()
    until_value = until or end.isoformat()
    summary = inbound_summary(since_value, until_value, actor_context=actor_context)
    notification = create_notification(
        "daily_mail_digest",
        "Mail digest generated",
        "Digest details are available only in the actor-scoped response.",
        {
            "since": since_value,
            "until": until_value,
            "details_redacted": True,
            "redaction_reason": "actor_scoped_digest",
        },
    )
    return {"summary": summary, "notification": notification}


def list_inbound_mail_messages(
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
    actor_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return list_inbound_messages(
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        actor_context=actor_context,
    )


def get_inbound_mail_summary(
    since: str | None = None,
    until: str | None = None,
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start, end = default_digest_window()
    summary = inbound_summary(
        since or start.isoformat(),
        until or end.isoformat(),
        actor_context=actor_context,
    )
    summary["sync_state"] = latest_sync_state(actor_context=actor_context)
    return summary


def draft_reply_for_message(message_id: str, *, actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    message = get_inbound_message(message_id, actor_context=actor_context)
    if not message:
        raise KeyError(message_id)
    source = "\n".join(
        [
            f"From: {message.get('sender', '')}",
            f"Subject: {message.get('subject', '')}",
            f"Summary: {message.get('summary') or message.get('snippet', '')}",
        ]
    )
    fallback = (
        "您好，邮件已收到。我们会根据邮件内容进一步确认相关事项，并在完成内部核对后回复您。"
    )
    try:
        llm = get_llm()
        response = llm.invoke(
            [
                SystemMessage(content="Draft a concise professional Chinese email reply. Do not send it."),
                HumanMessage(content=source),
            ]
        )
        draft = str(getattr(response, "content", response)).strip() or fallback
    except Exception:
        draft = fallback
    return {"message": message, "draft_reply": draft, "requires_dlp_before_send": True}


def latest_sync_state(actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    return get_sync_state(settings.imap_mailbox or "INBOX", actor_context=actor_context)
