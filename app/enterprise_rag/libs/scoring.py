from __future__ import annotations

import re
from collections import Counter
from typing import Any

from langchain_core.documents import Document


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
ENTITY_TOKEN_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]{2,}\b")
ENTITY_STOPWORDS = {
    "api",
    "eu",
    "gcp",
    "http",
    "https",
    "llm",
    "rpo",
    "rto",
    "saas",
    "sla",
    "sql",
    "ui",
    "us",
}
ANSWER_FOCUS_HINTS = (
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
    "don't show",
    "do not show",
    "allow retry",
)
ACTION_RECOMMENDATION_HINTS = (
    "recommend",
    "recommended",
    "should",
    "handle",
    "gracefully",
    "instead",
    "avoid",
    "provide",
    "retry",
    "refresh",
    "support",
    "don't show",
    "do not show",
    "few minutes",
    "pending",
)
NOISE_HINTS = (
    "summary:",
    "action items:",
    "meeting header",
    "agenda",
    "next steps",
    "owner:",
    "attendees:",
)


def tokenize(text: str) -> list[str]:
    return [term.lower() for term in TOKEN_PATTERN.findall(text or "") if len(term) > 1]


def extract_query_entity_anchors(query: str) -> list[str]:
    """Return distinctive identifiers suitable for bounded document scoping."""
    anchors: list[str] = []
    for token in ENTITY_TOKEN_PATTERN.findall(query or ""):
        lowered = token.lower()
        if lowered in ENTITY_STOPWORDS:
            continue
        has_mixed_case = any(char.isupper() for char in token[1:]) and any(char.islower() for char in token)
        has_identifier_separator = ("-" in token or "_" in token) and any(char.isalpha() for char in token)
        if not (has_mixed_case or has_identifier_separator):
            continue
        if lowered not in anchors:
            anchors.append(lowered)
    return anchors[:6]


def text_matches_entity_anchors(text: str, anchors: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(anchor in lowered for anchor in anchors)


def lexical_overlap_score(query: str, text: str) -> float:
    query_terms = set(tokenize(query))
    if not query_terms:
        return 0.0
    text_lower = (text or "").lower()
    hits = sum(1 for term in query_terms if term in text_lower)
    return hits / max(len(query_terms), 1)


def title_match_score(query: str, title: str) -> float:
    return lexical_overlap_score(query, title) * 1.4


def phrase_match_score(query: str, text: str) -> float:
    normalized_query = " ".join(tokenize(query))
    normalized_text = " ".join(tokenize(text))
    if not normalized_query or not normalized_text:
        return 0.0
    if normalized_query in normalized_text:
        return 1.0
    query_bigrams = {" ".join(pair) for pair in zip(normalized_query.split(), normalized_query.split()[1:])}
    if not query_bigrams:
        return 0.0
    hits = sum(1 for phrase in query_bigrams if phrase in normalized_text)
    return hits / max(len(query_bigrams), 1)


def answer_fact_hint_score(query: str, text: str, title: str = "") -> float:
    query_terms = tokenize(query)
    text_terms = tokenize(text + " " + title)
    if not query_terms or not text_terms:
        return 0.0
    query_counter = Counter(query_terms)
    text_counter = Counter(text_terms)
    overlap = 0
    for term, count in query_counter.items():
        overlap += min(count, text_counter.get(term, 0))
    return overlap / max(sum(query_counter.values()), 1)


def heuristic_candidate_score(query: str, *, content: str, title: str, source_type: str, retrieval_source: str) -> float:
    lexical = lexical_overlap_score(query, content)
    title_score = title_match_score(query, title)
    phrase = phrase_match_score(query, content)
    fact_hint = answer_fact_hint_score(query, content, title)
    lowered = (content or "").lower()
    focus_boost = 0.22 if any(token in lowered for token in ANSWER_FOCUS_HINTS) else 0.0
    answerability_boost = 0.18 if any(token in lowered for token in ACTION_RECOMMENDATION_HINTS) else 0.0
    noise_penalty = 0.18 if any(token in lowered for token in NOISE_HINTS) else 0.0
    source_boost = 0.08 if source_type else 0.0
    source_boost += 0.06 if source_type in {"fireflies", "gmail", "slack"} else 0.0
    retrieval_boost = 0.12 if retrieval_source == "hybrid" else 0.06 if retrieval_source == "sparse" else 0.0
    entity_anchors = extract_query_entity_anchors(query)
    entity_boost = 0.24 if entity_anchors and text_matches_entity_anchors(f"{title}\n{content}", entity_anchors) else 0.0
    return max(
        0.0,
        (lexical * 0.35)
        + (title_score * 0.2)
        + (phrase * 0.25)
        + (fact_hint * 0.2)
        + focus_boost
        + answerability_boost
        + source_boost
        + retrieval_boost
        + entity_boost
        - noise_penalty,
    )


def build_rerank_debug_rows(query: str, docs: list[Document], scores: list[float], *, retrieval_sources: dict[str, str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    retrieval_sources = retrieval_sources or {}
    for index, (doc, rerank_score) in enumerate(zip(docs, scores), start=1):
        chunk_id = str(doc.metadata.get("chunk_id") or "")
        rows.append(
            {
                "rank": index,
                "chunk_id": chunk_id,
                "doc_id": str(doc.metadata.get("doc_id") or ""),
                "source_type": str(doc.metadata.get("source_type") or ""),
                "title": str(doc.metadata.get("title") or ""),
                "retrieval_source": retrieval_sources.get(chunk_id, ""),
                "heuristic_score": heuristic_candidate_score(
                    query,
                    content=doc.page_content,
                    title=str(doc.metadata.get("title") or ""),
                    source_type=str(doc.metadata.get("source_type") or ""),
                    retrieval_source=retrieval_sources.get(chunk_id, ""),
                ),
                "rerank_score": float(rerank_score),
            }
        )
    return rows
