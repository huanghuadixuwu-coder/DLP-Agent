from __future__ import annotations

import re
from dataclasses import dataclass


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w@.])(?:\+?86[- ]?)?1[3-9]\d{9}(?![\w@.])")
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
API_KEY_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd|密钥|密码)\b\s*[:=]?\s*[\w.\-]{6,}"
)


RISK_KEYWORDS = {
    "high": (
        "身份证",
        "密码",
        "密钥",
        "secret",
        "token",
        "api key",
        "api_key",
        "银行卡",
        "客户名单",
        "泄露",
        "转账",
    ),
    "medium": (
        "手机号",
        "邮箱",
        "地址",
        "合同",
        "报价",
        "客户",
        "隐私",
        "个人信息",
    ),
}


@dataclass(frozen=True)
class Redaction:
    pii_type: str
    count: int


def rough_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def redact_message(message: str) -> tuple[str, list[Redaction]]:
    redactions: list[Redaction] = []
    # Email must be redacted before phone numbers, otherwise numeric email prefixes
    # such as 17388861183@163.com are incorrectly treated as phone numbers.
    patterns = [
        ("email", EMAIL_RE, "[EMAIL]"),
        ("secret", API_KEY_RE, "[SECRET]"),
        ("id_card", ID_RE, "[ID_CARD]"),
        ("bank_card", BANK_CARD_RE, "[BANK_CARD]"),
        ("phone", PHONE_RE, "[PHONE]"),
    ]

    redacted = message
    for pii_type, pattern, replacement in patterns:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            redactions.append(Redaction(pii_type, count))
    return redacted, redactions


def classify_risk(redacted_message: str, redactions: list[Redaction]) -> tuple[str, list[str]]:
    lowered = redacted_message.lower()
    reasons: list[str] = []

    if any(keyword.lower() in lowered for keyword in RISK_KEYWORDS["high"]):
        reasons.append("命中高风险关键词或外发场景")
    if any(item.pii_type in {"id_card", "secret", "bank_card"} for item in redactions):
        reasons.append("包含高敏结构化信息")
    if reasons:
        return "high", reasons

    non_destination_redactions = [item for item in redactions if item.pii_type != "email"]
    if non_destination_redactions or any(keyword.lower() in lowered for keyword in RISK_KEYWORDS["medium"]):
        reasons.append("包含个人信息或业务敏感信息")
        return "medium", reasons

    return "low", ["未发现明显敏感关键词或结构化 PII"]


def pack_context(redacted_message: str, budget_tokens: int) -> dict:
    segments = [segment.strip() for segment in re.split(r"[。；;\n]+", redacted_message) if segment.strip()]
    if not segments:
        segments = [redacted_message.strip()]

    def score(segment: str) -> int:
        lowered = segment.lower()
        score_value = 0
        for level_keywords in RISK_KEYWORDS.values():
            score_value += sum(3 for keyword in level_keywords if keyword.lower() in lowered)
        score_value += 5 if any(marker in segment for marker in ("[PHONE]", "[ID_CARD]", "[SECRET]", "[BANK_CARD]")) else 0
        return score_value

    selected: list[str] = []
    used = 0
    for segment in sorted(segments, key=score, reverse=True):
        segment_tokens = rough_tokens(segment)
        if used + segment_tokens > budget_tokens:
            continue
        selected.append(segment)
        used += segment_tokens

    return {
        "packed_context": "\n".join(selected),
        "original_tokens": rough_tokens(redacted_message),
        "packed_tokens": used,
        "truncated_segments": max(0, len(segments) - len(selected)),
    }


def scan_sensitive_message(message: str, context_budget: int = 600) -> dict:
    redacted, redactions = redact_message(message)
    risk_level, reasons = classify_risk(redacted, redactions)
    packed = pack_context(redacted, context_budget)
    return {
        "redacted_text": redacted,
        "risk_level": risk_level,
        "risk_reasons": reasons,
        "redactions": [{"pii_type": item.pii_type, "count": item.count} for item in redactions],
        "context_pack": packed,
        "storage_policy": "只存脱敏摘要、风险标签和审计元数据，不把原始敏感文本写入向量库或长期记忆。",
        "alert": risk_level in {"medium", "high"},
    }
