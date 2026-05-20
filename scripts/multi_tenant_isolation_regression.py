from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.enterprise_rag.core.types import EnterpriseDocument
from app.enterprise_rag.ingestion.indexer import upsert_enterprise_documents_hybrid
from app.hermes_dynamic_memory import _write_user_preference_candidates, init_hermes_dynamic_memory_store


BASE_URL = os.getenv("API_BASE_URL") or "http://localhost:8000"
TIMEOUT = httpx.Timeout(180.0, connect=30.0)
RECIPIENT = os.getenv("REGRESSION_RECIPIENT") or os.getenv("SMTP_USER") or "17388861183@163.com"


def _roles(*items: str) -> list[str]:
    return list(dict.fromkeys([*items, "user", "viewer"]))


ACTOR_A = {
    "tenant_id": "l3-tenant-a",
    "user_id": "l3-user-a",
    "workspace_id": "l3-workspace-a",
    "roles": _roles("admin", "mail_sender", "approver", "ingest_admin", "memory_admin"),
}
ACTOR_B = {
    "tenant_id": "l3-tenant-b",
    "user_id": "l3-user-b",
    "workspace_id": "l3-workspace-b",
    "roles": _roles("admin", "mail_sender", "approver", "ingest_admin", "memory_admin"),
}
ACTOR_A_READONLY = {
    "tenant_id": ACTOR_A["tenant_id"],
    "user_id": ACTOR_A["user_id"],
    "workspace_id": ACTOR_A["workspace_id"],
    "roles": ["viewer"],
}
ACTOR_A_SENDER_ONLY = {
    "tenant_id": ACTOR_A["tenant_id"],
    "user_id": ACTOR_A["user_id"],
    "workspace_id": ACTOR_A["workspace_id"],
    "roles": ["mail_sender", "user", "viewer"],
}


def _headers(actor: dict[str, Any]) -> dict[str, str]:
    return {
        "X-Tenant-Id": str(actor["tenant_id"]),
        "X-User-Id": str(actor["user_id"]),
        "X-Workspace-Id": str(actor["workspace_id"]),
        "X-Roles": ",".join(str(role) for role in actor.get("roles") or []),
    }


def _actor_payload(actor: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": str(actor["tenant_id"]),
        "user_id": str(actor["user_id"]),
        "workspace_id": str(actor["workspace_id"]),
        "roles": list(actor.get("roles") or []),
    }


def _request(
    method: str,
    path: str,
    *,
    actor: dict[str, Any],
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


def _poll_task(task_id: str, *, actor: dict[str, Any], expected: set[str], timeout_seconds: int = 180) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        task = _request("GET", f"/tasks/{task_id}", actor=actor)
        last = task if isinstance(task, dict) else {}
        if str(last.get("status")) in expected:
            return last
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for {task_id}; last={last}")


def _create_conversation(session_id: str, actor: dict[str, Any], title: str) -> str:
    payload = {"session_id": session_id, "title": title, **_actor_payload(actor)}
    conversation = _request("POST", "/conversations", actor=actor, json=payload)
    return str(conversation["conversation_id"])


def _seed_memory(session_id: str, conversation_id: str, actor: dict[str, Any], preference: str) -> str:
    init_hermes_dynamic_memory_store()
    records = _write_user_preference_candidates(
        session_id=session_id,
        conversation_id=conversation_id,
        actor_context={**_actor_payload(actor), "session_id": session_id, "conversation_id": conversation_id},
        source_turn_ids=[f"seed_user_{uuid.uuid4().hex[:8]}", f"seed_assistant_{uuid.uuid4().hex[:8]}"],
        preferences=[preference],
    )
    _assert(bool(records), "Failed to seed Hermes memory preference")
    return str(records[0]["fact_id"])


def _seed_enterprise_docs(run_id: str) -> dict[str, str]:
    doc_a = f"l3_iso_doc_a_{run_id}"
    doc_b = f"l3_iso_doc_b_{run_id}"
    upsert_enterprise_documents_hybrid(
        [
            EnterpriseDocument(
                doc_id=doc_a,
                source_type="google_drive",
                title="Tenant A L3 Isolation Policy",
                content=(
                    f"Tenant A confidential L3 isolation marker alpha-{run_id}. "
                    "The approval owner is Redwood Alpha only. This fact must never appear for tenant B."
                ),
                metadata={"business_domain": "security", "collection_version": "l3-isolation"},
            )
        ],
        actor_context=_actor_payload(ACTOR_A),
        replace_existing=True,
    )
    upsert_enterprise_documents_hybrid(
        [
            EnterpriseDocument(
                doc_id=doc_b,
                source_type="google_drive",
                title="Tenant B L3 Isolation Policy",
                content=(
                    f"Tenant B confidential L3 isolation marker beta-{run_id}. "
                    "The approval owner is Cedar Beta only. This fact must never appear for tenant A."
                ),
                metadata={"business_domain": "security", "collection_version": "l3-isolation"},
            )
        ],
        actor_context=_actor_payload(ACTOR_B),
        replace_existing=True,
    )
    return {"tenant_a_doc_id": doc_a, "tenant_b_doc_id": doc_b}


def _run_conversation_isolation(session_id: str, conv_a: str, conv_b: str, report: dict[str, Any]) -> None:
    own_a = _request("GET", f"/conversations/{conv_a}", actor=ACTOR_A)
    _assert(own_a["conversation_id"] == conv_a, "Actor A cannot read own conversation")
    _request("GET", f"/conversations/{conv_a}", actor=ACTOR_B, expected_status=403)
    _request("GET", f"/conversations/{conv_b}", actor=ACTOR_A, expected_status=403)

    list_a = _request("GET", "/conversations", actor=ACTOR_A, params={"session_id": session_id})
    list_b = _request("GET", "/conversations", actor=ACTOR_B, params={"session_id": session_id})
    ids_a = {str(item["conversation_id"]) for item in list_a}
    ids_b = {str(item["conversation_id"]) for item in list_b}
    _assert(conv_a in ids_a and conv_b not in ids_a, f"Actor A conversation list leaked: {ids_a}")
    _assert(conv_b in ids_b and conv_a not in ids_b, f"Actor B conversation list leaked: {ids_b}")
    report["checks"]["conversation_isolation"] = {"actor_a_visible": sorted(ids_a), "actor_b_visible": sorted(ids_b)}


def _run_task_isolation_and_permissions(session_id: str, conv_a: str, report: dict[str, Any]) -> str:
    replay_payload = {
        "session_id": session_id,
        "conversation_id": conv_a,
        "destination_email": RECIPIENT,
        **_actor_payload(ACTOR_A),
    }
    task = _request("POST", "/labs/dlp/scenarios/api_secret_pending_approval/replay", actor=ACTOR_A, json=replay_payload)
    task_id = str(task["task_id"])
    task = _poll_task(task_id, actor=ACTOR_A, expected={"pending_approval"})
    _assert(str(task.get("status")) == "pending_approval", f"Expected pending_approval, got {task}")

    _request("GET", f"/tasks/{task_id}", actor=ACTOR_B, expected_status=403)
    task_list_a = _request("GET", "/tasks", actor=ACTOR_A, params={"session_id": session_id})
    task_list_b = _request("GET", "/tasks", actor=ACTOR_B, params={"session_id": session_id})
    ids_a = {str(item["task_id"]) for item in task_list_a}
    ids_b = {str(item["task_id"]) for item in task_list_b}
    _assert(task_id in ids_a, "Actor A task list does not include own task")
    _assert(task_id not in ids_b, "Actor B task list leaked Actor A task")

    approval_payload = {"actor": "readonly-user", "reason": "permission regression", **_actor_payload(ACTOR_A_READONLY)}
    _request("POST", f"/tasks/{task_id}/approve", actor=ACTOR_A_READONLY, expected_status=403, json=approval_payload)

    sender_payload = {"actor": "sender-only", "reason": "permission regression", **_actor_payload(ACTOR_A_SENDER_ONLY)}
    _request("POST", f"/tasks/{task_id}/approve", actor=ACTOR_A_SENDER_ONLY, expected_status=403, json=sender_payload)

    report["checks"]["task_isolation_and_permissions"] = {
        "task_id": task_id,
        "status": task.get("status"),
        "actor_a_task_count": len(task_list_a),
        "actor_b_task_count": len(task_list_b),
        "cross_tenant_read_denied": True,
        "approve_without_role_denied": True,
    }
    return task_id


def _run_memory_isolation(session_id: str, conv_a: str, conv_b: str, report: dict[str, Any]) -> None:
    fact_a = _seed_memory(session_id, conv_a, ACTOR_A, "Remember tenant A prefers alpha-only escalation.")
    fact_b = _seed_memory(session_id, conv_b, ACTOR_B, "Remember tenant B prefers beta-only escalation.")

    mem_a = _request("GET", "/admin/memory-candidates", actor=ACTOR_A, params={"session_id": session_id, "status": "active", "limit": 20})
    mem_b = _request("GET", "/admin/memory-candidates", actor=ACTOR_B, params={"session_id": session_id, "status": "active", "limit": 20})
    candidates_a = dict(mem_a.get("candidates") or {})
    candidates_b = dict(mem_b.get("candidates") or {})
    ids_a = {str(item["fact_id"]) for item in candidates_a.get("user_memory_facts") or []}
    ids_b = {str(item["fact_id"]) for item in candidates_b.get("user_memory_facts") or []}
    _assert(fact_a in ids_a and fact_b not in ids_a, f"Actor A memory list leaked: {ids_a}")
    _assert(fact_b in ids_b and fact_a not in ids_b, f"Actor B memory list leaked: {ids_b}")

    _request("GET", "/admin/memory-candidates", actor=ACTOR_A_READONLY, expected_status=403, params={"session_id": session_id})

    report["checks"]["memory_isolation"] = {
        "actor_a_fact_id": fact_a,
        "actor_b_fact_id": fact_b,
        "cross_tenant_memory_hidden": True,
        "readonly_admin_denied": True,
    }


def _run_rag_isolation(session_id: str, conv_a: str, conv_b: str, run_id: str, report: dict[str, Any]) -> None:
    doc_ids = _seed_enterprise_docs(run_id)
    question_a = f"What is the approval owner for alpha-{run_id}?"
    question_b = f"What is the approval owner for beta-{run_id}?"
    query_a = {
        "session_id": session_id,
        "conversation_id": conv_a,
        "question": question_a,
        "top_k": 6,
        **_actor_payload(ACTOR_A),
    }
    query_b_wrong = {
        "session_id": session_id,
        "conversation_id": conv_b,
        "question": question_a,
        "top_k": 6,
        **_actor_payload(ACTOR_B),
    }
    query_b = {
        "session_id": session_id,
        "conversation_id": conv_b,
        "question": question_b,
        "top_k": 6,
        **_actor_payload(ACTOR_B),
    }
    result_a = _request("POST", "/enterprise-rag/query", actor=ACTOR_A, json=query_a)
    result_b_wrong = _request("POST", "/enterprise-rag/query", actor=ACTOR_B, json=query_b_wrong)
    result_b = _request("POST", "/enterprise-rag/query", actor=ACTOR_B, json=query_b)
    docs_a = {str(item) for item in result_a.get("supporting_doc_ids") or []}
    docs_b_wrong = {str(item) for item in result_b_wrong.get("supporting_doc_ids") or []}
    docs_b = {str(item) for item in result_b.get("supporting_doc_ids") or []}
    _assert(doc_ids["tenant_a_doc_id"] in docs_a, f"Tenant A did not retrieve own doc: {docs_a}")
    _assert(doc_ids["tenant_a_doc_id"] not in docs_b_wrong, f"Tenant B retrieved Tenant A doc: {docs_b_wrong}")
    _assert(doc_ids["tenant_b_doc_id"] in docs_b, f"Tenant B did not retrieve own doc: {docs_b}")

    _request(
        "POST",
        "/enterprise-rag/ingest",
        actor=ACTOR_A_READONLY,
        expected_status=403,
        json={"mode": "sample", "limit": 1, **_actor_payload(ACTOR_A_READONLY)},
    )
    _request(
        "GET",
        "/enterprise-rag/benchmark",
        actor=ACTOR_A_READONLY,
        expected_status=403,
        params={"limit": 1, "top_k": 4},
    )
    report["checks"]["rag_isolation_and_permissions"] = {
        **doc_ids,
        "tenant_a_supporting_doc_ids": sorted(docs_a),
        "tenant_b_wrong_supporting_doc_ids": sorted(docs_b_wrong),
        "tenant_b_supporting_doc_ids": sorted(docs_b),
        "readonly_ingest_denied": True,
        "readonly_benchmark_denied": True,
    }


def main() -> int:
    run_id = uuid.uuid4().hex[:8]
    session_id = f"l3_isolation_{run_id}"
    conv_a = _create_conversation(session_id, ACTOR_A, "L3 isolation tenant A")
    conv_b = _create_conversation(session_id, ACTOR_B, "L3 isolation tenant B")
    report: dict[str, Any] = {
        "base_url": BASE_URL,
        "session_id": session_id,
        "run_id": run_id,
        "actors": {
            "actor_a": _actor_payload(ACTOR_A),
            "actor_b": _actor_payload(ACTOR_B),
            "actor_a_readonly": _actor_payload(ACTOR_A_READONLY),
        },
        "conversations": {"actor_a": conv_a, "actor_b": conv_b},
        "checks": {},
    }

    _run_conversation_isolation(session_id, conv_a, conv_b, report)
    _run_task_isolation_and_permissions(session_id, conv_a, report)
    _run_memory_isolation(session_id, conv_a, conv_b, report)
    _run_rag_isolation(session_id, conv_a, conv_b, run_id, report)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MULTI_TENANT_ISOLATION_REGRESSION_FAILED: {exc}", file=sys.stderr)
        raise
