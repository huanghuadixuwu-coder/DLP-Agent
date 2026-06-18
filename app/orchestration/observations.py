from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.orchestration.types import TypedObservation


AGENT_CHAT_CONTEXT_OBSERVATION_TYPES = {
    "active_communication_thread",
    "communication_brief",
    "global_entry",
    "active_object_resolution_failed",
}


def make_typed_observation(
    *,
    observation_type: str,
    source: str,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    status: str = "completed",
    grounding_kind: str = "tool",
    provenance: dict[str, Any] | None = None,
    confidence: float = 0.0,
    missing_fields: list[str] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    side_effects: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    actor_context: dict[str, Any] | None = None,
    success: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    data = dict(payload or {})
    inferred_missing = missing_fields
    if inferred_missing is None:
        inferred_missing = [str(item) for item in list(data.get("missing_fields") or [])]

    inferred_constraints = constraints
    if inferred_constraints is None:
        inferred_constraints = []
        for key in ("constraints", "body_constraints", "source_policy", "permission_decision", "rate_limit_decision"):
            value = data.get(key)
            if isinstance(value, dict):
                inferred_constraints.append({"kind": key, **value})
            elif isinstance(value, list):
                inferred_constraints.extend(item for item in value if isinstance(item, dict))

    inferred_side_effects = side_effects
    if inferred_side_effects is None:
        inferred_side_effects = []
        if data.get("confirmation_required"):
            inferred_side_effects.append({"kind": "confirmation_required", "allowed": False})
        for key in ("side_effects", "writes_to"):
            value = data.get(key)
            if isinstance(value, list):
                inferred_side_effects.extend(
                    item if isinstance(item, dict) else {"kind": str(item)}
                    for item in value
                )

    observation = TypedObservation(
        observation_type=observation_type,
        status=status,
        source=source,
        grounding_kind=grounding_kind,
        summary=summary,
        payload=data,
        provenance=provenance or {"source": source},
        confidence=float(confidence or 0.0),
        missing_fields=list(inferred_missing or []),
        constraints=list(inferred_constraints or []),
        side_effects=list(inferred_side_effects or []),
        citations=list(citations or []),
        actor_context=dict(actor_context or data.get("actor_context") or {}),
    )
    result = asdict(observation)
    result.setdefault("kind", observation_type)
    result["success"] = (status not in {"failed", "error", "permission_denied", "rate_limited"}) if success is None else bool(success)
    result.update(extra)
    return result
