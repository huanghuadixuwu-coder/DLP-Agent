from __future__ import annotations

import re


ENTERPRISE_DEFAULT_AGGREGATION = "answer_all_parts"

PERSONA_KEYWORDS = (
    "你是谁",
    "介绍一下你自己",
    "介绍你自己",
    "你好",
    "hi",
    "hello",
    "hey",
    "你有什么功能",
    "你能做什么",
    "how can you help",
    "what can you do",
    "who are you",
    "introduce yourself",
)

PERSONA_TASK_HINTS = (
    "总结",
    "发送",
    "发给",
    "发到",
    "外发",
    "转发",
    "上传",
    "附件",
    "文件",
    "邮件",
    "收件",
    "发件",
    "检索",
    "起草",
    "回复",
    "analyze",
    "summarize",
    "upload",
    "send",
    "email",
    "draft",
    "reply",
)

PERSONA_COMPOUND_HINTS = ("然后", "再", "并且", "and then", "then", "同时", "顺便")


def looks_like_reply_draft_request(message: str) -> bool:
    text = (message or "").lower()
    hints = ("起草回复", "草拟回复", "回复草稿", "draft reply", "draft a reply", "reply draft")
    return any(hint in message or hint in text for hint in hints)


def looks_like_outbound_summary_request(message: str) -> bool:
    text = (message or "").lower()
    return bool(
        re.search(r"(发送|发出|发了|sent|send).*(多少|几封|统计|count|today|今天|昨日|昨天)", message + text)
        and ("邮件" in message or "邮箱" in message or "mail" in text or "email" in text)
    )


def looks_like_inbound_summary_request(message: str) -> bool:
    text = (message or "").lower()
    if looks_like_outbound_summary_request(message):
        return False
    return bool(
        re.search(r"(收到|收了|收件|来信|inbox|unread).*(多少|重要|今天|昨日|昨天|summary|digest|邮件|mail|email)", message + text)
    )


def looks_like_enterprise_context_request(message: str) -> bool:
    text = (message or "").lower()
    hints = (
        "客户",
        "供应商",
        "项目",
        "合同",
        "工单",
        "文档",
        "知识库",
        "jira",
        "linear",
        "github",
        "slack",
        "confluence",
        "hubspot",
        "google drive",
    )
    return any(hint in message or hint in text for hint in hints)


def is_plain_enterprise_question(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    if looks_like_outbound_summary_request(message) or looks_like_inbound_summary_request(message):
        return False
    if looks_like_reply_draft_request(message):
        return False
    return True


def looks_like_persona_signal(message: str) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    return any(hint in message or hint in text for hint in PERSONA_KEYWORDS)


def looks_like_high_confidence_persona_request(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    if not text or len(text) > 36:
        return False
    if not looks_like_persona_signal(message):
        return False
    if any(hint in message or hint in lowered for hint in PERSONA_TASK_HINTS):
        return False
    if any(hint in message or hint in lowered for hint in PERSONA_COMPOUND_HINTS):
        return False
    return True
