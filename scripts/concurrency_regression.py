from __future__ import annotations

import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.enterprise_rag.core.types import EnterpriseDocument  # noqa: E402
from app.enterprise_rag.ingestion.indexer import upsert_enterprise_documents_hybrid  # noqa: E402


BASE_URL = (os.getenv("API_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
RECIPIENT = os.getenv("REGRESSION_RECIPIENT") or os.getenv("SMTP_USER") or "17388861183@163.com"
TIMEOUT = httpx.Timeout(240.0, connect=30.0)
MAX_WORKERS = int(os.getenv("CONCURRENCY_REGRESSION_WORKERS") or "8")
TASK_CONSUMED_STATUSES = {"sent", "send_failed", "failed", "rejected", "pending_approval", "delivery_deferred"}
PROMETHEUS_METRIC_PREFIXES = (
    "agent_queue_backlog",
    "agent_requests_total",
    "agent_tasks_created_total",
    "agent_task_status_total",
    "agent_rate_limited_total",
    "agent_llm_errors_total",
    "agent_renderer_fallback_total",
    "agent_dependency_failures_total",
    "agent_request_latency_ms_count",
    "agent_retrieval_expansion_total",
)


ACTOR = {
    "tenant_id": "tenant-l3-concurrency",
    "user_id": "codex-concurrency-admin",
    "workspace_id": "workspace-l3-concurrency",
    "roles": ["admin", "mail_sender", "approver", "ingest_admin", "memory_admin", "user", "viewer"],
}
READONLY_ACTOR = {
    "tenant_id": ACTOR["tenant_id"],
    "user_id": "codex-concurrency-readonly",
    "workspace_id": ACTOR["workspace_id"],
    "roles": ["viewer"],
}


def _headers(actor: dict[str, Any] = ACTOR) -> dict[str, str]:
    return {
        "X-Tenant-Id": str(actor["tenant_id"]),
        "X-User-Id": str(actor["user_id"]),
        "X-Workspace-Id": str(actor["workspace_id"]),
        "X-Roles": ",".join(str(role) for role in actor.get("roles", [])),
    }


def _actor_payload(actor: dict[str, Any] = ACTOR) -> dict[str, Any]:
    return {
        "tenant_id": actor["tenant_id"],
        "user_id": actor["user_id"],
        "workspace_id": actor["workspace_id"],
        "roles": list(actor.get("roles") or []),
    }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _request(
    method: str,
    path: str,
    *,
    actor: dict[str, Any] = ACTOR,
    expect_error: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=_headers(actor)) as client:
        response = client.request(method, path, **kwargs)
    if expect_error:
        payload = response.json() if response.content else {}
        return {"status_code": response.status_code, "payload": payload}
    response.raise_for_status()
    return response.json() if response.content else {}


def _timed(name: str, fn) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = fn()
        return {
            "ok": True,
            "name": name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "result": result,
        }
    except Exception as exc:
        return {
            "ok": False,
            "name": name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": str(exc),
        }


def _create_conversation(session_id: str, title: str) -> str:
    payload = {"session_id": session_id, "title": title, **_actor_payload()}
    conversation = _request("POST", "/conversations", json=payload)
    return str(conversation["conversation_id"])


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
    payload = _request("GET", "/admin/queue-health")
    queue_status = dict(payload.get("queue_status") or {})
    return {
        "ok": bool(payload.get("ok")),
        "queues": dict(queue_status.get("queues") or {}),
        "permission_decision": payload.get("permission_decision") or {},
    }


def _metrics_snapshot() -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, headers=_headers(ACTOR)) as client:
        response = client.get("/metrics")
    response.raise_for_status()
    selected: dict[str, list[str]] = {name: [] for name in PROMETHEUS_METRIC_PREFIXES}
    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for prefix in PROMETHEUS_METRIC_PREFIXES:
            if line.startswith(prefix):
                selected[prefix].append(line)
                break
    return {name: lines[:12] for name, lines in selected.items() if lines}


def _seed_rag_doc(run_id: str) -> dict[str, Any]:
    doc_id = f"l3_concurrency_doc_{run_id}"
    marker = f"gamma-{run_id}"
    content = (
        f"Concurrency regression marker {marker}. "
        "The enterprise onboarding owner for this marker is Codex L3 Queue Sentinel. "
        "The recommended operational response is to keep API requests bounded, observe queue depth, "
        "and let Celery workers consume mail send tasks asynchronously."
    )
    details = upsert_enterprise_documents_hybrid(
        [
            EnterpriseDocument(
                doc_id=doc_id,
                source_type="google_drive",
                title=f"L3 concurrency regression note {run_id}",
                content=content,
                metadata={
                    "tenant_id": ACTOR["tenant_id"],
                    "user_id": ACTOR["user_id"],
                    "workspace_id": ACTOR["workspace_id"],
                    "source_type": "google_drive",
                    "business_domain": "regression",
                },
            )
        ],
        replace_existing=True,
        return_details=True,
        actor_context={**_actor_payload(), "session_id": f"seed_{run_id}"},
    )
    return {"doc_id": doc_id, "marker": marker, "details": details}


def _prepare_pending_mail(session_id: str, conversation_id: str, run_id: str) -> dict[str, Any]:
    message = (
        f"Send an email to {RECIPIENT}. "
        f"Body: L3 concurrency regression {run_id}: public queue-health smoke, no customer data, no credentials."
    )
    response = _chat(session_id, conversation_id, message)
    _assert(str(response.get("intent")) == "action_or_draft", f"mail prepare did not enter mail path: {response}")
    pending = dict(response.get("pending_confirmation") or {})
    _assert(bool(pending.get("mail_plan")), f"mail prepare did not create pending confirmation: {response}")
    return {
        "intent": response.get("intent"),
        "final_answer_source": response.get("final_answer_source"),
        "pending_confirmation_status": pending.get("status"),
        "confirmation_required": pending.get("confirmation_required"),
    }


def _poll_task(task_id: str, *, timeout_seconds: int = 240) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_task: dict[str, Any] = {}
    while time.time() < deadline:
        task = _request("GET", f"/tasks/{task_id}")
        last_task = task
        status = str(task.get("status") or "")
        if status in TASK_CONSUMED_STATUSES:
            return task
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for task {task_id}; last={last_task}")


def _verify_structured_permission_failure() -> dict[str, Any]:
    denied = _request(
        "POST",
        "/enterprise-rag/ingest",
        actor=READONLY_ACTOR,
        expect_error=True,
        json={
            "mode": "sample",
            "documents_path": "/app/documents.parquet",
            "questions_path": "/app/questions.parquet",
            "limit": 1,
            "reset": False,
            **_actor_payload(READONLY_ACTOR),
        },
    )
    detail = dict((denied.get("payload") or {}).get("detail") or {})
    decision = dict(detail.get("permission_decision") or {})
    _assert(denied.get("status_code") == 403, f"expected 403 permission failure, got: {denied}")
    _assert(detail.get("observation_type") == "permission_denied", f"missing structured observation: {denied}")
    _assert(decision.get("allowed") is False, f"permission decision should deny: {denied}")
    return {
        "status_code": denied.get("status_code"),
        "observation_type": detail.get("observation_type"),
        "permission_decision": decision,
    }


def main() -> int:
    run_id = uuid.uuid4().hex[:8]
    session_id = f"l3_concurrency_{run_id}"
    started = time.perf_counter()

    chat_conversation = _create_conversation(session_id, "L3 concurrency chat")
    rag_conversation = _create_conversation(session_id, "L3 concurrency RAG")
    mail_conversation = _create_conversation(session_id, "L3 concurrency mail")
    rag_seed = _seed_rag_doc(run_id)

    before_queue = _queue_health()
    before_metrics = _metrics_snapshot()
    pending_mail = _prepare_pending_mail(session_id, mail_conversation, run_id)

    marker = str(rag_seed["marker"])
    doc_id = str(rag_seed["doc_id"])

    def agent_chat_job(index: int) -> dict[str, Any]:
        response = _chat(
            session_id,
            chat_conversation,
            f"请简要说明你的功能是什么。这是 L3 并发 smoke #{index}，请基于系统能力概括。",
        )
        _assert(bool(response.get("answer")), f"agent chat returned empty answer: {response}")
        return {
            "intent": response.get("intent"),
            "routing_source": response.get("routing_source"),
            "queue_status": response.get("queue_status") or {},
        }

    def rag_job(index: int) -> dict[str, Any]:
        response = _request(
            "POST",
            "/enterprise-rag/query",
            json={
                "question": f"For concurrency marker {marker}, who is the enterprise onboarding owner?",
                "top_k": 8,
                "session_id": session_id,
                "conversation_id": rag_conversation,
                **_actor_payload(),
            },
        )
        supporting_doc_ids = [str(item) for item in response.get("supporting_doc_ids") or []]
        _assert(doc_id in supporting_doc_ids, f"seeded RAG doc not retrieved: {supporting_doc_ids}")
        return {
            "answer": response.get("answer", "")[:240],
            "supporting_doc_ids": supporting_doc_ids,
            "budget_profile": (response.get("retrieval_plan") or {}).get("budget_profile"),
            "expansion_triggered": (response.get("retrieval_stage_debug") or {}).get("expansion_triggered"),
            "queue_status": response.get("queue_status") or {},
        }

    def mail_draft_job() -> dict[str, Any]:
        response = _chat(
            session_id,
            mail_conversation,
            f"Polish body: L3 concurrency regression {run_id} passed the public queue-health smoke.",
        )
        _assert(str(response.get("intent")) == "action_or_draft", f"mail draft did not use mail intent: {response}")
        _assert(not response.get("task_id"), f"draft-only request unexpectedly created a task: {response}")
        return {
            "intent": response.get("intent"),
            "final_answer_source": response.get("final_answer_source"),
            "tool_observation_types": [
                str(item.get("observation_type") or "") for item in (response.get("tool_observations") or [])
            ],
        }

    def mail_confirm_job() -> dict[str, Any]:
        response = _chat(session_id, mail_conversation, "confirm send")
        task_id = str(response.get("task_id") or "")
        _assert(task_id, f"confirm send did not create task: {response}")
        return {
            "task_id": task_id,
            "task_status": response.get("task_status"),
            "task_risk_level": response.get("task_risk_level"),
            "queue_status": response.get("queue_status") or {},
        }

    jobs = {
        "agent_chat_1": lambda: agent_chat_job(1),
        "agent_chat_2": lambda: agent_chat_job(2),
        "enterprise_rag_1": lambda: rag_job(1),
        "enterprise_rag_2": lambda: rag_job(2),
        "mail_draft": mail_draft_job,
        "mail_confirm": mail_confirm_job,
        "queue_health_1": _queue_health,
        "queue_health_2": _queue_health,
        "prometheus_metrics_during": _metrics_snapshot,
    }

    concurrent_results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(_timed, name, fn): name for name, fn in jobs.items()}
        for future in as_completed(future_map):
            name = future_map[future]
            concurrent_results[name] = future.result()

    failed = {name: item for name, item in concurrent_results.items() if not item.get("ok")}
    _assert(not failed, f"concurrent jobs failed: {failed}")

    mail_confirm = dict(concurrent_results["mail_confirm"]["result"])
    task_id = str(mail_confirm.get("task_id") or "")
    final_task = _poll_task(task_id)
    final_status = str(final_task.get("status") or "")
    _assert(final_status in TASK_CONSUMED_STATUSES, f"mail worker/governance flow did not consume task: {final_task}")
    status_path = [str(item) for item in (final_task.get("status_path") or [])]
    _assert(
        any(item in status_path for item in ("processing", "pending_approval", "sent", "send_failed")),
        f"mail task does not show worker/governance progress: {final_task}",
    )

    after_queue = _queue_health()
    after_metrics = _metrics_snapshot()
    structured_failure = _verify_structured_permission_failure()

    output = {
        "ok": True,
        "base_url": BASE_URL,
        "session_id": session_id,
        "run_id": run_id,
        "max_workers": MAX_WORKERS,
        "total_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "rag_seed": rag_seed,
        "conversations": {
            "chat": chat_conversation,
            "rag": rag_conversation,
            "mail": mail_conversation,
        },
        "pending_mail": pending_mail,
        "concurrent_results": concurrent_results,
        "worker_consumption": {
            "task_id": task_id,
            "status": final_status,
            "delivery_status": final_task.get("delivery_status"),
            "delivery_error": final_task.get("delivery_error", ""),
            "risk_level": final_task.get("risk_level"),
            "approval_required": final_task.get("approval_required"),
            "risk_reasons": final_task.get("risk_reasons"),
            "status_path": final_task.get("status_path"),
        },
        "queue_health": {
            "before": before_queue,
            "after": after_queue,
        },
        "prometheus_metrics": {
            "before": before_metrics,
            "after": after_metrics,
        },
        "structured_failure_observation": structured_failure,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CONCURRENCY_REGRESSION_FAILED: {exc}", file=sys.stderr)
        raise
