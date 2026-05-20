from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.actor_context import ActorContext, actor_from_mapping, normalize_roles
from app.config import DATA_DIR, get_settings
from app.email_sender import send_email_smtp


EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _db_path() -> Path:
    path = Path(get_settings().auth_db_path or str(DATA_DIR / "auth.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _clean_email(email: str) -> str:
    return str(email or "").strip().lower()


def _mask_email(value: str) -> str:
    email = _clean_email(value)
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        masked = name[:1] + "*"
    else:
        masked = name[:2] + "***" + name[-1:]
    return f"{masked}@{domain}"


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").replace(";", ",").split(",") if item.strip()]


def _role_email_set(raw: str) -> set[str]:
    return {_clean_email(item) for item in _parse_csv(raw)}


def _allowed_domains() -> set[str]:
    settings = get_settings()
    configured = {item.lower() for item in _parse_csv(settings.auth_allowed_email_domains)}
    if configured:
        return configured
    smtp_username = _clean_email(settings.smtp_username)
    if "@" in smtp_username:
        return {smtp_username.split("@", 1)[1]}
    return set()


def _is_tencent_exmail_configured() -> bool:
    settings = get_settings()
    host_values = [str(settings.smtp_host or "").lower(), str(settings.imap_host or "").lower()]
    return any("exmail.qq.com" in value for value in host_values)


def _require_enterprise_email(email: str) -> str:
    cleaned = _clean_email(email)
    if not EMAIL_PATTERN.match(cleaned):
        raise ValueError("invalid_enterprise_email")
    if not get_settings().auth_enabled:
        raise ValueError("auth_not_enabled")
    if not _is_tencent_exmail_configured():
        raise ValueError("tencent_exmail_not_configured")
    domain = cleaned.split("@", 1)[1]
    allowed = _allowed_domains()
    if allowed and domain not in allowed:
        raise ValueError("email_domain_not_allowed")
    return cleaned


def _hash_secret(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _tenant_id_for_email(email: str) -> str:
    settings = get_settings()
    if str(settings.auth_default_tenant_id or "").strip():
        return str(settings.auth_default_tenant_id).strip()
    domain = email.split("@", 1)[1]
    normalized = re.sub(r"[^a-z0-9]+", "_", domain.lower()).strip("_")
    return f"exmail_{normalized or 'tenant'}"


def _workspace_id() -> str:
    return str(get_settings().auth_default_workspace_id or "default").strip() or "default"


def _roles_for_email(email: str) -> list[str]:
    settings = get_settings()
    cleaned = _clean_email(email)
    roles = {role.lower() for role in _parse_csv(settings.auth_default_roles)}
    roles.update({"user", "viewer"})
    if cleaned in _role_email_set(settings.auth_mail_sender_emails):
        roles.add("mail_sender")
    if cleaned in _role_email_set(settings.auth_approver_emails):
        roles.add("approver")
    if cleaned in _role_email_set(settings.auth_memory_admin_emails):
        roles.add("memory_admin")
    if cleaned in _role_email_set(settings.auth_ingest_admin_emails):
        roles.add("ingest_admin")
    if cleaned in _role_email_set(settings.auth_admin_emails):
        roles.add("admin")
    if "admin" in roles:
        roles.update({"mail_sender", "approver", "memory_admin", "ingest_admin"})
    return list(normalize_roles(sorted(roles), tenant_id=_tenant_id_for_email(cleaned), user_id=cleaned))


def _display_name(email: str) -> str:
    return email.split("@", 1)[0]


def json_loads(raw: str, *, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return default


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def init_auth_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_users (
                user_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                roles_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                email_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS auth_login_codes (
                code_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT 'login',
                expires_at TEXT NOT NULL,
                consumed_at TEXT NOT NULL DEFAULT '',
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                delivery_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                email TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                roles_json TEXT NOT NULL DEFAULT '[]',
                expires_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_auth_login_codes_email
                ON auth_login_codes(email, created_at);
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                ON auth_sessions(user_id, created_at);
            """
        )


def request_exmail_login_code(email: str) -> dict[str, Any]:
    init_auth_store()
    cleaned = _require_enterprise_email(email)
    now = _now()
    settings = get_settings()
    resend_cutoff = _iso(now - timedelta(seconds=int(settings.auth_login_code_resend_cooldown_seconds)))
    with _connect() as conn:
        recent = conn.execute(
            """
            SELECT * FROM auth_login_codes
            WHERE email = ? AND created_at >= ? AND consumed_at = ''
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (cleaned, resend_cutoff),
        ).fetchone()
        if recent:
            raise ValueError("login_code_resend_cooldown")

        code = _new_code()
        expires_at = now + timedelta(seconds=int(settings.auth_login_code_ttl_seconds))
        delivery = send_email_smtp(
            to_email=cleaned,
            subject="Tencent Exmail login code",
            body=(
                "Hello,\n\n"
                f"Your enterprise mailbox login code is: {code}\n"
                f"This code expires in {int(settings.auth_login_code_ttl_seconds // 60)} minutes.\n\n"
                "If you did not request this, you can ignore this email."
            ),
        )
        if not delivery.get("ok"):
            raise RuntimeError(str(delivery.get("error") or "login_code_delivery_failed"))

        conn.execute(
            """
            INSERT INTO auth_login_codes (
                code_id, email, code_hash, purpose, expires_at,
                consumed_at, delivery_status, delivery_error, created_at
            ) VALUES (?, ?, ?, 'login', ?, '', 'sent', '', ?)
            """,
            (
                f"acode_{uuid.uuid4().hex[:12]}",
                cleaned,
                _hash_secret(code),
                _iso(expires_at),
                _iso(now),
            ),
        )
    return {
        "ok": True,
        "email_masked": _mask_email(cleaned),
        "expires_in_seconds": int(settings.auth_login_code_ttl_seconds),
        "delivery_channel": "tencent_exmail_smtp",
    }


def _row_to_user_profile(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    roles = json_loads(str(row["roles_json"] or "[]"), default=[])
    return {
        "user_id": str(row["user_id"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "email": str(row["email"] or ""),
        "display_name": str(row["display_name"] or ""),
        "roles": [str(item) for item in roles],
        "status": str(row["status"] or "active"),
        "email_verified": bool(int(row["email_verified"] or 0)),
        "last_login_at": str(row["last_login_at"] or ""),
    }


def _upsert_user(conn: sqlite3.Connection, email: str) -> dict[str, Any]:
    now = _iso(_now())
    tenant_id = _tenant_id_for_email(email)
    workspace_id = _workspace_id()
    roles = _roles_for_email(email)
    user_id = email
    display_name = _display_name(email)
    existing = conn.execute("SELECT * FROM auth_users WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE auth_users
            SET tenant_id = ?, workspace_id = ?, display_name = ?, roles_json = ?, status = 'active',
                email_verified = 1, updated_at = ?, last_login_at = ?
            WHERE email = ?
            """,
            (tenant_id, workspace_id, display_name, json_dumps(roles), now, now, email),
        )
    else:
        conn.execute(
            """
            INSERT INTO auth_users (
                user_id, tenant_id, workspace_id, email, display_name, roles_json,
                status, email_verified, created_at, updated_at, last_login_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?)
            """,
            (user_id, tenant_id, workspace_id, email, display_name, json_dumps(roles), now, now, now),
        )
    row = conn.execute("SELECT * FROM auth_users WHERE email = ?", (email,)).fetchone()
    return _row_to_user_profile(row)


def verify_exmail_login_code(email: str, code: str) -> dict[str, Any]:
    init_auth_store()
    cleaned = _require_enterprise_email(email)
    code = str(code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("invalid_login_code_format")
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM auth_login_codes
            WHERE email = ? AND purpose = 'login' AND consumed_at = ''
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (cleaned,),
        ).fetchone()
        if not row:
            raise ValueError("login_code_not_found_or_consumed")
        if str(row["expires_at"] or "") <= _iso(now):
            raise ValueError("login_code_expired")
        if not hmac.compare_digest(str(row["code_hash"] or ""), _hash_secret(code)):
            raise ValueError("login_code_incorrect")
        conn.execute(
            "UPDATE auth_login_codes SET consumed_at = ? WHERE code_id = ?",
            (_iso(now), str(row["code_id"])),
        )
        user = _upsert_user(conn, cleaned)
        session_token = _new_session_token()
        expires_at = now + timedelta(hours=int(get_settings().auth_session_ttl_hours))
        conn.execute(
            """
            INSERT INTO auth_sessions (
                session_token, user_id, email, tenant_id, workspace_id, roles_json,
                expires_at, revoked_at, created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                session_token,
                str(user["user_id"]),
                cleaned,
                str(user["tenant_id"]),
                str(user["workspace_id"]),
                json_dumps(list(user.get("roles") or [])),
                _iso(expires_at),
                _iso(now),
                _iso(now),
            ),
        )
    return {
        "ok": True,
        "session_token": session_token,
        "expires_at": _iso(expires_at),
        "user": user,
    }


def get_authenticated_session(token: str) -> dict[str, Any]:
    init_auth_store()
    session_token = str(token or "").strip()
    if not session_token:
        return {}
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT s.*, u.display_name, u.status AS user_status, u.email_verified
            FROM auth_sessions s
            JOIN auth_users u ON u.user_id = s.user_id
            WHERE s.session_token = ?
            LIMIT 1
            """,
            (session_token,),
        ).fetchone()
        if not row:
            return {}
        if str(row["revoked_at"] or "").strip():
            return {}
        if str(row["expires_at"] or "") <= _iso(_now()):
            return {}
        if str(row["user_status"] or "active") != "active":
            return {}
        conn.execute("UPDATE auth_sessions SET last_used_at = ? WHERE session_token = ?", (_iso(_now()), session_token))
    roles = json_loads(str(row["roles_json"] or "[]"), default=[])
    return {
        "session_token": session_token,
        "user_id": str(row["user_id"] or ""),
        "tenant_id": str(row["tenant_id"] or ""),
        "workspace_id": str(row["workspace_id"] or ""),
        "email": str(row["email"] or ""),
        "display_name": str(row["display_name"] or ""),
        "roles": [str(item) for item in roles],
        "expires_at": str(row["expires_at"] or ""),
        "email_verified": bool(int(row["email_verified"] or 0)),
    }


def build_actor_from_auth_session(
    token: str,
    *,
    session_id: str = "",
    conversation_id: str = "",
) -> ActorContext | None:
    session = get_authenticated_session(token)
    if not session:
        return None
    return actor_from_mapping(
        {
            "tenant_id": session["tenant_id"],
            "user_id": session["user_id"],
            "workspace_id": session["workspace_id"],
            "roles": session["roles"],
        },
        session_id=session_id,
        conversation_id=conversation_id,
    )


def revoke_auth_session(token: str) -> dict[str, Any]:
    init_auth_store()
    session_token = str(token or "").strip()
    if not session_token:
        return {"ok": True, "revoked": False}
    with _connect() as conn:
        row = conn.execute("SELECT * FROM auth_sessions WHERE session_token = ?", (session_token,)).fetchone()
        if not row:
            return {"ok": True, "revoked": False}
        conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE session_token = ?", (_iso(_now()), session_token))
    return {"ok": True, "revoked": True}
