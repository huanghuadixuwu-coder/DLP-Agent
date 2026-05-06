from __future__ import annotations

import html
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

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


def _best_body_text(message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
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
    else:
        try:
            payload = message.get_content()
        except Exception:
            payload = ""
        content_type = message.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(str(payload))
        elif content_type == "text/html":
            html_parts.append(str(payload))

    plain_text = _normalize_mail_text("\n".join(plain_parts))
    if plain_text:
        return plain_text

    html_text = _extract_visible_html_text("\n".join(html_parts))
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
    text = _text_from_message(parsed)
    sender = _decode_addresses(str(parsed.get("From") or ""))
    subject = str(parsed.get("Subject") or "").strip()
    snippet = _summarize_text(text, 800)
    message_id = str(parsed.get("Message-ID") or "").strip().strip("<>")
    if not message_id:
        message_id = f"{mailbox}:{uid}"
    return {
        "message_id": message_id,
        "mailbox": mailbox,
        "uid": uid,
        "sender": sender,
        "recipients": _decode_addresses(str(parsed.get("To") or "")),
        "subject": subject,
        "received_at": _message_datetime(str(parsed.get("Date") or "")),
        "snippet": snippet,
        "summary": _generate_mail_summary(subject, sender, text),
        "risk_hint": _classify_risk_hint(text),
        "raw_size": len(raw_bytes),
        "is_seen": "\\Seen" in flags_blob,
    }


def sync_inbound_mail(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    settings = get_settings()
    mailbox = settings.imap_mailbox or "INBOX"
    if not settings.imap_enabled:
        state = update_sync_state(mailbox, last_error="IMAP is disabled. Set IMAP_ENABLED=true after configuring IMAP credentials.")
        return {"enabled": False, "synced": 0, "new": 0, "mailbox": mailbox, "state": state}
    if not settings.imap_username or not settings.imap_password:
        state = update_sync_state(mailbox, last_error="IMAP credentials are incomplete. Configure IMAP_USERNAME and IMAP_PASSWORD.")
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
                if upsert_inbound_message(parsed):
                    new_count += 1
                    create_notification(
                        "new_mail_received",
                        f"New mail: {parsed['subject'] or '(no subject)'}",
                        parsed["summary"] or parsed["snippet"],
                        {"message_id": parsed["message_id"], "sender": parsed["sender"], "received_at": parsed["received_at"]},
                    )
            state = update_sync_state(mailbox, last_seen_uid=max_uid, last_error="")
    except Exception as exc:
        state = update_sync_state(mailbox, last_error=str(exc))
        return {"enabled": True, "ok": False, "synced": synced, "new": new_count, "mailbox": mailbox, "error": str(exc), "state": state}

    return {"enabled": True, "ok": True, "synced": synced, "new": new_count, "mailbox": mailbox, "state": state}


def generate_daily_mail_digest(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    start, end = default_digest_window()
    since_value = since or start.isoformat()
    until_value = until or end.isoformat()
    summary = inbound_summary(since_value, until_value)
    title = f"Mail digest: {summary['total']} received, {summary['unread']} unread"
    important_lines = [
        f"- {item.get('subject') or '(no subject)'} from {item.get('sender')}: {item.get('summary') or item.get('snippet')}"
        for item in summary["important_messages"][:5]
    ]
    body = "\n".join(important_lines) if important_lines else "No important mail found in this window."
    notification = create_notification(
        "daily_mail_digest",
        title,
        body,
        {"summary": summary},
    )
    return {"summary": summary, "notification": notification}


def list_inbound_mail_messages(
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return list_inbound_messages(since=since, until=until, limit=limit, offset=offset)


def get_inbound_mail_summary(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    start, end = default_digest_window()
    summary = inbound_summary(since or start.isoformat(), until or end.isoformat())
    summary["sync_state"] = latest_sync_state()
    return summary


def draft_reply_for_message(message_id: str) -> dict[str, Any]:
    message = get_inbound_message(message_id)
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


def latest_sync_state() -> dict[str, Any]:
    settings = get_settings()
    return get_sync_state(settings.imap_mailbox or "INBOX")
