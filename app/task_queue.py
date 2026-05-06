from __future__ import annotations

from celery import Celery

from app.config import get_settings


RISK_QUEUE = "dlp_risk_queue"
EMAIL_QUEUE = "email_send_queue"
MAIL_QUEUE = "mail_inbound_queue"

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
