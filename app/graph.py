from __future__ import annotations

import time
import uuid
from functools import lru_cache
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.metrics import record_reflection
from app.models import Citation, RetrievalPreview
from app.observability import estimate_cost, normalize_usage, timed_node
from app.prompts import EXECUTOR_PROMPT, PLANNER_PROMPT, REACT_PROMPT, REFLECTION_PROMPT, REVISION_PROMPT, SYSTEM_BASE
from app.retrieval import (
    build_context_block,
    build_recent_turns_block,
    classify_query,
    is_follow_up_question,
    merge_context_sources,
    retrieve_conversation_summaries,
    retrieve_documents,
)
from app.session_store import get_recent_turns


class AgentState(TypedDict, total=False):
    request_id: str
    session_id: str
    problem_id: str
    question: str
    selected_mode: str
    mode_used: str
    query_type: str
    execution_stage: str
    requires_confirmation: bool
    approved_plan: bool
    is_follow_up: bool
    recent_turns: list[dict[str, Any]]
    recent_turns_block: str
    knowledge_docs: list[Any]
    memory_docs: list[Any]
    retrieved_docs: list[Any]
    context_text: str
    retrieval_preview: dict[str, Any]
    plan_steps: str
    draft_answer: str
    reflection_notes: str
    final_answer: str
    citations: list[dict[str, str]]
    token_in: int
    token_out: int
    estimated_cost: float
    retrieval_hits: int
    memory_hits: int
    used_conversation_memory: bool
    conversation_summary_written: bool
    node_latencies_ms: dict[str, float]


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model_main,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0.2,
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )


def _add_usage(state: AgentState, payload: Any) -> None:
    token_in, token_out = normalize_usage(payload)
    state["token_in"] = state.get("token_in", 0) + token_in
    state["token_out"] = state.get("token_out", 0) + token_out
    state["estimated_cost"] = estimate_cost(state["token_in"], state["token_out"])


def _build_citations(docs: list[Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for doc in docs:
        citations.append(
            {
                "source_type": str(doc.metadata.get("source_type", "unknown")),
                "title": str(doc.metadata.get("problem_title", "unknown")),
                "chunk_id": str(doc.metadata.get("chunk_id", "unknown")),
                "snippet": doc.page_content[:240],
            }
        )
    return citations


def load_problem_context(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "load_problem_context"):
        state["query_type"] = classify_query(state["question"])
        state["is_follow_up"] = is_follow_up_question(state["question"])
    return state


def load_conversation_memory(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "load_conversation_memory"):
        turns = get_recent_turns(state["session_id"], state["problem_id"], limit=5)
        state["recent_turns"] = turns
        state["recent_turns_block"] = build_recent_turns_block(turns)
    return state


def retrieve_from_chroma(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "retrieve_from_chroma"):
        docs = retrieve_documents(state["problem_id"], state["question"], state["query_type"], top_k=5)
        state["knowledge_docs"] = docs
    return state


def retrieve_runtime_memory(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "retrieve_runtime_memory"):
        docs = retrieve_conversation_summaries(state["session_id"], state["problem_id"], state["question"], top_k=2)
        state["memory_docs"] = docs
    return state


def merge_context(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "merge_context_sources"):
        recent_turns = state.get("recent_turns", [])
        memory_docs = state.get("memory_docs", [])
        knowledge_docs = state.get("knowledge_docs", [])
        state["used_conversation_memory"] = bool(recent_turns or memory_docs)
        state["memory_hits"] = len(recent_turns) + len(memory_docs)
        combined_docs = []
        if state.get("is_follow_up"):
            combined_docs.extend(memory_docs)
            combined_docs.extend(knowledge_docs)
        else:
            combined_docs.extend(knowledge_docs)
            combined_docs.extend(memory_docs)
        state["retrieved_docs"] = combined_docs
        state["retrieval_hits"] = len(combined_docs)

        knowledge_block = build_context_block(knowledge_docs)
        memory_block = build_context_block(memory_docs)
        state["context_text"] = merge_context_sources(
            state.get("recent_turns_block", ""),
            knowledge_block,
            memory_block,
        )
        state["retrieval_preview"] = RetrievalPreview(
            retrieval_hits=len(combined_docs),
            snippets=[Citation(**citation) for citation in _build_citations(combined_docs[:4])],
        ).model_dump()
    return state


def select_mode(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "select_mode"):
        mode = state["selected_mode"]
        if mode == "auto":
            state["mode_used"] = "plan_execute" if state["query_type"] == "step_by_step" else "react"
        else:
            state["mode_used"] = mode
        state["requires_confirmation"] = state["mode_used"] == "plan_execute" and state.get("execution_stage") == "preview"
    return state


def _build_react_agent(context_text: str):
    @tool
    def context_browser(question: str) -> str:
        """Read the retrieved context when you need evidence from the RAG results."""
        return f"Question: {question}\n\nRetrieved context:\n{context_text}"

    return create_react_agent(get_llm(), [context_browser])


def react_reason(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "react_reason"):
        agent = _build_react_agent(state["context_text"])
        result = agent.invoke(
            {
                "messages": [
                    SystemMessage(content=SYSTEM_BASE + "\n\n" + REACT_PROMPT.format(context=state["context_text"])),
                    HumanMessage(content=state["question"]),
                ]
            }
        )
        final_message = result["messages"][-1]
        state["draft_answer"] = getattr(final_message, "content", str(final_message))
        _add_usage(state, final_message)
    return state


def plan_task(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "plan_task"):
        if state.get("execution_stage") == "execute" and state.get("plan_steps"):
            return state
        llm = get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=SYSTEM_BASE),
                HumanMessage(
                    content=PLANNER_PROMPT.format(
                        question=state["question"],
                        context=state["context_text"],
                    )
                ),
            ]
        )
        state["plan_steps"] = response.content
        _add_usage(state, response)
    return state


def pause_for_confirmation(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "pause_for_confirmation"):
        state["final_answer"] = ""
    return state


def execute_plan(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "execute_plan"):
        llm = get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=SYSTEM_BASE),
                HumanMessage(
                    content=EXECUTOR_PROMPT.format(
                        question=state["question"],
                        plan=state.get("plan_steps", ""),
                        context=state["context_text"],
                    )
                ),
            ]
        )
        state["draft_answer"] = response.content
        _add_usage(state, response)
    return state


def reflect_answer(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "reflect_answer"):
        llm = get_llm()
        critique = llm.invoke(
            [
                SystemMessage(content=SYSTEM_BASE),
                HumanMessage(
                    content=REFLECTION_PROMPT.format(
                        question=state["question"],
                        draft=state["draft_answer"],
                        context=state["context_text"],
                    )
                ),
            ]
        )
        state["reflection_notes"] = critique.content
        _add_usage(state, critique)

        revision = llm.invoke(
            [
                SystemMessage(content=SYSTEM_BASE),
                HumanMessage(
                    content=REVISION_PROMPT.format(
                        question=state["question"],
                        reflection=state["reflection_notes"],
                        draft=state["draft_answer"],
                        context=state["context_text"],
                    )
                ),
            ]
        )
        state["final_answer"] = revision.content
        _add_usage(state, revision)
        record_reflection()
    return state


def finalize_response(state: AgentState) -> AgentState:
    with timed_node(state["node_latencies_ms"], "finalize_response"):
        if not state.get("final_answer"):
            state["final_answer"] = state.get("draft_answer", "未生成答案。")
        state["citations"] = _build_citations(state.get("retrieved_docs", []))
        state["conversation_summary_written"] = False
    return state


def route_after_mode(state: AgentState) -> Literal["react_reason", "plan_task", "reflect_first"]:
    if state["mode_used"] == "plan_execute":
        return "plan_task"
    if state["mode_used"] == "reflection":
        return "reflect_first"
    return "react_reason"


def route_after_plan(state: AgentState) -> Literal["pause_for_confirmation", "execute_plan"]:
    if state.get("execution_stage") == "preview" and not state.get("approved_plan", False):
        return "pause_for_confirmation"
    return "execute_plan"


def route_after_react(state: AgentState) -> Literal["reflect_answer", "finalize_response"]:
    if state["mode_used"] == "reflection":
        return "reflect_answer"
    return "finalize_response"


@lru_cache(maxsize=1)
def get_graph():
    graph = StateGraph(AgentState)
    graph.add_node("load_problem_context", load_problem_context)
    graph.add_node("load_conversation_memory", load_conversation_memory)
    graph.add_node("retrieve_from_chroma", retrieve_from_chroma)
    graph.add_node("retrieve_runtime_memory", retrieve_runtime_memory)
    graph.add_node("merge_context_sources", merge_context)
    graph.add_node("select_mode", select_mode)
    graph.add_node("react_reason", react_reason)
    graph.add_node("plan_task", plan_task)
    graph.add_node("pause_for_confirmation", pause_for_confirmation)
    graph.add_node("execute_plan", execute_plan)
    graph.add_node("reflect_first", react_reason)
    graph.add_node("reflect_answer", reflect_answer)
    graph.add_node("finalize_response", finalize_response)

    graph.set_entry_point("load_problem_context")
    graph.add_edge("load_problem_context", "load_conversation_memory")
    graph.add_edge("load_conversation_memory", "retrieve_from_chroma")
    graph.add_edge("retrieve_from_chroma", "retrieve_runtime_memory")
    graph.add_edge("retrieve_runtime_memory", "merge_context_sources")
    graph.add_edge("merge_context_sources", "select_mode")
    graph.add_conditional_edges(
        "select_mode",
        route_after_mode,
        {
            "react_reason": "react_reason",
            "plan_task": "plan_task",
            "reflect_first": "reflect_first",
        },
    )
    graph.add_conditional_edges(
        "plan_task",
        route_after_plan,
        {
            "pause_for_confirmation": "pause_for_confirmation",
            "execute_plan": "execute_plan",
        },
    )
    graph.add_conditional_edges(
        "react_reason",
        route_after_react,
        {
            "reflect_answer": "reflect_answer",
            "finalize_response": "finalize_response",
        },
    )
    graph.add_edge("execute_plan", "finalize_response")
    graph.add_edge("reflect_first", "reflect_answer")
    graph.add_edge("reflect_answer", "finalize_response")
    graph.add_edge("pause_for_confirmation", END)
    graph.add_edge("finalize_response", END)
    return graph.compile()


def _run_state(
    session_id: str,
    problem_id: str,
    question: str,
    mode: str,
    execution_stage: str,
    approved_plan: bool,
    request_id: str | None = None,
    plan_steps: str | None = None,
) -> AgentState:
    graph = get_graph()
    start = time.perf_counter()
    state: AgentState = {
        "request_id": request_id or str(uuid.uuid4()),
        "session_id": session_id,
        "problem_id": problem_id,
        "question": question,
        "selected_mode": mode,
        "execution_stage": execution_stage,
        "approved_plan": approved_plan,
        "plan_steps": plan_steps or "",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
        "retrieval_hits": 0,
        "memory_hits": 0,
        "used_conversation_memory": False,
        "conversation_summary_written": False,
        "node_latencies_ms": {},
    }
    result = graph.invoke(state)
    result["node_latencies_ms"]["total"] = round((time.perf_counter() - start) * 1000, 2)
    return result


def preview_plan(session_id: str, problem_id: str, question: str, mode: str) -> AgentState:
    return _run_state(session_id, problem_id, question, mode, execution_stage="preview", approved_plan=False)


def execute_confirmed_plan(
    session_id: str,
    problem_id: str,
    question: str,
    mode: str,
    plan_steps: str | None = None,
    request_id: str | None = None,
) -> AgentState:
    return _run_state(
        session_id,
        problem_id,
        question,
        mode,
        execution_stage="execute",
        approved_plan=True,
        request_id=request_id,
        plan_steps=plan_steps,
    )


def run_agent(session_id: str, problem_id: str, question: str, mode: str) -> AgentState:
    return _run_state(session_id, problem_id, question, mode, execution_stage="execute", approved_plan=True)
