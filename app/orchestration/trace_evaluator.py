from __future__ import annotations

from collections import Counter
from typing import Any

from app.orchestration.observations import make_typed_observation


HEAVY_TOOLS = {"enterprise_rag_query", "enterprise_search", "enterprise_answer", "memory_search"}
SIDE_EFFECTFUL_TOOL_HINTS = ("send_email", "approve", "reject", "ingest", "benchmark", "calendar_create", "calendar_update", "calendar_cancel", "meeting_create", "meeting_cancel", "meeting_attach")


def evaluate_agent_trace(result: dict[str, Any], actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    actor = dict(actor_context or result.get("actor_context") or {})
    observations = _observations(result)
    tool_calls = list(result.get("tool_calls") or [])
    tool_counts = Counter(str(call.get("tool_name") or "unknown") for call in tool_calls)
    issues: list[dict[str, Any]] = []

    issues.extend(_check_tool_selection(result, observations, tool_calls))
    issues.extend(_check_confirmation(result, observations, tool_calls))
    issues.extend(_check_memory_boundary(result, observations))
    issues.extend(_check_tenant_boundary(actor, observations))
    issues.extend(_check_heavy_tool_overuse(tool_counts))
    issues.extend(_check_failure_recovery(result, observations, tool_calls))
    issues.extend(_check_multi_agent_trace(observations, tool_calls))
    issues.extend(_check_pending_object_transition(result, tool_calls))
    issues.extend(_check_mail_confirmation_renderer_contract(result, observations))

    severity_score = {"low": 1, "medium": 3, "high": 6}
    penalty = sum(severity_score.get(str(issue.get("severity") or "low"), 1) for issue in issues)
    score = max(0.0, round(1.0 - min(penalty, 10) / 10.0, 3))
    return {
        "ok": not any(str(issue.get("severity")) in {"medium", "high"} for issue in issues),
        "score": score,
        "issues": issues,
        "tool_counts": dict(tool_counts),
        "observation_count": len(observations),
        "failure_count": len(_failed_tool_calls(tool_calls)),
        "recovery_observation_present": _has_recovery_observation(observations, result),
        "evaluated_checks": [
            "tool_selection",
            "confirmation_boundary",
            "memory_boundary",
            "tenant_boundary",
            "heavy_tool_budget",
            "failure_recovery",
            "multi_agent_dag_trace",
            "pending_object_transition",
            "mail_confirmation_renderer_contract",
        ],
    }


def trace_evaluation_observation(evaluation: dict[str, Any], actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    issue_codes = [str(item.get("code") or "") for item in list(evaluation.get("issues") or []) if item.get("code")]
    return make_typed_observation(
        observation_type="agent_trace_evaluation",
        source="agent_trace_evaluator",
        status="completed" if evaluation.get("ok") else "needs_review",
        grounding_kind="diagnostic",
        summary="Agent trace passed." if evaluation.get("ok") else "Agent trace needs review: " + ", ".join(issue_codes[:5]),
        payload=evaluation,
        provenance={"source": "agent_trace_evaluator"},
        confidence=0.9,
        actor_context=actor_context,
    )


def attach_trace_evaluation(result: dict[str, Any], actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    evaluation = evaluate_agent_trace(result, actor_context=actor_context)
    observation = trace_evaluation_observation(evaluation, actor_context=actor_context)
    result["agent_trace_evaluation"] = evaluation
    result.setdefault("task_plan", {})
    if isinstance(result["task_plan"], dict):
        result["task_plan"]["agent_trace_evaluation"] = evaluation
    result.setdefault("tool_observations", []).append(observation)
    return result


def _observations(result: dict[str, Any]) -> list[dict[str, Any]]:
    observations = list(result.get("tool_observations") or [])
    task_plan = dict(result.get("task_plan") or {})
    for key in ("tool_observations", "observations"):
        for item in list(task_plan.get(key) or []):
            if isinstance(item, dict):
                observations.append(item)
    return observations


def _check_tool_selection(result: dict[str, Any], observations: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required_grounding = str(result.get("required_grounding") or "")
    router_intent = str(result.get("router_intent") or result.get("intent") or "")
    tool_names = {str(call.get("tool_name") or "") for call in tool_calls}
    observation_types = {str(item.get("observation_type") or "") for item in observations}
    issues: list[dict[str, Any]] = []
    if (required_grounding == "retrieval" or "enterprise" in router_intent) and not (
        "enterprise_rag_query" in tool_names
        or "enterprise_rag_result" in observation_types
        or "enterprise_answer" in observation_types
        or result.get("citations")
    ):
        issues.append(
            {
                "code": "expected_enterprise_retrieval_missing",
                "severity": "medium",
                "detail": "Router required retrieval or enterprise intent, but trace lacks EnterpriseRAG evidence/tool observations.",
            }
        )
    return issues


def _check_confirmation(result: dict[str, Any], observations: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = dict(result.get("pending_confirmation") or {})
    has_confirmation_observation = any(str(item.get("observation_type") or "") in {"confirmation_required", "mail_confirmation_result"} for item in observations)
    issues: list[dict[str, Any]] = []
    for call in tool_calls:
        tool_name = str(call.get("tool_name") or "")
        if not any(hint in tool_name for hint in SIDE_EFFECTFUL_TOOL_HINTS):
            continue
        if str(call.get("status") or "") in {"confirmation_required", "pending_confirmation"}:
            continue
        if tool_name in {"enqueue_dlp_risk_task", "govern_dlp_task"}:
            continue
        if call.get("success") and not pending and not has_confirmation_observation and tool_name.startswith("send_email"):
            issues.append(
                {
                    "code": "side_effect_without_confirmation_trace",
                    "severity": "high",
                    "detail": f"Side-effectful tool {tool_name} appears successful without confirmation evidence.",
                }
            )
    return issues


def _check_memory_boundary(result: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    asks_enterprise = str(result.get("required_grounding") or "") == "retrieval" or "enterprise" in str(result.get("intent") or result.get("router_intent") or "")
    if not asks_enterprise:
        return []
    has_memory = bool(result.get("memory_reads")) or any("memory" in str(item.get("observation_type") or item.get("kind") or "") for item in observations)
    has_enterprise = bool(result.get("citations")) or any("enterprise" in str(item.get("observation_type") or item.get("source") or "") for item in observations)
    if has_memory and not has_enterprise:
        return [
            {
                "code": "memory_used_as_enterprise_fact_risk",
                "severity": "medium",
                "detail": "Trace used memory for an enterprise/retrieval request without EnterpriseRAG evidence.",
            }
        ]
    return []


def _check_tenant_boundary(actor: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not actor:
        return []
    expected_tenant = str(actor.get("tenant_id") or "")
    expected_workspace = str(actor.get("workspace_id") or "")
    issues: list[dict[str, Any]] = []
    for item in observations:
        observed_actor = dict(item.get("actor_context") or {})
        if not observed_actor:
            continue
        tenant = str(observed_actor.get("tenant_id") or "")
        workspace = str(observed_actor.get("workspace_id") or "")
        if expected_tenant and tenant and tenant != expected_tenant:
            issues.append({"code": "cross_tenant_observation", "severity": "high", "detail": f"Observation tenant {tenant} differs from actor tenant {expected_tenant}."})
        if expected_workspace and workspace and workspace != expected_workspace:
            issues.append({"code": "cross_workspace_observation", "severity": "medium", "detail": f"Observation workspace {workspace} differs from actor workspace {expected_workspace}."})
    return issues


def _check_heavy_tool_overuse(tool_counts: Counter[str]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    heavy_total = sum(count for tool, count in tool_counts.items() if tool in HEAVY_TOOLS)
    if heavy_total > 3:
        issues.append({"code": "heavy_tool_over_budget", "severity": "low", "detail": f"Heavy tool calls={heavy_total}; consider adaptive budget or cache reuse."})
    for tool, count in tool_counts.items():
        if tool in HEAVY_TOOLS and count > 2:
            issues.append({"code": "repeated_heavy_tool", "severity": "low", "detail": f"{tool} called {count} times in one request."})
    return issues


def _check_failure_recovery(result: dict[str, Any], observations: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = _failed_tool_calls(tool_calls) + list(result.get("partial_failures") or [])
    if not failures:
        return []
    if _has_recovery_observation(observations, result):
        return []
    return [
        {
            "code": "failure_without_recovery_observation",
            "severity": "medium",
            "detail": "Trace has failed tool calls/partial failures but lacks a recovery/fallback/degraded observation.",
        }
    ]


def _check_multi_agent_trace(observations: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    actions = [
        str((item.get("payload") or {}).get("action") or item.get("source") or "")
        for item in observations
        if isinstance(item.get("payload"), dict)
    ]
    actions.extend(str(call.get("tool_name") or "") for call in tool_calls)
    meeting_create_keys = _side_effect_action_keys(
        observations=observations,
        tool_calls=tool_calls,
        action_name="meeting_create_tencent_meeting",
    )
    if len(meeting_create_keys) > 1:
        issues.append(
            {
                "code": "duplicate_meeting_create_risk",
                "severity": "medium",
                "detail": "Trace contains repeated Tencent Meeting creation actions in one plan.",
            }
        )
    for item in observations:
        observation_type = str(item.get("observation_type") or "")
        status = str(item.get("status") or "")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        action = str(payload.get("action") or item.get("source") or "")
        if observation_type == "dependency_blocked" or status == "blocked":
            issues.append(
                {
                    "code": "multi_agent_dependency_blocked",
                    "severity": "medium",
                    "detail": f"Multi-agent step {action or '(unknown)'} was blocked by dependency state.",
                }
            )
        if _is_side_effect_action(action) and status == "completed" and not _has_confirmation_guardrail(observations):
            issues.append(
                {
                    "code": "multi_agent_side_effect_without_confirmation",
                    "severity": "high",
                    "detail": f"Side-effectful action {action} completed without confirmation observation.",
                }
            )
    if any("send_email" in action for action in actions) and not any(_is_dlp_observation(item) for item in observations):
        issues.append(
            {
                "code": "mail_send_without_dlp_trace",
                "severity": "high",
                "detail": "Trace includes email send action without DLP/privacy/governance observation.",
            }
        )
    return issues


def _check_pending_object_transition(result: dict[str, Any], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task_plan = dict(result.get("task_plan") or {})
    state = dict(task_plan.get("continuation_state") or {})
    decision = dict(state.get("decision") or {})
    active_objects = list(state.get("active_objects") or [])
    has_pending_confirmation = any(
        str(item.get("object_type") or "") in {"mail_confirmation", "domain_confirmation"}
        and str(item.get("status") or "") == "pending_confirmation"
        for item in active_objects
        if isinstance(item, dict)
    )
    if not has_pending_confirmation or str(decision.get("mode") or "") == "continue_existing":
        return []
    bypassed = [
        str(call.get("tool_name") or "")
        for call in tool_calls
        if bool(call.get("success"))
        and str(call.get("status") or "") not in {"confirmation_required", "pending_confirmation"}
        and _is_side_effect_action(str(call.get("tool_name") or ""))
    ]
    if not bypassed:
        return []
    return [
        {
            "code": "pending_confirmation_bypassed_by_new_side_effect",
            "severity": "high",
            "detail": "A new side-effectful tool completed while an existing pending confirmation remained unresolved: "
            + ", ".join(bypassed),
        }
    ]


def _check_mail_confirmation_renderer_contract(result: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    final_answer_source = str(result.get("final_answer_source") or "")
    tool_names = {str(call.get("tool_name") or "") for call in list(result.get("tool_calls") or [])}
    if final_answer_source != "mail_task_created_renderer" and "enqueue_dlp_risk_task" not in tool_names:
        return []
    observation_types = {str(item.get("observation_type") or "") for item in observations}
    if "task_status_result" in observation_types and "governed_mail_task_created" not in observation_types:
        return [
            {
                "code": "mail_confirmation_renderer_lost_source_artifact",
                "severity": "medium",
                "detail": "Mail confirmation created a governed task, but the renderer trace lacks the preserved mail/source-artifact observation.",
            }
        ]
    return []


def _side_effect_action_keys(
    *,
    observations: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    action_name: str,
) -> set[str]:
    keys: set[str] = set()
    for index, item in enumerate(observations):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        action = str(payload.get("action") or item.get("source") or "")
        if action != action_name:
            continue
        keys.add(str(payload.get("idempotency_key") or payload.get("task_id") or f"observation:{index}"))
    for index, call in enumerate(tool_calls):
        payload = call.get("result") if isinstance(call.get("result"), dict) else {}
        action = str(call.get("tool_name") or payload.get("action") or "")
        if action != action_name:
            continue
        keys.add(str(payload.get("idempotency_key") or payload.get("task_id") or f"tool_call:{index}"))
    return keys


def _failed_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [call for call in tool_calls if call and not bool(call.get("success", True))]


def _is_side_effect_action(action: str) -> bool:
    return any(hint in action for hint in SIDE_EFFECTFUL_TOOL_HINTS)


def _has_confirmation_guardrail(observations: list[dict[str, Any]]) -> bool:
    return any(str(item.get("observation_type") or "") in {"confirmation_required", "mail_confirmation_result"} for item in observations)


def _is_dlp_observation(item: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(item.get("observation_type") or ""),
            str(item.get("source") or ""),
            str((item.get("payload") or {}).get("action") or "") if isinstance(item.get("payload"), dict) else "",
        ]
    ).lower()
    return any(token in text for token in ("privacy", "dlp", "governance"))


def _has_recovery_observation(observations: list[dict[str, Any]], result: dict[str, Any]) -> bool:
    if str(result.get("degraded_from") or "none") != "none":
        return True
    if str(result.get("termination_reason") or "") in {"fatal_tool_failure", "needs_clarification", "budget_exhausted"}:
        return True
    for item in observations:
        text = " ".join(
            [
                str(item.get("observation_type") or ""),
                str(item.get("status") or ""),
                str(item.get("summary") or ""),
                str((item.get("payload") or {}).get("fallback_reason") or "") if isinstance(item.get("payload"), dict) else "",
            ]
        ).lower()
        if any(token in text for token in ("failed", "fallback", "degraded", "recovery", "deferred", "retry", "needs_rewrite")):
            return True
    return False
