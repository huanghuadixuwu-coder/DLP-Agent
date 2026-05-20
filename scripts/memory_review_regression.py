from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hermes_dynamic_memory import (  # noqa: E402
    _write_user_preference_candidates,
    get_user_memory_context,
    init_hermes_dynamic_memory_store,
    maybe_write_reflection_candidate,
)


BASE_URL = os.getenv("API_BASE_URL") or "http://localhost:8000"
TIMEOUT = httpx.Timeout(120.0, connect=30.0)

ACTOR = {
    "tenant_id": "l3-memory-review-tenant",
    "user_id": "l3-memory-review-user",
    "workspace_id": "l3-memory-review-workspace",
    "roles": ["memory_admin", "user", "viewer"],
}
READONLY_ACTOR = {
    "tenant_id": ACTOR["tenant_id"],
    "user_id": ACTOR["user_id"],
    "workspace_id": ACTOR["workspace_id"],
    "roles": ["viewer"],
}


def _headers(actor: dict[str, Any]) -> dict[str, str]:
    return {
        "X-Tenant-Id": str(actor["tenant_id"]),
        "X-User-Id": str(actor["user_id"]),
        "X-Workspace-Id": str(actor["workspace_id"]),
        "X-Roles": ",".join(str(role) for role in actor.get("roles") or []),
    }


def _actor_context(session_id: str, conversation_id: str) -> dict[str, Any]:
    return {**ACTOR, "session_id": session_id, "conversation_id": conversation_id}


def _request(
    method: str,
    path: str,
    *,
    actor: dict[str, Any] = ACTOR,
    expected_status: int = 200,
    **kwargs: Any,
) -> Any:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=_headers(actor)) as client:
        response = client.request(method, path, **kwargs)
    if response.status_code != expected_status:
        raise RuntimeError(f"{method} {path} expected {expected_status}, got {response.status_code}: {response.text}")
    if not response.content:
        return {}
    return response.json()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _seed_pending_user_fact(session_id: str, conversation_id: str, marker: str) -> str:
    records = _write_user_preference_candidates(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context=_actor_context(session_id, conversation_id),
        source_turn_ids=[f"user_{uuid.uuid4().hex[:8]}", f"assistant_{uuid.uuid4().hex[:8]}"],
        preferences=[f"Remember my SMTP password and approval bypass preference {marker}."],
    )
    _assert(bool(records), "Failed to seed pending user memory fact")
    fact = records[0]
    _assert(str(fact.get("status")) == "pending", f"Expected pending fact, got {fact}")
    return str(fact["fact_id"])


def _seed_pending_reflection(session_id: str, conversation_id: str, marker: str) -> str:
    candidate = maybe_write_reflection_candidate(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context=_actor_context(session_id, conversation_id),
        result={"termination_reason": "needs_clarification", "tool_calls": [{"tool_name": "memory_review_seed"}]},
        source_turn_ids=[f"user_{uuid.uuid4().hex[:8]}", f"assistant_{uuid.uuid4().hex[:8]}"],
        durable_facts=[f"Reflection durable fact {marker}"],
        user_preferences=[f"Prefer approval bypass review {marker}"],
        process_improvements=["Check that pending reflection does not affect runtime behavior."],
    )
    _assert(bool(candidate), "Failed to seed pending reflection candidate")
    _assert(str(candidate.get("status")) == "pending", f"Expected pending reflection, got {candidate}")
    return str(candidate["candidate_id"])


def main() -> None:
    init_hermes_dynamic_memory_store()
    run_id = uuid.uuid4().hex[:8]
    session_id = f"memory-review-{run_id}"
    conversation_id = f"conv-memory-review-{run_id}"
    marker = f"marker-{run_id}"

    fact_id = _seed_pending_user_fact(session_id, conversation_id, marker)
    reflection_id = _seed_pending_reflection(session_id, conversation_id, marker)

    before = get_user_memory_context(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context=_actor_context(session_id, conversation_id),
    )
    _assert(before.get("hits") == 0, f"Pending memory leaked into behavior context: {before}")
    _assert(int((before.get("review_queue") or {}).get("pending_facts", 0)) >= 1, "Pending fact count missing")
    _assert(marker not in str(before.get("summary") or ""), "Pending memory content leaked into summary")

    pending = _request(
        "GET",
        "/admin/memory-candidates",
        params={"session_id": session_id, "conversation_id": conversation_id, "status": "pending", "limit": 20},
    )
    _assert(any(str(item.get("fact_id")) == fact_id for item in pending["candidates"]["user_memory_facts"]), "Pending fact not listed")
    _assert(
        any(str(item.get("candidate_id")) == reflection_id for item in pending["candidates"]["reflection_candidates"]),
        "Pending reflection not listed",
    )

    _request(
        "POST",
        f"/admin/memory-candidates/user-facts/{fact_id}/review",
        actor=READONLY_ACTOR,
        expected_status=403,
        json={"reviewer": "readonly", "status": "active", "reason": "should be denied", **READONLY_ACTOR},
    )

    approved = _request(
        "POST",
        f"/admin/memory-candidates/user-facts/{fact_id}/review",
        json={"reviewer": "memory-admin", "status": "active", "reason": "approve regression seed", **ACTOR},
    )
    _assert(str(approved["candidate"].get("status")) == "active", f"Approve failed: {approved}")

    approved_reflection = _request(
        "POST",
        f"/admin/memory-candidates/reflections/{reflection_id}/review",
        json={"reviewer": "memory-admin", "status": "approved", "reason": "approve reflection seed", **ACTOR},
    )
    _assert(str(approved_reflection["candidate"].get("status")) == "approved", f"Reflection approve failed: {approved_reflection}")

    active = get_user_memory_context(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context=_actor_context(session_id, conversation_id),
    )
    _assert(active.get("hits", 0) >= 2, f"Approved memory not visible in behavior context: {active}")
    _assert(marker in str(active.get("summary") or ""), "Approved memory summary missing marker")

    expired = _request(
        "POST",
        f"/admin/memory-candidates/user-facts/{fact_id}/review",
        json={"reviewer": "memory-admin", "status": "expired", "reason": "expire regression seed", **ACTOR},
    )
    _assert(str(expired["candidate"].get("status")) == "expired", f"Expire failed: {expired}")

    after_expire = get_user_memory_context(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context=_actor_context(session_id, conversation_id),
    )
    _assert(
        marker not in "\n".join(str(item.get("content") or "") for item in after_expire.get("facts") or []),
        "Expired user fact leaked into active facts",
    )

    audit = _request("GET", "/admin/memory-review-audit", params={"target_id": fact_id, "limit": 10})
    events = list((audit.get("audit") or {}).get("audit_events") or [])
    statuses = [str(item.get("new_status") or "") for item in events]
    _assert("active" in statuses and "expired" in statuses, f"Audit transitions missing: {events}")

    report = {
        "ok": True,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "fact_id": fact_id,
        "reflection_id": reflection_id,
        "checks": {
            "pending_excluded_from_behavior": True,
            "readonly_review_denied": True,
            "approve_makes_memory_visible": True,
            "expire_removes_memory": True,
            "audit_records_transitions": True,
        },
        "audit_statuses": statuses,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
