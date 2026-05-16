from __future__ import annotations

import json
import re
from dataclasses import asdict
from time import perf_counter
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.conversation_memory import build_memory_context, compact_text, is_memory_follow_up
from app.conversation_store import get_turns
from app.graph import get_llm
from app.hermes_dynamic_memory import get_recent_compactions, get_structured_turn_summaries, get_user_memory_context, get_workspace_memory_context
from app.observability import estimate_cost, normalize_usage
from app.orchestration.final_renderer import fallback_final_answer, render_final_answer
from app.orchestration.registry import build_tool_executor_map, build_tool_registry
from app.orchestration.types import OrchestrationContext, PendingConfirmation, ReactTraceStep


CONTROLLER_PROMPT = """You are the routing and execution controller for a secure enterprise mail agent.

You must decide exactly one next action for the current loop step.

Return JSON only with this schema:
{
  "current_goal": "contextual_qa|knowledge_qa|status_or_mail|persona_or_smalltalk|action_or_draft|mixed",
  "thought_summary": "short visible summary",
  "confidence": 0.0,
  "action_type": "call_tool|read_memory|direct_answer|ask_clarification|request_confirmation|abort_with_reason",
  "tool_name": "",
  "tool_input": {},
  "memory_kind": "conversation_recent|conversation_summary|workspace_memory|user_model",
  "response_text": "",
  "confirmation_title": "",
  "confirmation_message": ""
}

Rules:
- Decide one step only.
- Always write response_text, confirmation_title, and confirmation_message in Chinese.
- Keep thought_summary short and user-safe. Do not reveal hidden chain-of-thought.
- If the user asks about previous turns, what was asked before, or what was discussed earlier, prefer read_memory with conversation_recent first.
- Use conversation_summary only after recent turns are insufficient or if the question clearly asks for a broader recap.
- Use workspace_memory for project rules, stable agreements, implementation plans, todolist, Docker-first constraints, or workspace notes.
- Use user_model for user preferences, communication style, remembered defaults, or "do you remember my preference" questions.
- Never use memory as proof for enterprise facts. For enterprise customer/document/meeting facts, call enterprise_rag_query.
- Use persona_or_chitchat only for greetings, self-introduction, lightweight chat, or capability questions. Do not route memory follow-up questions there.
- If the current observations already contain enough grounded information, choose direct_answer.
- If external facts are required, never imagine them. Read memory or call a tool first, then answer.
- If a tool is marked side_effectful or requires_confirmation, never call it directly. Choose request_confirmation instead.
- If the budget is almost exhausted and you have partial evidence, choose direct_answer with conservative wording.
- If the user intent is too ambiguous to continue safely, choose ask_clarification.
"""

def run_react_agent_request(
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
) -> dict[str, Any]:
    settings = get_settings()
    registry = build_tool_registry()
    executors = build_tool_executor_map()
    context = OrchestrationContext(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        safe_message=safe_message,
        display_message=display_message or message,
        upload_context=dict(upload_context or {}),
    )
    state: dict[str, Any] = {
        "request_id": str(uuid4()),
        "session_id": session_id,
        "conversation_id": conversation_id,
        "message": message,
        "safe_message": safe_message,
        "display_message": display_message or message,
        "upload_context": dict(upload_context or {}),
        "mode_used": "react",
        "planner_type": "react_controller",
        "route_mode": "slow",
        "router_intent": router_intent or "",
        "router_reason": router_reason or "",
        "required_grounding": required_grounding or "none",
        "recommended_tool": recommended_tool or "",
        "fast_path_used": False,
        "degraded_from": degraded_from or "none",
        "loop_step": 0,
        "max_steps": int(getattr(settings, "react_max_steps", 3)),
        "max_memory_reads": int(getattr(settings, "react_max_memory_reads", 2)),
        "max_tool_failures": int(getattr(settings, "react_max_tool_failures", 2)),
        "max_same_tool_retries": int(getattr(settings, "react_max_same_tool_retries", 1)),
        "current_goal": "",
        "working_memory": [],
        "observations": [],
        "react_trace": [],
        "tool_calls": [],
        "partial_failures": [],
        "retrieved_evidence": [],
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "context_sources": [],
        "workspace_memory_hits": 0,
        "transcript_hits": 0,
        "user_model_used": False,
        "needs_clarification": False,
        "clarification_question": None,
        "pending_confirmation": None,
        "confirmation_payload": {},
        "termination_reason": "",
        "final_answer_source": "",
        "answer": "",
        "routing_source": "react_controller",
        "routing_confidence": 0.0,
        "routing_reason": "",
        "candidate_intents": [],
        "memory_reads": [],
        "tool_observations": [],
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
        "node_latencies_ms": {},
        "tool_failures": 0,
        "tool_retry_counts": {},
    }

    started = perf_counter()
    try:
        for step_index in range(1, state["max_steps"] + 1):
            state["loop_step"] = step_index
            try:
                decision = _think_next_step(state, registry)
            except Exception as exc:
                if _is_timeout_like_error(exc):
                    state["degraded_from"] = "react_think"
                    if state.get("upload_context", {}).get("content_available") or state.get("memory_reads") or state.get("observations"):
                        state["answer"] = fallback_final_answer(
                            question=str(state.get("safe_message") or state.get("message") or ""),
                            current_goal=str(state.get("current_goal") or ""),
                            observations=list(state.get("observations", [])),
                            working_memory=list(state.get("working_memory", [])),
                            conservative=True,
                        )
                        state["termination_reason"] = "direct_answer"
                        state["final_answer_source"] = "react_timeout_degraded_answer"
                        break
                    state["needs_clarification"] = True
                    state["clarification_question"] = "当前规划阶段超时了。为了避免空想回答，我需要你把问题再聚焦一点，或让我直接基于现有上传内容继续。"
                    state["answer"] = state["clarification_question"]
                    state["termination_reason"] = "needs_clarification"
                    state["final_answer_source"] = "react_timeout_clarification"
                    break
                raise
            trace = ReactTraceStep(
                step_index=step_index,
                thought_summary=str(decision.get("thought_summary") or ""),
                action_type=str(decision.get("action_type") or "abort_with_reason"),  # type: ignore[arg-type]
                tool_name=str(decision.get("tool_name") or ""),
                tool_input=dict(decision.get("tool_input") or {}),
                memory_kind=str(decision.get("memory_kind") or ""),
                confidence=float(decision.get("confidence") or 0.0),
            )
            state["routing_confidence"] = max(float(state.get("routing_confidence", 0.0)), trace.confidence)
            if decision.get("current_goal"):
                state["current_goal"] = str(decision.get("current_goal"))
            state["routing_reason"] = trace.thought_summary or state.get("routing_reason", "")

            action_type = str(decision.get("action_type") or "abort_with_reason")
            if action_type == "read_memory":
                observation = _read_memory(context, state, str(decision.get("memory_kind") or "conversation_recent"))
                trace.observation_summary = observation["summary"]
                trace.status = "completed"
                _append_trace(state, trace)
                continue

            if action_type == "call_tool":
                tool_name = str(decision.get("tool_name") or "").strip()
                tool_input = dict(decision.get("tool_input") or {})
                tool_observation = _call_tool(context, state, registry, executors, tool_name, tool_input)
                trace.tool_name = tool_name
                trace.tool_input = tool_input
                trace.observation_summary = tool_observation["summary"]
                trace.status = "completed" if tool_observation["success"] else "failed"
                _append_trace(state, trace)
                if tool_observation["terminate"]:
                    break
                continue

            if action_type == "ask_clarification":
                state["needs_clarification"] = True
                state["clarification_question"] = str(decision.get("response_text") or "为了继续处理，我需要你补充更具体的信息。")
                state["answer"] = state["clarification_question"]
                state["termination_reason"] = "needs_clarification"
                state["final_answer_source"] = "clarification"
                trace.observation_summary = "Asked a clarification question."
                trace.status = "completed"
                _append_trace(state, trace)
                break

            if action_type == "request_confirmation":
                pending = PendingConfirmation(
                    action_name="guarded_action",
                    title=str(decision.get("confirmation_title") or "需要确认后继续"),
                    message=str(decision.get("confirmation_message") or "这个动作会产生外部状态变更，请先确认。"),
                    tool_name=str(decision.get("tool_name") or ""),
                    tool_input=dict(decision.get("tool_input") or {}),
                )
                state["pending_confirmation"] = asdict(pending)
                state["confirmation_payload"] = asdict(pending)
                state["answer"] = _render_confirmation_message(pending)
                state["termination_reason"] = "needs_confirmation"
                state["final_answer_source"] = "confirmation_request"
                trace.observation_summary = "Prepared a guarded confirmation request."
                trace.status = "completed"
                _append_trace(state, trace)
                break

            if action_type == "direct_answer":
                if _direct_answer_requires_grounding(state):
                    grounded = _auto_ground_before_direct_answer(context, state, registry, executors)
                    if grounded:
                        trace.observation_summary = grounded["summary"]
                        trace.status = "completed"
                        _append_trace(state, trace)
                        if grounded.get("terminate"):
                            break
                        continue
                response_text = str(decision.get("response_text") or "").strip()
                if not response_text:
                    response_text = _compose_final_answer(state, conservative=False)
                state["answer"] = response_text
                state["termination_reason"] = "direct_answer"
                state["final_answer_source"] = "react_direct_answer"
                trace.observation_summary = "Returned a grounded direct answer."
                trace.status = "completed"
                _append_trace(state, trace)
                break

            state["answer"] = str(decision.get("response_text") or "当前无法继续推进这个请求。")
            state["termination_reason"] = "abort_with_reason"
            state["final_answer_source"] = "controller_abort"
            trace.observation_summary = "Aborted with an explicit reason."
            trace.status = "completed"
            _append_trace(state, trace)
            break

        if not state.get("answer"):
            state["answer"] = _compose_final_answer(state, conservative=True)
            state["termination_reason"] = "budget_exhausted"
            state["final_answer_source"] = "budget_guardrail"
    except Exception:
        raise
    finally:
        state["node_latencies_ms"]["total"] = round((perf_counter() - started) * 1000.0, 2)

    state["candidate_intents"] = [state["current_goal"]] if state.get("current_goal") else []
    state["intent"] = state.get("current_goal") or "react_agent"
    state["router_intent"] = state.get("router_intent") or ""
    state["router_reason"] = state.get("router_reason") or state.get("routing_reason") or ""
    state["required_grounding"] = state.get("required_grounding") or "none"
    state["route_mode"] = "slow"
    state["fast_path_used"] = False
    state["task_plan"] = _render_react_snapshot(state)
    state["subtask_results"] = list(state.get("tool_calls", []))
    state["aggregation_strategy"] = "react_loop"
    state["loop_step_count"] = int(state.get("loop_step", 0))
    return state


def _append_trace(state: dict[str, Any], trace: ReactTraceStep) -> None:
    state.setdefault("react_trace", []).append(asdict(trace))


def _record_usage(state: dict[str, Any], payload: Any) -> None:
    token_in, token_out = normalize_usage(payload)
    state["token_in"] = int(state.get("token_in", 0)) + token_in
    state["token_out"] = int(state.get("token_out", 0)) + token_out
    state["estimated_cost"] = estimate_cost(state["token_in"], state["token_out"])


def _accumulate_latency(state: dict[str, Any], name: str, started: float) -> None:
    elapsed = round((perf_counter() - started) * 1000.0, 2)
    state["node_latencies_ms"][name] = round(float(state["node_latencies_ms"].get(name, 0.0)) + elapsed, 2)


def _tool_catalog_for_prompt(registry: dict[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for item in registry.values():
        catalog.append(
            {
                "name": item.name,
                "description": item.description,
                "input_schema": item.input_schema,
                "when_to_use": item.when_to_use,
                "reads_from": item.reads_from,
                "writes_to": item.writes_to,
                "read_only": item.read_only,
                "side_effectful": item.side_effectful,
                "requires_confirmation": item.requires_confirmation,
                "safe_when": item.safe_when,
                "returns_observation_type": item.returns_observation_type,
                "provider_constraints": item.provider_constraints,
                "examples": item.examples,
            }
        )
    return catalog


def _memory_request_scope(message: str) -> str:
    text = message or ""
    lowered = text.lower()
    preference_markers = (
        "偏好",
        "习惯",
        "我喜欢",
        "我不喜欢",
        "我希望",
        "我不希望",
        "记住我",
        "remember my",
        "my preference",
        "default",
        "preference",
    )
    workspace_markers = (
        "项目",
        "规则",
        "约定",
        "todolist",
        "todo",
        "docker",
        "验收",
        "计划",
        "架构",
        "memory/",
        "workspace",
    )
    broad_markers = (
        "之前聊",
        "前面讨论",
        "之前决定",
        "之前对话",
        "总结一下",
        "recap",
        "what we discussed",
        "earlier discussion",
    )
    if any(marker in text or marker in lowered for marker in preference_markers):
        return "user_model"
    if any(marker in text or marker in lowered for marker in workspace_markers):
        return "workspace_memory"
    if any(marker in text or marker in lowered for marker in broad_markers):
        return "broad"
    return "recent"


def _format_structured_turn_summary(item: dict[str, Any]) -> str:
    pieces = [
        f"summary={compact_text(str(item.get('summary') or ''), 220)}",
        f"intent={compact_text(str(item.get('intent') or ''), 80)}",
        f"risk={compact_text(str(item.get('risk_level') or 'low'), 40)}",
        f"goal={compact_text(str(item.get('user_goal') or ''), 160)}",
        f"outcome={compact_text(str(item.get('outcome') or ''), 180)}",
    ]
    if item.get("entities"):
        pieces.append("entities=" + ", ".join(str(value) for value in list(item.get("entities") or [])[:6]))
    if item.get("files_uploaded"):
        pieces.append("files_uploaded=" + ", ".join(str(value) for value in list(item.get("files_uploaded") or [])[:4]))
    if item.get("task_ids"):
        pieces.append("tasks=" + ", ".join(str(value) for value in list(item.get("task_ids") or [])[:4]))
    if item.get("key_files"):
        pieces.append("files=" + ", ".join(str(value) for value in list(item.get("key_files") or [])[:4]))
    if item.get("recipients"):
        pieces.append("recipients=" + ", ".join(str(value) for value in list(item.get("recipients") or [])[:4]))
    if item.get("failure_reason"):
        pieces.append(f"failure={compact_text(str(item.get('failure_reason')), 160)}")
    return "; ".join(part for part in pieces if part and not part.endswith("="))


def _think_next_step(state: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    message = str(state.get("safe_message") or state.get("message") or "")
    memory_reads = list(state.get("memory_reads", []))
    memory_scope = _memory_request_scope(message)
    if not state.get("observations") and not memory_reads and is_memory_follow_up(message):
        return {
            "current_goal": "contextual_qa",
            "thought_summary": "Read recent conversation memory first.",
            "confidence": 0.99,
            "action_type": "read_memory",
            "memory_kind": "conversation_recent",
            "tool_input": {},
        }
    if not state.get("observations") and not memory_reads and memory_scope == "user_model":
        return {
            "current_goal": "contextual_qa",
            "thought_summary": "Read user preference memory.",
            "confidence": 0.97,
            "action_type": "read_memory",
            "memory_kind": "user_model",
            "tool_input": {},
        }
    if not state.get("observations") and not memory_reads and memory_scope == "workspace_memory":
        return {
            "current_goal": "contextual_qa",
            "thought_summary": "Read workspace memory for project context.",
            "confidence": 0.94,
            "action_type": "read_memory",
            "memory_kind": "workspace_memory",
            "tool_input": {},
        }
    if is_memory_follow_up(message):
        recent_read = next((item for item in reversed(memory_reads) if str(item.get("kind")) == "conversation_recent"), None)
        summary_read = next((item for item in reversed(memory_reads) if str(item.get("kind")) == "conversation_summary"), None)
        workspace_read = next((item for item in reversed(memory_reads) if str(item.get("kind")) == "workspace_memory"), None)
        user_model_read = next((item for item in reversed(memory_reads) if str(item.get("kind")) == "user_model"), None)
        if memory_scope == "user_model" and not user_model_read:
            return {
                "current_goal": "contextual_qa",
                "thought_summary": "Preference question needs user model memory.",
                "confidence": 0.96,
                "action_type": "read_memory",
                "memory_kind": "user_model",
                "tool_input": {},
            }
        if memory_scope == "workspace_memory" and not workspace_read:
            return {
                "current_goal": "contextual_qa",
                "thought_summary": "Project-context question needs workspace memory.",
                "confidence": 0.94,
                "action_type": "read_memory",
                "memory_kind": "workspace_memory",
                "tool_input": {},
            }
        if memory_scope == "broad" and recent_read and not summary_read:
            return {
                "current_goal": "contextual_qa",
                "thought_summary": "Broad recap needs compressed conversation summary.",
                "confidence": 0.95,
                "action_type": "read_memory",
                "memory_kind": "conversation_summary",
                "tool_input": {},
            }
        if recent_read and int(recent_read.get("hits", 0)) > 0:
            return {
                "current_goal": "contextual_qa",
                "thought_summary": "Conversation context is sufficient.",
                "confidence": 0.96,
                "action_type": "direct_answer",
                "response_text": "",
                "tool_input": {},
            }
        if recent_read and int(recent_read.get("hits", 0)) == 0 and not summary_read:
            return {
                "current_goal": "contextual_qa",
                "thought_summary": "Recent memory is insufficient; read summary memory.",
                "confidence": 0.95,
                "action_type": "read_memory",
                "memory_kind": "conversation_summary",
                "tool_input": {},
            }
        if summary_read:
            return {
                "current_goal": "contextual_qa",
                "thought_summary": "Summary memory is sufficient.",
                "confidence": 0.92,
                "action_type": "direct_answer",
                "response_text": "",
                "tool_input": {},
            }
    if memory_scope in {"user_model", "workspace_memory"}:
        current = next((item for item in reversed(memory_reads) if str(item.get("kind")) == memory_scope), None)
        if current:
            return {
                "current_goal": "contextual_qa",
                "thought_summary": f"{memory_scope} memory is sufficient.",
                "confidence": 0.9,
                "action_type": "direct_answer",
                "response_text": "",
                "tool_input": {},
            }
    started = perf_counter()
    llm = get_llm()
    prompt_payload = {
        "message": state.get("safe_message") or state.get("message"),
        "display_message": state.get("display_message") or state.get("message"),
        "router_intent": state.get("router_intent") or "",
        "required_grounding": state.get("required_grounding") or "none",
        "recommended_tool": state.get("recommended_tool") or "",
        "loop_step": state.get("loop_step"),
        "max_steps": state.get("max_steps"),
        "current_goal": state.get("current_goal", ""),
        "working_memory": list(state.get("working_memory", []))[-4:],
        "observations": list(state.get("observations", []))[-5:],
        "memory_reads": list(state.get("memory_reads", [])),
        "tool_failures": int(state.get("tool_failures", 0)),
        "tool_retry_counts": dict(state.get("tool_retry_counts", {})),
        "upload_context": dict(state.get("upload_context", {})),
        "available_memory_kinds": ["conversation_recent", "conversation_summary", "workspace_memory", "user_model"],
        "available_tools": _tool_catalog_for_prompt(registry),
    }
    response = llm.invoke(
        [
            SystemMessage(content=CONTROLLER_PROMPT),
            HumanMessage(content=json.dumps(prompt_payload, ensure_ascii=False)),
        ]
    )
    _record_usage(state, response)
    _accumulate_latency(state, "react_think", started)
    decision = _parse_json_object(str(getattr(response, "content", response)))
    decision.setdefault("thought_summary", "")
    decision.setdefault("action_type", "abort_with_reason")
    decision.setdefault("current_goal", state.get("current_goal") or "mixed")
    decision.setdefault("tool_input", {})
    decision.setdefault("confidence", 0.0)
    return decision


def _read_memory(context: OrchestrationContext, state: dict[str, Any], memory_kind: str) -> dict[str, Any]:
    started = perf_counter()
    memory_kind = memory_kind or "conversation_recent"
    if len(state.get("memory_reads", [])) >= int(state.get("max_memory_reads", 2)):
        observation = {
            "kind": memory_kind,
            "observation_type": f"read_memory.{memory_kind}",
            "summary": "Memory read budget exhausted.",
            "hits": 0,
            "provenance": {"source": "budget_guardrail"},
            "memory_boundary": "context_only",
            "enterprise_citation_required": True,
        }
        state.setdefault("observations", []).append(observation)
        return observation

    if memory_kind == "conversation_summary":
        memory = build_memory_context(
            session_id=context.session_id,
            conversation_id=context.conversation_id,
            question=context.message,
        )
        compactions = get_recent_compactions(session_id=context.session_id, conversation_id=context.conversation_id, limit=3)
        structured = get_structured_turn_summaries(session_id=context.session_id, conversation_id=context.conversation_id, limit=6)
        summary_parts = [str(memory.get("memory_context") or "")]
        if compactions:
            summary_parts.append("Compacted transcript memory:\n" + "\n".join(str(item.get("summary") or "") for item in compactions))
        if structured:
            summary_parts.append("Structured turn summaries:\n" + "\n".join(_format_structured_turn_summary(item) for item in structured))
        summary_text = compact_text("\n\n".join(part for part in summary_parts if part).strip() or "No conversation summary memory is available.", 900)
        observation = {
            "kind": "conversation_summary",
            "hits": int(memory.get("memory_hits", 0)) + int(memory.get("merged_memory_hits", 0)) + len(compactions) + len(structured),
            "summary": summary_text,
            "turn_memory": list(memory.get("turn_memory", [])),
            "merged_memory": list(memory.get("merged_memory", [])),
            "compactions": compactions,
            "structured_turn_summaries": structured,
            "provenance": {"source": "conversation_summary", "compactions": len(compactions), "structured_turn_summaries": len(structured)},
        }
        state["memory_context"] = memory
        state["memory_hits"] = int(memory.get("memory_hits", 0))
        state["merged_memory_hits"] = int(memory.get("merged_memory_hits", 0))
    elif memory_kind == "workspace_memory":
        workspace = get_workspace_memory_context(context.message, top_k=6)
        observation = {
            "kind": "workspace_memory",
            "hits": int(workspace.get("hits", 0)),
            "summary": compact_text(str(workspace.get("summary") or "No workspace memory hits."), 900),
            "items": list(workspace.get("items") or []),
            "provenance": dict(workspace.get("provenance") or {}),
        }
        state.setdefault("memory_context", {})["workspace_memory"] = workspace
        state["workspace_memory_hits"] = int(workspace.get("hits", 0))
    elif memory_kind == "user_model":
        user_memory = get_user_memory_context(session_id=context.session_id, conversation_id=context.conversation_id, limit=8)
        observation = {
            "kind": "user_model",
            "hits": int(user_memory.get("hits", 0)),
            "summary": compact_text(str(user_memory.get("summary") or "No user preference memory is available."), 900),
            "facts": list(user_memory.get("facts") or []),
            "reflection_candidates": list(user_memory.get("reflection_candidates") or []),
            "provenance": dict(user_memory.get("provenance") or {}),
        }
        state.setdefault("memory_context", {})["user_model"] = user_memory
        state["user_model_used"] = bool(user_memory.get("hits"))
    else:
        turns = get_turns(context.conversation_id, limit=6)
        snippets = [
            f"{turn.get('role', 'unknown')}: {compact_text(str(turn.get('redacted_content') or turn.get('content') or ''), 180)}"
            for turn in turns
        ]
        summary_text = "\n".join(snippets) if snippets else "There are no recent turns to recall in this conversation."
        observation = {
            "kind": "conversation_recent",
            "hits": len(turns),
            "summary": compact_text(summary_text, 600),
            "turns": turns,
            "provenance": {"source": "conversation_store.turns", "limit": 6},
        }
        state.setdefault("memory_context", {})["recent_turns"] = turns
        state["transcript_hits"] = len(turns)

    observation.setdefault("observation_type", f"read_memory.{observation['kind']}")
    observation.setdefault("memory_boundary", "context_only")
    observation.setdefault("enterprise_citation_required", True)
    state.setdefault("memory_reads", []).append({"kind": observation["kind"], "hits": observation["hits"], "summary": observation["summary"], "provenance": observation.get("provenance", {})})
    state.setdefault("tool_calls", []).append(
        {
            "tool_name": f"read_memory.{observation['kind']}",
            "success": True,
            "status": "completed",
            "error": "",
            "result": observation,
        }
    )
    state.setdefault("tool_observations", []).append(observation)
    state.setdefault("observations", []).append(observation)
    state.setdefault("working_memory", []).append(observation["summary"])
    state.setdefault("retrieved_evidence", []).append(
        {
            "domain": observation["kind"],
            "summary": observation["summary"],
            "provenance": observation.get("provenance", {}),
        }
    )
    _accumulate_latency(state, "react_read_memory", started)
    return observation


def _is_timeout_like_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "timeout" in text or "timed out" in text


def _direct_answer_requires_grounding(state: dict[str, Any]) -> bool:
    required_grounding = str(state.get("required_grounding") or "none")
    if required_grounding == "none":
        return False
    if required_grounding == "memory":
        return not bool(state.get("memory_reads"))
    return not bool(state.get("observations"))


def _auto_ground_before_direct_answer(
    context: OrchestrationContext,
    state: dict[str, Any],
    registry: dict[str, Any],
    executors: dict[str, Any],
) -> dict[str, Any] | None:
    required_grounding = str(state.get("required_grounding") or "none")
    if required_grounding == "memory":
        memory_kind = str(state.get("recommended_tool") or "conversation_recent")
        observation = _read_memory(context, state, memory_kind)
        return {"summary": f"Direct answer blocked; fetched {memory_kind} first.", "terminate": False, "observation": observation}
    if required_grounding in {"tool", "retrieval"}:
        tool_name = str(state.get("recommended_tool") or "").strip()
        if tool_name and tool_name in registry:
            tool_observation = _call_tool(context, state, registry, executors, tool_name, _default_grounding_payload(tool_name, context))
            return {
                "summary": f"Direct answer blocked; fetched {tool_name} first.",
                "terminate": bool(tool_observation.get("terminate")),
                "observation": tool_observation,
            }
    return None


def _default_grounding_payload(tool_name: str, context: OrchestrationContext) -> dict[str, Any]:
    if tool_name == "enterprise_rag_query":
        return {"question": context.message, "top_k": 8}
    if tool_name == "memory_search":
        return {"query": context.message, "top_k": 6}
    if tool_name == "inbound_message_search":
        return {"query": context.message, "limit": 8}
    if tool_name == "conversation_context_fetch":
        return {"query": context.message}
    if tool_name in {"persona_or_chitchat", "uploaded_content_analyze"}:
        return {"message": context.message}
    return {}


def _call_tool(
    context: OrchestrationContext,
    state: dict[str, Any],
    registry: dict[str, Any],
    executors: dict[str, Any],
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    started = perf_counter()
    definition = registry.get(tool_name)
    if not definition:
        observation = {"summary": f"Unknown tool: {tool_name}", "success": False, "terminate": False}
        state["tool_failures"] = int(state.get("tool_failures", 0)) + 1
        state.setdefault("partial_failures", []).append({"tool_name": tool_name, "error": observation["summary"]})
        state.setdefault("observations", []).append({"kind": "tool_error", **observation})
        _accumulate_latency(state, "react_act", started)
        return observation

    if definition.side_effectful or definition.requires_confirmation:
        pending = PendingConfirmation(
            action_name=tool_name,
            title=f"需要确认后执行 {tool_name}",
            message=f"{tool_name} 会产生外部状态变更，请先确认再继续。",
            tool_name=tool_name,
            tool_input=tool_input,
        )
        state["pending_confirmation"] = asdict(pending)
        state["confirmation_payload"] = asdict(pending)
        state["answer"] = _render_confirmation_message(pending)
        state["termination_reason"] = "needs_confirmation"
        state["final_answer_source"] = "tool_confirmation_guardrail"
        observation = {"summary": f"Guardrailed side-effectful tool: {tool_name}", "success": True, "terminate": True}
        state.setdefault("observations", []).append({"kind": "confirmation", **observation})
        _accumulate_latency(state, "react_act", started)
        return observation

    retry_counts = dict(state.get("tool_retry_counts", {}))
    retry_counts[tool_name] = int(retry_counts.get(tool_name, 0))
    state["tool_retry_counts"] = retry_counts

    payload: dict[str, Any]
    error = ""
    success = False
    handler = executors.get(tool_name)
    try:
        payload = handler(tool_input, context, {}) if handler else {"error": f"No executor found for {tool_name}"}
        error = str(payload.get("error") or "")
        success = not error
    except Exception as exc:
        payload = {"error": str(exc)}
        error = str(exc)

    if not success:
        state["tool_failures"] = int(state.get("tool_failures", 0)) + 1
        retry_counts[tool_name] = int(retry_counts.get(tool_name, 0)) + 1
        state["tool_retry_counts"] = retry_counts
        state.setdefault("partial_failures", []).append({"tool_name": tool_name, "error": error or "unknown tool error"})

    summary = _summarize_tool_observation(tool_name, payload, success)
    call_record = {
        "tool_name": tool_name,
        "success": success,
        "status": "completed" if success else "failed",
        "error": error,
        "result": payload,
    }
    citations = list(payload.get("citations") or payload.get("evidence", {}).get("citations", []))
    state.setdefault("tool_calls", []).append(call_record)
    state.setdefault("tool_observations", []).append(
        {
            "tool_name": tool_name,
            "success": success,
            "summary": summary,
            "observation_type": definition.returns_observation_type,
            "payload": payload,
            "citations": citations,
        }
    )
    state.setdefault("observations", []).append(
        {
            "kind": "tool_result",
            "tool_name": tool_name,
            "success": success,
            "summary": summary,
            "observation_type": definition.returns_observation_type,
            "payload": payload,
            "citations": citations,
        }
    )
    state.setdefault("working_memory", []).append(summary)
    if citations:
        state["citations"] = citations[:10]
        state.setdefault("retrieved_evidence", []).extend(
            [
                {
                    **item,
                    "domain": item.get("source_type") or definition.returns_observation_type or "tool_result",
                }
                for item in citations[:6]
                if isinstance(item, dict)
            ]
        )
    if payload.get("context_sources"):
        state["context_sources"] = list(payload.get("context_sources") or [])
        state["workspace_memory_hits"] = int(payload.get("workspace_memory_hits", 0))
        state["transcript_hits"] = int(payload.get("transcript_hits", 0))
        state["user_model_used"] = bool(payload.get("user_model_used", False))
    if payload.get("memory_context"):
        state["memory_context"] = dict(payload.get("memory_context") or {})
        state["memory_hits"] = int((state["memory_context"] or {}).get("memory_retrieval_hits", state.get("memory_hits", 0)))

    _accumulate_latency(state, "react_act", started)
    terminate = False
    if not success and (
        int(state.get("tool_failures", 0)) >= int(state.get("max_tool_failures", 2))
        or int(retry_counts.get(tool_name, 0)) > int(state.get("max_same_tool_retries", 1))
    ):
        state["answer"] = _compose_final_answer(state, conservative=True)
        state["termination_reason"] = "fatal_tool_failure"
        state["final_answer_source"] = "tool_failure_guardrail"
        terminate = True
    return {"summary": summary, "success": success, "terminate": terminate}


def _summarize_tool_observation(tool_name: str, payload: dict[str, Any], success: bool) -> str:
    if not success:
        return compact_text(str(payload.get("error") or f"{tool_name} failed."), 220)
    if tool_name == "enterprise_rag_query":
        answer = compact_text(str(payload.get("answer") or ""), 220)
        supporting = list(payload.get("supporting_doc_ids") or [])
        return f"Enterprise evidence ready. docs={len(supporting)} answer={answer}"
    if tool_name in {"inbound_mail_summary", "outbound_mail_summary"}:
        total = payload.get("total", payload.get("total_sent", 0))
        unread = payload.get("unread", 0)
        return f"Mail status ready. total={total} unread={unread}"
    if tool_name == "governance_task_context_fetch":
        task = dict(payload.get("task") or {})
        if not task:
            return "No recoverable governance task in the current conversation."
        return f"Governance task status={task.get('status', 'unknown')} risk={task.get('risk_level', '')}"
    if tool_name == "memory_search":
        hits = list(payload.get("hits") or [])
        preview = ", ".join(str(item.get("title") or item.get("file_path") or "") for item in hits[:2])
        return f"Workspace memory hits={len(hits)} {preview}".strip()
    if tool_name == "conversation_context_fetch":
        memory = dict(payload.get("memory_context") or {})
        return f"Conversation context hits={int(memory.get('memory_hits', 0)) + int(memory.get('merged_memory_hits', 0))}"
    if tool_name == "inbound_message_search":
        messages = list(payload.get("messages") or [])
        return f"Inbound mail search hits={len(messages)}"
    if tool_name == "inbound_message_read":
        message = dict(payload.get("message") or {})
        return f"Read mail: {message.get('subject') or '(鏃犱富棰?'}"
    if tool_name == "inbound_reply_draft":
        return compact_text(str(payload.get("draft_reply") or ""), 220)
    if tool_name in {"uploaded_content_analyze", "persona_or_chitchat", "unsupported_capability", "enterprise_answer"}:
        return compact_text(str(payload.get("answer") or ""), 220)
    return compact_text(json.dumps(payload, ensure_ascii=False, default=str), 220)


def _render_confirmation_message(pending: PendingConfirmation) -> str:
    title = pending.title or "需要确认后继续"
    message = pending.message or "这个动作会产生外部状态变更，请先确认。"
    if pending.tool_name:
        return f"{title}\n\n{message}\n\n待执行动作：`{pending.tool_name}`"
    return f"{title}\n\n{message}"


def _contextual_answer_instruction(user_message: str) -> str:
    lowered = user_message.lower()
    exact_recall_markers = [
        "我刚才问了什么",
        "我上一句说了什么",
        "上一句",
        "刚才那句",
        "exactly what",
        "what did i just ask",
        "what was my last message",
    ]
    memory_check_markers = [
        "你记得吗",
        "还记得",
        "记不记得",
        "之前对话",
        "我们之前聊了什么",
        "do you remember",
        "remember what we talked about",
    ]
    if any(marker in user_message for marker in exact_recall_markers) or any(marker in lowered for marker in exact_recall_markers):
        return (
            "For this contextual_qa answer, prioritize exact recall of the user's most recent prior message if available. "
            "Keep the answer short and direct. Do not expand into a broader recap unless the exact recall is unavailable."
        )
    if any(marker in user_message for marker in memory_check_markers) or any(marker in lowered for marker in memory_check_markers):
        return (
            "For this contextual_qa answer, first state whether the prior context is remembered, then summarize only the main topics briefly. "
            "Do not replay every turn unless the user explicitly requests a verbatim recap."
        )
    return (
        "For this contextual_qa answer, provide a concise context-grounded summary that best matches the user's request, "
        "preferring summary over transcript-style replay."
    )


def _render_react_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "planner_type": "react_controller",
        "current_goal": state.get("current_goal") or "",
        "router_intent": state.get("router_intent") or "",
        "required_grounding": state.get("required_grounding") or "none",
        "recommended_tool": state.get("recommended_tool") or "",
        "loop_step": int(state.get("loop_step", 0)),
        "max_steps": int(state.get("max_steps", 0)),
        "termination_reason": state.get("termination_reason") or "",
        "react_trace": list(state.get("react_trace", [])),
        "pending_confirmation": state.get("pending_confirmation") or {},
        "final_answer_source": state.get("final_answer_source") or "",
        "memory_reads": list(state.get("memory_reads", [])),
        "tool_observations": list(state.get("tool_observations", [])),
    }


def _deterministic_final_answer(state: dict[str, Any], *, conservative: bool) -> str:
    return fallback_final_answer(
        question=str(state.get("safe_message") or state.get("message") or ""),
        current_goal=str(state.get("current_goal") or ""),
        observations=list(state.get("observations", [])),
        working_memory=list(state.get("working_memory", [])),
        conservative=conservative,
    )


def _deterministic_contextual_answer(state: dict[str, Any]) -> str:
    return fallback_final_answer(
        question=str(state.get("safe_message") or state.get("message") or ""),
        current_goal="contextual_qa",
        observations=list(state.get("observations", [])),
        working_memory=list(state.get("working_memory", [])),
        conservative=False,
    )


def _compose_final_answer(state: dict[str, Any], *, conservative: bool) -> str:
    started = perf_counter()
    rendered = render_final_answer(
        question=str(state.get("safe_message") or state.get("message") or ""),
        current_goal=str(state.get("current_goal") or "mixed"),
        observations=list(state.get("observations", []))[-8:],
        working_memory=list(state.get("working_memory", []))[-4:],
        conservative=conservative,
    )
    state["token_in"] = int(state.get("token_in", 0)) + int(rendered.get("token_in", 0))
    state["token_out"] = int(state.get("token_out", 0)) + int(rendered.get("token_out", 0))
    state["estimated_cost"] = float(state.get("estimated_cost", 0.0)) + float(rendered.get("estimated_cost", 0.0))
    _accumulate_latency(state, "react_compose_answer", started)
    return str(rendered.get("answer") or _deterministic_final_answer(state, conservative=conservative))


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("No JSON object found in controller output.")
