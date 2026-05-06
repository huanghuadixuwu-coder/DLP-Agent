from __future__ import annotations

import re
from dataclasses import dataclass


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w@.])(?:\+?86[- ]?)?1[3-9]\d{9}(?![\w@.])")
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
INTERNAL_IP_RE = re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")
JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9._-]{10,}\.[a-zA-Z0-9._-]{10,}\b")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]{10,}")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
DB_URL_RE = re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb|redis|amqp|jdbc|oracle|sqlserver)://[^\s'\";]+")
API_KEY_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key|db[_-]?url|connection[_-]?string|private[_-]?key|client[_-]?secret|session[_-]?token|auth[_-]?token|apikey|密钥|密码)\b\s*[:=]?\s*[\w./:@\-]{6,}"
)
FIELD_VALUE_RE = re.compile(
    r"(?im)\b(phone|mobile|email|mail|id|id_card|identity|bank|bank_card|card|token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key|db[_-]?url|connection[_-]?string|customer|client|contract|quote|internal[_-]?ip|手机号|邮箱|身份证|银行卡|密钥|密码|数据库连接)\b\s*[:=,]\s*([^\s,;]+)"
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
        "private key",
        "数据库连接",
        "连接串",
        "银行卡",
        "客户名单",
        "泄露",
        "转账",
        "access_key",
        "db_url",
        "connection_string",
        "bearer",
        "jwt",
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
        "工单",
        "数据库",
        "内网",
        "internal",
        "quote",
        "contract",
        "customer",
    ),
}

STRUCTURED_FILE_HINTS = (".csv", ".json", ".log", "text/csv", "application/json", "text/plain")


@dataclass(frozen=True)
class Redaction:
    pii_type: str
    count: int


def rough_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def redact_message(message: str) -> tuple[str, list[Redaction]]:
    redactions: list[Redaction] = []
    patterns = [
        ("email", EMAIL_RE, "[EMAIL]"),
        ("secret", API_KEY_RE, "[SECRET]"),
        ("bearer", BEARER_RE, "[SECRET]"),
        ("jwt", JWT_RE, "[SECRET]"),
        ("private_key", PRIVATE_KEY_RE, "[SECRET]"),
        ("db_url", DB_URL_RE, "[SECRET]"),
        ("id_card", ID_RE, "[ID_CARD]"),
        ("bank_card", BANK_CARD_RE, "[BANK_CARD]"),
        ("internal_ip", INTERNAL_IP_RE, "[INTERNAL_IP]"),
        ("phone", PHONE_RE, "[PHONE]"),
    ]

    redacted = message or ""
    for pii_type, pattern, replacement in patterns:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            redactions.append(Redaction(pii_type, count))
    return redacted, redactions


def _is_structured_source(source_filename: str, source_content_type: str) -> bool:
    lowered_filename = (source_filename or "").lower()
    lowered_content_type = (source_content_type or "").lower()
    return any(lowered_filename.endswith(suffix) for suffix in (".csv", ".json", ".log")) or any(
        hint in lowered_content_type for hint in STRUCTURED_FILE_HINTS
    )


def _field_value_hits(message: str) -> list[tuple[str, str]]:
    return [(match.group(1).lower(), match.group(2)) for match in FIELD_VALUE_RE.finditer(message or "")]


def classify_risk(
    raw_message: str,
    redacted_message: str,
    redactions: list[Redaction],
    *,
    source_filename: str = "",
    source_content_type: str = "",
) -> tuple[str, list[str]]:
    lowered = redacted_message.lower()
    reasons: list[str] = []
    field_hits = _field_value_hits(raw_message)
    structured_source = _is_structured_source(source_filename, source_content_type)

    if any(keyword.lower() in lowered for keyword in RISK_KEYWORDS["high"]):
        reasons.append("命中高风险关键词或敏感资产标识。")
    if any(item.pii_type in {"id_card", "secret", "bank_card", "private_key", "db_url", "jwt"} for item in redactions):
        reasons.append("包含高敏结构化信息或凭证。")
    if structured_source and any(
        key in {"token", "secret", "password", "passwd", "pwd", "api_key", "access_key", "db_url", "connection_string", "密钥", "密码", "数据库连接"}
        for key, _ in field_hits
    ):
        reasons.append("结构化文件中命中了高风险字段和值模式。")
    if reasons:
        return "high", reasons

    non_destination_redactions = [item for item in redactions if item.pii_type != "email"]
    if structured_source and any(
        key in {"phone", "mobile", "email", "mail", "customer", "client", "contract", "quote", "internal_ip", "手机号", "邮箱"}
        for key, _ in field_hits
    ):
        reasons.append("结构化文件中命中了个人信息或业务敏感字段。")
        return "medium", reasons
    if non_destination_redactions or any(keyword.lower() in lowered for keyword in RISK_KEYWORDS["medium"]):
        reasons.append("包含个人信息或业务敏感信息。")
        return "medium", reasons

    return "low", ["未发现明显敏感关键词或结构化 PII。"]


def pack_context(redacted_message: str, budget_tokens: int) -> dict:
    segments = [segment.strip() for segment in re.split(r"[。；;\n]+", redacted_message) if segment.strip()]
    if not segments:
        segments = [redacted_message.strip()]

    def score(segment: str) -> int:
        lowered = segment.lower()
        score_value = 0
        for level_keywords in RISK_KEYWORDS.values():
            score_value += sum(3 for keyword in level_keywords if keyword.lower() in lowered)
        score_value += 5 if any(
            marker in segment for marker in ("[PHONE]", "[ID_CARD]", "[SECRET]", "[BANK_CARD]", "[INTERNAL_IP]")
        ) else 0
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


def scan_sensitive_message(
    message: str,
    context_budget: int = 600,
    *,
    source_filename: str = "",
    source_content_type: str = "",
) -> dict:
    redacted, redactions = redact_message(message)
    risk_level, reasons = classify_risk(
        message,
        redacted,
        redactions,
        source_filename=source_filename,
        source_content_type=source_content_type,
    )
    packed = pack_context(redacted, context_budget)
    return {
        "redacted_text": redacted,
        "risk_level": risk_level,
        "risk_reasons": reasons,
        "redactions": [{"pii_type": item.pii_type, "count": item.count} for item in redactions],
        "context_pack": packed,
        "storage_policy": "只存脱敏摘要、风险标签和审计元数据，不把原始敏感文本写入向量库或长期记忆。",
        "alert": risk_level in {"medium", "high", "critical"},
    }
