from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


SUMMARY_HINTS = (
    "summary",
    "summarize",
    "overview",
    "概述",
    "总结",
    "摘要",
    "讲了什么",
    "说说",
    "内容概览",
)

CRITIQUE_HINTS = (
    "不好",
    "问题",
    "评价",
    "评估",
    "审阅",
    "review",
    "critique",
    "evaluate",
    "feedback",
    "improve",
)

REWRITE_HINTS = (
    "改写",
    "重写",
    "润色",
    "优化表达",
    "rewrite",
    "polish",
    "improve wording",
)

ACTION_ITEM_HINTS = (
    "行动项",
    "待办",
    "TODO",
    "action items",
    "next steps",
    "follow-up",
)

QA_HINTS = (
    "?",
    "？",
    "what",
    "which",
    "how",
    "why",
    "where",
    "when",
    "是否",
    "有没有",
    "哪些",
    "什么",
    "多少",
    "提到",
    "限制",
)

UPLOAD_REFERENCE_HINTS = (
    "这篇文档",
    "这个文件",
    "该文件",
    "这个附件",
    "上传的文件",
    "刚才那个文件",
    "这封邮件",
    "刚才那封邮件",
    "this file",
    "this document",
    "this attachment",
    "this email",
    "uploaded file",
)

EMAIL_REPLY_HINTS = (
    "回复这封邮件",
    "直接回复",
    "起草回复",
    "草拟回复",
    "draft reply",
    "reply to this email",
    "reply to the email",
)

OUTBOUND_ACTION_HINTS = (
    "send",
    "forward",
    "email",
    "mail",
    "deliver",
    "share externally",
    "share with",
    "发送",
    "发给",
    "发到",
    "外发",
    "转发",
    "邮件给",
)


@dataclass(frozen=True)
class UploadIntentDecision:
    route: str = "none"
    content_kind: str = "unknown"
    task_type: str = "summarize"
    reason: str = ""
    unsupported_reason: str = ""


def build_upload_context(
    *,
    uploaded_filename: str = "",
    uploaded_content_type: str = "",
    uploaded_text: str = "",
    upload_blob_id: str = "",
    source_parse_status: str = "not_provided",
    source_parse_error: str = "",
    summary: str = "",
    key_snippets: list[str] | None = None,
    recalled: bool = False,
) -> dict[str, Any]:
    filename = (uploaded_filename or "").strip()
    content_type = (uploaded_content_type or "").strip()
    text = uploaded_text or ""
    content_kind = infer_upload_content_kind(
        uploaded_filename=filename,
        uploaded_content_type=content_type,
        uploaded_text=text,
    )
    parse_status = source_parse_status or ("parsed" if text.strip() else "not_provided")
    return {
        "kind": content_kind,
        "filename": filename,
        "content_type": content_type,
        "parse_status": parse_status,
        "parse_error": source_parse_error or "",
        "content_available": bool(text.strip() or summary.strip() or (key_snippets or []) or filename or source_parse_error),
        "uploaded_text": text,
        "upload_blob_id": upload_blob_id.strip(),
        "summary": summary.strip(),
        "key_snippets": [item.strip() for item in (key_snippets or []) if str(item).strip()][:3],
        "recalled": recalled,
    }


def infer_upload_content_kind(
    *,
    uploaded_filename: str = "",
    uploaded_content_type: str = "",
    uploaded_text: str = "",
) -> str:
    filename = (uploaded_filename or "").lower()
    content_type = (uploaded_content_type or "").lower()
    head = (uploaded_text or "")[:4000]
    if filename.endswith(".eml") or "message/rfc822" in content_type:
        return "email"
    if re.search(r"(?im)^(from|to|subject|date):", head):
        return "email"
    if filename or uploaded_text.strip():
        return "document"
    return "unknown"


def infer_upload_task_type(message: str) -> str:
    text = (message or "").strip().lower()
    if any(hint in message or hint in text for hint in REWRITE_HINTS):
        return "rewrite"
    if any(hint in message or hint in text for hint in ACTION_ITEM_HINTS):
        return "extract_action_items"
    if any(hint in message or hint in text for hint in CRITIQUE_HINTS):
        return "critique"
    if any(hint in message or hint in text for hint in SUMMARY_HINTS):
        return "summarize"
    if any(hint in message or hint in text for hint in QA_HINTS):
        return "qa"
    return "summarize"


def refers_to_recent_upload(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(hint in message or hint in text for hint in UPLOAD_REFERENCE_HINTS)


def looks_like_uploaded_email_reply_request(message: str) -> bool:
    text = (message or "").strip().lower()
    return any(hint in message or hint in text for hint in EMAIL_REPLY_HINTS)


def looks_like_explicit_outbound_request(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    if not text:
        return False
    has_action = any(hint in text or hint in lowered for hint in OUTBOUND_ACTION_HINTS)
    if not has_action:
        return False
    if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        return True
    target_hints = ("客户", "合作方", "供应商", "对方", "邮箱", "recipient", "partner", "customer", "vendor", "client", "external", "to ")
    content_hints = ("总结后", "summarize and send", "send this", "send the following", "发给", "发到", "外发", "转发")
    return any(hint in text or hint in lowered for hint in target_hints) or any(hint in text or hint in lowered for hint in content_hints)


def classify_upload_request(
    *,
    message: str,
    upload_context: dict[str, Any] | None = None,
) -> UploadIntentDecision:
    context = dict(upload_context or {})
    if not context.get("content_available"):
        return UploadIntentDecision(route="none", reason="No uploaded content available.")
    if looks_like_explicit_outbound_request(message):
        return UploadIntentDecision(
            route="outbound",
            content_kind=str(context.get("kind") or "unknown"),
            task_type=infer_upload_task_type(message),
            reason="Detected explicit outbound intent with uploaded content.",
        )
    content_kind = str(context.get("kind") or "unknown")
    if content_kind == "email" and looks_like_uploaded_email_reply_request(message):
        return UploadIntentDecision(
            route="unsupported",
            content_kind="email",
            task_type="qa",
            reason="Uploaded email reply drafting is not supported yet.",
            unsupported_reason="当前暂不支持基于上传邮件直接起草回复。你可以先让我总结这封邮件，或粘贴正文让我草拟回复。",
        )
    return UploadIntentDecision(
        route="analyze",
        content_kind=content_kind,
        task_type=infer_upload_task_type(message),
        reason="Uploaded content should be analyzed before any outbound governance.",
    )
