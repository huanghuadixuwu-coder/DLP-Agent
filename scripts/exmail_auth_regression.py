from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth_identity import _clean_email, _connect, _hash_secret, _iso, _now, init_auth_store  # noqa: E402
from app.config import get_settings  # noqa: E402


BASE_URL = os.getenv("API_BASE_URL") or "http://localhost:8000"
TIMEOUT = httpx.Timeout(120.0, connect=30.0)


def _request(
    method: str,
    path: str,
    *,
    token: str = "",
    expected_status: int = 200,
    **kwargs: Any,
) -> Any:
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["X-Auth-Session"] = token
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=headers) as client:
        response = client.request(method, path, **kwargs)
    if response.status_code != expected_status:
        raise RuntimeError(f"{method} {path} expected {expected_status}, got {response.status_code}: {response.text}")
    if not response.content:
        return {}
    return response.json()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _seed_code(email: str, code: str) -> None:
    init_auth_store()
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_login_codes (
                code_id, email, code_hash, purpose, expires_at,
                consumed_at, delivery_status, delivery_error, created_at
            ) VALUES (?, ?, ?, 'login', ?, '', 'seeded', '', ?)
            """,
            (
                f"acode_regression_{uuid.uuid4().hex[:8]}",
                email,
                _hash_secret(code),
                _iso(now + timedelta(minutes=10)),
                _iso(now),
            ),
        )


def main() -> None:
    settings = get_settings()
    email = _clean_email(os.getenv("AUTH_REGRESSION_EMAIL") or settings.smtp_username)
    _assert("@" in email, "AUTH_REGRESSION_EMAIL or SMTP_USERNAME must be set")
    code = "246810"
    session_id = "exmail-auth-regression"

    _seed_code(email, code)
    verified = _request("POST", "/auth/exmail/verify-code", json={"email": email, "code": code})
    token = str(verified.get("session_token") or "")
    _assert(bool(token), f"Missing auth session token: {verified}")

    me = _request("GET", "/auth/me", token=token)
    actor = dict(me.get("actor_context") or {})
    _assert(str(actor.get("user_id")) == email, f"Actor user_id does not match email: {actor}")
    _assert("viewer" in set(actor.get("roles") or []), f"Expected viewer role: {actor}")

    conversation = _request(
        "POST",
        "/conversations",
        token=token,
        json={"session_id": session_id, "title": "Exmail auth regression"},
    )
    conversation_id = str(conversation.get("conversation_id") or "")
    _assert(bool(conversation_id), f"Conversation was not created: {conversation}")
    _assert(str(conversation.get("user_id")) == email, f"Conversation actor was not derived from auth session: {conversation}")

    listed = _request("GET", "/conversations", token=token, params={"session_id": session_id})
    _assert(any(str(item.get("conversation_id")) == conversation_id for item in listed), "Created conversation not listed")

    logout = _request("POST", "/auth/logout", token=token, json={})
    _assert(bool(logout.get("revoked")), f"Logout did not revoke token: {logout}")
    _request("GET", "/auth/me", token=token, expected_status=401)

    print(
        json.dumps(
            {
                "ok": True,
                "email": email,
                "tenant_id": actor.get("tenant_id"),
                "workspace_id": actor.get("workspace_id"),
                "roles": actor.get("roles"),
                "conversation_id": conversation_id,
                "checks": {
                    "verify_code_issued_session": True,
                    "auth_me_returns_actor": True,
                    "conversation_uses_auth_actor": True,
                    "logout_revokes_session": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
