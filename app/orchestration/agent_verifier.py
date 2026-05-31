from __future__ import annotations

import re
from typing import Any

from app.conversation_memory import compact_text
from app.orchestration.observations import make_typed_observation


SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def verify_agent_answer(
    *,
    question: str,
    current_goal: str,
    observations: list[dict[str, Any]],
    answer: str,
    pending_confirmation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    checks = [
        _check_confirmation_boundary,
        _check_permission_boundary,
        _check_enterprise_citation_boundary,
        _check_memory_boundary,
        _check_attachment_leakage,
    ]
    for check in checks:
        issue = check(
            question=question,
            current_goal=current_goal,
            observations=observations,
            answer=answer,
            pending_confirmation=pending_confirmation or {},
        )
        if issue:
            violations.append(issue)

    max_severity = "none"
    for item in violations:
        severity = str(item.get("severity") or "low")
        if SEVERITY_ORDER.get(severity, 0) > SEVERITY_ORDER.get(max_severity, 0):
            max_severity = severity

    rewrite_instructions = [str(item.get("rewrite_instruction") or "") for item in violations if item.get("rewrite_instruction")]
    return {
        "ok": not violations,
        "needs_rewrite": bool(violations),
        "max_severity": max_severity,
        "violations": violations,
        "rewrite_instructions": rewrite_instructions,
    }


def verifier_observation(verdict: dict[str, Any], actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return make_typed_observation(
        observation_type="agent_verifier_verdict",
        source="agent_verifier",
        status="completed" if verdict.get("ok") else "needs_rewrite",
        grounding_kind="guardrail",
        summary=compact_text("; ".join(verdict.get("rewrite_instructions") or []) or "Agent verifier passed.", 240),
        payload=verdict,
        provenance={"source": "agent_verifier", "checks": ["permission", "citation", "memory_boundary", "confirmation", "attachment_leakage"]},
        confidence=0.92,
        actor_context=actor_context,
    )


def _check_confirmation_boundary(**kwargs: Any) -> dict[str, Any] | None:
    answer = str(kwargs.get("answer") or "")
    pending = dict(kwargs.get("pending_confirmation") or {})
    observations = list(kwargs.get("observations") or [])
    has_guarded_effect = bool(pending) or any(item.get("side_effects") for item in observations)
    if not has_guarded_effect:
        return None
    if _claims_action_completed(answer):
        return {
            "code": "confirmation_bypass_risk",
            "severity": "high",
            "evidence": "A side-effectful or confirmation-required action is pending, but the answer sounds completed.",
            "rewrite_instruction": "Do not say the external action has completed. State only the pending confirmation or current guarded state from observations.",
        }
    return None


def _check_permission_boundary(**kwargs: Any) -> dict[str, Any] | None:
    answer = str(kwargs.get("answer") or "")
    observations = list(kwargs.get("observations") or [])
    denied = [
        item
        for item in observations
        if str(item.get("status") or "").lower() in {"permission_denied", "forbidden"}
        or str(item.get("observation_type") or "") == "permission_denied"
        or "permission_denied" in str(item.get("summary") or "").lower()
    ]
    if denied and _claims_action_completed(answer):
        return {
            "code": "permission_boundary_violation",
            "severity": "high",
            "evidence": "A permission denial observation is present, but the answer claims success.",
            "rewrite_instruction": "Explain that the operation is blocked by permission observations and do not claim success.",
        }
    return None


def _check_enterprise_citation_boundary(**kwargs: Any) -> dict[str, Any] | None:
    current_goal = str(kwargs.get("current_goal") or "")
    observations = list(kwargs.get("observations") or [])
    enterprise_obs = [item for item in observations if "enterprise" in str(item.get("observation_type") or item.get("source") or "")]
    if "knowledge" not in current_goal and "enterprise" not in current_goal and not enterprise_obs:
        return None
    if not enterprise_obs:
        return None
    has_citations = any(list(item.get("citations") or []) for item in enterprise_obs)
    insufficient = any("没有检索到足够" in str(item.get("payload", {}).get("answer") or item.get("summary") or "") for item in enterprise_obs)
    if not has_citations and not insufficient:
        return {
            "code": "missing_enterprise_citation",
            "severity": "medium",
            "evidence": "Enterprise fact answer path has no citations.",
            "rewrite_instruction": "Use conservative wording and do not present enterprise claims as fully grounded without citations.",
        }
    return None


def _check_memory_boundary(**kwargs: Any) -> dict[str, Any] | None:
    question = str(kwargs.get("question") or "").lower()
    current_goal = str(kwargs.get("current_goal") or "")
    observations = list(kwargs.get("observations") or [])
    memory_obs = [item for item in observations if "memory" in str(item.get("observation_type") or item.get("kind") or "")]
    enterprise_obs = [item for item in observations if "enterprise" in str(item.get("observation_type") or item.get("source") or "")]
    asks_enterprise = "enterprise" in current_goal or "knowledge" in current_goal or any(token in question for token in ("meeting", "customer", "contract", "enterprise", "rag", "客户", "合同", "会议"))
    if asks_enterprise and memory_obs and not enterprise_obs:
        return {
            "code": "memory_used_without_enterprise_evidence",
            "severity": "medium",
            "evidence": "Memory observations are available, but no EnterpriseRAG evidence supports an enterprise fact answer.",
            "rewrite_instruction": "Treat memory only as context. If enterprise evidence is missing, say the answer is not sufficiently grounded in enterprise citations.",
        }
    return None


def _check_attachment_leakage(**kwargs: Any) -> dict[str, Any] | None:
    answer = str(kwargs.get("answer") or "")
    observations = list(kwargs.get("observations") or [])
    for item in observations:
        payload = dict(item.get("payload") or {})
        source_policy = dict(payload.get("source_policy") or {})
        body_constraints = dict(payload.get("body_constraints") or {})
        if source_policy.get("attachment_source") == "attachment_only" or body_constraints.get("attachment_source") == "attachment_only":
            attachment_text = ""
            for attachment in list(payload.get("attachments") or payload.get("resolved_attachments") or []):
                if isinstance(attachment, dict):
                    attachment_text += " " + str(attachment.get("content") or attachment.get("text") or "")
            if attachment_text and _contains_long_overlap(answer, attachment_text):
                return {
                    "code": "attachment_content_leakage",
                    "severity": "high",
                    "evidence": "Answer appears to copy attachment-only content.",
                    "rewrite_instruction": "Do not expose attachment-only content in the visible answer. Mention attachment handling only at state level.",
                }
    return None


def _claims_action_completed(answer: str) -> bool:
    text = answer.lower()
    markers = ("已发送", "发送成功", "已完成", "已经发出", "sent successfully", "has been sent", "completed successfully")
    return any(marker in answer or marker in text for marker in markers)


def _contains_long_overlap(answer: str, source: str) -> bool:
    normalized_answer = re.sub(r"\s+", "", answer)
    normalized_source = re.sub(r"\s+", "", source)
    if len(normalized_source) < 80 or len(normalized_answer) < 80:
        return False
    for idx in range(0, max(len(normalized_source) - 80, 1), 40):
        if normalized_source[idx : idx + 80] and normalized_source[idx : idx + 80] in normalized_answer:
            return True
    return False
