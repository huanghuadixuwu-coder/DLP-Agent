from __future__ import annotations

from celery import Celery
from celery.result import AsyncResult
from redis import Redis

from app.config import get_settings


RISK_QUEUE = "dlp_risk_queue"
EMAIL_QUEUE = "email_send_queue"
MAIL_QUEUE = "mail_inbound_queue"
ENTERPRISE_QUEUE = "enterprise_rag_queue"

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
    },
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
)


def enqueue_dlp_risk_task(task_id: str) -> str:
    celery_app.send_task("app.task_worker.process_dlp_outbound_task", args=[task_id], queue=RISK_QUEUE)
    return task_id


def enqueue_email_send_task(task_id: str) -> str:
    celery_app.send_task("app.task_worker.send_dlp_email_task", args=[task_id], queue=EMAIL_QUEUE)
    return task_id


def enqueue_inbound_mail_sync() -> str:
    result = celery_app.send_task("app.task_worker.sync_inbound_mail_task", queue=MAIL_QUEUE)
    return str(result.id)


def enqueue_daily_mail_digest() -> str:
    result = celery_app.send_task("app.task_worker.generate_daily_mail_digest_task", queue=MAIL_QUEUE)
    return str(result.id)


def enqueue_enterprise_ingest(payload: dict) -> str:
    result = celery_app.send_task("app.task_worker.enterprise_rag_ingest_task", args=[payload], queue=ENTERPRISE_QUEUE)
    return str(result.id)


def enqueue_enterprise_benchmark(payload: dict) -> str:
    result = celery_app.send_task("app.task_worker.enterprise_rag_benchmark_task", args=[payload], queue=ENTERPRISE_QUEUE)
    return str(result.id)


def get_async_task_status(task_id: str) -> dict:
    result = AsyncResult(task_id, app=celery_app)
    payload: dict = {
        "task_id": task_id,
        "state": str(result.state or "PENDING"),
        "ready": bool(result.ready()),
        "successful": bool(result.successful()) if result.ready() else False,
        "failed": bool(result.failed()) if result.ready() else False,
    }
    if result.ready():
        try:
            value = result.get(propagate=False, timeout=0)
            payload["result"] = value if not isinstance(value, BaseException) else ""
            payload["error"] = str(value) if isinstance(value, BaseException) else ""
        except Exception as exc:
            payload["result"] = ""
            payload["error"] = str(exc)
    else:
        info = result.info
        payload["info"] = info if isinstance(info, dict) else str(info or "")
    return payload


def get_queue_health() -> dict:
    queue_names = [RISK_QUEUE, EMAIL_QUEUE, MAIL_QUEUE, ENTERPRISE_QUEUE]
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        backlog = {queue_name: int(client.llen(queue_name)) for queue_name in queue_names}
        return {"ok": True, "queues": backlog, "queue_count": len(backlog), "error": ""}
    except Exception as exc:
        return {
            "ok": False,
            "queues": {queue_name: 0 for queue_name in queue_names},
            "queue_count": len(queue_names),
            "error": str(exc),
        }
