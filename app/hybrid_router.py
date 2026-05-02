from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.observability import estimate_cost, normalize_usage
from app.privacy_lab import redact_message


VALID_INTENTS = {
    "long_document_budget",
    "privacy_alert",
    "entity_disambiguation",
    "framework_opinion",
    "reminder_assistant",
    "leetcode_rag",
    "general_agent_question",
}


@dataclass
class RouteDecision:
    intent: str
    source: str
    confidence: float
    reason: str
    candidate_intents: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    safe_message: str | None = None
    needs_clarification: bool = False
    clarification_question: str | None = None
    token_in: int = 0
    token_out: int = 0
    estimated_cost: float = 0.0


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _rule_candidates(message: str, problem_id: str | None) -> list[tuple[str, float, str, str]]:
    lowered = message.lower()
    redacted, redactions = redact_message(message)
    candidates: list[tuple[str, float, str, str]] = []

    privacy_tokens = (
        "隐私",
        "敏感",
        "脱敏",
        "预警",
        "泄露",
        "外发",
        "手机号",
        "身份证",
        "银行卡",
        "邮箱",
        "密码",
        "客户名单",
        "api_key",
        "api key",
        "secret",
        "token",
        "password",
    )
    if redactions or _contains_any(message, privacy_tokens):
        confidence = 0.98 if redactions else 0.9
        candidates.append(("privacy_alert", confidence, "privacy_rule", "命中 PII/密钥/隐私预警规则。"))

    long_doc_tokens = (
        "1.5m",
        "10k",
        "memory",
        "context",
        "prompt",
        "rag",
        "长文档",
        "整本书",
        "全书",
        "上下文",
        "分层摘要",
        "上下文预算",
        "超大",
        "pdf",
        "压缩",
        "token",
    )
    if _contains_any(message, long_doc_tokens):
        confidence = 0.9 if ("1.5m" in lowered or "10k" in lowered or "整本书" in message) else 0.72
        candidates.append(("long_document_budget", confidence, "long_doc_rule", "命中长文档/上下文预算相关规则。"))

    if "apple" in lowered or "苹果" in message:
        candidates.append(("entity_disambiguation", 0.86, "apple_rule", "命中 apple/苹果 歧义实体规则。"))

    framework_tokens = (
        "langchain",
        "langgraph",
        "llamaindex",
        "semantic kernel",
        "原生 sdk",
        "框架",
        "没用了",
        "不用框架",
        "大模型编排",
    )
    if _contains_any(message, framework_tokens):
        candidates.append(("framework_opinion", 0.88, "framework_rule", "命中框架选型观点规则。"))

    reminder_tokens = ("提醒", "闹钟", "定时", "明天", "后天", "remind", "alarm", "scheduler")
    if _contains_any(message, reminder_tokens):
        candidates.append(("reminder_assistant", 0.82, "reminder_rule", "命中提醒/闹钟助手规则。"))

    leetcode_tokens = ("leetcode", "代码题", "复杂度", "边界情况", "题解", "算法题")
    if problem_id or _contains_any(message, leetcode_tokens):
        confidence = 0.84 if problem_id else 0.62
        candidates.append(("leetcode_rag", confidence, "leetcode_rule", "命中 LeetCode/代码解释规则。"))

    if not candidates:
        candidates.append(("general_agent_question", 0.4, "default_rule", "未命中强规则，按通用 Agent 问题处理。"))

    return sorted(candidates, key=lambda item: item[1], reverse=True)


def rule_route(message: str, problem_id: str | None) -> RouteDecision:
    redacted, _ = redact_message(message)
    candidates = _rule_candidates(message, problem_id)
    top_intent, top_confidence, top_rule, top_reason = candidates[0]
    return RouteDecision(
        intent=top_intent,
        source="rule",
        confidence=top_confidence,
        reason=top_reason,
        candidate_intents=[item[0] for item in candidates],
        matched_rules=[item[2] for item in candidates],
        safe_message=redacted,
    )


ROUTER_PROMPT = """You are an intent router for a Chinese Agent/RAG demo.

Choose exactly one intent from:
- long_document_budget: long documents, context window, token budget, hierarchical summaries, memory compression.
- privacy_alert: sensitive data, PII, redaction, privacy warning, api keys, tokens, passwords.
- entity_disambiguation: apple/Apple ambiguity, entity clarification, metadata filtering.
- framework_opinion: LangChain, LangGraph, raw SDK, framework value.
- reminder_assistant: alarm, reminder, scheduler, todo assistant.
- leetcode_rag: LeetCode, algorithm explanation, code question, complexity, edge cases.
- general_agent_question: other Agent/RAG engineering questions.

Return strict JSON only:
{
  "intent": "...",
  "confidence": 0.0,
  "reason": "...",
  "needs_clarification": false,
  "clarification_question": null
}
"""


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("LLM router did not return a JSON object")
    return json.loads(match.group(0))


def llm_route(message: str, candidate_intents: list[str]) -> RouteDecision:
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.llm_model_main,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0,
        timeout=12,
        max_retries=0,
    )
    response = llm.invoke(
        [
            SystemMessage(content=ROUTER_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "message": message,
                        "candidate_intents_from_rules": candidate_intents,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    parsed = _parse_json_object(str(response.content))
    intent = str(parsed.get("intent", "general_agent_question"))
    if intent not in VALID_INTENTS:
        intent = "general_agent_question"
    confidence = float(parsed.get("confidence", 0.0))
    token_in, token_out = normalize_usage(response)
    return RouteDecision(
        intent=intent,
        source="llm",
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(parsed.get("reason", "LLM router selected the intent.")),
        candidate_intents=candidate_intents,
        matched_rules=[],
        safe_message=message,
        needs_clarification=bool(parsed.get("needs_clarification", False)),
        clarification_question=parsed.get("clarification_question"),
        token_in=token_in,
        token_out=token_out,
        estimated_cost=estimate_cost(token_in, token_out),
    )


def route_intent(message: str, problem_id: str | None = None) -> RouteDecision:
    rule_decision = rule_route(message, problem_id)
    candidates = _rule_candidates(message, problem_id)
    ambiguous_rules = len(candidates) > 1 and candidates[0][1] - candidates[1][1] < 0.12

    # Privacy routing is deterministic and uses a redacted message for all later model calls.
    if rule_decision.intent == "privacy_alert" and rule_decision.confidence >= 0.75:
        return rule_decision

    # High-confidence rules avoid a model call and keep routing cheap/fast.
    if rule_decision.confidence >= 0.82 and not ambiguous_rules:
        return rule_decision

    settings = get_settings()
    if not settings.llm_api_key:
        rule_decision.source = "fallback"
        rule_decision.reason = f"{rule_decision.reason} LLM router skipped because LLM_API_KEY is not configured."
        return rule_decision

    try:
        llm_decision = llm_route(rule_decision.safe_message or message, rule_decision.candidate_intents)
    except Exception as exc:
        rule_decision.source = "fallback"
        rule_decision.reason = f"{rule_decision.reason} LLM router failed: {str(exc)[:160]}"
        return rule_decision

    if llm_decision.confidence < 0.45:
        return RouteDecision(
            intent="general_agent_question",
            source="fallback",
            confidence=llm_decision.confidence,
            reason=f"LLM router confidence too low. Original reason: {llm_decision.reason}",
            candidate_intents=rule_decision.candidate_intents,
            safe_message=rule_decision.safe_message,
            token_in=llm_decision.token_in,
            token_out=llm_decision.token_out,
            estimated_cost=llm_decision.estimated_cost,
        )

    llm_decision.safe_message = rule_decision.safe_message
    return llm_decision
