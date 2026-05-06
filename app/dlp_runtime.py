from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.graph import get_llm
from app.unified_corpus import documents_to_evidence, retrieve_unified_evidence


RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def max_risk_level(*levels: str) -> str:
    normalized = [level if level in RISK_ORDER else "low" for level in levels if level]
    if not normalized:
        return "low"
    return max(normalized, key=lambda item: RISK_ORDER[item])


def retrieve_dlp_evidence(query: str, top_k: int = 4) -> list[dict[str, Any]]:
    documents = retrieve_unified_evidence(query, domain="privacy", top_k=top_k)
    return documents_to_evidence(documents)


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def model_summary_and_risk(
    *,
    redacted_text: str,
    evidence: list[dict[str, Any]],
    requested_action: str,
    destination_email: str,
    source_filename: str,
) -> dict[str, Any]:
    llm = get_llm()
    evidence_lines = [
        f"- {item.get('title', 'policy')} | {item.get('source_type', '')}: {item.get('snippet', '')}"
        for item in evidence[:4]
    ]
    prompt = "\n".join(
        [
            "You are assisting an enterprise DLP outbound approval workflow.",
            "Use the redacted outbound content and policy evidence to produce a short outbound summary and a conservative risk judgement.",
            "Return strict JSON with keys: summary, risk_level, risk_reasons.",
            "risk_level must be one of: low, medium, high.",
            "Do not mention internal implementation details.",
            "",
            f"Requested action: {requested_action}",
            f"Destination email: {destination_email or 'missing'}",
            f"Source filename: {source_filename or 'text'}",
            "",
            "Policy evidence:",
            *(evidence_lines or ["- No evidence retrieved."]),
            "",
            "Redacted outbound content:",
            redacted_text[:4000],
        ]
    )
    response = llm.invoke(
        [
            SystemMessage(content="Return only valid JSON. Be conservative for DLP risk judgements."),
            HumanMessage(content=prompt),
        ]
    )
    parsed = _extract_json_object(getattr(response, "content", str(response)))
    summary = str(parsed.get("summary", "")).strip()
    risk_level = str(parsed.get("risk_level", "low")).strip().lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"
    risk_reasons = [str(item).strip() for item in parsed.get("risk_reasons", []) if str(item).strip()]
    return {
        "summary": summary,
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
        "raw_response": getattr(response, "content", str(response)),
    }
