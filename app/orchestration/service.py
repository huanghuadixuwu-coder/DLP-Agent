from __future__ import annotations

from dataclasses import asdict
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.conversation_memory import is_memory_follow_up
from app.orchestration.aggregator import aggregate_results
from app.orchestration.executor import execute_task_plan
from app.orchestration.planner import plan_message
from app.orchestration.react_controller import run_react_agent_request
from app.orchestration.tracing import render_task_plan
from app.orchestration.types import OrchestrationContext

logger = logging.getLogger(__name__)


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
) -> dict[str, Any]:
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
    )
    if dag_result is not None:
        return dag_result

    try:
        return run_react_agent_request(
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
    except Exception:
        logger.exception("ReAct controller failed; evaluating legacy fallback")
        settings = get_settings()
        if is_memory_follow_up(safe_message or message) or not settings.enable_legacy_orchestration_fallback:
            raise
        logger.warning(
            "Compatibility shim active: legacy orchestration fallback is serving a ReAct failure. "
            "Replacement owner: Supervisor + ReAct controller + multi-agent DAG typed recovery. "
            "Removal trigger: delete after typed recovery observations replace this fallback and "
            "agent/mail/rag Docker regressions pass."
        )
        return _legacy_orchestrate_agent_request(
            session_id=session_id,
            conversation_id=conversation_id,
            message=message,
            safe_message=safe_message,
            display_message=display_message,
            upload_context=upload_context,
            actor_context=actor_context,
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
    observations = list(dag_result.get("observations") or [])
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


def _legacy_orchestrate_agent_request(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    safe_message: str,
    display_message: str = "",
    upload_context: dict[str, Any] | None = None,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Compatibility shim: replacement owner is Supervisor final rendering over
    # ReAct/DAG typed observations. Remove with
    # ENABLE_LEGACY_ORCHESTRATION_FALLBACK after failure recovery no longer
    # needs legacy planning/execution/aggregation.
    started = perf_counter()
    plan = plan_message(message, upload_context or {})
    context = OrchestrationContext(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        safe_message=safe_message,
        display_message=display_message or message,
        upload_context=dict(upload_context or {}),
        actor_context=dict(actor_context or {}),
    )
    results = execute_task_plan(plan, context)
    aggregated = aggregate_results(plan, results)
    latency_ms = (perf_counter() - started) * 1000.0
    intent = _derive_intent(plan)
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid4()),
        "message": message,
        "safe_message": safe_message,
        "display_message": display_message or message,
        "answer": aggregated["answer"],
        "intent": intent,
        "routing_source": "planner",
        "routing_confidence": float(plan.confidence),
        "routing_reason": plan.planner_reason,
        "candidate_intents": sorted({item.capability for item in plan.subtasks}),
        "mode_used": "legacy_orchestration",
        "tool_calls": [
            {
                "tool_name": item.capability,
                "success": item.success,
                "status": item.status,
                "error": item.error,
                "result": item.payload,
            }
            for item in results
        ],
        "retrieved_evidence": aggregated["retrieved_evidence"],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": aggregated["citations"],
        "memory_context": aggregated.get("memory_context") or {},
        "memory_hits": int((aggregated.get("memory_context") or {}).get("memory_retrieval_hits", 0)),
        "merged_memory_hits": 0,
        "context_sources": aggregated.get("context_sources") or [],
        "workspace_memory_hits": int(aggregated.get("workspace_memory_hits", 0)),
        "transcript_hits": int(aggregated.get("transcript_hits", 0)),
        "user_model_used": bool(aggregated.get("user_model_used", False)),
        "reflection_notes": None,
        "upload_context": aggregated.get("upload_context") or dict(upload_context or {}),
        "actor_context": dict(actor_context or {}),
        "route_mode": "slow",
        "router_intent": "",
        "router_reason": "",
        "required_grounding": "none",
        "fast_path_used": False,
        "degraded_from": "none",
        "planner_type": plan.planner_type,
        "task_plan": render_task_plan(plan),
        "subtask_results": aggregated["subtask_results"],
        "aggregation_strategy": plan.aggregation_strategy,
        "partial_failures": aggregated["partial_failures"],
        "react_trace": [],
        "loop_step_count": 0,
        "termination_reason": "",
        "pending_confirmation": {},
        "confirmation_payload": {},
        "final_answer_source": "legacy_aggregator",
        "memory_reads": [],
        "tool_observations": [],
        "node_latencies_ms": {"total": latency_ms},
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
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


def _derive_intent(plan) -> str:
    capabilities = {item.capability for item in plan.subtasks}
    if len(capabilities) > 1:
        return "compound_enterprise_assistant"
    if "enterprise_rag_query" in capabilities:
        return "enterprise_rag_query"
    if "inbound_reply_draft" in capabilities:
        return "inbound_mail_assistant"
    if "inbound_mail_summary" in capabilities:
        return "inbound_mail_assistant"
    if "outbound_mail_summary" in capabilities:
        return "outbound_mail_assistant"
    if "persona_or_chitchat" in capabilities:
        return "persona_or_chitchat"
    if "uploaded_content_analyze" in capabilities:
        return "uploaded_content_analyze"
    if "unsupported_capability" in capabilities:
        return "unsupported_capability"
    return "orchestrated_agent"
