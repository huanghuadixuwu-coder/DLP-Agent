from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.conversation_memory import compact_text
from app.graph import get_llm
from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND


SOURCE_RESOLVER_PROMPT = """You resolve content sources for a secure enterprise mail agent.

The user is asking for a mail action. Decide which available candidate content the user intends to use.
Return strict JSON only:
{
  "referential_request": true,
  "selected_candidate_ids": ["candidate-id"],
  "source_mode": "inline_body|uploaded_content|prior_assistant_answer|prior_user_text|meeting_result|mail_thread|communication_brief|none",
  "compose_mode": "direct_body|recipient_ready_summary|synthesize|verbatim_copy",
  "needs_clarification": false,
  "confidence": 0.0,
  "reason": ""
}

Rules:
- Interpret semantic references, not just fixed keywords. Examples include Chinese phrases such as "该信息", "这个方案", "前面提到的时限", "把关于某主题的处理方式发给对方", and equivalent English expressions.
- Select only candidate IDs that exist in the provided candidate list.
- A user_inline_text candidate is direct body content.
- An uploaded_text candidate is uploaded content and must retain its attachment/source boundary.
- A prior assistant answer is reference material. Default to recipient_ready_summary so the mail renderer rewrites it for the recipient and removes conversational scaffolding.
- A meeting_result candidate is reference material for a meeting invitation or meeting follow-up email.
- A mail_thread candidate is reference material from an existing thread and should keep its provenance boundary.
- A communication_brief candidate is the preferred structured closeout source. Treat it as reference material for recipient-ready rendering, not direct body text.
- Use synthesize when the user explicitly asks to combine multiple prior sources.
- Use verbatim_copy only when the user explicitly requests exact, unchanged forwarding.
- If multiple candidates are plausible and the user did not identify the intended one, set needs_clarification=true and do not guess.
- If no candidate supports the request, set source_mode=none and needs_clarification=true.
"""

ALLOWED_SOURCE_MODES = {"inline_body", "uploaded_content", "prior_assistant_answer", "prior_user_text", "meeting_result", "mail_thread", "communication_brief", "none"}
ALLOWED_COMPOSE_MODES = {"direct_body", "recipient_ready_summary", "synthesize", "verbatim_copy"}
REFERENCE_SOURCE_KINDS = {"assistant_last_answer", "meeting_result", "mail_thread", "user_recent_text", COMMUNICATION_BRIEF_SOURCE_KIND}
HIGH_CONFIDENCE_REFERENCE_MARKERS = (
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
GENERIC_STOP_WORDS = {
    "the",
    "and",
    "for",
    "to",
    "with",
    "send",
    "mail",
    "email",
    "this",
    "that",
    "information",
}


def _explicit_brief_reference(message: str) -> bool:
    text = message or ""
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "active brief",
            "latest brief",
            "communication brief",
            "current brief",
            "the brief",
        )
    )


def _explicit_prior_assistant_reference(message: str) -> bool:
    text = message or ""
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "prior assistant answer",
            "previous assistant answer",
            "last assistant answer",
            "earlier assistant answer",
            "prior answer",
            "previous answer",
            "last answer",
            "earlier answer",
            "assistant answer",
            "上一轮回答",
            "上一条回答",
            "刚才的回答",
            "前面提到的回答",
            "前面提到的",
            "上面的方案",
            "上面的信息",
            "上面的内容",
            "该回答",
            "上述回答",
        )
    )


def resolve_mail_source_request(
    *,
    message: str,
    candidates: list[dict[str, Any]],
    legacy_referential_request: bool = False,
    explicit_summary: bool = False,
) -> dict[str, Any]:
    normalized_candidates = [_normalize_candidate(item, index=index) for index, item in enumerate(candidates)]
    inline = next((item for item in normalized_candidates if item["kind"] == "user_inline_text"), None)
    if inline:
        return _resolution(
            selected_candidate_ids=[inline["candidate_id"]],
            source_mode="inline_body",
            compose_mode="direct_body",
            referential_request=False,
            confidence=1.0,
            reason="The current user message contains an explicit inline body.",
            classifier_source="deterministic_explicit_body",
        )

    if not normalized_candidates:
        return _resolution(
            source_mode="none",
            needs_clarification=True,
            confidence=1.0,
            reason="No outbound content candidates are available.",
            classifier_source="deterministic_empty_candidates",
        )
    uploaded = next((item for item in normalized_candidates if item["kind"] == "uploaded_text"), None)
    if uploaded:
        return _resolution(
            selected_candidate_ids=[uploaded["candidate_id"]],
            source_mode="uploaded_content",
            compose_mode="direct_body",
            referential_request=False,
            confidence=0.9,
            reason="Selected the explicit uploaded content source before prior communication briefs.",
            classifier_source="deterministic_uploaded_content",
        )
    brief_candidates = [item for item in normalized_candidates if item["kind"] == COMMUNICATION_BRIEF_SOURCE_KIND]
    if _explicit_brief_reference(message):
        if len(brief_candidates) == 1:
            return _resolution(
                selected_candidate_ids=[brief_candidates[0]["candidate_id"]],
                source_mode="communication_brief",
                compose_mode="recipient_ready_summary",
                referential_request=True,
                confidence=0.95,
                reason="Selected the explicitly referenced communication brief.",
                classifier_source="deterministic_explicit_communication_brief_reference",
            )
        if len(brief_candidates) > 1:
            return _resolution(
                source_mode="none",
                needs_clarification=True,
                confidence=0.9,
                reason="The user referenced a communication brief, but multiple briefs are available.",
                classifier_source="deterministic_explicit_communication_brief_ambiguous",
            )
    assistant_candidates = [item for item in normalized_candidates if item["kind"] == "assistant_last_answer"]
    anchored_assistant = _deterministic_anchor_match(message, assistant_candidates)
    if anchored_assistant and _explicit_prior_assistant_reference(message):
        return _resolution(
            selected_candidate_ids=[anchored_assistant["candidate_id"]],
            source_mode="prior_assistant_answer",
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=float(anchored_assistant.get("confidence") or 0.0),
            reason="Selected the prior assistant answer matched by explicit request anchors before default communication brief closeout.",
            classifier_source="deterministic_prior_assistant_anchor_match",
        )
    if _explicit_prior_assistant_reference(message):
        if len(assistant_candidates) == 1:
            return _resolution(
                selected_candidate_ids=[assistant_candidates[0]["candidate_id"]],
                source_mode="prior_assistant_answer",
                compose_mode="recipient_ready_summary",
                referential_request=True,
                confidence=0.88,
                reason="Selected the single prior assistant answer explicitly requested by the user.",
                classifier_source="deterministic_explicit_prior_assistant_reference",
            )
        if len(assistant_candidates) > 1:
            return _resolution(
                source_mode="none",
                needs_clarification=True,
                confidence=0.86,
                reason="The user requested a prior assistant answer, but multiple prior answers are available.",
                classifier_source="deterministic_explicit_prior_assistant_ambiguous",
            )
        return _resolution(
            source_mode="none",
            needs_clarification=True,
            confidence=0.9,
            reason="The user requested a prior assistant answer, but no prior assistant answer source is available.",
            classifier_source="deterministic_explicit_prior_assistant_unavailable",
        )
    competing_reference_candidates = [
        item
        for item in normalized_candidates
        if item["kind"] in REFERENCE_SOURCE_KINDS
        and item["kind"] not in {COMMUNICATION_BRIEF_SOURCE_KIND, "assistant_last_answer"}
    ]
    anchored_reference = _deterministic_anchor_match(message, competing_reference_candidates)
    if anchored_reference:
        return _resolution(
            selected_candidate_ids=[anchored_reference["candidate_id"]],
            source_mode=_source_mode_for_kind(str(anchored_reference.get("kind") or "")),
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=float(anchored_reference.get("confidence") or 0.0),
            reason="Selected the explicit reference candidate matched by request anchors before prior communication briefs.",
            classifier_source="deterministic_reference_anchor_match",
        )
    if len(brief_candidates) == 1:
        return _resolution(
            selected_candidate_ids=[brief_candidates[0]["candidate_id"]],
            source_mode="communication_brief",
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=0.93,
            reason="Selected the structured communication brief as the preferred mail closeout source.",
            classifier_source="deterministic_communication_brief",
        )
    if len(brief_candidates) > 1:
        return _resolution(
            source_mode="none",
            needs_clarification=True,
            confidence=0.9,
            reason="Multiple communication briefs are available; the intended closeout source is ambiguous.",
            classifier_source="deterministic_communication_brief_ambiguous",
        )
    if anchored_assistant:
        return _resolution(
            selected_candidate_ids=[anchored_assistant["candidate_id"]],
            source_mode="prior_assistant_answer",
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=float(anchored_assistant.get("confidence") or 0.0),
            reason="Selected the prior assistant answer matched by request anchors because no communication brief was available.",
            classifier_source="deterministic_prior_assistant_anchor_match",
        )
    if (legacy_referential_request or explicit_summary) and len(assistant_candidates) == 1:
        return _resolution(
            selected_candidate_ids=[assistant_candidates[0]["candidate_id"]],
            source_mode="prior_assistant_answer",
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=0.82,
            reason="Selected the single prior assistant answer for an explicit referential mail request.",
            classifier_source="deterministic_prior_assistant_reference",
        )

    try:
        settings = get_settings()
        response = get_llm(
            model=settings.llm_model_router,
            timeout=max(settings.llm_router_timeout_seconds, 1.0),
            temperature=0.0,
            max_retries=settings.llm_router_max_retries,
        ).invoke(
            [
                SystemMessage(content=SOURCE_RESOLVER_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "message": message,
                            "candidates": [
                                {
                                    "candidate_id": item["candidate_id"],
                                    "kind": item["kind"],
                                    "label": item["label"],
                                    "content_preview": compact_text(item["content"], 900),
                                }
                                for item in normalized_candidates
                            ],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        parsed = _parse_json_object(str(getattr(response, "content", response)))
        return _validate_llm_resolution(parsed, normalized_candidates)
    except Exception as exc:
        return _safe_fallback(
            candidates=normalized_candidates,
            message=message,
            legacy_referential_request=legacy_referential_request,
            explicit_summary=explicit_summary,
            error=str(exc),
        )


def apply_source_resolution(
    candidates: list[dict[str, Any]],
    source_resolution: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    selected_ids = [str(item) for item in list((source_resolution or {}).get("selected_candidate_ids") or []) if str(item)]
    if not selected_ids:
        return list(candidates)
    rank = {candidate_id: index for index, candidate_id in enumerate(selected_ids)}
    return sorted(
        list(candidates),
        key=lambda item: (rank.get(str(item.get("candidate_id") or ""), len(rank)),),
    )


def _normalize_candidate(candidate: dict[str, Any], *, index: int) -> dict[str, str]:
    item = dict(candidate or {})
    kind = str(item.get("kind") or "")
    candidate_id = str(item.get("candidate_id") or f"{kind or 'candidate'}:{index}")
    return {
        "candidate_id": candidate_id,
        "kind": kind,
        "label": str(item.get("label") or kind or candidate_id),
        "content": str(item.get("content") or ""),
    }


def _validate_llm_resolution(parsed: dict[str, Any], candidates: list[dict[str, str]]) -> dict[str, Any]:
    available_ids = {item["candidate_id"] for item in candidates}
    selected_ids = [
        str(item)
        for item in list(parsed.get("selected_candidate_ids") or [])
        if str(item) in available_ids
    ]
    source_mode = str(parsed.get("source_mode") or "none")
    compose_mode = str(parsed.get("compose_mode") or "recipient_ready_summary")
    if source_mode not in ALLOWED_SOURCE_MODES:
        source_mode = "none"
    if compose_mode not in ALLOWED_COMPOSE_MODES:
        compose_mode = "recipient_ready_summary"
    needs_clarification = bool(parsed.get("needs_clarification"))
    if source_mode != "none" and not selected_ids:
        needs_clarification = True
        source_mode = "none"
    if source_mode == "none":
        needs_clarification = True
    return _resolution(
        selected_candidate_ids=selected_ids,
        source_mode=source_mode,
        compose_mode=compose_mode,
        referential_request=bool(parsed.get("referential_request")),
        needs_clarification=needs_clarification,
        confidence=float(parsed.get("confidence") or 0.0),
        reason=str(parsed.get("reason") or ""),
        classifier_source="llm",
    )


def _safe_fallback(
    *,
    candidates: list[dict[str, str]],
    message: str,
    legacy_referential_request: bool,
    explicit_summary: bool,
    error: str,
) -> dict[str, Any]:
    uploaded = next((item for item in candidates if item["kind"] == "uploaded_text"), None)
    if uploaded:
        return _resolution(
            selected_candidate_ids=[uploaded["candidate_id"]],
            source_mode="uploaded_content",
            compose_mode="direct_body",
            referential_request=False,
            confidence=0.8,
            reason="LLM source resolution failed; preserved the explicit upload boundary.",
            classifier_source="safe_fallback",
            classifier_error=error,
        )
    brief_candidates = [item for item in candidates if item["kind"] == COMMUNICATION_BRIEF_SOURCE_KIND]
    reference_candidates = [
        item
        for item in candidates
        if item["kind"] in REFERENCE_SOURCE_KINDS
        and item["kind"] not in {COMMUNICATION_BRIEF_SOURCE_KIND, "assistant_last_answer"}
    ]
    assistant_candidates = [item for item in candidates if item["kind"] == "assistant_last_answer"]
    anchored = _deterministic_anchor_match(message, reference_candidates)
    if anchored:
        return _resolution(
            selected_candidate_ids=[anchored["candidate_id"]],
            source_mode=_source_mode_for_kind(str(anchored.get("kind") or "")),
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=float(anchored.get("confidence") or 0.0),
            reason="LLM source resolution failed; selected the unique reference candidate matched by request anchors.",
            classifier_source="safe_fallback_anchor_match",
            classifier_error=error,
        )
    if len(brief_candidates) == 1:
        return _resolution(
            selected_candidate_ids=[brief_candidates[0]["candidate_id"]],
            source_mode="communication_brief",
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=0.82,
            reason="LLM source resolution failed; selected the structured communication brief as the preferred closeout source.",
            classifier_source="safe_fallback_communication_brief",
            classifier_error=error,
        )
    if len(brief_candidates) > 1:
        return _resolution(
            source_mode="none",
            needs_clarification=True,
            confidence=0.8,
            reason="LLM source resolution failed and multiple communication briefs are available.",
            classifier_source="safe_fallback_communication_brief_ambiguous",
            classifier_error=error,
        )
    has_high_confidence_reference = any(marker in message or marker in message.lower() for marker in HIGH_CONFIDENCE_REFERENCE_MARKERS)
    if (legacy_referential_request or explicit_summary or has_high_confidence_reference) and len(assistant_candidates) == 1:
        return _resolution(
            selected_candidate_ids=[assistant_candidates[0]["candidate_id"]],
            source_mode="prior_assistant_answer",
            compose_mode="recipient_ready_summary",
            referential_request=True,
            confidence=0.72,
            reason="LLM source resolution failed; used the single explicit prior-answer reference.",
            classifier_source="safe_fallback",
            classifier_error=error,
        )
    return _resolution(
        source_mode="none",
        needs_clarification=True,
        confidence=0.0,
        reason="LLM source resolution failed and no safe deterministic source could be selected.",
        classifier_source="safe_fallback",
        classifier_error=error,
    )


def _source_mode_for_kind(kind: str) -> str:
    if kind == "uploaded_text":
        return "uploaded_content"
    if kind == "meeting_result":
        return "meeting_result"
    if kind == "mail_thread":
        return "mail_thread"
    if kind == COMMUNICATION_BRIEF_SOURCE_KIND:
        return "communication_brief"
    if kind == "user_recent_text":
        return "prior_user_text"
    if kind == "assistant_last_answer":
        return "prior_assistant_answer"
    if kind == "user_inline_text":
        return "inline_body"
    return "none"


def _deterministic_anchor_match(message: str, candidates: list[dict[str, str]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    anchors = _request_anchors(message)
    if not anchors:
        return None
    scored: list[tuple[float, dict[str, str]]] = []
    for item in candidates:
        haystack = f"{item.get('label', '')} {item.get('content', '')}".lower()
        score = 0.0
        for anchor in anchors:
            if anchor in haystack:
                score += 2.0 if len(anchor) >= 4 else 1.0
        if score:
            scored.append((score, item))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 2.0 or best_score < second_score + 1.0:
        return None
    return {**best, "confidence": min(0.86, 0.58 + best_score * 0.07)}


def _request_anchors(message: str) -> list[str]:
    text = message or ""
    anchors: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", text):
        token = raw.lower().strip("-_")
        if len(token) >= 2 and token not in GENERIC_STOP_WORDS:
            anchors.add(token)
    for raw in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        token = raw.strip()
        for size in (2, 3, 4):
            if len(token) >= size:
                for index in range(0, len(token) - size + 1):
                    anchors.add(token[index : index + size].lower())
    return sorted(anchors, key=lambda item: (-len(item), item))[:32]


def _resolution(
    *,
    selected_candidate_ids: list[str] | None = None,
    source_mode: str,
    compose_mode: str = "recipient_ready_summary",
    referential_request: bool = False,
    needs_clarification: bool = False,
    confidence: float,
    reason: str,
    classifier_source: str,
    classifier_error: str = "",
) -> dict[str, Any]:
    return {
        "observation_type": "mail_source_resolution",
        "status": "needs_clarification" if needs_clarification else "resolved",
        "selected_candidate_ids": list(selected_candidate_ids or []),
        "source_mode": source_mode,
        "compose_mode": compose_mode,
        "referential_request": referential_request,
        "needs_clarification": needs_clarification,
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 4),
        "reason": reason,
        "classifier_source": classifier_source,
        "classifier_error": classifier_error,
    }


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("mail source resolver did not return a JSON object")
    return parsed
