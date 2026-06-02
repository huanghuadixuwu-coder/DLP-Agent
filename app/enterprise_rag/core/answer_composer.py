from __future__ import annotations

import json
import re
from dataclasses import asdict

from langchain_core.messages import HumanMessage, SystemMessage

from app.enterprise_rag.core.types import AnswerPlan, CanonicalFact, EnterpriseRagAnswer, EvidencePack
from app.graph import get_llm


INSUFFICIENT_EVIDENCE_TEXT = "当前企业知识库中没有检索到足够可靠证据来回答这个问题。"
PARTIAL_EVIDENCE_PREFIX = "基于现有证据可确认的是："
RECOMMENDATION_PREFIX = "基于现有证据，相关建议是："

ANSWER_INTENTS = ("recommendation", "explanation", "fact_lookup", "mixed")

RECOMMENDATION_RULES = (
    (re.compile(r"\bwhat did .* recommend\b", re.IGNORECASE), "Matched an explicit recommendation question."),
    (re.compile(r"\bhow should (we|the .*|users?) handle\b", re.IGNORECASE), "Matched an explicit handling question."),
    (re.compile(r"\bwhat should (we|the .*|users?) do\b", re.IGNORECASE), "Matched an explicit action question."),
    (re.compile(r"(建议|如何处理|怎么处理|应该怎么做)"), "Matched a high-confidence recommendation phrase."),
)
EXPLANATION_RULES = (
    (re.compile(r"\bwhy\b", re.IGNORECASE), "Matched an explicit why question."),
    (re.compile(r"(原因|原理|为什么)"), "Matched an explanation phrase."),
)
FACT_LOOKUP_RULES = (
    (re.compile(r"\bdid (the notes|they) mention\b", re.IGNORECASE), "Matched a direct mention lookup."),
    (re.compile(r"\bwhat (was|is) the (text|wording|copy)\b", re.IGNORECASE), "Matched a wording lookup."),
    (re.compile(r"(是否提到|文案|措辞|错误文案|按钮文案)"), "Matched a fact lookup phrase."),
)
MIXED_RULE_HINTS = (
    re.compile(r"\bwhy\b", re.IGNORECASE),
    re.compile(r"\bhow should\b", re.IGNORECASE),
    re.compile(r"(为什么.*怎么处理|原因.*建议|原因.*如何处理)"),
)

QUESTION_FOCUS_PATTERNS: dict[str, tuple[re.Pattern[str], str]] = {
    "error_state": (re.compile(r"(error state|error text|entitlement not found|错误状态|错误文案)", re.IGNORECASE), "Question explicitly asks about an error state."),
    "message_text": (re.compile(r"(message text|wording|copy|文案|措辞|提示语|显示什么)", re.IGNORECASE), "Question explicitly asks about UI wording."),
    "support_channel": (re.compile(r"(support channel|support path|support team|支持渠道|支持团队)", re.IGNORECASE), "Question explicitly asks about support routing."),
    "sla": (re.compile(r"\bSLA\b|服务级别|响应时间", re.IGNORECASE), "Question explicitly asks about SLA detail."),
    "quoted_text": (re.compile(r"['\"“”‘’].+?['\"“”‘’]"), "Question explicitly references quoted wording."),
    "button_or_field": (re.compile(r"(refresh button|retry button|按钮|字段|label)", re.IGNORECASE), "Question explicitly asks about a button or field."),
}
FOCUS_SLOT_MAP: dict[str, list[str]] = {
    "error_state": ["error_avoidance", "user_facing_message"],
    "message_text": ["user_facing_message"],
    "support_channel": ["support_path", "fallback_path"],
    "sla": ["support_path", "fallback_path"],
    "quoted_text": ["user_facing_message"],
    "button_or_field": ["recovery_step", "last_checked_feedback"],
}

SECONDARY_DETAIL_HINTS = (
    "support sla",
    "sla",
    "support channel",
    "current error state",
    "entitlement not found",
    "invite you",
    "forward this entitlement id",
    "update copy",
)
TRANSCRIPT_STYLE_HINTS = (
    "action item",
    "update copy",
    "owner:",
    "speaker:",
    "attendees:",
)

NON_RECOMMENDATION_PROMPT = """You answer enterprise knowledge questions in Chinese.

Rules:
- Use only the supplied facts and runtime context.
- Keep the answer concise and direct.
- Never include speaker names, timestamps, meeting-log formatting, or raw transcript quotes.
- If the question is asking for a direct fact, answer that fact first.
- If the evidence is partial, say so conservatively without inventing anything.
"""

RECOMMENDATION_RENDER_PROMPT = """You are a technical summarization assistant, not a meeting transcript forwarder.

Use only the structured observations below:
- canonical/core facts
- answer slots
- answer plan

Rules:
- Answer in Chinese.
- Do not output speaker names, timestamps, action-item wording, or raw meeting transcript style.
- Do not quote or paste raw English evidence sentences.
- Do not invent facts that are not present in the structured facts.
- For recommendation questions, give a direct synthesized recommendation first, then concise handling details.
- If evidence is partial but enough to answer, use conservative wording rather than falling back to a fact list.
"""


def _parse_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("No JSON object found.")


def _matches_any_rule(question: str, rules: tuple[tuple[re.Pattern[str], str], ...]) -> tuple[bool, str]:
    for pattern, reason in rules:
        if pattern.search(question):
            return True, reason
    return False, ""


def _classify_answer_intent(question: str, question_type: str) -> dict[str, object]:
    mixed_rule = sum(1 for pattern in MIXED_RULE_HINTS if pattern.search(question))
    recommendation_match, recommendation_reason = _matches_any_rule(question, RECOMMENDATION_RULES)
    explanation_match, explanation_reason = _matches_any_rule(question, EXPLANATION_RULES)
    fact_lookup_match, fact_lookup_reason = _matches_any_rule(question, FACT_LOOKUP_RULES)

    if mixed_rule >= 2 or (recommendation_match and explanation_match):
        return {
            "answer_intent": "mixed",
            "classifier_source": "rule",
            "classifier_confidence": 0.95,
            "classifier_reason": "Matched both recommendation and explanation signals.",
        }
    if fact_lookup_match and not recommendation_match:
        return {
            "answer_intent": "fact_lookup",
            "classifier_source": "rule",
            "classifier_confidence": 0.94,
            "classifier_reason": fact_lookup_reason,
        }
    if recommendation_match and not fact_lookup_match:
        return {
            "answer_intent": "recommendation",
            "classifier_source": "rule",
            "classifier_confidence": 0.94,
            "classifier_reason": recommendation_reason,
        }
    if explanation_match and not recommendation_match:
        return {
            "answer_intent": "explanation",
            "classifier_source": "rule",
            "classifier_confidence": 0.93,
            "classifier_reason": explanation_reason,
        }

    prompt = """Classify the enterprise question into exactly one label.

Allowed labels:
- recommendation
- explanation
- fact_lookup
- mixed

Definitions:
- recommendation: asks what should be done, what was recommended, how to handle something.
- explanation: asks why something happened, cause, rationale, principle.
- fact_lookup: asks whether something was mentioned or asks for exact wording / specific detail.
- mixed: asks for both explanation and action, or mixes high-level recommendation with a specific detail lookup.

Return JSON only:
{"answer_intent": "...", "classifier_confidence": 0.0, "classifier_reason": "..."}
"""
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(content=json.dumps({"question": question, "question_type": question_type}, ensure_ascii=False)),
            ]
        )
        parsed = _parse_json_object(str(getattr(response, "content", response)))
        intent = str(parsed.get("answer_intent") or "").strip()
        if intent in ANSWER_INTENTS:
            return {
                "answer_intent": intent,
                "classifier_source": "llm",
                "classifier_confidence": float(parsed.get("classifier_confidence") or 0.72),
                "classifier_reason": str(parsed.get("classifier_reason") or "LLM classified the answer intent."),
            }
    except Exception:
        pass

    fallback_intent = "recommendation" if question_type in {"semantic", "constrained"} else "fact_lookup"
    return {
        "answer_intent": fallback_intent,
        "classifier_source": "llm",
        "classifier_confidence": 0.51,
        "classifier_reason": "Fell back to the default intent because rule and LLM classification were inconclusive.",
    }


def _detect_question_focus(question: str) -> dict[str, object]:
    labels: list[str] = []
    reasons: list[str] = []
    for label, (pattern, reason) in QUESTION_FOCUS_PATTERNS.items():
        if pattern.search(question):
            labels.append(label)
            reasons.append(reason)
    focused_slots = _dedupe_values([slot for label in labels for slot in FOCUS_SLOT_MAP.get(label, [])])
    focused_entities = _dedupe_values([match.group(0).strip() for pattern, _reason in QUESTION_FOCUS_PATTERNS.values() for match in pattern.finditer(question)])
    return {
        "labels": labels,
        "focus_type": labels[0] if labels else "general",
        "focused_entities": focused_entities,
        "focused_slots": focused_slots,
        "confidence": 0.9 if labels else 0.35,
        "source_fact_ids": [],
        "reason": "; ".join(reasons) if reasons else "No narrow secondary-detail focus detected.",
    }


def _attach_focus_source_fact_ids(question_focus: dict[str, object], facts: list[CanonicalFact]) -> dict[str, object]:
    focus_labels = list(question_focus.get("labels") or [])
    if not focus_labels:
        return question_focus
    source_fact_ids: list[str] = []
    for fact in facts:
        if not _fact_matches_focus(fact, focus_labels):
            continue
        for source_id in fact.source_fact_ids:
            if source_id and source_id not in source_fact_ids:
                source_fact_ids.append(source_id)
    updated = dict(question_focus)
    updated["source_fact_ids"] = source_fact_ids[:8]
    return updated


def _override_intent_for_focus(answer_intent: str, focus_labels: list[str]) -> tuple[str, str]:
    detail_focus = {"error_state", "message_text", "support_channel", "sla", "quoted_text", "button_or_field"}
    if not detail_focus.intersection(focus_labels):
        return answer_intent, "No question-focus override was required."
    if answer_intent == "recommendation":
        return "fact_lookup", "Question focus explicitly targets a detail that should be answered directly."
    if answer_intent == "explanation":
        return "mixed", "Question focus mixes explanation with a specific detail lookup."
    return answer_intent, "Question focus was already aligned with the chosen answer intent."


def _fact_matches_focus(fact: CanonicalFact, focus_labels: list[str]) -> bool:
    lowered = fact.normalized_fact.lower()
    if "error_state" in focus_labels and any(token in lowered for token in ("error state", "not entitled", "entitlement not found", "error")):
        return True
    if "message_text" in focus_labels and any(token in lowered for token in ("say", "wording", "copy", "show", "text", "message")):
        return True
    if "support_channel" in focus_labels and "support" in lowered:
        return True
    if "sla" in focus_labels and "sla" in lowered:
        return True
    if "button_or_field" in focus_labels and any(token in lowered for token in ("button", "refresh", "retry", "label")):
        return True
    if "quoted_text" in focus_labels and any(token in lowered for token in ("say", "show", "text", "wording", "copy")):
        return True
    return False


def _fact_matches_question(question: str, fact: CanonicalFact) -> bool:
    fact_lower = fact.normalized_fact.lower()
    query_terms = [term for term in re.split(r"[^a-z0-9]+", question.lower()) if len(term) > 3]
    return any(term in fact_lower for term in query_terms)


def _is_secondary_detail(fact: CanonicalFact) -> bool:
    lowered = fact.normalized_fact.lower()
    return any(token in lowered for token in SECONDARY_DETAIL_HINTS)


def _is_transcript_noise_fact(fact: CanonicalFact) -> bool:
    lowered = fact.normalized_fact.lower()
    if any(token in lowered for token in ("action item", "update copy", "owner:", "attendees:", "joins late", "walkthrough", "dry run", "rounding differences", "simple diagram")):
        return True
    if lowered.startswith(("chloe,", "priya,", "alex,", "sam,", "maya,")):
        return True
    return False


def _split_core_and_secondary_facts(
    question: str,
    canonical_facts: list[CanonicalFact],
    excluded_facts: list[CanonicalFact],
    *,
    answer_intent: str,
    focus_labels: list[str],
) -> tuple[list[CanonicalFact], list[CanonicalFact]]:
    core: list[CanonicalFact] = []
    secondary: list[CanonicalFact] = []
    combined = sorted([*canonical_facts, *excluded_facts], key=lambda item: item.score, reverse=True)
    seen: set[str] = set()
    for fact in combined:
        key = fact.normalized_fact.lower()
        if key in seen:
            continue
        seen.add(key)
        if fact.metadata.get("entity_alignment_required") and not fact.metadata.get("entity_aligned"):
            continue
        if _fact_matches_focus(fact, focus_labels):
            core.append(fact)
            continue
        if _is_transcript_noise_fact(fact):
            secondary.append(fact)
            continue
        if answer_intent == "recommendation":
            if _is_secondary_detail(fact):
                secondary.append(fact)
            else:
                core.append(fact)
        elif answer_intent == "fact_lookup":
            if _fact_matches_question(question, fact) or fact.fact_type in {"constraint", "support", "fallback"}:
                core.append(fact)
            else:
                secondary.append(fact)
        elif answer_intent == "explanation":
            if fact.fact_type in {"background", "constraint", "recommendation"} or _fact_matches_question(question, fact):
                core.append(fact)
            else:
                secondary.append(fact)
        else:
            if _is_secondary_detail(fact) and not _fact_matches_question(question, fact):
                secondary.append(fact)
            else:
                core.append(fact)
    return core[:8], secondary[:8]


def _render_canonical_fact(fact: CanonicalFact) -> str:
    text = fact.normalized_fact.strip()
    if text and text[-1] not in ".!?。！？；;":
        text += "。"
    return text


def _dedupe_values(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(value.strip())
    return output


def _build_answer_slots(core_facts: list[CanonicalFact]) -> dict[str, list[str]]:
    slots = {
        "issue_context": [],
        "recommendation_summary": [],
        "recommended_action": [],
        "recommended_state": [],
        "user_facing_message": [],
        "recovery_step": [],
        "fallback_path": [],
        "error_avoidance": [],
        "last_checked_feedback": [],
        "support_path": [],
    }
    for fact in core_facts:
        lowered = fact.normalized_fact.lower()
        rendered = _render_canonical_fact(fact)
        if any(token in lowered for token in ("delay", "delays", "propagation", "syncing", "subscription", "entitlement", "not immediately available", "not available")):
            slots["issue_context"].append(rendered)
        if any(token in lowered for token in ("pending", "gracefully", "rather than failed", "instead of failed", "not failed", "intermediate state")):
            slots["recommended_state"].append(rendered)
        if any(token in lowered for token in ("recommend", "should", "handle", "provide", "allow", "avoid", "tell the user", "include")):
            slots["recommended_action"].append(rendered)
        if any(token in lowered for token in ("still syncing", "few minutes", "may take", "tell the user", "say", "message", "copy", "wording")):
            slots["user_facing_message"].append(rendered)
        if any(token in lowered for token in ("retry", "refresh", "last checked")):
            slots["recovery_step"].append(rendered)
        if "last checked" in lowered:
            slots["last_checked_feedback"].append(rendered)
        if any(token in lowered for token in ("support", "contacting support", "support instructions", "support channel")):
            slots["fallback_path"].append(rendered)
            slots["support_path"].append(rendered)
        if any(token in lowered for token in ("don't show", "do not show", "not entitled", "avoid", "scary error", "entitlement not found")):
            slots["error_avoidance"].append(rendered)
        slots["recommendation_summary"].append(rendered)
    return {key: _dedupe_values(values) for key, values in slots.items()}


def _measure_slot_coverage(answer_slots: dict[str, list[str]]) -> dict[str, object]:
    tracked_generic_slots = [
        "issue_context",
        "recommended_state",
        "user_facing_message",
        "recovery_step",
        "fallback_path",
    ]
    filled = [slot for slot in tracked_generic_slots if answer_slots.get(slot)]
    missing = [slot for slot in tracked_generic_slots if slot not in filled]
    domain_filled = [slot for slot in ("error_avoidance", "last_checked_feedback", "support_path") if answer_slots.get(slot)]
    coverage = len(filled) / len(tracked_generic_slots)
    return {
        "filled_slots": filled,
        "missing_slots": missing,
        "domain_filled_slots": domain_filled,
        "coverage_ratio": coverage,
        "has_minimum_recommendation_structure": len(filled) >= 3 and bool(answer_slots.get("recommended_state") or answer_slots.get("recovery_step")),
    }


def _english_marker(answer: str) -> bool:
    english_tokens = re.findall(r"[A-Za-z]{4,}", answer or "")
    return len(english_tokens) >= 18


def _contains_transcript_style(answer: str, core_facts: list[CanonicalFact]) -> bool:
    lowered = (answer or "").lower()
    if any(token in lowered for token in TRANSCRIPT_STYLE_HINTS):
        return True
    if re.search(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", answer or ""):
        return True
    speakers = {str(item.metadata.get("speaker") or "").strip().lower() for item in core_facts if item.metadata.get("speaker")}
    speakers.discard("")
    return any(speaker and speaker in lowered for speaker in speakers)


def _postcheck_polished_answer(answer: str, core_facts: list[CanonicalFact]) -> tuple[bool, str]:
    if _contains_transcript_style(answer, core_facts):
        return False, "transcript_style_leak"
    if re.search(r"[\"“”'][A-Za-z][^\"“”']{20,}[\"“”']", answer or ""):
        return False, "quoted_english_leak"
    if _english_marker(answer):
        return False, "too_much_raw_english"
    return True, "none"


def _summarize_issue_context(answer_slots: dict[str, list[str]]) -> str:
    joined = " ".join(answer_slots.get("issue_context") or []).lower()
    if any(token in joined for token in ("entitlement", "subscription", "syncing", "propagation")):
        return "这类问题通常来自订阅 entitlement 的传播或同步存在延迟。"
    if joined:
        return "这类情况通常对应流程中的短暂中间状态，而不一定代表真正失败。"
    return ""


def _summarize_recommended_state(answer_slots: dict[str, list[str]]) -> str:
    joined = " ".join(answer_slots.get("recommended_state") or []).lower()
    if "pending" in joined:
        return "建议把它按待同步或待处理状态来处理，而不是直接判定为失败。"
    if joined:
        return "建议先按一个可恢复的中间状态处理，而不是直接给出最终失败结论。"
    return "建议先给用户一个可继续推进的中间状态，而不是直接阻断流程。"


def _summarize_error_avoidance(answer_slots: dict[str, list[str]]) -> str:
    joined = " ".join(answer_slots.get("error_avoidance") or []).lower()
    if "not entitled" in joined:
        return "界面上不要直接显示“not entitled”这类容易造成误解的报错。"
    if joined:
        return "界面上应避免直接暴露容易引发误解的失败报错。"
    return ""


def _summarize_user_message(answer_slots: dict[str, list[str]]) -> str:
    joined = " ".join(answer_slots.get("user_facing_message") or []).lower()
    if any(token in joined for token in ("still syncing", "few minutes", "may take")):
        return "更合适的做法是明确告诉用户订阅仍在同步，可能还需要几分钟。"
    if joined:
        return "同时要给用户一个明确、可理解的状态说明，而不是只给技术错误。"
    return ""


def _summarize_recovery_step(answer_slots: dict[str, list[str]]) -> str:
    joined = " ".join(answer_slots.get("recovery_step") or []).lower()
    if "last checked" in joined and any(token in joined for token in ("retry", "refresh")):
        return "还应提供清晰的重试或刷新入口，并在可能时展示最近一次检查时间。"
    if any(token in joined for token in ("retry", "refresh")):
        return "还应提供清晰的重试或刷新入口。"
    if joined:
        return "还应给出明确的后续操作入口，方便用户自行恢复。"
    return ""


def _summarize_fallback_path(answer_slots: dict[str, list[str]]) -> str:
    joined = " ".join(answer_slots.get("fallback_path") or []).lower()
    if "support" in joined:
        return "如果短时间内仍未恢复，再提供对应的支持渠道或处理指引。"
    if joined:
        return "如果无法自行恢复，应提供明确的后续求助路径。"
    return ""


def _build_recommendation_template(
    question: str,
    answer_slots: dict[str, list[str]],
    slot_coverage: dict[str, object],
) -> tuple[str, str]:
    weak = not bool(slot_coverage.get("has_minimum_recommendation_structure"))
    lines: list[str] = []
    opener = PARTIAL_EVIDENCE_PREFIX if weak else RECOMMENDATION_PREFIX
    lines.append(opener)
    lines.append(_summarize_recommended_state(answer_slots))
    context_sentence = _summarize_issue_context(answer_slots)
    if context_sentence:
        lines.append(context_sentence)
    for sentence in (
        _summarize_error_avoidance(answer_slots),
        _summarize_user_message(answer_slots),
        _summarize_recovery_step(answer_slots),
        _summarize_fallback_path(answer_slots),
    ):
        if sentence:
            lines.append(sentence)
    filtered_lines = [line for line in lines if line]
    return "\n".join(filtered_lines), ("weak_template" if weak else "template")


def _build_direct_fact_answer(question: str, core_facts: list[CanonicalFact]) -> str:
    if not core_facts:
        return INSUFFICIENT_EVIDENCE_TEXT
    top = [_render_canonical_fact(item) for item in core_facts[:2]]
    return PARTIAL_EVIDENCE_PREFIX + "\n" + "\n".join(top)


def _generate_non_recommendation_answer(
    *,
    answer_intent: str,
    question: str,
    core_facts: list[CanonicalFact],
    secondary_facts: list[CanonicalFact],
    question_focus: dict[str, object],
    context_text: str,
    evidence_text: str,
) -> tuple[str, str]:
    if not core_facts:
        return INSUFFICIENT_EVIDENCE_TEXT, "insufficient_core_facts"
    prompt = (
        f"Answer intent: {answer_intent}\n"
        f"Question:\n{question}\n\n"
        f"Question focus:\n{json.dumps(question_focus, ensure_ascii=False)}\n\n"
        "Core facts:\n"
        + "\n".join(f"- {_render_canonical_fact(item)}" for item in core_facts[:6])
        + "\n\nSecondary facts:\n"
        + ("\n".join(f"- {_render_canonical_fact(item)}" for item in secondary_facts[:4]) or "(none)")
        + f"\n\nRuntime context:\n{context_text or '(none)'}\n\n"
        + f"Evidence snippets:\n{evidence_text}\n\n"
        + "Answer in Chinese. If the intent is fact_lookup, answer the requested detail first. "
        + "If the intent is explanation, give the conclusion and then the cause. "
        + "If the intent is mixed, separate the factual confirmation from the recommended handling."
    )
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=NON_RECOMMENDATION_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        answer = str(response.content).strip()
        if answer:
            return answer, "none"
    except Exception:
        pass
    return _build_direct_fact_answer(question, core_facts), "generation_error"


def _polish_recommendation_template(
    question: str,
    template_answer: str,
    answer_slots: dict[str, list[str]],
    core_facts: list[CanonicalFact],
) -> tuple[str, bool, bool, str]:
    prompt = (
        "You are polishing a Chinese enterprise answer. "
        "Keep the existing structure and facts, but make the prose smoother. "
        "Do not add facts. Do not include speaker names, timestamps, quoted transcript text, or action-item phrasing."
    )
    slot_payload = {key: value for key, value in answer_slots.items() if value}
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=(
                        f"Question:\n{question}\n\n"
                        f"Template answer:\n{template_answer}\n\n"
                        f"Answer slots:\n{json.dumps(slot_payload, ensure_ascii=False)}\n\n"
                        "Return a concise polished Chinese answer."
                    )
                ),
            ]
        )
        polished = str(response.content).strip()
        accepted, reason = _postcheck_polished_answer(polished, core_facts)
        if accepted:
            return polished, True, False, "none"
        return template_answer, True, True, reason
    except Exception:
        return template_answer, False, True, "generation_error"


def _generate_recommendation_answer_from_state(
    *,
    question: str,
    core_facts: list[CanonicalFact],
    secondary_facts: list[CanonicalFact],
    answer_slots: dict[str, list[str]],
    slot_coverage: dict[str, object],
    answer_plan: AnswerPlan,
    question_focus: dict[str, object],
) -> tuple[str, str, str, dict[str, str]]:
    if not core_facts:
        return INSUFFICIENT_EVIDENCE_TEXT, "weak_template", "insufficient_core_facts", {}
    payload = {
        "question": question,
        "question_focus": question_focus,
        "core_facts": [asdict(fact) for fact in core_facts[:8]],
        "secondary_facts": [asdict(fact) for fact in secondary_facts[:4]],
        "answer_slots": {key: value for key, value in answer_slots.items() if value},
        "slot_coverage": slot_coverage,
        "answer_plan": asdict(answer_plan),
        "format": {
            "direct_answer": "1句直接结论",
            "details": "1到3句处理建议",
            "style": "自然中文总结，不要证据句罗列",
        },
    }
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=RECOMMENDATION_RENDER_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        answer = str(response.content).strip()
        accepted, reason = _postcheck_polished_answer(answer, core_facts)
        if answer and accepted:
            return answer, "llm_structured", "none", {}
        template_answer, template_source = _build_recommendation_template(question, answer_slots, slot_coverage)
        return template_answer, template_source, "transcript_style_leak" if reason != "none" else "generation_error", {
            "stage": "postcheck",
            "error_type": reason,
            "message": "Structured recommendation renderer output failed post-check.",
        }
    except Exception as exc:
        template_answer, template_source = _build_recommendation_template(question, answer_slots, slot_coverage)
        return template_answer, template_source, "generation_error", {
            "stage": "llm_invoke",
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
        }


def _dedupe_visible_citations(citations) -> list[dict]:
    visible: list[dict] = []
    seen_doc_ids: set[str] = set()
    seen_titles: set[str] = set()
    for item in citations:
        doc_id = str(item.doc_id or "").strip()
        title = str(item.title or "").strip().lower()
        if doc_id and doc_id in seen_doc_ids:
            continue
        if not doc_id and title and title in seen_titles:
            continue
        if doc_id:
            seen_doc_ids.add(doc_id)
        if title:
            seen_titles.add(title)
        visible.append(
            {
                "doc_id": item.doc_id,
                "chunk_id": item.chunk_id,
                "source_type": item.source_type,
                "title": item.title,
                "snippet": item.snippet,
                "score": item.score,
            }
        )
        if len(visible) >= 3:
            break
    return visible


def _dedupe_citation_objects(citations):
    visible = []
    seen_doc_ids: set[str] = set()
    seen_titles: set[str] = set()
    for item in citations:
        doc_id = str(item.doc_id or "").strip()
        title = str(item.title or "").strip().lower()
        if doc_id and doc_id in seen_doc_ids:
            continue
        if not doc_id and title and title in seen_titles:
            continue
        if doc_id:
            seen_doc_ids.add(doc_id)
        if title:
            seen_titles.add(title)
        visible.append(item)
        if len(visible) >= 3:
            break
    return visible


def _build_answer_plan(answer_intent: str, answer_slots: dict[str, list[str]], question_focus: dict[str, object]) -> AnswerPlan:
    if answer_intent == "recommendation":
        direct_answer = "先给出建议结论，再解释状态处理方式和用户可见动作。"
        key_points = []
        if answer_slots.get("recommended_state"):
            key_points.append("把问题按中间状态处理，而不是直接视为失败。")
        if answer_slots.get("error_avoidance"):
            key_points.append("避免对用户展示容易误解的失败报错。")
        if answer_slots.get("user_facing_message"):
            key_points.append("明确说明当前仍在同步或处理中。")
        if answer_slots.get("recovery_step"):
            key_points.append("提供清晰的重试或刷新入口。")
        if answer_slots.get("fallback_path"):
            key_points.append("在仍未恢复时提供支持渠道或处理指引。")
        return AnswerPlan(
            question_type=answer_intent,
            direct_answer=direct_answer,
            key_points=key_points,
            optional_context="；".join(answer_slots.get("issue_context") or [])[:180],
            excluded_content=["speaker names", "timestamps", "quoted transcript wording", "meeting action items"],
            style_notes=["优先输出中文归纳结论。", "不要直接抄写英文原句。"],
        )
    return AnswerPlan(
        question_type=answer_intent,
        direct_answer="先直接回答问题，再补充必要的证据限定。",
        key_points=[f"问题焦点：{label}" for label in (question_focus.get("labels") or [])][:4],
        optional_context="",
        excluded_content=["speaker names", "timestamps", "quoted transcript wording"],
        style_notes=["保持简洁直接。"],
    )


def _extract_fact_hits(question: str, evidence: EvidencePack) -> list[str]:
    hits = []
    lowered_question = (question or "").lower()
    query_terms = [term for term in lowered_question.split() if term]
    for fact in evidence.supporting_facts:
        fact_lower = fact.lower()
        if any(token in fact_lower for token in query_terms):
            hits.append(fact)
    return hits[:6] or evidence.supporting_facts[:4]


def compose_enterprise_answer(
    question: str,
    evidence: EvidencePack,
    *,
    question_type: str = "basic",
    context_text: str = "",
    context_sources: list[str] | None = None,
    workspace_memory_hits: int = 0,
    transcript_hits: int = 0,
    user_model_used: bool = False,
) -> EnterpriseRagAnswer:
    classifier = _classify_answer_intent(question, question_type)
    question_focus = _detect_question_focus(question)
    answer_intent, override_reason = _override_intent_for_focus(
        str(classifier["answer_intent"]),
        list(question_focus.get("labels") or []),
    )
    core_facts, secondary_facts = _split_core_and_secondary_facts(
        question,
        list(evidence.canonical_facts),
        list(evidence.excluded_facts),
        answer_intent=answer_intent,
        focus_labels=list(question_focus.get("labels") or []),
    )
    question_focus = _attach_focus_source_fact_ids(question_focus, [*core_facts, *secondary_facts])
    answer_slots = _build_answer_slots(core_facts) if answer_intent == "recommendation" else {}
    slot_coverage = _measure_slot_coverage(answer_slots) if answer_intent == "recommendation" else {}
    answer_plan = _build_answer_plan(answer_intent, answer_slots, question_focus)
    entity_alignment_required = any(
        bool(fact.metadata.get("entity_alignment_required"))
        for fact in [*evidence.canonical_facts, *evidence.excluded_facts]
    )
    aligned_doc_ids = {
        str(fact.metadata.get("doc_id") or "")
        for fact in core_facts
        if str(fact.metadata.get("doc_id") or "")
    }
    answer_citations = [
        item
        for item in evidence.citations
        if not entity_alignment_required or item.doc_id in aligned_doc_ids
    ]
    evidence_text = "\n\n".join(
        [
            f"[{index}] doc_id={item.doc_id} source={item.source_type} title={item.title}\n{item.snippet}"
            for index, item in enumerate(answer_citations, start=1)
        ]
    )
    visible_citations = _dedupe_visible_citations(answer_citations)
    visible_citation_objects = _dedupe_citation_objects(answer_citations)

    draft_answer = ""
    rewritten_answer = ""
    rewrite_applied = False
    polish_applied = False
    polish_rejected = False
    polish_rejected_reason = "none"
    fallback_reason = "none"
    final_answer_source = "template"
    renderer_error: dict[str, str] = {}

    if evidence.missing_evidence or not core_facts:
        answer = INSUFFICIENT_EVIDENCE_TEXT
        fallback_reason = "insufficient_core_facts"
        final_answer_source = "weak_template"
    elif answer_intent == "recommendation":
        answer, final_answer_source, fallback_reason, renderer_error = _generate_recommendation_answer_from_state(
            question=question,
            core_facts=core_facts,
            secondary_facts=secondary_facts,
            answer_slots=answer_slots,
            slot_coverage=slot_coverage,
            answer_plan=answer_plan,
            question_focus=question_focus,
        )
        draft_answer = answer
        if fallback_reason == "none" and not bool(slot_coverage.get("has_minimum_recommendation_structure")):
            # Low slot coverage is diagnostic only when the structured renderer still produced a safe answer.
            final_answer_source = "llm_structured_partial"
        elif fallback_reason == "generation_error" and not bool(slot_coverage.get("has_minimum_recommendation_structure")):
            fallback_reason = "slot_coverage_too_low"
        rewrite_applied = final_answer_source.startswith("llm_structured")
        rewritten_answer = answer if rewrite_applied else ""
    else:
        answer, fallback_reason = _generate_non_recommendation_answer(
            answer_intent=answer_intent,
            question=question,
            core_facts=core_facts,
            secondary_facts=secondary_facts,
            question_focus=question_focus,
            context_text=context_text,
            evidence_text=evidence_text,
        )
        draft_answer = answer
        final_answer_source = "template" if fallback_reason == "none" else "weak_template"

    if _contains_transcript_style(answer, core_facts):
        fallback_reason = "transcript_style_leak"
        if answer_intent == "recommendation":
            answer, final_answer_source = _build_recommendation_template(question, answer_slots, {"has_minimum_recommendation_structure": False})
        else:
            answer = _build_direct_fact_answer(question, core_facts)
            final_answer_source = "weak_template"

    return EnterpriseRagAnswer(
        answer=answer,
        citations=visible_citation_objects,
        supporting_doc_ids=sorted(aligned_doc_ids) if entity_alignment_required else evidence.supporting_doc_ids,
        missing_evidence=evidence.missing_evidence,
        confidence=evidence.confidence,
        supporting_facts=evidence.supporting_facts,
        supporting_fact_details=evidence.supporting_fact_details,
        canonical_facts=evidence.canonical_facts,
        retrieval_stage_debug=evidence.retrieval_stage_debug,
        rerank_debug=evidence.rerank_debug,
        evidence_fact_hits=_extract_fact_hits(question, evidence),
        answer_debug={
            "answerable": bool(core_facts),
            "answer_intent": answer_intent,
            "classifier_source": classifier["classifier_source"],
            "classifier_confidence": classifier["classifier_confidence"],
            "classifier_reason": classifier["classifier_reason"],
            "question_focus": question_focus,
            "question_focus_override_reason": override_reason,
            "confidence_reason": "Core facts and answer slots were assembled for the final answer." if core_facts else "No core facts were retained.",
            "core_facts": [asdict(fact) for fact in core_facts],
            "secondary_facts": [asdict(fact) for fact in secondary_facts],
            "canonical_facts": [asdict(fact) for fact in evidence.canonical_facts],
            "excluded_facts": [asdict(fact) for fact in evidence.excluded_facts],
            "answer_slots": answer_slots,
            "slot_coverage": slot_coverage,
            "answer_plan": asdict(answer_plan),
            "draft_answer": draft_answer,
            "rewritten_answer": rewritten_answer,
            "rewrite_applied": rewrite_applied,
            "polish_applied": polish_applied,
            "polish_rejected": polish_rejected,
            "polish_rejected_reason": polish_rejected_reason,
            "renderer_error": renderer_error,
            "fallback_reason": fallback_reason,
            "final_answer_source": final_answer_source,
            "template_used": answer_intent == "recommendation",
            "visible_citation_count": len(visible_citations),
            "deduped_citations": visible_citations,
            "duplicate_visible_citations": max(0, min(len(evidence.citations), 3) - len(visible_citations)),
        },
        context_sources=list(context_sources or []),
        workspace_memory_hits=workspace_memory_hits,
        transcript_hits=transcript_hits,
        user_model_used=user_model_used,
    )
