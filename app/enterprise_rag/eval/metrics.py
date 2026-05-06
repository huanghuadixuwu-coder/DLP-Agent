from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().replace("’", "'").replace("“", '"').replace("”", '"').split())


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(_normalize(text)) if len(token) > 1}


def _soft_match(fact: str, candidate_text: str) -> bool:
    norm_fact = _normalize(fact)
    norm_candidate = _normalize(candidate_text)
    if not norm_fact or not norm_candidate:
        return False
    if norm_fact in norm_candidate:
        return True
    fact_tokens = _tokens(norm_fact)
    candidate_tokens = _tokens(norm_candidate)
    if not fact_tokens or not candidate_tokens:
        return False
    overlap = fact_tokens & candidate_tokens
    overlap_ratio = len(overlap) / len(fact_tokens)
    if overlap_ratio >= 0.55:
        return True
    key_tokens = {token for token in fact_tokens if len(token) >= 4}
    if key_tokens and len(key_tokens & candidate_tokens) / len(key_tokens) >= 0.5:
        return True
    return False


def doc_recall(expected_doc_ids: list[str], actual_doc_ids: list[str]) -> float:
    expected = {item for item in expected_doc_ids if item}
    if not expected:
        return 0.0
    actual = set(actual_doc_ids)
    return len(expected & actual) / len(expected)


def answer_fact_coverage(answer_facts: list[str], answer: str) -> float:
    facts = [fact for fact in answer_facts if fact]
    if not facts:
        return 0.0
    hits = sum(1 for fact in facts if _soft_match(fact, answer))
    return hits / len(facts)


def evidence_fact_coverage(answer_facts: list[str], supporting_facts: list[str]) -> float:
    facts = [fact for fact in answer_facts if fact]
    if not facts:
        return 0.0
    supporting = list(supporting_facts or [])
    hits = sum(1 for fact in facts if any(_soft_match(fact, candidate) for candidate in supporting))
    return hits / len(facts)


def supporting_fact_hits(answer_facts: list[str], supporting_facts: list[str]) -> list[str]:
    facts = [fact for fact in answer_facts if fact]
    supporting = list(supporting_facts or [])
    return [fact for fact in facts if any(_soft_match(fact, candidate) for candidate in supporting)]
