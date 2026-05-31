from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, TypeVar

from app.metrics import record_dependency_failure
from app.orchestration.observations import make_typed_observation


T = TypeVar("T")


@dataclass(slots=True)
class CircuitState:
    service: str
    state: str = "closed"
    failure_count: int = 0
    opened_at: float = 0.0
    last_error: str = ""
    threshold: int = 3
    cooldown_seconds: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_CIRCUITS: dict[str, CircuitState] = {}


def get_circuit_state(service: str) -> CircuitState:
    if service not in _CIRCUITS:
        _CIRCUITS[service] = CircuitState(service=service)
    state = _CIRCUITS[service]
    if state.state == "open" and (time.time() - state.opened_at) >= state.cooldown_seconds:
        state.state = "half_open"
    return state


def record_success(service: str) -> CircuitState:
    state = get_circuit_state(service)
    state.state = "closed"
    state.failure_count = 0
    state.last_error = ""
    state.opened_at = 0.0
    return state


def record_failure(service: str, error: str, *, threshold: int = 3, cooldown_seconds: float = 30.0) -> CircuitState:
    state = get_circuit_state(service)
    state.threshold = threshold
    state.cooldown_seconds = cooldown_seconds
    state.failure_count += 1
    state.last_error = error
    if state.failure_count >= threshold:
        state.state = "open"
        state.opened_at = time.time()
    return state


def is_circuit_open(service: str) -> bool:
    return get_circuit_state(service).state == "open"


def make_failure_observation(
    *,
    service: str,
    operation: str,
    error: str,
    fallback_strategy: str,
    retry_count: int = 0,
    actor_context: dict[str, Any] | None = None,
    severity: str = "medium",
) -> dict[str, Any]:
    record_dependency_failure(service, operation, fallback_strategy)
    circuit = get_circuit_state(service).to_dict()
    payload = {
        "service": service,
        "operation": operation,
        "error": error,
        "fallback_strategy": fallback_strategy,
        "retry_count": retry_count,
        "circuit_state": circuit,
        "severity": severity,
    }
    return make_typed_observation(
        observation_type="dependency_failure",
        source=service,
        status="degraded",
        grounding_kind="guardrail",
        summary=f"{service}.{operation} failed; fallback={fallback_strategy}.",
        payload=payload,
        provenance={"source": service, "operation": operation},
        confidence=0.95,
        actor_context=actor_context,
    )


def run_with_retry(
    operation: Callable[[], T],
    *,
    service: str,
    operation_name: str,
    attempts: int = 2,
    retry_delay_seconds: float = 0.1,
    circuit_threshold: int = 3,
    cooldown_seconds: float = 30.0,
) -> tuple[bool, T | None, Exception | None, int]:
    if is_circuit_open(service):
        return False, None, RuntimeError(f"circuit_open:{service}"), 0
    last_exc: Exception | None = None
    total_attempts = max(1, int(attempts))
    for index in range(total_attempts):
        try:
            value = operation()
            record_success(service)
            return True, value, None, index
        except Exception as exc:  # pragma: no cover - exercised by integration paths.
            last_exc = exc
            record_failure(service, str(exc), threshold=circuit_threshold, cooldown_seconds=cooldown_seconds)
            if index < total_attempts - 1 and retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
    return False, None, last_exc, total_attempts - 1
