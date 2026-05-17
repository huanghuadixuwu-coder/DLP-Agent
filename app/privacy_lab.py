from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.config import DATA_DIR, get_settings


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
_POLICY_CACHE_LOCK = Lock()
_POLICY_CACHE: dict[str, Any] = {}


@dataclass(frozen=True)
class Redaction:
    pii_type: str
    count: int


@dataclass(frozen=True)
class PrivacyPolicy:
    policy_id: str
    version: str
    status: str
    high_keywords: tuple[str, ...]
    medium_keywords: tuple[str, ...]
    regex_overrides: tuple[dict[str, str], ...]
    loaded_at: str
    source: str = "static"
    load_error: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _policy_db_path(db_path: str | Path | None = None) -> Path:
    configured = db_path or getattr(get_settings(), "privacy_policy_db_path", str(DATA_DIR / "privacy_policies.db"))
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect_policy_store(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_policy_db_path(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_privacy_policy_store(db_path: str | Path | None = None) -> None:
    with _connect_policy_store(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS privacy_policies (
                policy_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                high_keywords TEXT NOT NULL DEFAULT '[]',
                medium_keywords TEXT NOT NULL DEFAULT '[]',
                regex_overrides TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_privacy_policies_status_version
                ON privacy_policies(status, version, updated_at);
            """
        )


def _json_list(value: Any, *, default: list[Any] | None = None) -> list[Any]:
    if value is None or value == "":
        return list(default or [])
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    parsed = json.loads(str(value))
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    return list(default or [])


def _static_policy(*, source: str = "static", load_error: str = "") -> PrivacyPolicy:
    return PrivacyPolicy(
        policy_id="static",
        version="static",
        status="active",
        high_keywords=tuple(RISK_KEYWORDS["high"]),
        medium_keywords=tuple(RISK_KEYWORDS["medium"]),
        regex_overrides=(),
        loaded_at=_now(),
        source=source,
        load_error=load_error,
    )


def _policy_from_row(row: sqlite3.Row) -> PrivacyPolicy:
    high_keywords = tuple(str(item).strip() for item in _json_list(row["high_keywords"]) if str(item).strip())
    medium_keywords = tuple(str(item).strip() for item in _json_list(row["medium_keywords"]) if str(item).strip())
    overrides: list[dict[str, str]] = []
    for index, item in enumerate(_json_list(row["regex_overrides"])):
        if isinstance(item, str):
            item = {"name": f"dynamic_regex_{index}", "pattern": item, "replacement": "[SENSITIVE]"}
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or "").strip()
        if not pattern:
            continue
        overrides.append(
            {
                "name": str(item.get("name") or f"dynamic_regex_{index}"),
                "pii_type": str(item.get("pii_type") or item.get("name") or "dynamic_sensitive"),
                "pattern": pattern,
                "replacement": str(item.get("replacement") or "[SENSITIVE]"),
            }
        )
    return PrivacyPolicy(
        policy_id=str(row["policy_id"]),
        version=str(row["version"]),
        status=str(row["status"]),
        high_keywords=high_keywords,
        medium_keywords=medium_keywords,
        regex_overrides=tuple(overrides),
        loaded_at=_now(),
        source="sqlite",
    )


def load_active_privacy_policy(db_path: str | Path | None = None) -> PrivacyPolicy:
    """Load the active policy, reusing the cached policy while its version is unchanged."""
    try:
        init_privacy_policy_store(db_path)
        with _connect_policy_store(db_path) as conn:
            head = conn.execute(
                """
                SELECT policy_id, version, updated_at
                FROM privacy_policies
                WHERE status = 'active'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
            if not head:
                return _static_policy()
            cache_key = str(_policy_db_path(db_path))
            cached = _POLICY_CACHE.get(cache_key)
            if (
                isinstance(cached, PrivacyPolicy)
                and cached.policy_id == str(head["policy_id"])
                and cached.version == str(head["version"])
            ):
                return cached
            row = conn.execute(
                """
                SELECT policy_id, version, status, high_keywords, medium_keywords, regex_overrides, updated_at
                FROM privacy_policies
                WHERE policy_id = ?
                LIMIT 1
                """,
                (head["policy_id"],),
            ).fetchone()
            if not row:
                return _static_policy()
            policy = _policy_from_row(row)
            with _POLICY_CACHE_LOCK:
                _POLICY_CACHE[cache_key] = policy
            return policy
    except Exception as exc:
        return _static_policy(source="static_fallback", load_error=str(exc))


def upsert_privacy_policy(
    *,
    policy_id: str,
    version: str,
    status: str = "draft",
    high_keywords: list[str] | tuple[str, ...] | None = None,
    medium_keywords: list[str] | tuple[str, ...] | None = None,
    regex_overrides: list[dict[str, str] | str] | None = None,
    db_path: str | Path | None = None,
) -> dict[str, str]:
    init_privacy_policy_store(db_path)
    normalized_status = status if status in {"draft", "active", "archived"} else "draft"
    now = _now()
    with _connect_policy_store(db_path) as conn:
        if normalized_status == "active":
            conn.execute("UPDATE privacy_policies SET status = 'archived' WHERE status = 'active' AND policy_id != ?", (policy_id,))
        conn.execute(
            """
            INSERT INTO privacy_policies (
                policy_id, version, status, high_keywords, medium_keywords, regex_overrides, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                version = excluded.version,
                status = excluded.status,
                high_keywords = excluded.high_keywords,
                medium_keywords = excluded.medium_keywords,
                regex_overrides = excluded.regex_overrides,
                updated_at = excluded.updated_at
            """,
            (
                policy_id,
                version,
                normalized_status,
                json.dumps(list(high_keywords or []), ensure_ascii=False),
                json.dumps(list(medium_keywords or []), ensure_ascii=False),
                json.dumps(list(regex_overrides or []), ensure_ascii=False),
                now,
            ),
        )
    with _POLICY_CACHE_LOCK:
        _POLICY_CACHE.pop(str(_policy_db_path(db_path)), None)
    return {"policy_id": policy_id, "version": version, "status": normalized_status, "updated_at": now}


def activate_privacy_policy(policy_id: str, *, db_path: str | Path | None = None) -> dict[str, str]:
    init_privacy_policy_store(db_path)
    now = _now()
    with _connect_policy_store(db_path) as conn:
        row = conn.execute("SELECT policy_id, version FROM privacy_policies WHERE policy_id = ?", (policy_id,)).fetchone()
        if not row:
            raise ValueError(f"privacy policy not found: {policy_id}")
        conn.execute("UPDATE privacy_policies SET status = 'archived', updated_at = ? WHERE status = 'active' AND policy_id != ?", (now, policy_id))
        conn.execute("UPDATE privacy_policies SET status = 'active', updated_at = ? WHERE policy_id = ?", (now, policy_id))
    with _POLICY_CACHE_LOCK:
        _POLICY_CACHE.pop(str(_policy_db_path(db_path)), None)
    return {"policy_id": str(row["policy_id"]), "version": str(row["version"]), "status": "active", "updated_at": now}


def rough_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _compile_dynamic_patterns(policy: PrivacyPolicy | None) -> list[tuple[str, re.Pattern[str], str]]:
    compiled: list[tuple[str, re.Pattern[str], str]] = []
    for override in (policy.regex_overrides if policy else ()):
        try:
            compiled.append(
                (
                    str(override.get("pii_type") or override.get("name") or "dynamic_sensitive"),
                    re.compile(str(override.get("pattern") or ""), re.I | re.M),
                    str(override.get("replacement") or "[SENSITIVE]"),
                )
            )
        except re.error:
            continue
    return compiled


def redact_message(message: str, policy: PrivacyPolicy | None = None) -> tuple[str, list[Redaction]]:
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
    patterns.extend(_compile_dynamic_patterns(policy))

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
    policy: PrivacyPolicy | None = None,
) -> tuple[str, list[str]]:
    lowered = redacted_message.lower()
    reasons: list[str] = []
    field_hits = _field_value_hits(raw_message)
    structured_source = _is_structured_source(source_filename, source_content_type)
    high_keywords = tuple(RISK_KEYWORDS["high"]) + tuple(policy.high_keywords if policy else ())
    medium_keywords = tuple(RISK_KEYWORDS["medium"]) + tuple(policy.medium_keywords if policy else ())

    if any(keyword.lower() in lowered for keyword in high_keywords):
        reasons.append("命中高风险关键词或敏感资产标识。")
    if any(
        item.pii_type in {"id_card", "secret", "bank_card", "private_key", "db_url", "jwt"}
        or item.pii_type.startswith("dynamic")
        for item in redactions
    ):
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
    if non_destination_redactions or any(keyword.lower() in lowered for keyword in medium_keywords):
        reasons.append("包含个人信息或业务敏感信息。")
        return "medium", reasons

    return "low", ["未发现明显敏感关键词或结构化 PII。"]


def pack_context(redacted_message: str, budget_tokens: int, policy: PrivacyPolicy | None = None) -> dict:
    segments = [segment.strip() for segment in re.split(r"[。；;\n]+", redacted_message) if segment.strip()]
    if not segments:
        segments = [redacted_message.strip()]
    scoring_keywords = tuple(RISK_KEYWORDS["high"]) + tuple(RISK_KEYWORDS["medium"])
    if policy:
        scoring_keywords += tuple(policy.high_keywords) + tuple(policy.medium_keywords)

    def score(segment: str) -> int:
        lowered = segment.lower()
        score_value = 0
        score_value += sum(3 for keyword in scoring_keywords if keyword.lower() in lowered)
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
    policy = load_active_privacy_policy()
    redacted, redactions = redact_message(message, policy=policy)
    risk_level, reasons = classify_risk(
        message,
        redacted,
        redactions,
        source_filename=source_filename,
        source_content_type=source_content_type,
        policy=policy,
    )
    packed = pack_context(redacted, context_budget, policy=policy)
    return {
        "redacted_text": redacted,
        "risk_level": risk_level,
        "risk_reasons": reasons,
        "redactions": [{"pii_type": item.pii_type, "count": item.count} for item in redactions],
        "context_pack": packed,
        "storage_policy": "只存脱敏摘要、风险标签和审计元数据，不把原始敏感文本写入向量库或长期记忆。",
        "alert": risk_level in {"medium", "high", "critical"},
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "policy_source": policy.source,
        "policy_loaded_at": policy.loaded_at,
        "policy_load_error": policy.load_error,
    }
