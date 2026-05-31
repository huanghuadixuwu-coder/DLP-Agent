from __future__ import annotations

import json
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.enterprise_rag.core.query_planner import infer_source_types
from app.enterprise_rag.libs.metadata import normalize_source_type
from app.graph import get_llm
from app.orchestration.policies import (
    looks_like_enterprise_context_request,
    looks_like_high_confidence_persona_request,
    looks_like_inbound_summary_request,
    looks_like_outbound_summary_request,
    looks_like_persona_signal,
    looks_like_reply_draft_request,
)
from app.orchestration.domain_agents import resolve_domain_agent_for_action
from app.orchestration.registry import build_tool_registry
from app.orchestration.types import AgentSubtask, AgentTaskPlan
from app.upload_analysis import classify_upload_request


VALID_ENTERPRISE_SOURCE_TYPES = {
    "gmail",
    "slack",
    "linear",
    "jira",
    "github",
    "google_drive",
    "confluence",
    "hubspot",
    "fireflies",
    "unknown",
}


def plan_message(message: str, upload_context: dict[str, Any] | None = None) -> AgentTaskPlan:
    heuristic = _heuristic_plan(message, upload_context or {})
    if _should_accept_heuristic(heuristic):
        return _finalize_plan(heuristic)
    llm_plan = _llm_plan(message)
    if llm_plan and llm_plan.subtasks:
        return _finalize_plan(llm_plan, fallback=heuristic)
    return _finalize_plan(heuristic)


def _heuristic_plan(message: str, upload_context: dict[str, Any]) -> AgentTaskPlan:
    text = (message or "").lower()
    subtasks: list[AgentSubtask] = []
    persona_signal = looks_like_persona_signal(message)
    upload_decision = classify_upload_request(message=message, upload_context=upload_context)
    if upload_decision.route == "unsupported":
        subtasks.append(
            AgentSubtask(
                task_id=_task_id("unsupported_capability"),
                capability="unsupported_capability",
                input={
                    "reason": upload_decision.unsupported_reason,
                    "capability": "uploaded_email_reply_draft",
                },
            )
        )
        return AgentTaskPlan(
            original_message=message,
            subtasks=subtasks,
            aggregation_strategy="answer_all_parts",
            planner_reason="Heuristic upload-aware planner detected an unsupported uploaded-email action and returned explicit guidance.",
            confidence=0.96,
            planner_type="heuristic",
        )
    elif upload_decision.route == "analyze":
        subtasks.append(
            AgentSubtask(
                task_id=_task_id("uploaded_content_analyze"),
                capability="uploaded_content_analyze",
                input={
                    "content_kind": upload_decision.content_kind,
                    "task_type": upload_decision.task_type,
                    "message": message,
                    "uploaded_filename": str(upload_context.get("filename") or ""),
                    "uploaded_content_type": str(upload_context.get("content_type") or ""),
                    "uploaded_text": str(upload_context.get("uploaded_text") or ""),
                    "source_parse_status": str(upload_context.get("parse_status") or "not_provided"),
                    "source_parse_error": str(upload_context.get("parse_error") or ""),
                },
            )
        )
        if persona_signal and not looks_like_high_confidence_persona_request(message):
            subtasks.insert(
                0,
                AgentSubtask(
                    task_id=_task_id("persona_or_chitchat"),
                    capability="persona_or_chitchat",
                    input={"message": message},
                ),
            )
    if subtasks:
        return AgentTaskPlan(
            original_message=message,
            subtasks=subtasks,
            aggregation_strategy="answer_all_parts",
            planner_reason="Heuristic upload-aware planner selected the uploaded content analysis path.",
            confidence=0.92,
            planner_type="heuristic",
        )
    if looks_like_high_confidence_persona_request(message):
        subtasks.append(
            AgentSubtask(
                task_id=_task_id("persona_or_chitchat"),
                capability="persona_or_chitchat",
                input={"message": message},
            )
        )
        return AgentTaskPlan(
            original_message=message,
            subtasks=subtasks,
            aggregation_strategy="answer_all_parts",
            planner_reason="High-confidence persona/chitchat rule matched a standalone assistant-introduction request.",
            confidence=0.97,
            planner_type="heuristic",
        )
    if _contains_outbound_count(text, message):
        subtasks.append(AgentSubtask(task_id=_task_id("outbound_mail_summary"), capability="outbound_mail_summary"))
    if _contains_inbound_count(text, message):
        subtasks.append(AgentSubtask(task_id=_task_id("inbound_mail_summary"), capability="inbound_mail_summary"))
    if looks_like_reply_draft_request(message):
        search_task = AgentSubtask(
            task_id=_task_id("inbound_message_search"),
            capability="inbound_message_search",
            input={"query": message, "limit": 5},
            user_visible=False,
        )
        subtasks.append(search_task)
        if looks_like_enterprise_context_request(message):
            subtasks.append(
                AgentSubtask(
                    task_id=_task_id("enterprise_rag_query"),
                    capability="enterprise_rag_query",
                    input={"question": message, "source_types": infer_source_types(message)},
                )
            )
        subtasks.append(
            AgentSubtask(
                task_id=_task_id("inbound_reply_draft"),
                capability="inbound_reply_draft",
                dependencies=[search_task.task_id],
            )
        )
    if not subtasks and looks_like_inbound_summary_request(message):
        subtasks.append(AgentSubtask(task_id=_task_id("inbound_mail_summary"), capability="inbound_mail_summary"))
    if not subtasks:
        subtasks.append(
            AgentSubtask(
                task_id=_task_id("enterprise_rag_query"),
                capability="enterprise_rag_query",
                input={"question": message, "source_types": infer_source_types(message)},
            )
        )
    return AgentTaskPlan(
        original_message=message,
        subtasks=subtasks,
        aggregation_strategy="compose_reply_with_context" if any(item.capability == "inbound_reply_draft" for item in subtasks) else "answer_all_parts",
        planner_reason="Heuristic capability planner selected mail, reply-drafting, and enterprise knowledge subtasks.",
        confidence=0.68,
        planner_type="heuristic",
    )


def _llm_plan(message: str) -> AgentTaskPlan | None:
    tool_catalog = build_tool_registry()
    prompt = """You are the planner for a secure enterprise mail agent.

Return JSON only using this schema:
{
  "planner_reason": "...",
  "confidence": 0.0,
  "aggregation_strategy": "answer_all_parts|compose_reply_with_context|status_and_next_action",
    "subtasks": [
    {
      "task_id": "optional stable step id",
      "agent": "mail|calendar|meeting|dlp|enterprise_rag|memory|supervisor",
      "capability": "one tool name from the catalog",
      "action": "same as capability unless a more specific action is needed",
      "input": {},
      "parameters": {},
      "dependencies": [],
      "risk": "low|medium|high",
      "confirmation_required": false,
      "idempotency_key": "",
      "expected_observation_type": "",
      "resource_scope": {},
      "user_visible": true
    }
  ]
}

Rules:
- Use only capabilities from the provided tool catalog.
- Prefer multiple subtasks for compound questions.
- If the request is a normal enterprise knowledge question, include enterprise_rag_query.
- If the user asks for mail counts sent and received, include outbound_mail_summary and inbound_mail_summary.
- If the user asks to draft a reply, include inbound_message_search and inbound_reply_draft. Add enterprise_rag_query when project/customer/document context is requested.
- If the user is asking who the assistant is, what it can do, how to use it, or is engaging in lightweight chitchat, include persona_or_chitchat.
- Do not use tools that mutate state.
- If uncertain, fall back to a single enterprise_rag_query subtask.
"""
    catalog = [
        {
            "name": item.name,
            "description": item.description,
            "input_schema": item.input_schema,
        }
        for item in tool_catalog.values()
        if not item.mutating
    ]
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(content=json.dumps({"message": message, "tool_catalog": catalog}, ensure_ascii=False)),
            ]
        )
        parsed = _parse_json_object(str(getattr(response, "content", response)))
        subtasks = []
        valid_names = set(tool_catalog)
        for item in parsed.get("subtasks", []):
            capability = str(item.get("capability") or "").strip()
            if capability not in valid_names:
                continue
            definition = tool_catalog[capability]
            action = str(item.get("action") or capability).strip()
            parameters = dict(item.get("parameters") or item.get("input") or {})
            subtasks.append(
                AgentSubtask(
                    task_id=str(item.get("task_id") or _task_id(capability)),
                    capability=capability,
                    action=action,
                    agent=str(item.get("agent") or resolve_domain_agent_for_action(action)),
                    input=parameters,
                    parameters=parameters,
                    dependencies=[str(dep) for dep in item.get("dependencies") or []],
                    risk=str(item.get("risk") or "low"),
                    confirmation_required=bool(item.get("confirmation_required", False) or definition.requires_confirmation),
                    idempotency_key=str(item.get("idempotency_key") or ""),
                    expected_observation_type=str(item.get("expected_observation_type") or definition.returns_observation_type),
                    resource_scope=dict(item.get("resource_scope") or {}),
                    mutating=definition.mutating,
                    parallelizable=definition.parallelizable,
                    user_visible=bool(item.get("user_visible", True)),
                )
            )
        if not subtasks:
            return None
        return AgentTaskPlan(
            original_message=message,
            subtasks=subtasks,
            aggregation_strategy=str(parsed.get("aggregation_strategy") or "answer_all_parts"),
            planner_reason=str(parsed.get("planner_reason") or "LLM planned the available capabilities."),
            confidence=float(parsed.get("confidence") or 0.74),
            planner_type="llm",
        )
    except Exception:
        return None


def _finalize_plan(plan: AgentTaskPlan, fallback: AgentTaskPlan | None = None) -> AgentTaskPlan:
    registry = build_tool_registry()
    normalized: list[AgentSubtask] = []
    task_ids: set[str] = set()
    for item in plan.subtasks:
        if item.capability not in registry:
            continue
        definition = registry[item.capability]
        task_id = item.task_id or _task_id(item.capability)
        if task_id in task_ids:
            task_id = _task_id(item.capability)
        task_ids.add(task_id)
        normalized_input = dict(item.input or {})
        if item.capability == "enterprise_rag_query":
            requested_source_types = [
                normalize_source_type(source_type)
                for source_type in list(normalized_input.get("source_types") or [])
                if str(source_type).strip()
            ]
            requested_source_types = [
                source_type for source_type in requested_source_types if source_type in VALID_ENTERPRISE_SOURCE_TYPES and source_type != "unknown"
            ]
            normalized_input["question"] = str(normalized_input.get("question") or plan.original_message)
            normalized_input["source_types"] = requested_source_types or infer_source_types(plan.original_message)
        normalized.append(
            AgentSubtask(
                task_id=task_id,
                capability=item.capability,
                action=item.action or item.capability,
                agent=item.agent or resolve_domain_agent_for_action(item.action or item.capability),
                input=normalized_input,
                parameters=dict(item.parameters or normalized_input),
                dependencies=[dep for dep in item.dependencies if dep],
                risk=item.risk or "low",
                confirmation_required=bool(item.confirmation_required or definition.requires_confirmation),
                idempotency_key=item.idempotency_key,
                expected_observation_type=item.expected_observation_type or definition.returns_observation_type,
                resource_scope=dict(item.resource_scope or {}),
                mutating=definition.mutating,
                parallelizable=definition.parallelizable,
                user_visible=item.user_visible,
            )
        )
    if not normalized and fallback:
        return _finalize_plan(fallback)
    if not normalized:
        normalized = [AgentSubtask(task_id=_task_id("enterprise_rag_query"), capability="enterprise_rag_query", input={"question": plan.original_message, "source_types": infer_source_types(plan.original_message)})]
    return AgentTaskPlan(
        original_message=plan.original_message,
        subtasks=normalized,
        aggregation_strategy=plan.aggregation_strategy,
        planner_reason=plan.planner_reason,
        confidence=plan.confidence,
        planner_type=plan.planner_type,
    )


def _task_id(capability: str) -> str:
    return f"task_{capability}_{uuid.uuid4().hex[:8]}"


def _contains_outbound_count(text: str, message: str) -> bool:
    return bool(
        re.search(r"(发送|发出|发了|sent|send).*(多少|几封|统计|count|today|今天|昨日|昨天)", message + text)
        and ("邮件" in message or "邮箱" in message or "mail" in text or "email" in text)
    )


def _contains_inbound_count(text: str, message: str) -> bool:
    receive_tokens = ("收到", "收了", "收件", "来信", "receive", "received", "inbox", "unread")
    mail_tokens = ("邮件", "邮箱", "mail", "email")
    scope_tokens = ("多少", "几封", "重要", "今天", "昨日", "昨天", "today", "summary", "digest")
    return any(token in message or token in text for token in receive_tokens) and any(
        token in message or token in text for token in mail_tokens
    ) and any(token in message or token in text for token in scope_tokens)


def _should_accept_heuristic(plan: AgentTaskPlan) -> bool:
    capabilities = [item.capability for item in plan.subtasks]
    if len(capabilities) > 1:
        return True
    if not capabilities:
        return False
    if capabilities[0] in {"outbound_mail_summary", "inbound_mail_summary", "inbound_reply_draft", "uploaded_content_analyze", "unsupported_capability", "persona_or_chitchat"}:
        return True
    return False


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("No JSON object found")
