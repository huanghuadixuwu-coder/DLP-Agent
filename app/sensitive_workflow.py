from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.privacy_lab import scan_sensitive_message


class SensitiveWorkflowState(TypedDict, total=False):
    message: str
    business_context: str
    recipient_type: str
    destination_email: str
    source_filename: str
    source_content_type: str
    requested_action: str
    context_budget: int
    redacted_text: str
    risk_level: str
    risk_reasons: list[str]
    redactions: list[dict[str, Any]]
    context_pack: dict[str, Any]
    proposed_action: str
    final_result: str
    draft_summary: str
    simulated_delivery_result: str
    approval_required: bool
    status: str
    audit_events: list[dict[str, Any]]


def _draft_summary(text: str, limit: int = 420) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "未提取到可总结内容。"
    if len(cleaned) <= limit:
        return f"摘要草稿：{cleaned}"
    return f"摘要草稿：{cleaned[:limit]}..."


def _detect_pii(state: SensitiveWorkflowState) -> SensitiveWorkflowState:
    result = scan_sensitive_message(state["message"], int(state.get("context_budget", 600)))
    state["redacted_text"] = str(result["redacted_text"])
    state["risk_level"] = str(result["risk_level"])
    state["risk_reasons"] = [str(item) for item in result["risk_reasons"]]
    state["redactions"] = list(result["redactions"])
    state["context_pack"] = dict(result["context_pack"])
    state.setdefault("audit_events", []).append({"event_type": "pii_scanned", "risk_level": state["risk_level"]})
    return state


def _draft_action(state: SensitiveWorkflowState) -> SensitiveWorkflowState:
    risk_level = state.get("risk_level", "low")
    state["draft_summary"] = _draft_summary(state.get("redacted_text", ""))
    state["simulated_delivery_result"] = ""
    if risk_level in {"medium", "high", "critical"}:
        state["approval_required"] = True
        state["status"] = "pending_approval"
        state["proposed_action"] = "检测到敏感外发风险。任务已挂起，审批通过后只发送脱敏摘要。"
        state["final_result"] = ""
    else:
        state["approval_required"] = False
        state["status"] = "completed"
        state["proposed_action"] = "低风险。可直接调用邮件工具发送脱敏摘要。"
        state["final_result"] = "低风险任务已通过安全检查，等待邮件工具执行。"
    state.setdefault("audit_events", []).append(
        {
            "event_type": "action_drafted",
            "status": state["status"],
            "approval_required": state["approval_required"],
        }
    )
    return state


def _finalize(state: SensitiveWorkflowState) -> SensitiveWorkflowState:
    state.setdefault("audit_events", []).append({"event_type": "workflow_finalized", "status": state.get("status")})
    return state


def build_sensitive_outbound_graph():
    graph = StateGraph(SensitiveWorkflowState)
    graph.add_node("detect_pii", _detect_pii)
    graph.add_node("draft_action", _draft_action)
    graph.add_node("finalize", _finalize)
    graph.set_entry_point("detect_pii")
    graph.add_edge("detect_pii", "draft_action")
    graph.add_edge("draft_action", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


_GRAPH = build_sensitive_outbound_graph()


def run_sensitive_outbound_workflow(
    message: str,
    business_context: str = "",
    recipient_type: str = "",
    destination_email: str = "17388861183@163.com",
    source_filename: str = "",
    source_content_type: str = "",
    requested_action: str = "summarize_and_send",
    context_budget: int = 600,
) -> dict[str, Any]:
    result = _GRAPH.invoke(
        {
            "message": message,
            "business_context": business_context,
            "recipient_type": recipient_type,
            "destination_email": destination_email,
            "source_filename": source_filename,
            "source_content_type": source_content_type,
            "requested_action": requested_action,
            "context_budget": context_budget,
            "audit_events": [],
        }
    )
    return dict(result)
