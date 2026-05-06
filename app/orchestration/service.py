from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.orchestration.aggregator import aggregate_results
from app.orchestration.executor import execute_task_plan
from app.orchestration.planner import plan_message
from app.orchestration.tracing import render_task_plan
from app.orchestration.types import OrchestrationContext


def orchestrate_agent_request(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    safe_message: str,
    display_message: str = "",
    upload_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    plan = plan_message(message, upload_context or {})
    context = OrchestrationContext(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        safe_message=safe_message,
        display_message=display_message or message,
        upload_context=dict(upload_context or {}),
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
        "mode_used": "orchestration",
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
        "planner_type": plan.planner_type,
        "task_plan": render_task_plan(plan),
        "subtask_results": aggregated["subtask_results"],
        "aggregation_strategy": plan.aggregation_strategy,
        "partial_failures": aggregated["partial_failures"],
        "node_latencies_ms": {"total": latency_ms},
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }


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
