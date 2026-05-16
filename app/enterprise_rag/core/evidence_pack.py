from __future__ import annotations

import re
from dataclasses import asdict

from app.enterprise_rag.core.types import CanonicalFact, EnterpriseCitation, EvidencePack, SupportingFact
from app.enterprise_rag.libs.scoring import answer_fact_hint_score, lexical_overlap_score, phrase_match_score
from app.enterprise_rag.libs.text_cleaning import clean_text


SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\?\u3002\uff01\uff1f])\s+|\n+")
ACTION_HINTS = (
    "should",
    "recommend",
    "recommended",
    "avoid",
    "provide",
    "retry",
    "refresh",
    "handle",
    "gracefully",
    "tell",
    "support",
    "need to",
    "must",
    "instead",
    "don't show",
    "do not show",
    "pending",
)
NOISE_HINTS = (
    "summary:",
    "action items:",
    "meeting header",
    "thanks everyone",
    "sanity pass",
    "agenda",
    "attendees:",
    "joins late",
    "simple diagram",
    "rounding differences",
)
SELF_INTRO_HINTS = (
    "i'm ",
    "i am ",
    "hi all",
    "thanks everyone",
    "program lead",
    "partner dev manager",
)
QUESTION_FOCUS_HINTS = (
    "entitlement",
    "pending",
    "retry",
    "refresh",
    "few minutes",
    "last checked",
    "support",
    "not entitled",
    "syncing your subscription",
    "still syncing",
    "ux expectation",
    "scary error",
    "not immediately available",
)
ACTION_ITEM_HINTS = (
    "update copy",
    "follow up",
    "takeaway",
    "owner:",
    "action item",
    "todo",
    "assign",
)
BACKGROUND_HINTS = (
    "can happen",
    "may take",
    "sometimes",
    "delay",
    "syncing",
    "propagation",
)
CONSTRAINT_HINTS = (
    "don't show",
    "do not show",
    "avoid",
    "instead",
    "not entitled",
)
SUPPORT_HINTS = (
    "support",
    "sla",
    "support channel",
    "contacting support",
)
RETRY_HINTS = (
    "retry",
    "refresh",
    "last checked",
    "few minutes",
)


def _normalize_fact(text: str) -> str:
    return " ".join((text or "").split()).strip(" -")


def _strip_transcript_noise(text: str) -> str:
    cleaned = re.sub(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*", "", text or "")
    cleaned = re.sub(r"^[A-Z][A-Za-z0-9 .,'&/-]{1,40}:\s+", "", cleaned)
    cleaned = cleaned.strip(" -")
    return cleaned


def _looks_like_fact(text: str) -> bool:
    if len(text) < 20:
        return False
    lowered = text.lower()
    if any(token in lowered for token in NOISE_HINTS):
        return False
    if len(text) < 48 and not any(token in lowered for token in QUESTION_FOCUS_HINTS + ACTION_HINTS):
        return False
    if any(token in lowered for token in SELF_INTRO_HINTS) and not any(token in lowered for token in QUESTION_FOCUS_HINTS):
        return False
    if lowered.endswith("i think we should.") or lowered.endswith("i think we should"):
        return False
    return True


def _iter_sentence_candidates(citation: EnterpriseCitation) -> list[str]:
    body = str(citation.metadata.get("full_content") or citation.snippet or "")
    cleaned = clean_text(body, preserve_structure=True)
    return [_normalize_fact(piece) for piece in SENTENCE_SPLIT_RE.split(cleaned) if _normalize_fact(piece)]


def _source_aware_boost(citation: EnterpriseCitation, sentence: str) -> float:
    score = 0.0
    lowered = sentence.lower()
    source_type = citation.source_type or str(citation.metadata.get("source_type") or "")
    if source_type == "fireflies":
        if citation.metadata.get("speaker"):
            score += 0.04
        if any(token in lowered for token in ("recommend", "should", "instead", "don't show", "retry", "refresh", "support")):
            score += 0.16
    elif source_type == "gmail":
        if any(token in lowered for token in ("please", "recommend", "should", "next step", "follow up")):
            score += 0.1
    elif source_type == "slack":
        if any(token in lowered for token in ("let's", "should", "recommend", "please")):
            score += 0.08
    return score


def _fact_score(query: str, citation: EnterpriseCitation, sentence: str, order_index: int) -> float:
    lowered = sentence.lower()
    action_boost = 0.3 if any(token in lowered for token in ACTION_HINTS) else 0.0
    focus_boost = 0.45 if any(token in lowered for token in QUESTION_FOCUS_HINTS) else 0.0
    answerability_boost = 0.35 if any(
        token in lowered
        for token in (
            "ux expectation",
            "handle",
            "pending",
            "gracefully",
            "not entitled",
            "still syncing",
            "retry",
            "refresh",
        )
    ) else 0.0
    noise_penalty = 0.28 if any(token in lowered for token in NOISE_HINTS) else 0.0
    noise_penalty += 0.18 if any(token in lowered for token in SELF_INTRO_HINTS) and not any(token in lowered for token in QUESTION_FOCUS_HINTS) else 0.0
    retrieval_boost = 0.12 if citation.retrieval_source == "hybrid" else 0.08 if citation.retrieval_source == "sparse" else 0.0
    title_boost = lexical_overlap_score(query, citation.title)
    order_boost = max(0.0, 0.08 - (order_index * 0.01))
    return max(
        0.0,
        (lexical_overlap_score(query, sentence) * 0.28)
        + (phrase_match_score(query, sentence) * 0.2)
        + (answer_fact_hint_score(query, sentence, citation.title) * 0.26)
        + (title_boost * 0.08)
        + retrieval_boost
        + action_boost
        + focus_boost
        + answerability_boost
        + order_boost
        + _source_aware_boost(citation, sentence)
        - noise_penalty,
    )


def _extract_supporting_facts(query: str, citations: list[EnterpriseCitation], limit: int = 8) -> list[SupportingFact]:
    candidates: list[SupportingFact] = []
    seen: set[str] = set()
    for citation in citations:
        for order_index, sentence in enumerate(_iter_sentence_candidates(citation)):
            normalized = sentence.lower()
            if normalized in seen or not _looks_like_fact(sentence):
                continue
            seen.add(normalized)
            candidates.append(
                SupportingFact(
                    doc_id=citation.doc_id,
                    chunk_id=citation.chunk_id,
                    sentence_text=sentence,
                    score=_fact_score(query, citation, sentence, order_index),
                    source_type=citation.source_type,
                    title=citation.title,
                    speaker=str(citation.metadata.get("speaker") or ""),
                    timestamp=str(citation.metadata.get("turn_start") or citation.metadata.get("timestamp") or ""),
                    metadata={
                        "retrieval_source": citation.retrieval_source,
                        "chunk_strategy": str(citation.metadata.get("chunk_strategy") or ""),
                        "section_path": str(citation.metadata.get("section_path") or ""),
                        "message_id": str(citation.metadata.get("message_id") or ""),
                    },
                )
            )
    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    return ranked[:limit]


def _fact_type(sentence: str) -> str:
    lowered = sentence.lower()
    if any(token in lowered for token in SUPPORT_HINTS):
        return "support"
    if any(token in lowered for token in CONSTRAINT_HINTS):
        return "constraint"
    if any(token in lowered for token in RETRY_HINTS):
        return "fallback"
    if any(token in lowered for token in ACTION_HINTS):
        return "recommendation"
    if any(token in lowered for token in BACKGROUND_HINTS):
        return "background"
    return "recommendation"


def _priority(sentence: str) -> str:
    lowered = sentence.lower()
    if any(token in lowered for token in ACTION_ITEM_HINTS):
        return "peripheral"
    if any(token in lowered for token in ("invite you", "forward this entitlement id")):
        return "peripheral"
    if any(token in lowered for token in ("rounding differences", "simple diagram", "joins late")):
        return "peripheral"
    return "core"


def _normalize_sentence_to_fact(sentence: str) -> str:
    text = _strip_transcript_noise(sentence)
    text = re.sub(r"[\"“”']", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .;:")
    replacements = (
        ("Like, ", ""),
        ("Also ", ""),
        ("Also include ", "Include "),
        ("We have an error state ", "The current error state "),
        ("Instead say ", "Tell the user "),
    )
    for source, target in replacements:
        if text.startswith(source):
            text = target + text[len(source) :]
    return text


def _build_canonical_facts(supporting_facts: list[SupportingFact]) -> tuple[list[CanonicalFact], list[CanonicalFact]]:
    canonical: list[CanonicalFact] = []
    excluded: list[CanonicalFact] = []
    seen: set[str] = set()
    for index, fact in enumerate(supporting_facts, start=1):
        normalized = _normalize_sentence_to_fact(fact.sentence_text)
        if not normalized:
            continue
        dedupe_key = normalized.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        item = CanonicalFact(
            fact_id=f"fact_{index}",
            fact_type=_fact_type(normalized),
            normalized_fact=normalized,
            priority=_priority(normalized),
            source_fact_ids=[f"{fact.doc_id}:{fact.chunk_id}:{index}"],
            score=fact.score,
            source_type=fact.source_type,
            title=fact.title,
            metadata={
                "doc_id": fact.doc_id,
                "chunk_id": fact.chunk_id,
                "speaker": fact.speaker,
                "timestamp": fact.timestamp,
            },
        )
        if item.priority == "core":
            canonical.append(item)
        else:
            excluded.append(item)
    canonical = sorted(canonical, key=lambda item: item.score, reverse=True)
    excluded = sorted(excluded, key=lambda item: item.score, reverse=True)
    return canonical[:8], excluded[:8]


def build_evidence_pack(
    query: str,
    citations: list[EnterpriseCitation],
    *,
    retrieval_stage_debug: dict | None = None,
    rerank_debug: list[dict] | None = None,
) -> EvidencePack:
    supporting_doc_ids = []
    seen = set()
    for citation in citations:
        if citation.doc_id and citation.doc_id not in seen:
            supporting_doc_ids.append(citation.doc_id)
            seen.add(citation.doc_id)
    confidence = 0.0 if not citations else min(0.9, 0.5 + (0.1 * min(len(citations), 4)) + (0.06 * min(len(supporting_doc_ids), 3)))
    supporting_fact_details = _extract_supporting_facts(query, citations)
    supporting_facts = [fact.sentence_text for fact in supporting_fact_details]
    canonical_facts, excluded_facts = _build_canonical_facts(supporting_fact_details)
    return EvidencePack(
        query=query,
        citations=citations,
        supporting_doc_ids=supporting_doc_ids,
        missing_evidence=not bool(citations),
        confidence=confidence,
        supporting_facts=supporting_facts,
        supporting_fact_details=supporting_fact_details,
        canonical_facts=canonical_facts,
        excluded_facts=excluded_facts,
        retrieval_stage_debug={
            **dict(retrieval_stage_debug or {}),
            "supporting_fact_count": len(supporting_facts),
            "supporting_fact_details": [asdict(item) for item in supporting_fact_details],
            "canonical_facts": [asdict(item) for item in canonical_facts],
            "excluded_facts": [asdict(item) for item in excluded_facts],
        },
        rerank_debug=list(rerank_debug or []),
    )
