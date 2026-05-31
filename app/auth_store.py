from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


AUTH_DDL_LOCK_KEY = 86420532
_INITIALIZED = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().postgres_dsn, row_factory=dict_row)


def init_auth_store() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (AUTH_DDL_LOCK_KEY,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_login_codes (
                email TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_hash TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                roles TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_auth_sessions_email ON auth_sessions(email);
            """
        )
    _INITIALIZED = True


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def mask_email(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized:
        return normalized
    name, domain = normalized.split("@", 1)
    if len(name) <= 2:
        masked = name[:1] + "*"
    else:
        masked = f"{name[:2]}***{name[-1:]}"
    return f"{masked}@{domain}"


def actor_for_email(email: str) -> dict[str, Any]:
    normalized = normalize_email(email)
    domain = normalized.split("@", 1)[1] if "@" in normalized else "local"
    user_slug = normalized.replace("@", "_at_").replace(".", "_").replace("+", "_")
    tenant_slug = domain.replace(".", "_").replace("-", "_")
    return {
        "email": normalized,
        "tenant_id": f"mail-{tenant_slug}",
        "user_id": f"mail-{user_slug}",
        "workspace_id": f"mail-{tenant_slug}-default",
        # This is a local enterprise trial binding, not IAM. Keep broad roles so
        # the demo can exercise governed side-effect flows after confirmation.
        "roles": ["admin", "user", "viewer"],
    }


def create_login_code(email: str, *, ttl_minutes: int = 10) -> dict[str, Any]:
    init_auth_store()
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        raise ValueError("invalid_email")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_login_codes (email, code_hash, expires_at, attempt_count, created_at)
            VALUES (%s, %s, %s, 0, %s)
            ON CONFLICT (email)
            DO UPDATE SET code_hash = EXCLUDED.code_hash,
                          expires_at = EXCLUDED.expires_at,
                          attempt_count = 0,
                          created_at = EXCLUDED.created_at
            """,
            (normalized, _hash(code), _iso(now + timedelta(minutes=ttl_minutes)), _iso(now)),
        )
    return {"email": normalized, "email_masked": mask_email(normalized), "code": code, "expires_in_seconds": ttl_minutes * 60}


def verify_login_code(email: str, code: str, *, session_ttl_hours: int = 12) -> dict[str, Any]:
    init_auth_store()
    normalized = normalize_email(email)
    cleaned_code = (code or "").strip()
    if not normalized or not cleaned_code:
        raise ValueError("missing_email_or_code")
    with _connect() as conn:
        row = conn.execute("SELECT * FROM auth_login_codes WHERE email = %s", (normalized,)).fetchone()
        if not row:
            raise ValueError("code_not_requested")
        expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        if expires_at < _now():
            raise ValueError("code_expired")
        if int(row.get("attempt_count") or 0) >= 5:
            raise ValueError("too_many_attempts")
        if str(row.get("code_hash") or "") != _hash(cleaned_code):
            conn.execute("UPDATE auth_login_codes SET attempt_count = attempt_count + 1 WHERE email = %s", (normalized,))
            raise ValueError("invalid_code")

        token = secrets.token_urlsafe(32)
        actor = actor_for_email(normalized)
        now = _now()
        conn.execute("DELETE FROM auth_login_codes WHERE email = %s", (normalized,))
        conn.execute(
            """
            INSERT INTO auth_sessions (
                session_hash, email, tenant_id, user_id, workspace_id, roles, expires_at, created_at, revoked_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '')
            """,
            (
                _hash(token),
                normalized,
                actor["tenant_id"],
                actor["user_id"],
                actor["workspace_id"],
                ",".join(actor["roles"]),
                _iso(now + timedelta(hours=session_ttl_hours)),
                _iso(now),
            ),
        )
    return {"session_token": token, "user": actor, "expires_in_seconds": session_ttl_hours * 3600}


def resolve_session_token(token: str) -> dict[str, Any] | None:
    init_auth_store()
    cleaned = (token or "").strip()
    if not cleaned:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM auth_sessions WHERE session_hash = %s", (_hash(cleaned),)).fetchone()
    if not row or str(row.get("revoked_at") or ""):
        return None
    expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires_at < _now():
        return None
    return {
        "email": str(row.get("email") or ""),
        "tenant_id": str(row.get("tenant_id") or ""),
        "user_id": str(row.get("user_id") or ""),
        "workspace_id": str(row.get("workspace_id") or ""),
        "roles": [part.strip() for part in str(row.get("roles") or "").split(",") if part.strip()],
        "expires_at": str(row.get("expires_at") or ""),
    }


def revoke_session_token(token: str) -> bool:
    init_auth_store()
    cleaned = (token or "").strip()
    if not cleaned:
        return False
    with _connect() as conn:
        result = conn.execute(
            "UPDATE auth_sessions SET revoked_at = %s WHERE session_hash = %s AND revoked_at = ''",
            (_iso(_now()), _hash(cleaned)),
        )
    return bool(result.rowcount)
