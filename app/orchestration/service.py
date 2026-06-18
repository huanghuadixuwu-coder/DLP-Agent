from __future__ import annotations

import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.orchestration.final_renderer import render_final_answer
from app.orchestration.react_controller import run_react_agent_request
from app.orchestration.types import OrchestrationContext
from app.resilience import make_failure_observation

logger = logging.getLogger(__name__)


def _prepend_initial_observations(
    result: dict[str, Any],
    initial_observations: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    observations = [dict(item) for item in list(initial_observations or []) if isinstance(item, dict)]
    if not observations:
        return result
    payload = dict(result or {})
    existing = list(payload.get("tool_observations") or payload.get("observations") or [])
    payload["tool_observations"] = [*observations, *existing]
    task_plan = dict(payload.get("task_plan") or {})
    task_plan_observations = list(task_plan.get("tool_observations") or [])
    task_plan["tool_observations"] = [*observations, *task_plan_observations]
    task_plan["agent_chat_context"] = {
        "observation_types": [str(item.get("observation_type") or "") for item in observations],
    }
    payload["task_plan"] = task_plan
    return payload


def orchestrate_agent_request(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    safe_message: str,
    display_message: str = "",
    upload_context: dict[str, Any] | None = None,
    router_intent: str = "",
    required_grounding: str = "none",
    recommended_tool: str = "",
    router_reason: str = "",
    degraded_from: str = "none",
    actor_context: dict[str, Any] | None = None,
    initial_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context_observations = [dict(item) for item in list(initial_observations or []) if isinstance(item, dict)]
    dag_result = _try_multi_agent_dag_request(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        safe_message=safe_message,
        display_message=display_message,
        upload_context=upload_context,
        router_intent=router_intent,
        required_grounding=required_grounding,
        recommended_tool=recommended_tool,
        router_reason=router_reason,
        degraded_from=degraded_from,
        actor_context=actor_context,
        initial_observations=context_observations,
    )
    if dag_result is not None:
        return dag_result

    try:
        result = run_react_agent_request(
            session_id=session_id,
            conversation_id=conversation_id,
            message=message,
            safe_message=safe_message,
            display_message=display_message,
            upload_context=upload_context,
            router_intent=router_intent,
            required_grounding=required_grounding,
            recommended_tool=recommended_tool,
            router_reason=router_reason,
            degraded_from=degraded_from,
            actor_context=actor_context,
        )
        return _prepend_initial_observations(result, context_observations)
    except Exception as exc:
        logger.exception("ReAct controller failed; returning typed orchestration recovery")
        return _recover_react_controller_failure(
            session_id=session_id,
            conversation_id=conversation_id,
            message=message,
            safe_message=safe_message,
            display_message=display_message,
            upload_context=upload_context,
            router_intent=router_intent,
            required_grounding=required_grounding,
            recommended_tool=recommended_tool,
            router_reason=router_reason,
            degraded_from=degraded_from,
            actor_context=actor_context,
            initial_observations=context_observations,
            error=exc,
        )


def _try_multi_agent_dag_request(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    safe_message: str,
    display_message: str = "",
    upload_context: dict[str, Any] | None = None,
    router_intent: str = "",
    required_grounding: str = "none",
    recommended_tool: str = "",
    router_reason: str = "",
    degraded_from: str = "none",
    actor_context: dict[str, Any] | None = None,
    initial_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    from app.orchestration.dag_executor import execute_dag_plan
    from app.orchestration.final_renderer import render_final_answer
    from app.orchestration.multi_agent_planner import plan_multi_agent_dag_request, render_dag_plan

    plan = plan_multi_agent_dag_request(message=safe_message or message, actor_context=actor_context)
    if plan is None:
        return None

    started = perf_counter()
    context = OrchestrationContext(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        safe_message=safe_message,
        display_message=display_message or message,
        upload_context=dict(upload_context or {}),
        actor_context=dict(actor_context or {}),
    )
    dag_result = execute_dag_plan(plan, context, allow_side_effects=False)
    observations = [
        *[dict(item) for item in list(initial_observations or []) if isinstance(item, dict)],
        *list(dag_result.get("observations") or []),
    ]
    pending_confirmation = _pending_confirmation_from_observations(observations, plan)
    rendered = render_final_answer(
        question=display_message or message,
        current_goal="multi_agent_dag",
        observations=observations,
        working_memory=[],
        conservative=bool(pending_confirmation),
        pending_confirmation=pending_confirmation,
        actor_context=actor_context,
    )
    if rendered.get("failure_observation"):
        observations.append(dict(rendered["failure_observation"]))

    latency_ms = (perf_counter() - started) * 1000.0
    tool_calls = _tool_calls_from_observations(observations)
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid4()),
        "message": message,
        "safe_message": safe_message,
        "display_message": display_message or message,
        "answer": str(rendered.get("answer") or ""),
        "intent": "multi_agent_dag",
        "routing_source": "multi_agent_dag_planner",
        "routing_confidence": float(plan.confidence),
        "routing_reason": plan.planner_reason,
        "candidate_intents": ["calendar_agent", "meeting_agent", "multi_agent_dag"],
        "mode_used": "multi_agent_dag",
        "tool_calls": tool_calls,
        "retrieved_evidence": [],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": _citations_from_observations(observations),
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "context_sources": [],
        "workspace_memory_hits": 0,
        "transcript_hits": 0,
        "user_model_used": False,
        "reflection_notes": None,
        "upload_context": dict(upload_context or {}),
        "actor_context": dict(actor_context or {}),
        "route_mode": "slow",
        "router_intent": router_intent,
        "router_reason": router_reason,
        "required_grounding": required_grounding,
        "fast_path_used": False,
        "degraded_from": degraded_from,
        "recommended_tool": recommended_tool,
        "planner_type": plan.planner_type,
        "task_plan": {
            **render_dag_plan(plan),
            "dag_executor": {
                "ok": bool(dag_result.get("ok")),
                "validation_errors": list(dag_result.get("validation_errors") or []),
                "blackboard": dict(dag_result.get("blackboard") or {}),
            },
            "tool_observations": observations,
        },
        "subtask_results": list(dag_result.get("results") or []),
        "aggregation_strategy": plan.aggregation_strategy,
        "partial_failures": [
            item
            for item in tool_calls
            if not bool(item.get("success")) and str(item.get("status") or "") != "needs_confirmation"
        ],
        "react_trace": [],
        "loop_step_count": 0,
        "termination_reason": "needs_confirmation" if pending_confirmation else "direct_answer",
        "pending_confirmation": pending_confirmation,
        "confirmation_payload": pending_confirmation,
        "final_answer_source": "multi_agent_dag_renderer",
        "memory_reads": [],
        "tool_observations": observations,
        "node_latencies_ms": {"total": latency_ms, "dag_executor": latency_ms},
        "token_in": int(rendered.get("token_in", 0)),
        "token_out": int(rendered.get("token_out", 0)),
        "estimated_cost": float(rendered.get("estimated_cost", 0.0)),
    }


def _recover_react_controller_failure(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    safe_message: str,
    display_message: str = "",
    upload_context: dict[str, Any] | None = None,
    router_intent: str = "",
    required_grounding: str = "none",
    recommended_tool: str = "",
    router_reason: str = "",
    degraded_from: str = "none",
    actor_context: dict[str, Any] | None = None,
    initial_observations: list[dict[str, Any]] | None = None,
    error: Exception,
) -> dict[str, Any]:
    started = perf_counter()
    failure_observation = make_failure_observation(
        service="react_controller",
        operation="orchestrate_agent_request",
        error=f"{type(error).__name__}: {error}",
        fallback_strategy="typed_recovery_final_renderer",
        retry_count=0,
        actor_context=actor_context,
        severity="high",
        retryable=True,
    )
    failure_observation["summary"] = "The agent orchestration path failed; a recovery response was returned."
    failure_observation["success"] = False
    observations = [
        *[dict(item) for item in list(initial_observations or []) if isinstance(item, dict)],
        failure_observation,
    ]
    rendered = render_final_answer(
        question=display_message or message,
        current_goal=router_intent or "orchestration_recovery",
        observations=observations,
        working_memory=[],
        conservative=True,
        pending_confirmation={},
        actor_context=actor_context,
    )
    if rendered.get("failure_observation"):
        observations.append(dict(rendered["failure_observation"]))
    latency_ms = (perf_counter() - started) * 1000.0
    tool_calls = _tool_calls_from_observations(observations)
    partial_failures = [
        item
        for item in tool_calls
        if not bool(item.get("success")) and str(item.get("status") or "") != "needs_confirmation"
    ]
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid4()),
        "message": message,
        "safe_message": safe_message,
        "display_message": display_message or message,
        "answer": str(rendered.get("answer") or ""),
        "intent": router_intent or "orchestration_recovery",
        "routing_source": "react_controller_recovery",
        "routing_confidence": 0.0,
        "routing_reason": router_reason or "ReAct controller failed and returned typed recovery observation.",
        "candidate_intents": [router_intent] if router_intent else ["orchestration_recovery"],
        "mode_used": "react_recovery",
        "tool_calls": tool_calls,
        "retrieved_evidence": [],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": _citations_from_observations(observations),
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "context_sources": [],
        "workspace_memory_hits": 0,
        "transcript_hits": 0,
        "user_model_used": False,
        "reflection_notes": None,
        "upload_context": dict(upload_context or {}),
        "actor_context": dict(actor_context or {}),
        "route_mode": "slow",
        "router_intent": router_intent,
        "router_reason": router_reason,
        "required_grounding": required_grounding,
        "fast_path_used": False,
        "degraded_from": degraded_from or "react_controller",
        "recommended_tool": recommended_tool,
        "planner_type": "react_controller",
        "task_plan": {
            "planner_type": "react_controller",
            "current_goal": router_intent or "orchestration_recovery",
            "termination_reason": "controller_recovery",
            "final_answer_source": "orchestration_recovery_renderer",
            "tool_observations": observations,
            "verifier_verdict": dict(rendered.get("verifier_verdict") or {}),
            "verifier_rewrite_applied": bool(rendered.get("verifier_rewrite_applied", False)),
        },
        "subtask_results": tool_calls,
        "aggregation_strategy": "typed_recovery",
        "partial_failures": partial_failures,
        "react_trace": [],
        "loop_step_count": 0,
        "termination_reason": "controller_recovery",
        "pending_confirmation": {},
        "confirmation_payload": {},
        "final_answer_source": "orchestration_recovery_renderer",
        "memory_reads": [],
        "tool_observations": observations,
        "node_latencies_ms": {"total": latency_ms},
        "token_in": int(rendered.get("token_in", 0)),
        "token_out": int(rendered.get("token_out", 0)),
        "estimated_cost": float(rendered.get("estimated_cost", 0.0)),
    }


def _pending_confirmation_from_observations(observations: list[dict[str, Any]], plan) -> dict[str, Any]:
    from app.orchestration.multi_agent_planner import render_dag_plan

    for item in observations:
        if str(item.get("observation_type") or "") != "confirmation_required":
            continue
        payload = dict(item.get("payload") or {})
        action = str(payload.get("action") or "")
        return {
            "action_name": action,
            "title": "Confirm domain-agent action",
            "message": "This operation changes external resources and requires explicit confirmation.",
            "tool_name": action,
            "tool_input": dict(payload.get("parameters") or {}),
            "dag_plan": render_dag_plan(plan),
            "idempotency_key": str(payload.get("idempotency_key") or ""),
            "risk": str(payload.get("risk") or "medium"),
            "confirmation_required": True,
        }
    return {}


def _tool_calls_from_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in observations:
        if not isinstance(item, dict) or str(item.get("observation_type") or "") == "agent_trace_evaluation":
            continue
        payload = dict(item.get("payload") or {})
        calls.append(
            {
                "tool_name": str(payload.get("action") or item.get("source") or ""),
                "success": bool(item.get("success")),
                "status": str(item.get("status") or ""),
                "error": str(payload.get("error") or ""),
                "result": payload,
            }
        )
    return calls


def _citations_from_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in observations:
        for citation in list(item.get("citations") or []):
            if not isinstance(citation, dict):
                continue
            key = str(citation.get("doc_id") or citation.get("id") or citation)
            if key in seen:
                continue
            seen.add(key)
            citations.append(citation)
    return citations
