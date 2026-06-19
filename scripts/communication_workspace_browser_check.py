from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _assert_true(value: Any, label: str) -> None:
    if not value:
        raise AssertionError(f"{label}: expected truthy value, got {value!r}")


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _seed_workspace_thread() -> dict[str, str]:
    from app.communication.brief_store import upsert_communication_brief
    from app.communication.thread_store import set_active_communication_thread
    from app.communication.types import CommunicationBrief, CommunicationThreadRef
    from scripts.communication_copilot_regression import _seed_thread_store_message

    suffix = uuid4().hex[:8]
    session_id = f"browser-session-{suffix}"
    conversation_id = f"browser-conversation-{suffix}"
    thread_id = f"browser-thread-{suffix}"
    actor_context = {
        "tenant_id": "local-dev",
        "user_id": "local-user",
        "workspace_id": "default",
        "roles": ["admin", "mail_sender"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"browser-message-{suffix}",
        uid=f"browser-uid-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Browser check thread",
        received_at="2026-06-18T11:00:00+00:00",
        summary="Customer thread used by the workspace browser check.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "active browser thread")
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Browser check thread",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-18T11:00:00+00:00",
        latest_summary="Customer thread used by the workspace browser check.",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id=f"browser-brief-{suffix}",
        conversation_id=conversation_id,
        thread_ref=thread_ref,
        employee_goal="Use this selected thread as the Copilot context.",
        customer_context_summary="Browser check seeded customer thread.",
        recommended_next_action="draft_reply",
        source_observation_ids=["browser-check"],
        confidence=0.86,
        actor_context=actor_context,
    )
    upsert_communication_brief(brief, actor_context=actor_context, refresh_reason="browser_check")
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "brief_id": brief.brief_id,
    }


def run_check(url: str, api_base_url: str) -> dict[str, Any]:
    seeded = _seed_workspace_thread()
    with httpx.Client(timeout=30.0) as client:
        page = client.get(
            url,
            params={
                "session_id": seeded["session_id"],
                "conversation_id": seeded["conversation_id"],
                "thread_id": seeded["thread_id"],
            },
        )
        page.raise_for_status()
        _assert_true("Traceback" not in page.text, "Streamlit page has no traceback")

        selected = client.post(f"{api_base_url}/internal/communication/threads/{seeded['thread_id']}/active")
        selected.raise_for_status()
        _assert_equal(
            (selected.json().get("active_thread") or {}).get("thread_id"),
            seeded["thread_id"],
            "selected thread API",
        )
        workspace = client.get(
            f"{api_base_url}/internal/communication/workspace",
            params={
                "session_id": seeded["session_id"],
                "conversation_id": seeded["conversation_id"],
                "thread_id": seeded["thread_id"],
                "refresh": "false",
            },
        )
        workspace.raise_for_status()
        _assert_equal(
            (workspace.json().get("selected_thread") or {}).get("thread_id"),
            seeded["thread_id"],
            "workspace selected thread",
        )
        refreshed = client.get(
            f"{api_base_url}/internal/communication/workspace",
            params={
                "session_id": seeded["session_id"],
                "conversation_id": seeded["conversation_id"],
                "refresh": "false",
            },
        )
        refreshed.raise_for_status()
        _assert_equal(
            (refreshed.json().get("selected_thread") or {}).get("thread_id"),
            seeded["thread_id"],
            "workspace selected thread after refresh",
        )
        _assert_equal(
            (refreshed.json().get("latest_brief") or {}).get("brief", {}).get("brief_id"),
            seeded["brief_id"],
            "workspace latest brief after refresh",
        )

    return {
        "ok": True,
        "url": url,
        "api_base_url": api_base_url,
        "thread_id": seeded["thread_id"],
        "brief_id": seeded["brief_id"],
        "selection_survived_refresh": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Communication workspace browser check")
    parser.add_argument("--url", required=True, help="Streamlit workspace URL, for example http://web:8501")
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("INTERNAL_API_BASE_URL") or os.getenv("API_BASE_URL") or "http://localhost:8000",
    )
    args = parser.parse_args()
    try:
        result = run_check(args.url, args.api_base_url.rstrip("/"))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
