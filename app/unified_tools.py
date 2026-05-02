from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent_cli import parse_time_expression
from app.disambiguation_lab import answer_apple_query
from app.graph import run_agent
from app.labs_long_doc import allocate_context
from app.privacy_lab import scan_sensitive_message
from app.raw_vs_langgraph import compare_raw_llm_and_langgraph
from app.reminder_store import create_reminder, delete_reminder, list_reminders
from app.unified_corpus import documents_to_evidence, evidence_source_counts, retrieve_unified_evidence


@dataclass
class ToolExecution:
    tool_name: str
    input_summary: str
    output_summary: str
    success: bool
    result: dict[str, Any]
    error: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "success": self.success,
            "result": self.result,
            "error": self.error,
        }


def _safe_summary(value: Any, limit: int = 260) -> str:
    text = str(value).replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _normalize_retrieval_error(exc: Exception) -> tuple[str, str]:
    raw = str(exc)
    if "1113" in raw or "余额不足" in raw or "429" in raw:
        return (
            "Unified RAG retrieval unavailable",
            "Unified RAG retrieval is temporarily unavailable because the embedding/model quota is exhausted. "
            "The agent will continue with local fallback logic for this turn.",
        )
    return (
        "Unified RAG retrieval unavailable",
        "Unified RAG retrieval is temporarily unavailable, so the agent is using local fallback logic for this turn.",
    )


def _safe_evidence(
    query: str,
    domain: str,
    top_k: int = 4,
    entity_type: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    try:
        docs = retrieve_unified_evidence(query, domain=domain, top_k=top_k, entity_type=entity_type)
        return documents_to_evidence(docs), True
    except Exception as exc:
        title, snippet = _normalize_retrieval_error(exc)
        return (
            [
                {
                    "chunk_id": f"{domain}-retrieval-error",
                    "domain": domain,
                    "intent": "",
                    "source_type": "retrieval_error",
                    "title": title,
                    "entity_type": entity_type or "",
                    "sensitivity": "",
                    "snippet": snippet,
                }
            ],
            False,
        )


def _citation_view(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": item["chunk_id"],
            "source_type": item["source_type"],
            "title": item["title"],
            "snippet": item["snippet"],
            "domain": item["domain"],
            "entity_type": item.get("entity_type", ""),
        }
        for item in evidence
    ]


def run_budget_context_tool(message: str) -> ToolExecution:
    evidence, retrieval_ok = _safe_evidence(message, domain="long_doc", top_k=5)
    result = allocate_context(message, 10_000)
    if evidence:
        result["evidence"] = evidence
        result["retrieved_layers"] = evidence_source_counts(evidence)
        result["citations"] = _citation_view(evidence)
        result["analysis"]["retrieval_note"] = (
            "Evidence was retrieved from the unified Chroma RAG corpus."
            if retrieval_ok
            else "Unified RAG retrieval was unavailable, so this answer used local fallback knowledge plus context-budget logic."
        )
    return ToolExecution(
        tool_name="budget_context_tool",
        input_summary=_safe_summary(message),
        output_summary=f"scope={result['question_scope']}, used={result['used_tokens']}/{result['budget_tokens']}, layers={result['retrieved_layers']}",
        success=True,
        result=result,
    )


def run_privacy_scan_tool(message: str) -> ToolExecution:
    result = scan_sensitive_message(message, 600)
    evidence, retrieval_ok = _safe_evidence(result["redacted_text"], domain="privacy", top_k=4)
    result["evidence"] = evidence
    result["citations"] = _citation_view(evidence)
    result["retrieval_note"] = (
        "Evidence was retrieved from the unified Chroma RAG corpus."
        if retrieval_ok
        else "Unified RAG retrieval was unavailable, so this answer used local privacy rules and fallback guidance."
    )
    return ToolExecution(
        tool_name="privacy_scan_tool",
        input_summary="raw message redacted before model composition",
        output_summary=f"risk={result['risk_level']}, redactions={result['redactions']}, alert={result['alert']}",
        success=True,
        result=result,
    )


def run_disambiguate_entity_tool(message: str) -> ToolExecution:
    result = answer_apple_query(message)
    evidence: list[dict[str, Any]] = []
    retrieval_ok = True
    if not result["needs_clarification"]:
        evidence, retrieval_ok = _safe_evidence(
            message,
            domain="disambiguation",
            top_k=4,
            entity_type=result.get("entity_type"),
        )
        if evidence:
            result["retrieved_docs"] = evidence
    result["evidence"] = evidence
    result["retrieval_note"] = (
        "Evidence was retrieved from the unified Chroma RAG corpus."
        if retrieval_ok
        else "Unified RAG retrieval was unavailable, so this answer used local entity-disambiguation logic."
    )
    return ToolExecution(
        tool_name="disambiguate_entity_tool",
        input_summary=_safe_summary(message),
        output_summary=f"entity={result['entity_type']}, needs_clarification={result['needs_clarification']}",
        success=True,
        result=result,
    )


def run_framework_compare_tool(message: str) -> ToolExecution:
    evidence, retrieval_ok = _safe_evidence(message, domain="framework", top_k=4)
    result = compare_raw_llm_and_langgraph(message)
    result["evidence"] = evidence
    result["citations"] = _citation_view(evidence)
    result["retrieval_note"] = (
        "Evidence was retrieved from the unified Chroma RAG corpus."
        if retrieval_ok
        else "Unified RAG retrieval was unavailable, so this answer used local comparison material."
    )
    return ToolExecution(
        tool_name="framework_compare_tool",
        input_summary=_safe_summary(message),
        output_summary="compared raw SDK and LangGraph across workflow, tools, observability, and control",
        success=True,
        result=result,
    )


def run_reminder_tool(message: str) -> ToolExecution:
    lowered = message.lower()
    if "list" in lowered or "查看" in message or "有哪些" in message:
        result = {"action": "list", "reminders": list_reminders()}
    elif "delete" in lowered or "删除" in message:
        parts = message.split()
        reminder_id = parts[-1] if parts else ""
        result = {"action": "delete", "deleted": delete_reminder(reminder_id), "id": reminder_id}
    else:
        task_text, remind_at = parse_time_expression(message)
        result = {"action": "create", "reminder": create_reminder(task_text, remind_at)}
    return ToolExecution(
        tool_name="reminder_tool",
        input_summary=_safe_summary(message),
        output_summary=_safe_summary(result),
        success=True,
        result=result,
    )


def run_leetcode_rag_tool(session_id: str, problem_id: str | None, message: str, mode: str) -> ToolExecution:
    if not problem_id:
        return ToolExecution(
            tool_name="leetcode_rag_tool",
            input_summary=_safe_summary(message),
            output_summary="problem_id is missing, skipped LeetCode RAG",
            success=False,
            result={},
            error="problem_id is required for leetcode_rag_tool",
        )
    rag_mode = "reflection" if mode == "reflection" else "react"
    result = run_agent(session_id, problem_id, message, rag_mode)
    return ToolExecution(
        tool_name="leetcode_rag_tool",
        input_summary=_safe_summary(message),
        output_summary=f"mode={result.get('mode_used')}, retrieval_hits={result.get('retrieval_hits')}",
        success=True,
        result=dict(result),
    )
