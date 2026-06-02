from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.conversation_memory import is_memory_follow_up
from app.enterprise_rag.libs.scoring import extract_query_entity_anchors
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
- Use fast with intent=mail_action for outbound draft, send, reply, forward, or mail-edit requests. Interpret natural phrasing semantically; do not require a fixed send keyword.
- Route by requested capability, not by whether every downstream mail field is already resolved. A request to deliver, forward, or transfer prior information to an email recipient is mail_action even when the body source is referential or ambiguous. The Mail Agent will resolve the source or ask its own clarification question.
- Use fast for clear enterprise fact questions that should directly call enterprise_rag_query.
- Use fast for greetings, self-introduction, capability questions, and simple chit-chat.
- Use slow only for mixed, multi-step, ambiguous, or planning-heavy requests.
- If the answer needs external facts, set required_grounding to memory, tool, or retrieval. Never leave it as none in that case.

Examples:
- "把该信息发送给 user@example.com" -> {"route_mode":"fast","intent":"mail_action","required_grounding":"tool","recommended_tool":"mail_action_resolve"}
- "劳烦将前面提到的方案转交至 user@example.com" -> {"route_mode":"fast","intent":"mail_action","required_grounding":"tool","recommended_tool":"mail_action_resolve"}
- "把前两轮讨论整理后发给 user@example.com" -> {"route_mode":"fast","intent":"mail_action","required_grounding":"tool","recommended_tool":"mail_action_resolve"}
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

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SAFE_REFERENTIAL_MAIL_MARKERS = (
    "该信息",
    "该方案",
    "这个方案",
    "上述",
    "前面提到",
    "刚才提到",
    "this information",
    "that information",
    "this solution",
    "that solution",
)

FACT_QUESTION_MARKERS = (
    "?",
    "？",
    "是什么",
    "如何",
    "怎么",
    "哪些",
    "多少",
    "时限",
    "顺序",
    "what ",
    "how ",
    "which ",
    "when ",
    "where ",
    "why ",
)


def route_agent_request(
    *,
    message: str,
    safe_message: str,
    upload_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = perf_counter()

    def _finish(decision: dict[str, Any]) -> dict[str, Any]:
        decision["router_latency_ms"] = round((perf_counter() - started) * 1000.0, 2)
        return decision

    settings = get_settings()
    upload_context = dict(upload_context or {})
    heuristic = _heuristic_router_decision(message=message, safe_message=safe_message, upload_context=upload_context)
    if _is_high_confidence_heuristic_route(heuristic, message=safe_message or message):
        heuristic["routing_source"] = "fast_router_heuristic"
        heuristic["degraded_from"] = "none"
        return _finish(heuristic)
    if settings.enable_fast_path_router:
        try:
            return _finish(_llm_router_decision(message=message, safe_message=safe_message, upload_context=upload_context))
        except Exception:
            heuristic["degraded_from"] = "router"
            heuristic["routing_source"] = "fast_router_fallback"
            return _finish(heuristic)
    heuristic["routing_source"] = "fast_router_disabled"
    return _finish(heuristic)


def _is_high_confidence_heuristic_route(decision: dict[str, Any], *, message: str) -> bool:
    intent = str(decision.get("intent") or "")
    if float(decision.get("confidence") or 0.0) >= 0.9:
        return True
    if intent != "enterprise_fact":
        return False
    text = (message or "").lower()
    return sum(1 for hint in ENTERPRISE_HINTS if _matches_hint(message, text, hint)) >= 2


def _matches_hint(text: str, lowered: str, hint: str) -> bool:
    normalized_hint = str(hint or "").strip().lower()
    if not normalized_hint:
        return False
    if len(normalized_hint) <= 3 and normalized_hint.isascii() and normalized_hint.isalpha():
        return bool(re.search(rf"\b{re.escape(normalized_hint)}\b", lowered))
    return normalized_hint in lowered or normalized_hint in text


def _llm_router_decision(*, message: str, safe_message: str, upload_context: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    llm = get_llm(
        model=settings.llm_model_router,
        timeout=max(settings.llm_router_timeout_seconds, 1.0),
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

    if looks_like_explicit_outbound_request(text) or _looks_like_safe_degraded_mail_action(text):
        return {
            "route_mode": "fast",
            "intent": "mail_action",
            "required_grounding": "tool",
            "recommended_tool": "mail_action_resolve",
            "recommended_tool_input": {},
            "router_reason": "The request should enter the Mail Agent. Downstream source resolution remains responsible for selecting or clarifying the outbound body.",
            "confidence": 0.82,
            "routing_source": "fast_router_fallback",
            "degraded_from": "router",
        }

    if is_memory_follow_up(text) or any(_matches_hint(text, lowered, hint) for hint in CONTEXTUAL_MEMORY_HINTS):
        recommended_tool = "conversation_recent"
        if any(_matches_hint(text, lowered, hint) for hint in PREFERENCE_HINTS):
            recommended_tool = "user_model"
        elif any(_matches_hint(text, lowered, hint) for hint in WORKSPACE_MEMORY_HINTS):
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

    if any(_matches_hint(text, lowered, hint) for hint in MAILBOX_HINTS):
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

    if any(_matches_hint(text, lowered, hint) for hint in PERSONA_HINTS):
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

    if any(_matches_hint(text, lowered, hint) for hint in ENTERPRISE_HINTS):
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

    if extract_query_entity_anchors(text) and any(marker in text or marker in lowered for marker in FACT_QUESTION_MARKERS):
        return {
            "route_mode": "fast",
            "intent": "enterprise_fact",
            "required_grounding": "retrieval",
            "recommended_tool": "enterprise_rag_query",
            "recommended_tool_input": {},
            "router_reason": "The request is a fact question with a distinctive entity identifier and should retrieve bounded enterprise evidence directly.",
            "confidence": 0.9,
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


def _looks_like_safe_degraded_mail_action(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    if not EMAIL_PATTERN.search(text):
        return False
    return any(marker in text or marker in lowered for marker in SAFE_REFERENTIAL_MAIL_MARKERS)


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

    enterprise_hits = sum(1 for hint in ENTERPRISE_HINTS if _matches_hint(text, lowered, hint))
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
    if any(_matches_hint(message, lowered, hint) for hint in PREFERENCE_HINTS):
        return "user_model"
    if any(_matches_hint(message, lowered, hint) for hint in WORKSPACE_MEMORY_HINTS):
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
