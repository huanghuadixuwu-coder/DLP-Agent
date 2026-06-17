from __future__ import annotations

import json

from celery import Celery
from redis import Redis

from app.config import get_settings
from app.resilience import make_failure_observation, run_with_retry


RISK_QUEUE = "dlp_risk_queue"
EMAIL_QUEUE = "email_send_queue"
MAIL_QUEUE = "mail_inbound_queue"
ENTERPRISE_QUEUE = "enterprise_rag_queue"
MEETING_QUEUE = "meeting_queue"
MEMORY_QUEUE = "memory_write_queue"

settings = get_settings()

celery_app = Celery(
    "dlp_agent_tasks",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.task_worker"],
)
celery_app.conf.update(
    task_default_queue=RISK_QUEUE,
    task_routes={
        "app.task_worker.process_dlp_outbound_task": {"queue": RISK_QUEUE},
        "app.task_worker.send_dlp_email_task": {"queue": EMAIL_QUEUE},
        "app.task_worker.sync_inbound_mail_task": {"queue": MAIL_QUEUE},
        "app.task_worker.generate_daily_mail_digest_task": {"queue": MAIL_QUEUE},
        "app.task_worker.enterprise_rag_ingest_task": {"queue": ENTERPRISE_QUEUE},
        "app.task_worker.enterprise_rag_benchmark_task": {"queue": ENTERPRISE_QUEUE},
        "app.task_worker.enterprise_rag_query_task": {"queue": ENTERPRISE_QUEUE},
        "app.task_worker.process_domain_meeting_task": {"queue": MEETING_QUEUE},
        "app.task_worker.write_conversation_memory_summary_task": {"queue": MEMORY_QUEUE},
    },
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
)


def enqueue_dlp_risk_task(task_id: str) -> str:
    _send_task_with_resilience("app.task_worker.process_dlp_outbound_task", args=[task_id], queue=RISK_QUEUE)
    return task_id


def enqueue_email_send_task(task_id: str) -> str:
    _send_task_with_resilience("app.task_worker.send_dlp_email_task", args=[task_id], queue=EMAIL_QUEUE)
    return task_id


def enqueue_inbound_mail_sync() -> str:
    result = _send_task_with_resilience("app.task_worker.sync_inbound_mail_task", queue=MAIL_QUEUE)
    return str(result.id)


def enqueue_daily_mail_digest(actor_context: dict | None = None) -> str:
    result = _send_task_with_resilience(
        "app.task_worker.generate_daily_mail_digest_task",
        args=[actor_context or {}],
        queue=MAIL_QUEUE,
    )
    return str(result.id)


def enqueue_enterprise_ingest(payload: dict) -> str:
    result = _send_task_with_resilience("app.task_worker.enterprise_rag_ingest_task", args=[payload], queue=ENTERPRISE_QUEUE)
    return str(result.id)


def enqueue_enterprise_benchmark(payload: dict) -> str:
    result = _send_task_with_resilience("app.task_worker.enterprise_rag_benchmark_task", args=[payload], queue=ENTERPRISE_QUEUE)
    return str(result.id)


def enqueue_enterprise_query(payload: dict) -> str:
    result = _send_task_with_resilience("app.task_worker.enterprise_rag_query_task", args=[payload], queue=ENTERPRISE_QUEUE)
    task_id = str(result.id)
    _record_enterprise_task_submission(task_id, task_kind="query", payload=payload)
    return task_id


def get_enterprise_task_status(task_id: str) -> dict:
    result = celery_app.AsyncResult(task_id)
    state = str(result.state or "PENDING").lower()
    progress = dict(result.info or {}) if isinstance(result.info, dict) else {}
    response = {
        "task_id": task_id,
        "task_kind": "",
        "state": state,
        "ready": bool(result.ready()),
        "successful": bool(result.successful()) if result.ready() else False,
        "progress": progress if state not in {"success", "failure"} else {},
        "result": dict(result.result or {}) if state == "success" and isinstance(result.result, dict) else {},
        "error": str(result.result) if state == "failure" else "",
        "actor_context": {},
        "correlation_id": "",
    }
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        raw = client.get(f"enterprise_rag_task:{task_id}")
        metadata = json.loads(raw) if raw else {}
        response["task_kind"] = str(metadata.get("task_kind") or "")
        response["actor_context"] = dict(metadata.get("actor_context") or {})
        response["correlation_id"] = str(metadata.get("correlation_id") or "")
    except Exception as exc:
        response["metadata_error"] = str(exc)
    return response


def enqueue_meeting_task(task_id: str) -> str:
    _send_task_with_resilience("app.task_worker.process_domain_meeting_task", args=[task_id], queue=MEETING_QUEUE)
    return task_id


def enqueue_conversation_memory_summary(payload: dict) -> str:
    result = _send_task_with_resilience(
        "app.task_worker.write_conversation_memory_summary_task",
        args=[payload],
        queue=MEMORY_QUEUE,
    )
    return str(result.id)


def get_queue_health() -> dict:
    queue_names = [RISK_QUEUE, EMAIL_QUEUE, MAIL_QUEUE, ENTERPRISE_QUEUE, MEETING_QUEUE, MEMORY_QUEUE]
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        backlog = {queue_name: int(client.llen(queue_name)) for queue_name in queue_names}
        return {"ok": True, "queues": backlog, "queue_count": len(backlog), "error": ""}
    except Exception as exc:
        observation = make_failure_observation(
            service="redis",
            operation="queue_health",
            error=str(exc),
            fallback_strategy="return_zero_backlog_and_degraded_queue_health",
            retry_count=0,
        )
        return {
            "ok": False,
            "queues": {queue_name: 0 for queue_name in queue_names},
            "queue_count": len(queue_names),
            "error": str(exc),
            "failure_observation": observation,
        }


def _send_task_with_resilience(task_name: str, *, queue: str, args: list | None = None):
    ok, result, exc, retry_count = run_with_retry(
        lambda: celery_app.send_task(task_name, args=args or [], queue=queue),
        service="celery",
        operation_name=f"send_task:{task_name}",
        attempts=2,
        retry_delay_seconds=0.2,
    )
    if ok and result is not None:
        return result
    observation = make_failure_observation(
        service="celery",
        operation=f"send_task:{task_name}",
        error=str(exc or "unknown celery enqueue failure"),
        fallback_strategy="raise_structured_enqueue_failure",
        retry_count=retry_count,
    )
    raise RuntimeError(observation["payload"]["error"])


def _record_enterprise_task_submission(task_id: str, *, task_kind: str, payload: dict) -> None:
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        client.setex(
            f"enterprise_rag_task:{task_id}",
            86400,
            json.dumps(
                {
                    "task_kind": task_kind,
                    "actor_context": dict(payload.get("actor_context") or {}),
                    "correlation_id": str(payload.get("correlation_id") or ""),
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        # Task execution remains valid if progress metadata is temporarily unavailable.
        pass
