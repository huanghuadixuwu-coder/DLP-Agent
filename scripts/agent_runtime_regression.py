from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.orchestration.agent_verifier import verify_agent_answer
from app.orchestration.observations import AGENT_CHAT_CONTEXT_OBSERVATION_TYPES, make_typed_observation
from app.orchestration.trace_evaluator import evaluate_agent_trace
from app.resilience import make_failure_observation
from app.metrics import render_metrics


REQUIRED_OBSERVATION_KEYS = {
    "observation_type",
    "status",
    "source",
    "provenance",
    "confidence",
    "missing_fields",
    "constraints",
    "side_effects",
    "citations",
    "actor_context",
    "payload",
}


def main() -> None:
    actor = {"tenant_id": "tenant-a", "user_id": "user-a", "workspace_id": "workspace-a"}
    observation = make_typed_observation(
        observation_type="enterprise_rag_result",
        source="enterprise_rag_query",
        grounding_kind="retrieval",
        summary="Retrieved EnterpriseRAG evidence.",
        payload={"answer": "The evidence says use a pending state.", "missing_fields": []},
        citations=[{"doc_id": "doc-1", "title": "Meeting notes"}],
        confidence=0.91,
        actor_context=actor,
    )

    missing_keys = sorted(REQUIRED_OBSERVATION_KEYS - set(observation))
    memory_verdict = verify_agent_answer(
        question="What did the customer meeting recommend?",
        current_goal="knowledge_qa",
        observations=[
            make_typed_observation(
                observation_type="read_memory.workspace_memory",
                source="workspace_memory",
                grounding_kind="memory",
                summary="A prior note mentioned a customer meeting.",
                payload={"memory_boundary": "context_only"},
                confidence=0.7,
                actor_context=actor,
            )
        ],
        answer="会议建议客户在流程里处理这个状态。",
    )
    confirmation_verdict = verify_agent_answer(
        question="Send it now",
        current_goal="action_or_draft",
        observations=[
            make_typed_observation(
                observation_type="confirmation_required",
                source="react_controller",
                status="needs_confirmation",
                grounding_kind="guardrail",
                summary="Sending requires confirmation.",
                payload={"confirmation_required": True},
                side_effects=[{"kind": "mail_send", "allowed": False}],
                confidence=1.0,
                actor_context=actor,
            )
        ],
        answer="邮件已发送成功。",
        pending_confirmation={"tool_name": "send_email_smtp"},
    )
    trace_evaluation = evaluate_agent_trace(
        {
            "intent": "enterprise_rag_query",
            "required_grounding": "retrieval",
            "actor_context": actor,
            "tool_calls": [{"tool_name": "memory_search", "success": True, "status": "completed"}],
            "tool_observations": [
                make_typed_observation(
                    observation_type="read_memory.workspace_memory",
                    source="workspace_memory",
                    grounding_kind="memory",
                    summary="Memory-only context.",
                    payload={},
                    confidence=0.7,
                    actor_context=actor,
                )
            ],
            "memory_reads": [{"kind": "workspace_memory", "hits": 1}],
            "citations": [],
        },
        actor_context=actor,
    )
    failure_observation = make_failure_observation(
        service="llm",
        operation="final_renderer",
        error="timeout",
        fallback_strategy="deterministic_final_answer",
        retry_count=1,
        actor_context=actor,
    )
    context_observations = [
        make_typed_observation(
            observation_type=observation_type,
            source="agent_chat_context_regression",
            grounding_kind="state",
            summary=f"Context observation contract check: {observation_type}",
            payload={"thread_id": "thread-runtime", "brief_id": "brief-runtime"},
            provenance={"source": "agent_runtime_regression"},
            confidence=1.0,
            actor_context=actor,
            status="failed" if observation_type == "active_object_resolution_failed" else "completed",
            success=False if observation_type == "active_object_resolution_failed" else None,
        )
        for observation_type in sorted(AGENT_CHAT_CONTEXT_OBSERVATION_TYPES)
    ]
    context_contract_ok = all(
        not (REQUIRED_OBSERVATION_KEYS - set(item))
        and item.get("observation_type") in AGENT_CHAT_CONTEXT_OBSERVATION_TYPES
        and item.get("actor_context") == actor
        for item in context_observations
    )
    import app.orchestration.react_controller as react_controller

    react_initial_observation = make_typed_observation(
        observation_type="active_communication_thread",
        source="agent_runtime_regression",
        grounding_kind="state",
        summary="Initial contextual thread must be present before ReAct planning.",
        payload={"thread_id": "thread-runtime-live-state", "brief_id": ""},
        provenance={"source": "agent_runtime_regression"},
        confidence=1.0,
        actor_context=actor,
    )
    captured_react_state: dict[str, list[dict[str, object]]] = {}
    original_think_next_step = react_controller._think_next_step

    def _capture_initial_state(state: dict[str, object], _registry: dict[str, object]) -> dict[str, object]:
        captured_react_state["observations"] = list(state.get("observations") or [])
        captured_react_state["tool_observations"] = list(state.get("tool_observations") or [])
        return {
            "current_goal": "contextual_qa",
            "thought_summary": "Initial observations were available before planning.",
            "confidence": 0.99,
            "action_type": "direct_answer",
            "response_text": "Context was available before planning.",
            "tool_input": {},
        }

    try:
        react_controller._think_next_step = _capture_initial_state
        react_result = react_controller.run_react_agent_request(
            session_id="session-runtime-live-state",
            conversation_id="conversation-runtime-live-state",
            message="Use the active communication thread.",
            safe_message="Use the active communication thread.",
            display_message="Use the active communication thread.",
            router_intent="enterprise_fact",
            required_grounding="tool",
            recommended_tool="enterprise_rag_query",
            actor_context=actor,
            initial_observations=[react_initial_observation],
        )
    finally:
        react_controller._think_next_step = original_think_next_step

    react_initial_state_ok = (
        captured_react_state.get("observations", [{}])[0].get("observation_type") == "active_communication_thread"
        and captured_react_state.get("tool_observations", [{}])[0].get("observation_type") == "active_communication_thread"
        and (react_result.get("tool_observations") or [{}])[0].get("observation_type") == "active_communication_thread"
    )
    metrics_text = render_metrics().decode("utf-8", errors="ignore")

    result = {
        "ok": not missing_keys
        and context_contract_ok
        and react_initial_state_ok
        and memory_verdict.get("needs_rewrite")
        and confirmation_verdict.get("needs_rewrite")
        and not trace_evaluation.get("ok")
        and failure_observation.get("observation_type") == "dependency_failure"
        and "agent_dependency_failures_total" in metrics_text,
        "checks": {
            "typed_observation_contract": not missing_keys,
            "agent_chat_context_observation_contract": context_contract_ok,
            "react_initial_observations_live_before_planning": react_initial_state_ok,
            "memory_boundary_guard": memory_verdict.get("needs_rewrite"),
            "confirmation_guard": confirmation_verdict.get("needs_rewrite"),
            "trace_evaluator_flags_bad_trace": not trace_evaluation.get("ok"),
            "failure_observation_contract": failure_observation.get("observation_type") == "dependency_failure",
            "dependency_failure_metric": "agent_dependency_failures_total" in metrics_text,
        },
        "missing_observation_keys": missing_keys,
        "memory_verdict": memory_verdict,
        "confirmation_verdict": confirmation_verdict,
        "trace_evaluation": trace_evaluation,
        "failure_observation": failure_observation,
        "context_observation_types": [item.get("observation_type") for item in context_observations],
        "react_captured_initial_types": [
            item.get("observation_type") for item in captured_react_state.get("observations", [])
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
