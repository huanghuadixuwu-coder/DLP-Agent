from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from typing import Any
from uuid import uuid4

from app.metrics import record_dependency_blocked, record_multi_agent_plan_failure, record_side_effect_blocked
from app.orchestration.domain_agents import action_belongs_to_agent, resolve_domain_agent_for_action
from app.orchestration.observations import make_typed_observation
from app.orchestration.registry import build_tool_registry
from app.orchestration.tool_discovery import dispatch_tool_call
from app.orchestration.types import AgentSubtask, AgentTaskPlan, OrchestrationContext, ToolResult


def create_plan_blackboard(
    *,
    plan_id: str | None = None,
    correlation_id: str | None = None,
    conversation_id: str = "",
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "plan_id": plan_id or f"plan_{uuid4().hex[:12]}",
        "correlation_id": correlation_id or f"corr_{uuid4().hex[:12]}",
        "conversation_id": conversation_id,
        "actor_context": dict(actor_context or {}),
        "step_status": {},
        "observations": [],
        "created_resource_ids": [],
    }


def append_plan_observation(blackboard: dict[str, Any], task_id: str, observation: dict[str, Any]) -> dict[str, Any]:
    blackboard.setdefault("observations", []).append({"task_id": task_id, "observation": observation})
    blackboard.setdefault("step_status", {})[task_id] = observation.get("status") or "completed"
    payload = observation.get("payload") if isinstance(observation.get("payload"), dict) else {}
    for resource_id in list(payload.get("created_resource_ids") or []):
        blackboard.setdefault("created_resource_ids", []).append(resource_id)
    return blackboard


def execute_dag_plan(
    plan: AgentTaskPlan | dict[str, Any],
    context: OrchestrationContext,
    *,
    allow_side_effects: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    subtasks = _normalize_subtasks(plan)
    validation_errors = validate_dag_plan(subtasks)
    blackboard = create_plan_blackboard(
        conversation_id=context.conversation_id,
        actor_context=context.actor_context,
    )
    if validation_errors:
        for error in validation_errors:
            record_multi_agent_plan_failure(str(error.get("reason") or "validation_error"))
        observation = make_typed_observation(
            observation_type="dag_plan_validation",
            source="dag_executor",
            status="failed",
            grounding_kind="diagnostic",
            summary="DAG plan validation failed.",
            payload={"errors": validation_errors},
            provenance={"source": "dag_executor"},
            confidence=1.0,
            actor_context=context.actor_context,
            success=False,
        )
        append_plan_observation(blackboard, "validation", observation)
        return {"ok": False, "results": [], "observations": [observation], "blackboard": blackboard, "validation_errors": validation_errors}

    pending = {item.task_id: item for item in subtasks}
    results: dict[str, ToolResult] = {}
    observations: list[dict[str, Any]] = []

    while pending:
        ready = [item for item in pending.values() if all(dep in results for dep in item.dependencies)]
        if not ready:
            blocked = [(item, _dependency_blocked_observation(item, context, "unresolved_dependencies")) for item in pending.values()]
            for item, observation in blocked:
                record_dependency_blocked(item.agent, item.action or item.capability)
                observations.append(observation)
                append_plan_observation(blackboard, item.task_id, observation)
                results[item.task_id] = _tool_result_from_observation(item, observation)
                pending.pop(item.task_id, None)
            break

        executable: list[AgentSubtask] = []
        for item in ready:
            failed_deps = [dep for dep in item.dependencies if dep in results and not results[dep].success]
            if failed_deps:
                observation = _dependency_blocked_observation(item, context, "failed_dependencies", failed_deps)
                record_dependency_blocked(item.agent, item.action or item.capability)
                observations.append(observation)
                append_plan_observation(blackboard, item.task_id, observation)
                results[item.task_id] = _tool_result_from_observation(item, observation)
                pending.pop(item.task_id, None)
            else:
                executable.append(item)

        parallel_batch = [item for item in executable if item.parallelizable and _is_read_only(item)]
        serial_batch = [item for item in executable if item not in parallel_batch]

        if parallel_batch:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(parallel_batch))) as pool:
                future_to_task = {
                    pool.submit(_execute_one, item, context, results, allow_side_effects): item
                    for item in parallel_batch
                }
                for future, item in future_to_task.items():
                    observation = future.result()
                    observations.append(observation)
                    append_plan_observation(blackboard, item.task_id, observation)
                    results[item.task_id] = _tool_result_from_observation(item, observation)
                    pending.pop(item.task_id, None)

        for item in serial_batch:
            observation = _execute_one(item, context, results, allow_side_effects)
            observations.append(observation)
            append_plan_observation(blackboard, item.task_id, observation)
            results[item.task_id] = _tool_result_from_observation(item, observation)
            pending.pop(item.task_id, None)

    ordered_results = [results[item.task_id] for item in subtasks if item.task_id in results]
    return {
        "ok": all(result.success for result in ordered_results),
        "results": [asdict(result) for result in ordered_results],
        "observations": observations,
        "blackboard": blackboard,
        "validation_errors": [],
    }


def validate_dag_plan(subtasks: list[AgentSubtask]) -> list[dict[str, Any]]:
    registry = build_tool_registry()
    errors: list[dict[str, Any]] = []
    ids = [item.task_id for item in subtasks]
    if len(ids) != len(set(ids)):
        errors.append({"reason": "duplicate_task_id", "detail": "DAG plan contains duplicate task ids."})
    id_set = set(ids)
    for item in subtasks:
        action = item.action or item.capability
        if not item.task_id:
            errors.append({"reason": "missing_task_id", "detail": "Every DAG step requires task_id."})
        if not action or action not in registry:
            errors.append({"reason": "unknown_action", "task_id": item.task_id, "action": action})
        if item.parameters and not isinstance(item.parameters, dict):
            errors.append({"reason": "invalid_parameters", "task_id": item.task_id})
        for dep in item.dependencies:
            if dep not in id_set:
                errors.append({"reason": "unknown_dependency", "task_id": item.task_id, "dependency": dep})
        if item.agent and action in registry and not action_belongs_to_agent(action, item.agent):
            errors.append({"reason": "agent_action_mismatch", "task_id": item.task_id, "agent": item.agent, "action": action})
    errors.extend(_cycle_errors(subtasks))
    return errors


def _normalize_subtasks(plan: AgentTaskPlan | dict[str, Any]) -> list[AgentSubtask]:
    raw_subtasks = list(plan.subtasks if isinstance(plan, AgentTaskPlan) else plan.get("subtasks") or plan.get("steps") or [])
    registry = build_tool_registry()
    normalized: list[AgentSubtask] = []
    for raw in raw_subtasks:
        data = asdict(raw) if is_dataclass(raw) else dict(raw or {})
        action = str(data.get("action") or data.get("capability") or "").strip()
        capability = str(data.get("capability") or action).strip()
        definition = registry.get(action or capability)
        parameters = dict(data.get("parameters") or data.get("input") or {})
        agent = str(data.get("agent") or resolve_domain_agent_for_action(action or capability)).strip()
        normalized.append(
            AgentSubtask(
                task_id=str(data.get("task_id") or f"step_{len(normalized) + 1}"),
                capability=capability,
                action=action or capability,
                agent=agent,
                input=parameters,
                parameters=parameters,
                dependencies=[str(dep) for dep in list(data.get("dependencies") or []) if str(dep)],
                risk=str(data.get("risk") or "low"),
                confirmation_required=bool(data.get("confirmation_required", False) or getattr(definition, "requires_confirmation", False)),
                idempotency_key=str(data.get("idempotency_key") or ""),
                expected_observation_type=str(data.get("expected_observation_type") or getattr(definition, "returns_observation_type", "")),
                resource_scope=dict(data.get("resource_scope") or {}),
                mutating=bool(data.get("mutating", False) or getattr(definition, "mutating", False)),
                parallelizable=bool(data.get("parallelizable", getattr(definition, "parallelizable", True))),
                user_visible=bool(data.get("user_visible", True)),
                status=str(data.get("status") or "pending"),
            )
        )
    return normalized


def _cycle_errors(subtasks: list[AgentSubtask]) -> list[dict[str, Any]]:
    graph = {item.task_id: list(item.dependencies) for item in subtasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    errors: list[dict[str, Any]] = []

    def visit(node: str) -> None:
        if node in visiting:
            errors.append({"reason": "cycle_detected", "task_id": node})
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep)
        visiting.remove(node)
        visited.add(node)

    for task_id in graph:
        visit(task_id)
    return errors


def _execute_one(
    item: AgentSubtask,
    context: OrchestrationContext,
    prior_results: dict[str, ToolResult],
    allow_side_effects: bool,
) -> dict[str, Any]:
    registry = build_tool_registry()
    definition = registry[item.action or item.capability]
    if (definition.side_effectful or definition.requires_confirmation or item.confirmation_required) and not allow_side_effects:
        record_side_effect_blocked(item.agent, item.action or item.capability)
        return make_typed_observation(
            observation_type="confirmation_required",
            source="dag_executor",
            status="needs_confirmation",
            grounding_kind="guardrail",
            summary=f"Side-effectful step requires confirmation: {item.action or item.capability}",
            payload={
                **_communication_subordinate_metadata(item.agent, item.action or item.capability),
                "task_id": item.task_id,
                "agent": item.agent,
                "action": item.action or item.capability,
                "parameters": item.parameters,
                "risk": item.risk,
                "idempotency_key": item.idempotency_key,
                "confirmation_required": True,
            },
            provenance={"source": "dag_executor", "agent": item.agent, "action": item.action or item.capability},
            confidence=1.0,
            side_effects=[{"kind": "tool_call", "allowed": False, "reason": "requires_confirmation"}],
            actor_context=context.actor_context,
            success=True,
        )

    dependency_payloads = {
        dep_id: prior_results[dep_id].payload
        for dep_id in item.dependencies
        if dep_id in prior_results
    }
    dispatched = dispatch_tool_call(
        item.action or item.capability,
        item.parameters,
        context,
        dependency_payloads,
        allow_side_effects=allow_side_effects,
    )
    result = dict(dispatched.get("result") or {})
    status = str(result.get("status") or ("completed" if dispatched.get("ok") else "failed"))
    if status == "provider_not_configured":
        status = "unavailable"
    citations = list(result.get("citations") or [])
    if not citations and isinstance(result.get("evidence"), dict):
        citations = list(result["evidence"].get("citations") or [])
    communication_metadata = _communication_subordinate_metadata(item.agent, item.action or item.capability)
    return make_typed_observation(
        observation_type=str(dispatched.get("observation_type") or item.expected_observation_type or "tool_result"),
        source=item.action or item.capability,
        status=status,
        grounding_kind="tool",
        summary=str(result.get("summary") or result.get("message") or dispatched.get("error") or f"{item.action or item.capability} completed."),
        payload={
            **result,
            **communication_metadata,
            "task_id": item.task_id,
            "agent": item.agent,
            "action": item.action or item.capability,
            "idempotency_key": item.idempotency_key,
        },
        provenance={"source": item.action or item.capability, "agent": item.agent},
        confidence=0.88 if dispatched.get("ok") else 0.35,
        citations=citations,
        actor_context=context.actor_context,
        success=bool(dispatched.get("ok")),
    )


def _dependency_blocked_observation(
    item: AgentSubtask,
    context: OrchestrationContext,
    reason: str,
    failed_deps: list[str] | None = None,
) -> dict[str, Any]:
    return make_typed_observation(
        observation_type="dependency_blocked",
        source="dag_executor",
        status="blocked",
        grounding_kind="diagnostic",
        summary=f"Step blocked by dependency state: {reason}",
        payload={
            "task_id": item.task_id,
            "agent": item.agent,
            "action": item.action or item.capability,
            "reason": reason,
            "failed_dependencies": list(failed_deps or []),
        },
        provenance={"source": "dag_executor"},
        confidence=1.0,
        actor_context=context.actor_context,
        success=False,
    )


def _tool_result_from_observation(item: AgentSubtask, observation: dict[str, Any]) -> ToolResult:
    return ToolResult(
        task_id=item.task_id,
        capability=item.action or item.capability,
        success=bool(observation.get("success")) and str(observation.get("status") or "") == "completed",
        payload=dict(observation.get("payload") or {}),
        error=str((observation.get("payload") or {}).get("error") or ""),
        citations=list(observation.get("citations") or []),
        missing_evidence=False,
        status=str(observation.get("status") or "completed"),
    )


def _is_read_only(item: AgentSubtask) -> bool:
    definition = build_tool_registry().get(item.action or item.capability)
    return bool(definition and definition.read_only and not definition.side_effectful and not definition.requires_confirmation and not item.mutating)


def _communication_subordinate_metadata(agent: str, action: str) -> dict[str, str]:
    if agent == "enterprise_rag" or action.startswith("enterprise_"):
        return {
            "communication_role": "grounding_provider",
            "communication_input_kind": "grounding_bundle",
        }
    if agent == "meeting" or action.startswith("meeting_"):
        return {
            "communication_role": "escalation_provider",
            "communication_input_kind": "meeting_escalation_candidate",
        }
    return {}
