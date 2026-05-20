from __future__ import annotations

import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.enterprise_rag.core.types import EnterpriseDocument
from app.enterprise_rag.ingestion.indexer import upsert_enterprise_documents_hybrid


BASE_URL = os.getenv("API_BASE_URL") or "http://localhost:8000"
RECIPIENT = os.getenv("REGRESSION_RECIPIENT") or os.getenv("SMTP_USER") or "17388861183@163.com"
TIMEOUT = httpx.Timeout(240.0, connect=30.0)
MAX_WORKERS = int(os.getenv("CONCURRENCY_REGRESSION_WORKERS") or "8")


ACTOR = {
    "tenant_id": "l3-concurrency-tenant",
    "user_id": "l3-concurrency-user",
    "workspace_id": "l3-concurrency-workspace",
    "roles": ["admin", "mail_sender", "approver", "ingest_admin", "memory_admin", "user", "viewer"],
}
READONLY_ACTOR = {
    "tenant_id": ACTOR["tenant_id"],
    "user_id": ACTOR["user_id"],
    "workspace_id": ACTOR["workspace_id"],
    "roles": ["viewer"],
}


METRIC_NAMES = (
    "agent_queue_backlog",
    "agent_requests_total",
    "agent_tasks_created_total",
    "agent_task_status_total",
    "agent_rate_limited_total",
    "agent_llm_errors_total",
    "agent_renderer_fallback_total",
    "agent_request_latency_ms_count",
)


def _headers(actor: dict[str, Any] = ACTOR) -> dict[str, str]:
    return {
        "X-Tenant-Id": str(actor["tenant_id"]),
        "X-User-Id": str(actor["user_id"]),
        "X-Workspace-Id": str(actor["workspace_id"]),
        "X-Roles": ",".join(str(role) for role in actor.get("roles") or []),
    }


def _actor_payload(actor: dict[str, Any] = ACTOR) -> dict[str, Any]:
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
    actor: dict[str, Any] = ACTOR,
    expected_status: int = 200,
    parse_json: bool = True,
    **kwargs: Any,
) -> Any:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.update(_headers(actor))
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=headers) as client:
        response = client.request(method, path, **kwargs)
    if response.status_code != expected_status:
        raise RuntimeError(f"{method} {path} expected {expected_status}, got {response.status_code}: {response.text}")
    if not parse_json:
        return response.text
    if not response.content:
        return {}
    return response.json()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _timed(name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn()
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {"name": name, "ok": True, "latency_ms": latency_ms, "result": result}
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {"name": name, "ok": False, "latency_ms": latency_ms, "error": str(exc)}


def _create_conversation(session_id: str, title: str) -> str:
    payload = {"session_id": session_id, "title": title, **_actor_payload()}
    response = _request("POST", "/conversations", json=payload)
    return str(response["conversation_id"])


def _chat(session_id: str, conversation_id: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "message": message,
        "mode": "auto",
        "show_steps": False,
        **_actor_payload(),
        **extra,
    }
    return _request("POST", "/agent/chat", json=payload)


def _queue_health() -> dict[str, Any]:
    return _request("GET", "/admin/queue-health")


def _metrics_snapshot() -> dict[str, Any]:
    raw = _request("GET", "/metrics", parse_json=False)
    selected: dict[str, list[str]] = {name: [] for name in METRIC_NAMES}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        metric_name = line.split("{", 1)[0].split(" ", 1)[0]
        if metric_name in selected:
            selected[metric_name].append(line)
    return {
        "line_count": len(raw.splitlines()),
        "selected_metrics": {name: values[:8] for name, values in selected.items() if values},
    }


def _seed_rag_doc(run_id: str) -> str:
    doc_id = f"l3_concurrency_doc_{run_id}"
    upsert_enterprise_documents_hybrid(
        [
            EnterpriseDocument(
                doc_id=doc_id,
                source_type="google_drive",
                title="L3 Concurrency Readiness Note",
                content=(
                    f"L3 concurrency marker gamma-{run_id}. "
                    "The concurrent readiness owner is Orion Load Team. "
                    "This document is scoped to the concurrency regression tenant."
                ),
                metadata={"business_domain": "platform", "collection_version": "l3-concurrency"},
            )
        ],
        actor_context=_actor_payload(),
        replace_existing=True,
    )
    return doc_id


def _prepare_pending_mail(session_id: str, conversation_id: str) -> dict[str, Any]:
    response = _chat(
        session_id,
        conversation_id,
        f"Please send this public concurrency update to {RECIPIENT}. Keep it brief.",
        uploaded_filename="concurrency-update.txt",
        uploaded_content_type="text/plain",
        uploaded_text=(
            "Public concurrency regression update. The API, queue health, and worker consumption smoke test completed. "
            "No customer data, credentials, secrets, or personal information are included."
        ),
    )
    _assert(str(response.get("termination_reason")) == "needs_confirmation", f"Mail prepare did not produce pending confirmation: {response}")
    _assert(bool(response.get("pending_confirmation")), f"Missing pending confirmation: {response}")
    return {
        "final_answer_source": response.get("final_answer_source"),
        "termination_reason": response.get("termination_reason"),
    }


def _poll_task(task_id: str, *, expected: set[str], timeout_seconds: int = 240) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        task = _request("GET", f"/tasks/{task_id}")
        last = task if isinstance(task, dict) else {}
        if str(last.get("status")) in expected:
            return last
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for task {task_id}; last={last}")


def _verify_structured_permission_failure() -> dict[str, Any]:
    payload = {"mode": "sample", "limit": 1, **_actor_payload(READONLY_ACTOR)}
    response = _request(
        "POST",
        "/enterprise-rag/ingest",
        actor=READONLY_ACTOR,
        expected_status=403,
        json=payload,
    )
    detail = dict(response.get("detail") or {})
    _assert(detail.get("observation_type") == "permission_denied", f"Permission failure is not structured: {response}")
    decision = dict(detail.get("permission_decision") or {})
    _assert(decision.get("allowed") is False, f"Permission decision did not deny: {response}")
    return {
        "observation_type": detail.get("observation_type"),
        "action": decision.get("action"),
        "reason": decision.get("reason"),
        "required_roles": decision.get("required_roles"),
        "actor_roles": decision.get("actor_roles"),
    }


def main() -> int:
    run_id = uuid.uuid4().hex[:8]
    session_id = f"l3_concurrency_{run_id}"
    chat_conversation_id = _create_conversation(session_id, "L3 concurrency chat")
    mail_conversation_id = _create_conversation(session_id, "L3 concurrency mail")
    rag_conversation_id = _create_conversation(session_id, "L3 concurrency rag")
    rag_doc_id = _seed_rag_doc(run_id)

    before_queue = _queue_health()
    before_metrics = _metrics_snapshot()
    pending_mail = _prepare_pending_mail(session_id, mail_conversation_id)

    def agent_chat_task(index: int) -> dict[str, Any]:
        response = _chat(
            session_id,
            chat_conversation_id,
            f"请简要说明你的功能是什么。这是 L3 并发 smoke #{index}。",
        )
        _assert(response.get("answer"), f"Empty agent answer: {response}")
        return {
            "final_answer_source": response.get("final_answer_source"),
            "intent": response.get("intent"),
            "queue_status": response.get("queue_status"),
        }

    def rag_query_task(index: int) -> dict[str, Any]:
        payload = {
            "session_id": session_id,
            "conversation_id": rag_conversation_id,
            "question": f"What team owns gamma-{run_id} concurrent readiness? Query {index}",
            "top_k": 6,
            **_actor_payload(),
        }
        response = _request("POST", "/enterprise-rag/query", json=payload)
        docs = {str(item) for item in response.get("supporting_doc_ids") or []}
        _assert(rag_doc_id in docs, f"RAG query did not retrieve seeded doc {rag_doc_id}: {docs}")
        return {
            "supporting_doc_ids": sorted(docs),
            "missing_evidence": response.get("missing_evidence"),
            "task_mode": response.get("task_mode"),
        }

    def mail_draft_task() -> dict[str, Any]:
        response = _chat(
            session_id,
            chat_conversation_id,
            "Polish this email body professionally. body: Public concurrency smoke completed and queue health remained observable.",
        )
        _assert(not response.get("task_id"), f"Draft-only unexpectedly created task: {response}")
        _assert(response.get("answer"), f"Draft-only returned empty answer: {response}")
        return {"final_answer_source": response.get("final_answer_source"), "task_id": response.get("task_id")}

    def mail_confirm_task() -> dict[str, Any]:
        response = _chat(session_id, mail_conversation_id, "confirm send")
        task_id = str(response.get("task_id") or "")
        _assert(task_id, f"Confirm send did not create task: {response}")
        return {"task_id": task_id, "task_status": response.get("task_status"), "delivery_status": response.get("delivery_status")}

    def queue_health_task(index: int) -> dict[str, Any]:
        health = _queue_health()
        return {"index": index, "queue_status": health.get("queue_status") or {}}

    jobs: list[tuple[str, Callable[[], Any]]] = [
        ("agent_chat_1", lambda: agent_chat_task(1)),
        ("agent_chat_2", lambda: agent_chat_task(2)),
        ("enterprise_rag_1", lambda: rag_query_task(1)),
        ("enterprise_rag_2", lambda: rag_query_task(2)),
        ("mail_draft", mail_draft_task),
        ("mail_confirm", mail_confirm_task),
        ("queue_health_1", lambda: queue_health_task(1)),
        ("queue_health_2", lambda: queue_health_task(2)),
        ("metrics_during", _metrics_snapshot),
    ]

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_timed, name, fn) for name, fn in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    total_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    failures = [item for item in results if not item.get("ok")]
    _assert(not failures, f"Concurrent jobs failed: {json.dumps(failures, ensure_ascii=False)}")

    confirm_result = next(item for item in results if item["name"] == "mail_confirm")["result"]
    task_id = str(confirm_result["task_id"])
    terminal_task = _poll_task(task_id, expected={"sent", "send_failed", "delivery_deferred", "pending_approval"})
    consumed_statuses = {"sent", "send_failed", "delivery_deferred", "pending_approval"}
    _assert(
        str(terminal_task.get("status")) in consumed_statuses,
        f"Mail confirm task was not consumed by worker or governance flow: {terminal_task}",
    )
    status_path = [str(item) for item in (terminal_task.get("status_path") or [])]
    _assert(
        any(item in status_path for item in ("processing", "pending_approval", "sent", "send_failed")),
        f"Mail task does not show worker/governance progress: {terminal_task}",
    )

    after_queue = _queue_health()
    after_metrics = _metrics_snapshot()
    permission_failure = _verify_structured_permission_failure()

    report = {
        "base_url": BASE_URL,
        "session_id": session_id,
        "run_id": run_id,
        "max_workers": MAX_WORKERS,
        "total_latency_ms": total_latency_ms,
        "rag_doc_id": rag_doc_id,
        "conversations": {
            "chat": chat_conversation_id,
            "mail": mail_conversation_id,
            "rag": rag_conversation_id,
        },
        "pending_mail": pending_mail,
        "concurrent_results": sorted(results, key=lambda item: item["name"]),
        "worker_consumption": {
            "task_id": task_id,
            "status": terminal_task.get("status"),
            "delivery_status": terminal_task.get("delivery_status"),
            "smtp_provider": terminal_task.get("smtp_provider"),
            "status_path": terminal_task.get("status_path"),
            "approval_required": terminal_task.get("approval_required"),
            "risk_level": terminal_task.get("risk_level"),
            "risk_reasons": terminal_task.get("risk_reasons"),
        },
        "queue_health": {
            "before": before_queue.get("queue_status") or {},
            "after": after_queue.get("queue_status") or {},
        },
        "prometheus_metrics": {
            "before": before_metrics,
            "after": after_metrics,
        },
        "structured_failure_observation": permission_failure,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CONCURRENCY_REGRESSION_FAILED: {exc}", file=sys.stderr)
        raise
