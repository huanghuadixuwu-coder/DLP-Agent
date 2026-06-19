from __future__ import annotations

import json
from typing import Any

from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.config import get_settings


def _channel(task_id: str) -> str:
    return f"dlp_task_events:{task_id}"


def publish_task_event(payload: dict[str, Any]) -> None:
    settings = get_settings()
    with Redis.from_url(settings.redis_url, decode_responses=True) as client:
        client.publish(_channel(str(payload["task_id"])), json.dumps(payload, ensure_ascii=False))


async def task_event_iterator(task_id: str):
    settings = get_settings()
    client = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(_channel(task_id))
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not isinstance(data, str):
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
    finally:
        await pubsub.unsubscribe(_channel(task_id))
        await pubsub.close()
        await client.aclose()


def build_task_event(
    *,
    task_id: str,
    status: str,
    event_type: str,
    message: str,
    risk_level: str = "",
    delivery_error: str = "",
    delivery_status: str = "",
    thread_id: str = "",
    brief_id: str = "",
    communication_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    payload = {
        "task_id": task_id,
        "status": status,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "risk_level": risk_level,
        "delivery_error": delivery_error,
        "delivery_status": delivery_status,
    }
    if thread_id:
        payload["thread_id"] = thread_id
    if brief_id:
        payload["brief_id"] = brief_id
    if communication_context:
        payload["communication_context"] = dict(communication_context)
    return payload
