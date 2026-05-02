from __future__ import annotations

import json
import time
import uuid
from functools import lru_cache
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.conversation_memory import build_memory_context
from app.graph import get_llm
from app.hybrid_router import route_intent
from app.metrics import record_reflection
from app.observability import estimate_cost, normalize_usage, timed_node
from app.unified_tools import (
    ToolExecution,
    run_budget_context_tool,
    run_disambiguate_entity_tool,
    run_framework_compare_tool,
    run_leetcode_rag_tool,
    run_privacy_scan_tool,
    run_reminder_tool,
)


Intent = Literal[
    "long_document_budget",
    "privacy_alert",
    "entity_disambiguation",
    "framework_opinion",
    "reminder_assistant",
    "leetcode_rag",
    "general_agent_question",
]


class UnifiedAgentState(TypedDict, total=False):
    request_id: str
    session_id: str
    message: str
    safe_message: str
    selected_mode: str
    mode_used: str
    problem_id: str | None
    conversation_id: str | None
    show_steps: bool
    intent: Intent
    routing_source: str
    routing_confidence: float
    routing_reason: str
    candidate_intents: list[str]
    planned_tools: list[str]
    tool_calls: list[dict[str, Any]]
    tool_results: dict[str, Any]
    retrieved_evidence: list[dict[str, Any]]
    memory_context: dict[str, Any]
    memory_hits: int
    merged_memory_hits: int
    answer: str
    draft_answer: str
    reflection_notes: str | None
    needs_clarification: bool
    clarification_question: str | None
    privacy: dict[str, Any]
    context_budget: dict[str, Any]
    citations: list[dict[str, Any]]
    token_in: int
    token_out: int
    estimated_cost: float
    node_latencies_ms: dict[str, float]


def _add_usage(state: UnifiedAgentState, payload: Any) -> None:
    token_in, token_out = normalize_usage(payload)
    state["token_in"] = state.get("token_in", 0) + token_in
    state["token_out"] = state.get("token_out", 0) + token_out
    state["estimated_cost"] = estimate_cost(state["token_in"], state["token_out"])


def classify_intent(state: UnifiedAgentState) -> UnifiedAgentState:
    with timed_node(state["node_latencies_ms"], "classify_intent"):
        decision = route_intent(state["message"], state.get("problem_id"))
        state["intent"] = decision.intent  # type: ignore[assignment]
        state["routing_source"] = decision.source
        state["routing_confidence"] = decision.confidence
        state["routing_reason"] = decision.reason
        state["candidate_intents"] = decision.candidate_intents
        state["safe_message"] = decision.safe_message or state["message"]
        state["needs_clarification"] = decision.needs_clarification
        state["clarification_question"] = decision.clarification_question
        state["token_in"] = state.get("token_in", 0) + decision.token_in
        state["token_out"] = state.get("token_out", 0) + decision.token_out
        state["estimated_cost"] = estimate_cost(state["token_in"], state["token_out"])
    return state


def plan_tool_use(state: UnifiedAgentState) -> UnifiedAgentState:
    with timed_node(state["node_latencies_ms"], "plan_tool_use"):
        mapping = {
            "long_document_budget": ["budget_context_tool"],
            "privacy_alert": ["privacy_scan_tool"],
            "entity_disambiguation": ["disambiguate_entity_tool"],
            "framework_opinion": ["framework_compare_tool"],
            "reminder_assistant": ["reminder_tool"],
            "leetcode_rag": ["leetcode_rag_tool"],
            "general_agent_question": [],
        }
        state["planned_tools"] = mapping[state["intent"]]
        if state["selected_mode"] == "auto":
            state["mode_used"] = "plan_execute" if state["intent"] in {"long_document_budget", "privacy_alert"} else "react"
        else:
            state["mode_used"] = state["selected_mode"]
    return state


def _record_tool(state: UnifiedAgentState, execution: ToolExecution) -> None:
    state.setdefault("tool_calls", []).append(execution.model_dump())
    state.setdefault("tool_results", {})[execution.tool_name] = execution.result
    evidence = execution.result.get("evidence", [])
    if evidence:
        state.setdefault("retrieved_evidence", []).extend(evidence)


def run_tools(state: UnifiedAgentState) -> UnifiedAgentState:
    with timed_node(state["node_latencies_ms"], "run_tools"):
        state["safe_message"] = state.get("safe_message", state["message"])
        for tool_name in state.get("planned_tools", []):
            if tool_name == "budget_context_tool":
                execution = run_budget_context_tool(state["message"])
                state["context_budget"] = {
                    "limit": execution.result.get("budget_tokens"),
                    "used": execution.result.get("used_tokens"),
                    "allocation": execution.result.get("allocation"),
                    "retrieved_layers": execution.result.get("retrieved_layers"),
                }
                state["citations"] = execution.result.get("citations", [])
            elif tool_name == "privacy_scan_tool":
                execution = run_privacy_scan_tool(state["message"])
                state["privacy"] = {
                    "redacted": bool(execution.result.get("redactions")),
                    "risk_level": execution.result.get("risk_level"),
                    "redactions": execution.result.get("redactions", []),
                    "alert": execution.result.get("alert"),
                }
                state["context_budget"] = execution.result.get("context_pack", {})
                state["safe_message"] = execution.result.get("redacted_text", state.get("safe_message", state["message"]))
                state["citations"] = execution.result.get("citations", [])
            elif tool_name == "disambiguate_entity_tool":
                execution = run_disambiguate_entity_tool(state["message"])
                state["needs_clarification"] = bool(execution.result.get("needs_clarification"))
                state["clarification_question"] = execution.result.get("clarification_question")
                state["citations"] = execution.result.get("retrieved_docs", [])
            elif tool_name == "framework_compare_tool":
                execution = run_framework_compare_tool(state["message"])
                state["citations"] = execution.result.get("citations", [])
            elif tool_name == "reminder_tool":
                execution = run_reminder_tool(state["message"])
            elif tool_name == "leetcode_rag_tool":
                execution = run_leetcode_rag_tool(
                    state["session_id"],
                    state.get("problem_id"),
                    state["message"],
                    state["selected_mode"],
                )
                if execution.success:
                    state["citations"] = execution.result.get("citations", [])
            else:
                execution = ToolExecution(tool_name, "", "", False, {}, "unknown tool")
            _record_tool(state, execution)
    return state


def load_conversation_memory(state: UnifiedAgentState) -> UnifiedAgentState:
    with timed_node(state["node_latencies_ms"], "load_conversation_memory"):
        conversation_id = state.get("conversation_id")
        if not conversation_id:
            state["memory_context"] = {}
            state["memory_hits"] = 0
            state["merged_memory_hits"] = 0
            return state
        memory = build_memory_context(
            session_id=state["session_id"],
            conversation_id=conversation_id,
            question=state["message"],
        )
        state["memory_context"] = memory
        state["memory_hits"] = int(memory.get("memory_hits", 0))
        state["merged_memory_hits"] = int(memory.get("merged_memory_hits", 0))
    return state


UNIFIED_AGENT_PROMPT = """You are a Chinese-speaking Agent/RAG assistant.

Answer the user's question directly and naturally.
Default style:
- Use normal prose, not a fixed template.
- Do not add headings like "简要回答", "详细解释", "项目演示", or "生产化建议" unless the user explicitly asks for that structure.
- Keep answers concise by default. Expand only when the question needs it.
- If the user asks about the current system itself, answer from the provided runtime context and tool context explicitly.
- If a detail is unknown, say so plainly instead of making it up.

Privacy rules:
- If the privacy tool redacted the input, never reveal the original sensitive values.

Clarification rules:
- If clarification is needed, ask the clarification question and stop.

Runtime context:
{runtime_context}

Routing and tool context:
{tool_context}
"""


def _runtime_context_for_prompt() -> str:
    settings = get_settings()
    runtime_context = {
        "llm_model_main": settings.llm_model_main,
        "embedding_model": settings.embedding_model,
        "vector_store": "chroma",
        "collection": settings.chroma_collection,
    }
    return json.dumps(runtime_context, ensure_ascii=False, default=str, indent=2)


def _tool_context_for_prompt(state: UnifiedAgentState) -> str:
    public_state = {
        "intent": state.get("intent"),
        "routing_source": state.get("routing_source"),
        "routing_confidence": state.get("routing_confidence"),
        "routing_reason": state.get("routing_reason"),
        "candidate_intents": state.get("candidate_intents", []),
        "mode_used": state.get("mode_used"),
        "tool_calls": state.get("tool_calls", []),
        "retrieved_evidence": state.get("retrieved_evidence", []),
        "conversation_memory": state.get("memory_context", {}),
        "context_budget": state.get("context_budget", {}),
        "privacy": state.get("privacy", {}),
        "needs_clarification": state.get("needs_clarification", False),
        "clarification_question": state.get("clarification_question"),
        "citations": state.get("citations", []),
    }
    return json.dumps(public_state, ensure_ascii=False, default=str, indent=2)


def compose_answer(state: UnifiedAgentState) -> UnifiedAgentState:
    with timed_node(state["node_latencies_ms"], "compose_answer"):
        if state.get("needs_clarification") and state.get("clarification_question"):
            state["answer"] = str(state["clarification_question"])
            state["draft_answer"] = state["answer"]
            return state

        llm = get_llm()
        try:
            response = llm.invoke(
                [
                    SystemMessage(
                        content=UNIFIED_AGENT_PROMPT.format(
                            runtime_context=_runtime_context_for_prompt(),
                            tool_context=_tool_context_for_prompt(state),
                        )
                    ),
                    HumanMessage(content=state.get("safe_message", state["message"])),
                ]
            )
            state["answer"] = str(response.content)
            state["draft_answer"] = state["answer"]
            _add_usage(state, response)
        except Exception as exc:
            state["answer"] = _fallback_answer(state, str(exc))
            state["draft_answer"] = state["answer"]
    return state


def _fallback_answer(state: UnifiedAgentState, error: str) -> str:
    intent = state.get("intent", "general_agent_question")
    if intent == "privacy_alert":
        return "模型回答生成失败，但工具层已经完成脱敏和风险判断。请查看调试面板中的 privacy、retrieved_evidence 和 tool_calls。"
    if intent == "entity_disambiguation" and state.get("clarification_question"):
        return str(state["clarification_question"])
    return f"模型回答生成失败，但已经完成工具分析。错误摘要：{error[:160]}"


def optional_reflection(state: UnifiedAgentState) -> UnifiedAgentState:
    with timed_node(state["node_latencies_ms"], "optional_reflection"):
        privacy_high = state.get("privacy", {}).get("risk_level") == "high"
        if state.get("selected_mode") != "reflection" and not privacy_high:
            return state
        if state.get("needs_clarification"):
            return state

        llm = get_llm()
        try:
            response = llm.invoke(
                [
                    SystemMessage(content="You are reviewing a Chinese Agent/RAG engineering answer. Be concise."),
                    HumanMessage(
                        content=(
                            "Check whether this answer misses key engineering points, leaks private data, "
                            "or ignores tool evidence. Return short Chinese notes.\n\n"
                            f"Question: {state.get('safe_message', state['message'])}\n\n"
                            f"Answer: {state.get('answer', '')}\n\n"
                            f"Tool context: {_tool_context_for_prompt(state)}"
                        )
                    ),
                ]
            )
            state["reflection_notes"] = str(response.content)
            _add_usage(state, response)
            record_reflection()
        except Exception:
            state["reflection_notes"] = None
    return state


def finalize_agent_response(state: UnifiedAgentState) -> UnifiedAgentState:
    with timed_node(state["node_latencies_ms"], "finalize_agent_response"):
        state.setdefault("privacy", {})
        state.setdefault("context_budget", {})
        state.setdefault("citations", [])
        state.setdefault("tool_calls", [])
        state.setdefault("retrieved_evidence", [])
        state.setdefault("routing_source", "rule")
        state.setdefault("routing_confidence", 0.0)
        state.setdefault("routing_reason", "")
        state.setdefault("candidate_intents", [])
        state.setdefault("needs_clarification", False)
        state.setdefault("clarification_question", None)
    return state


@lru_cache(maxsize=1)
def get_unified_graph():
    graph = StateGraph(UnifiedAgentState)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("plan_tool_use", plan_tool_use)
    graph.add_node("run_tools", run_tools)
    graph.add_node("load_conversation_memory", load_conversation_memory)
    graph.add_node("compose_answer", compose_answer)
    graph.add_node("optional_reflection", optional_reflection)
    graph.add_node("finalize_agent_response", finalize_agent_response)

    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "plan_tool_use")
    graph.add_edge("plan_tool_use", "run_tools")
    graph.add_edge("run_tools", "load_conversation_memory")
    graph.add_edge("load_conversation_memory", "compose_answer")
    graph.add_edge("compose_answer", "optional_reflection")
    graph.add_edge("optional_reflection", "finalize_agent_response")
    graph.add_edge("finalize_agent_response", END)
    return graph.compile()


def run_unified_agent(
    session_id: str,
    message: str,
    mode: str = "auto",
    problem_id: str | None = None,
    conversation_id: str | None = None,
    show_steps: bool = True,
) -> UnifiedAgentState:
    start = time.perf_counter()
    state: UnifiedAgentState = {
        "request_id": str(uuid.uuid4()),
        "session_id": session_id,
        "message": message,
        "safe_message": message,
        "selected_mode": mode,
        "problem_id": problem_id,
        "conversation_id": conversation_id,
        "show_steps": show_steps,
        "tool_calls": [],
        "tool_results": {},
        "retrieved_evidence": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
        "node_latencies_ms": {},
    }
    result = get_unified_graph().invoke(state)
    result["node_latencies_ms"]["total"] = round((time.perf_counter() - start) * 1000, 2)
    return result
