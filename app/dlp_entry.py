from __future__ import annotations

import re
from dataclasses import dataclass, field


EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

SEND_ACTION_TOKENS = (
    "send",
    "forward",
    "deliver",
    "share externally",
    "share with",
    "email this",
    "mail this",
    "send this",
    "send the following",
    "发送",
    "发给",
    "发到",
    "外发",
    "转发",
    "邮件给",
)

CONTENT_HINT_TOKENS = (
    "summary",
    "summarize",
    "text",
    "content",
    "body",
    "notes",
    "report",
    "log",
    "csv",
    "json",
    "markdown",
    "file",
    "attachment",
    "document",
    "正文",
    "文本",
    "内容",
    "文段",
    "这段",
    "如下",
    "总结",
    "摘要",
    "整理",
    "文件",
    "附件",
)

OUTBOUND_TARGET_HINTS = (
    "customer",
    "client",
    "partner",
    "vendor",
    "recipient",
    "external",
    "邮箱",
    "客户",
    "合作方",
    "供应商",
    "对方",
)

GENERIC_ONLY_PATTERNS = (
    r"^\s*帮我发一下这个[。.!?？]*\s*$",
    r"^\s*帮我把这个发一下[。.!?？]*\s*$",
    r"^\s*发一下这个[。.!?？]*\s*$",
    r"^\s*send\s+this[.?!]*\s*$",
    r"^\s*forward\s+this[.?!]*\s*$",
    r"^\s*send\s+it[.?!]*\s*$",
    r"^\s*(?:please\s+)?send\s+this(?:\s+(?:note|text|message|paragraph))?\s+to\s+\S+[.?!]*\s*$",
    r"^\s*(?:please\s+)?forward\s+this(?:\s+(?:note|text|message|paragraph))?\s+to\s+\S+[.?!]*\s*$",
    r"^\s*(?:please\s+)?send\s+the\s+following(?:\s+(?:note|text|content|paragraph))?(?:\s+to\s+\S+)?[.?!]*\s*$",
    r"^\s*帮我把如下文段发送(?:到\s*[^\s]+)?[。.!?？]*\s*$",
    r"^\s*帮我把如下内容发送(?:到\s*[^\s]+)?[。.!?？]*\s*$",
)

STRIP_PHRASES = (
    "please",
    "请",
    "帮我",
    "麻烦",
    "发送",
    "发给",
    "发到",
    "外发",
    "转发",
    "邮件",
    "邮箱",
    "summary",
    "summarize",
    "content",
    "text",
    "body",
    "note",
    "notes",
    "正文",
    "文本",
    "内容",
    "文段",
    "如下",
    "这段",
)


@dataclass(frozen=True)
class DlpEntryDecision:
    route: str
    destination_email: str = ""
    missing_fields: list[str] = field(default_factory=list)
    clarification_question: str = ""
    entry_issue_type: str = ""


def extract_destination_email(message: str) -> str:
    match = EMAIL_PATTERN.search(message or "")
    return match.group(0) if match else ""


def looks_like_outbound_action(message: str, uploaded_filename: str = "") -> bool:
    text = message or ""
    lowered = text.lower()
    has_send_action = any(token in lowered or token in text for token in SEND_ACTION_TOKENS)
    has_content_hint = any(token in lowered or token in text for token in CONTENT_HINT_TOKENS)
    has_email = bool(extract_destination_email(text))
    has_target_hint = any(token in lowered or token in text for token in OUTBOUND_TARGET_HINTS)
    if has_send_action and _is_generic_only_message(text):
        return True
    if has_send_action and has_email:
        return True
    if has_send_action and has_target_hint:
        return True
    if has_send_action and has_content_hint:
        return True
    return False


def _is_generic_only_message(message: str) -> bool:
    compact = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not compact:
        return True
    return any(re.match(pattern, compact, flags=re.IGNORECASE) for pattern in GENERIC_ONLY_PATTERNS)


def _extract_inline_payload(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    for delimiter in ("\n", "：", ":"):
        if delimiter in text:
            tail = text.split(delimiter, 1)[1].strip()
            if tail:
                return tail
    marker_patterns = (
        r"(?:内容|正文|文段|text|content)\s*(?:是|为|如下)?\s*(.+)$",
        r"(?:发送|发给|发到|外发).+?(?:给|到)\s*[^\s,，]+\s*[,，\s]*(.+)$",
    )
    for pattern in marker_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            tail = match.group(1).strip()
            if tail:
                return tail
    return ""


def has_meaningful_payload(message: str, uploaded_text: str = "") -> bool:
    if (uploaded_text or "").strip():
        return True

    text = (message or "").strip()
    if not text:
        return False
    if _is_generic_only_message(text):
        return False

    inline_payload = _extract_inline_payload(text)
    if inline_payload:
        probe = EMAIL_PATTERN.sub(" ", inline_payload)
        probe = re.sub(r"[\s\W_]+", "", probe)
        return len(probe) >= 2

    normalized = EMAIL_PATTERN.sub(" ", text)
    lowered = normalized.lower()
    for phrase in STRIP_PHRASES:
        lowered = lowered.replace(phrase.lower(), " ")
    lowered = re.sub(r"[\s\W_]+", "", lowered)
    if not lowered:
        return False
    if re.search(r"[\u4e00-\u9fff]", lowered):
        return len(lowered) >= 6
    return len(lowered) >= 10


def _clarification_question(missing_fields: list[str], entry_issue_type: str) -> str:
    missing = set(missing_fields)
    if entry_issue_type == "file_parse_failed":
        if "destination_email" in missing:
            return "我已保留你的外发请求，但上传文件无法解析且缺少收件邮箱。请补充收件邮箱，并重新上传文件或直接粘贴正文。"
        return "我已保留你的外发请求，但上传文件暂时无法解析。请重新上传文件或直接粘贴要发送的正文。"
    if missing == {"destination_email", "content"}:
        return "我已保留你的外发请求，但缺少收件邮箱和要发送的内容。请补充收件邮箱，并粘贴正文或上传文件。"
    if missing == {"destination_email"}:
        return "我已保留你的外发请求，但缺少收件邮箱。请直接回复收件邮箱地址。"
    if missing == {"content"}:
        return "我已保留你的外发请求，但缺少要发送的正文内容。请直接粘贴正文或上传文件。"
    return "我已保留你的外发请求，但还需要补充信息后才能继续处理。"


def classify_dlp_entry(
    *,
    message: str,
    destination_email: str = "",
    uploaded_text: str = "",
    uploaded_filename: str = "",
    source_parse_status: str = "not_provided",
    source_parse_error: str = "",
) -> DlpEntryDecision:
    if not looks_like_outbound_action(message, uploaded_filename):
        return DlpEntryDecision(route="non_dlp")

    destination_email = (destination_email or "").strip() or extract_destination_email(message)
    payload_present = has_meaningful_payload(message, uploaded_text)
    parse_failed = bool((source_parse_error or "").strip()) or source_parse_status in {"parse_failed", "empty", "invalid"}

    missing_fields: list[str] = []
    if not destination_email:
        missing_fields.append("destination_email")
    if not payload_present:
        missing_fields.append("content")

    if parse_failed and not payload_present:
        return DlpEntryDecision(
            route="input_invalid",
            destination_email=destination_email,
            missing_fields=missing_fields,
            clarification_question=_clarification_question(missing_fields, "file_parse_failed"),
            entry_issue_type="file_parse_failed",
        )

    if missing_fields:
        issue_type = "missing_destination_email_and_content" if len(missing_fields) == 2 else f"missing_{missing_fields[0]}"
        return DlpEntryDecision(
            route="needs_clarification",
            destination_email=destination_email,
            missing_fields=missing_fields,
            clarification_question=_clarification_question(missing_fields, issue_type),
            entry_issue_type=issue_type,
        )

    return DlpEntryDecision(
        route="normal_outbound",
        destination_email=destination_email,
        missing_fields=[],
        clarification_question="",
        entry_issue_type="",
    )
