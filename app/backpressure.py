from __future__ import annotations

from dataclasses import asdict, dataclass
from time import time
from typing import Any

from redis import Redis

from app.actor_context import ActorContext, actor_from_mapping
from app.config import get_settings
from app.metrics import record_rate_limited


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    resource: str
    limit: int
    window_seconds: int
    remaining: int
    count: int = 0
    reason: str = "allowed"
    source: str = "redis"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


RESOURCE_LIMITS: dict[str, tuple[int, int]] = {
    "agent_chat": (60, 60),
    "enterprise_rag_query": (30, 60),
    "enterprise_rag_ingest": (3, 300),
    "enterprise_rag_benchmark": (3, 300),
    "mail_send": (10, 60),
    "llm": (120, 60),
    "reranker": (80, 60),
}


def _redis_client() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def check_rate_limit(actor: ActorContext | dict[str, Any], resource: str) -> RateLimitDecision:
    actor_obj = actor_from_mapping(actor) if isinstance(actor, dict) else actor
    limit, window_seconds = RESOURCE_LIMITS.get(resource, (60, 60))
    if "admin" in actor_obj.roles:
        return RateLimitDecision(
            allowed=True,
            resource=resource,
            limit=limit,
            window_seconds=window_seconds,
            remaining=limit,
            reason="admin_bypass",
            source="role",
        )
    bucket = int(time() // window_seconds)
    key = f"rate:{resource}:{actor_obj.tenant_id}:{actor_obj.user_id}:{bucket}"
    try:
        client = _redis_client()
        count = int(client.incr(key))
        if count == 1:
            client.expire(key, window_seconds + 2)
        remaining = max(limit - count, 0)
        if count > limit:
            record_rate_limited(resource)
            return RateLimitDecision(
                allowed=False,
                resource=resource,
                limit=limit,
                window_seconds=window_seconds,
                remaining=0,
                count=count,
                reason="limit_exceeded",
            )
        return RateLimitDecision(
            allowed=True,
            resource=resource,
            limit=limit,
            window_seconds=window_seconds,
            remaining=remaining,
            count=count,
        )
    except Exception as exc:
        # The guard should degrade open rather than take down local Docker demos.
        return RateLimitDecision(
            allowed=True,
            resource=resource,
            limit=limit,
            window_seconds=window_seconds,
            remaining=limit,
            reason=f"rate_limit_store_unavailable:{type(exc).__name__}",
            source="fallback_open",
        )
