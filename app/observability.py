from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from app.config import get_settings


def configure_observability() -> None:
    settings = get_settings()
    os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "true" if settings.langsmith_tracing else "false"
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"

    provider = TracerProvider(resource=Resource.create({"service.name": "leetcode-rag-agent"}))
    trace.set_tracer_provider(provider)


@contextmanager
def timed_node(node_latencies_ms: dict[str, float], name: str):
    tracer = trace.get_tracer("leetcode-rag-agent")
    start = time.perf_counter()
    with tracer.start_as_current_span(name):
        yield
    node_latencies_ms[name] = round((time.perf_counter() - start) * 1000, 2)


def normalize_usage(payload: Any) -> tuple[int, int]:
    if payload is None:
        return (0, 0)

    if hasattr(payload, "usage_metadata") and payload.usage_metadata:
        usage = payload.usage_metadata
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    if hasattr(payload, "response_metadata") and payload.response_metadata:
        usage = payload.response_metadata.get("token_usage", {})
        return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))

    if isinstance(payload, dict):
        return int(payload.get("input_tokens", 0)), int(payload.get("output_tokens", 0))

    return (0, 0)


def estimate_cost(token_in: int, token_out: int) -> float:
    settings = get_settings()
    input_cost = (token_in / 1_000_000) * settings.model_input_cost_per_1m
    output_cost = (token_out / 1_000_000) * settings.model_output_cost_per_1m
    return round(input_cost + output_cost, 6)
