from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.conversation_memory import is_memory_follow_up
from app.graph import get_llm
from app.upload_analysis import infer_upload_task_type, looks_like_explicit_outbound_request


ROUTER_PROMPT = """You are the L0 router for a secure enterprise mail agent.

Classify the request quickly. Prefer fast route when the request can be answered directly, with one tool call, or with current uploaded content.

Return JSON only:
{
  "route_mode": "fast|slow",
  "intent": "upload_analysis|contextual_memory|mail_status|enterprise_fact|persona|mail_action|mixed",
  "required_grounding": "none|memory|tool|retrieval",
  "recommended_tool": "",
  "router_reason": "",
  "confidence": 0.0
}

Rules:
- Use fast for uploaded document summarize/qa/critique/rewrite/action-item extraction.
- Use fast for previous-conversation recall and remembered preference questions.
- Use fast for mailbox status or task status lookups.
- Use fast for clear enterprise fact questions that should directly call enterprise_rag_query.
- Use fast for greetings, self-introduction, capability questions, and simple chit-chat.
- Use slow only for mixed, multi-step, ambiguous, or planning-heavy requests.
- If the answer needs external facts, set required_grounding to memory, tool, or retrieval. Never leave it as none in that case.
"""


PERSONA_HINTS = (
    "你好",
    "您好",
    "hi",
    "hello",
    "hey",
    "你是谁",
    "你的功能",
    "你能做什么",
    "what can you do",
    "who are you",
    "your function",
)

MAILBOX_HINTS = (
    "收件箱",
    "收件",
    "未读",
    "邮件摘要",
    "邮件早报",
    "今天发了多少",
    "发了多少封",
    "外发状态",
    "任务状态",
    "审批状态",
    "最近发了什么",
    "inbox",
    "unread",
    "mail status",
    "delivery status",
)

ENTERPRISE_HINTS = (
    "meeting",
    "onboarding",
    "customer",
    "entitlement",
    "fireflies",
    "slack",
    "jira",
    "linear",
    "github",
    "confluence",
    "google drive",
    "hubspot",
    "企业知识",
    "会议",
    "客户",
    "纪要",
    "内部文档",
    "工单",
    "合同",
)

PREFERENCE_HINTS = (
    "偏好",
    "默认",
    "记得我不喜欢",
    "remember my preference",
    "default behavior",
)

WORKSPACE_MEMORY_HINTS = (
    "docker",
    "todolist",
    "readme",
    "项目约定",
    "工作区",
    "workspace",
    "implementation plan",
)

CONTEXTUAL_MEMORY_HINTS = (
    "我刚才问了什么",
    "我上一句说了什么",
    "我们之前聊了什么",
    "你记得吗",
    "还记得吗",
    "what did i just ask",
    "what was my last message",
    "what did we discuss",
    "do you remember",
    "remember what we talked about",
)


def route_agent_request(
    *,
    message: str,
    safe_message: str,
    upload_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    upload_context = dict(upload_context or {})
    if settings.enable_fast_path_router:
        try:
            return _llm_router_decision(message=message, safe_message=safe_message, upload_context=upload_context)
        except Exception:
            decision = _heuristic_router_decision(message=message, safe_message=safe_message, upload_context=upload_context)
            decision["degraded_from"] = "router"
            decision["routing_source"] = "fast_router_fallback"
            return decision
    decision = _heuristic_router_decision(message=message, safe_message=safe_message, upload_context=upload_context)
    decision["routing_source"] = "fast_router_disabled"
    return decision


def _llm_router_decision(*, message: str, safe_message: str, upload_context: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    llm = get_llm(
        model=settings.llm_model_router,
        timeout=settings.llm_router_timeout_seconds,
        temperature=0.0,
        max_retries=settings.llm_router_max_retries,
    )
    payload = {
        "message": safe_message or message,
        "upload_context": {
            "kind": str(upload_context.get("kind") or "unknown"),
            "filename": str(upload_context.get("filename") or ""),
            "content_available": bool(upload_context.get("content_available")),
            "parse_status": str(upload_context.get("parse_status") or "not_provided"),
            "summary": str(upload_context.get("summary") or ""),
        },
    }
    response = llm.invoke(
        [
            SystemMessage(content=ROUTER_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
    )
    decision = _parse_json_object(str(getattr(response, "content", response)))
    return _normalize_router_decision(decision, message=message, safe_message=safe_message, upload_context=upload_context, source="fast_router")


def _heuristic_router_decision(*, message: str, safe_message: str, upload_context: dict[str, Any]) -> dict[str, Any]:
    text = (safe_message or message or "").strip()
    lowered = text.lower()
    has_upload = bool(upload_context.get("content_available"))

    if has_upload and not looks_like_explicit_outbound_request(text):
        task_type = infer_upload_task_type(text)
        return {
            "route_mode": "fast",
            "intent": "upload_analysis",
            "required_grounding": "none",
            "recommended_tool": "uploaded_content_analyze",
            "recommended_tool_input": {"task_type": task_type},
            "router_reason": f"Uploaded content is available and the request maps to uploaded_content_analyze/{task_type}.",
            "confidence": 0.93,
            "routing_source": "fast_router_fallback",
            "degraded_from": "router",
        }

    if is_memory_follow_up(text) or any(hint in lowered or hint in text for hint in CONTEXTUAL_MEMORY_HINTS):
        recommended_tool = "conversation_recent"
        if any(hint in text or hint in lowered for hint in PREFERENCE_HINTS):
            recommended_tool = "user_model"
        elif any(hint in text or hint in lowered for hint in WORKSPACE_MEMORY_HINTS):
            recommended_tool = "workspace_memory"
        elif any(marker in text for marker in ("之前聊了什么", "之前讨论", "总结一下之前")):
            recommended_tool = "conversation_summary"
        return {
            "route_mode": "fast",
            "intent": "contextual_memory",
            "required_grounding": "memory",
            "recommended_tool": recommended_tool,
            "recommended_tool_input": {},
            "router_reason": "The request asks about prior conversation context, remembered preferences, or workspace agreements.",
            "confidence": 0.95,
            "routing_source": "fast_router_fallback",
            "degraded_from": "router",
        }

    if any(hint in text or hint in lowered for hint in MAILBOX_HINTS):
        recommended_tool = "inbound_mail_summary"
        if "发了多少" in text or "外发" in text or "sent" in lowered:
            recommended_tool = "outbound_mail_summary"
        if "任务" in text or "审批" in text or "status" in lowered:
            recommended_tool = "governance_task_context_fetch"
        return {
            "route_mode": "fast",
            "intent": "mail_status",
            "required_grounding": "tool",
            "recommended_tool": recommended_tool,
            "recommended_tool_input": {},
            "router_reason": "The request is a direct mailbox or task status lookup.",
            "confidence": 0.92,
            "routing_source": "fast_router_fallback",
            "degraded_from": "router",
        }

    if any(hint in text or hint in lowered for hint in PERSONA_HINTS):
        return {
            "route_mode": "fast",
            "intent": "persona",
            "required_grounding": "none",
            "recommended_tool": "persona_or_chitchat",
            "recommended_tool_input": {},
            "router_reason": "The request is a greeting, capability question, or persona-style prompt.",
            "confidence": 0.91,
            "routing_source": "fast_router_fallback",
            "degraded_from": "router",
        }

    if any(hint in text or hint in lowered for hint in ENTERPRISE_HINTS):
        return {
            "route_mode": "fast",
            "intent": "enterprise_fact",
            "required_grounding": "retrieval",
            "recommended_tool": "enterprise_rag_query",
            "recommended_tool_input": {},
            "router_reason": "The request looks like a grounded enterprise fact question that should directly retrieve evidence.",
            "confidence": 0.88,
            "routing_source": "fast_router_fallback",
            "degraded_from": "router",
        }

    return {
        "route_mode": "slow",
        "intent": "mixed",
        "required_grounding": "none",
        "recommended_tool": "",
        "recommended_tool_input": {},
        "router_reason": "The request is mixed, ambiguous, or likely needs multi-step planning.",
        "confidence": 0.72,
        "routing_source": "fast_router_fallback",
        "degraded_from": "router",
    }


def _normalize_router_decision(
    decision: dict[str, Any],
    *,
    message: str,
    safe_message: str,
    upload_context: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    text = safe_message or message
    lowered = text.lower()
    normalized = {
        "route_mode": str(decision.get("route_mode") or "slow"),
        "intent": str(decision.get("intent") or "mixed"),
        "required_grounding": str(decision.get("required_grounding") or "none"),
        "recommended_tool": str(decision.get("recommended_tool") or ""),
        "recommended_tool_input": dict(decision.get("recommended_tool_input") or {}),
        "router_reason": str(decision.get("router_reason") or ""),
        "confidence": float(decision.get("confidence") or 0.0),
        "routing_source": source,
        "degraded_from": "none",
    }

    if normalized["route_mode"] not in {"fast", "slow"}:
        normalized["route_mode"] = "slow"
    if normalized["intent"] not in {"upload_analysis", "contextual_memory", "mail_status", "enterprise_fact", "persona", "mail_action", "mixed"}:
        normalized["intent"] = "mixed"
    if normalized["required_grounding"] not in {"none", "memory", "tool", "retrieval"}:
        normalized["required_grounding"] = "none"

    enterprise_hits = sum(1 for hint in ENTERPRISE_HINTS if hint in text or hint in lowered)
    if enterprise_hits >= 2 and not is_memory_follow_up(text):
        normalized.update(
            {
                "route_mode": "fast",
                "intent": "enterprise_fact",
                "required_grounding": "retrieval",
                "recommended_tool": "enterprise_rag_query",
                "recommended_tool_input": {},
                "router_reason": "The request strongly matches an enterprise fact query and should retrieve evidence directly.",
                "confidence": max(float(normalized.get("confidence") or 0.0), 0.9),
            }
        )

    if normalized["intent"] == "upload_analysis" and not normalized["recommended_tool"]:
        normalized["recommended_tool"] = "uploaded_content_analyze"
        normalized["recommended_tool_input"] = {"task_type": infer_upload_task_type(safe_message or message)}
    if normalized["intent"] == "contextual_memory" and not normalized["recommended_tool"]:
        normalized["recommended_tool"] = _memory_tool_for_message(safe_message or message)
        normalized["required_grounding"] = "memory"
    if normalized["intent"] == "mail_status" and not normalized["recommended_tool"]:
        normalized["recommended_tool"] = _mail_status_tool_for_message(safe_message or message)
        normalized["required_grounding"] = "tool"
    if normalized["intent"] == "enterprise_fact" and not normalized["recommended_tool"]:
        normalized["recommended_tool"] = "enterprise_rag_query"
        normalized["required_grounding"] = "retrieval"
    if normalized["intent"] == "persona" and not normalized["recommended_tool"]:
        normalized["recommended_tool"] = "persona_or_chitchat"

    if bool(upload_context.get("content_available")) and normalized["intent"] == "mixed" and not looks_like_explicit_outbound_request(safe_message or message):
        normalized.update(
            {
                "route_mode": "fast",
                "intent": "upload_analysis",
                "required_grounding": "none",
                "recommended_tool": "uploaded_content_analyze",
                "recommended_tool_input": {"task_type": infer_upload_task_type(safe_message or message)},
                "router_reason": normalized["router_reason"] or "Uploaded content is available, so prefer direct content analysis.",
            }
        )
    return normalized


def _memory_tool_for_message(message: str) -> str:
    lowered = message.lower()
    if any(hint in message or hint in lowered for hint in PREFERENCE_HINTS):
        return "user_model"
    if any(hint in message or hint in lowered for hint in WORKSPACE_MEMORY_HINTS):
        return "workspace_memory"
    if any(marker in message for marker in ("之前聊了什么", "之前讨论", "总结一下之前")):
        return "conversation_summary"
    return "conversation_recent"


def _mail_status_tool_for_message(message: str) -> str:
    lowered = message.lower()
    if "任务" in message or "审批" in message or "status" in lowered:
        return "governance_task_context_fetch"
    if "发了多少" in message or "外发" in message or "sent" in lowered:
        return "outbound_mail_summary"
    return "inbound_mail_summary"


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("No JSON object found in router output.")
