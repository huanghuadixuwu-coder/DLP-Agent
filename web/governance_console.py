from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
EXTERNAL_API_URL = os.getenv("PUBLIC_API_BASE_URL", "http://localhost:8010")
EXTERNAL_PROMETHEUS_URL = os.getenv("PUBLIC_PROMETHEUS_BASE_URL", "http://localhost:9091")
EXTERNAL_GOVERNANCE_URL = os.getenv("PUBLIC_GOVERNANCE_BASE_URL", "http://localhost:8512")
ADMIN_HEADERS = {
    "X-Tenant-Id": os.getenv("GOVERNANCE_TENANT_ID", "local-dev"),
    "X-User-Id": os.getenv("GOVERNANCE_USER_ID", "governance-admin"),
    "X-Workspace-Id": os.getenv("GOVERNANCE_WORKSPACE_ID", "default"),
    "X-Roles": os.getenv("GOVERNANCE_ROLES", "admin,approver,viewer,user,mail_sender"),
}


st.set_page_config(page_title="安全外发治理台", page_icon="🛡️", layout="wide")

st.markdown(
    """
    <style>
    .gov-card {
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 1rem;
        background: #fff;
        box-shadow: 0 10px 24px rgba(15, 23, 42, 0.06);
        margin-bottom: 0.8rem;
    }
    .gov-muted { color: #64748b; font-size: 0.9rem; }
    .risk-high { color: #b91c1c; font-weight: 700; }
    .risk-medium { color: #b45309; font-weight: 700; }
    .risk-low { color: #047857; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)


def friendly_api_error(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "API service unavailable. Check Docker api service."
    if isinstance(exc, httpx.TimeoutException):
        return "API request timed out."
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.text.strip()
        return f"API request failed with HTTP {exc.response.status_code}.{f' {detail}' if detail else ''}"
    return str(exc)


def api_get(path: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any] | list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=timeout, headers=ADMIN_HEADERS) as client:
            response = client.get(f"{API_BASE_URL}{path}", params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise RuntimeError(friendly_api_error(exc)) from exc


def api_post(path: str, payload: dict[str, Any] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=timeout, headers=ADMIN_HEADERS) as client:
            response = client.post(f"{API_BASE_URL}{path}", json=payload or {})
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise RuntimeError(friendly_api_error(exc)) from exc


def task_status_label(status: str) -> str:
    return {
        "needs_clarification": "待补充",
        "input_invalid": "输入无效",
        "pending_approval": "待治理审批",
        "queued": "排队中",
        "processing": "风险判断中",
        "approved": "已批准",
        "queued_for_send": "待发送",
        "sending": "发送中",
        "sent": "已发送",
        "send_failed": "发送失败",
        "delivery_deferred": "延后恢复",
        "dead_letter": "死信待恢复",
        "rejected": "已驳回",
        "failed": "执行失败",
    }.get(status, status or "unknown")


def risk_html(risk: str) -> str:
    risk = risk or "-"
    css = "risk-low" if risk == "low" else "risk-medium" if risk == "medium" else "risk-high" if risk in {"high", "critical"} else ""
    return f"<span class='{css}'>{risk}</span>"


def load_tasks(status: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if status and status != "all":
        params["status"] = status
    payload = api_get("/tasks", params=params)
    return payload if isinstance(payload, list) else []


st.title("安全外发治理台")
st.caption(f"治理台 `{EXTERNAL_GOVERNANCE_URL}` 只承载治理、诊断、恢复与审计；用户工作台不直接暴露高风险审批控件。")

top_cols = st.columns(3)
try:
    stats = api_get("/admin/task-stats")
    task_stats = (stats or {}).get("task_stats", {}) if isinstance(stats, dict) else {}
    top_cols[0].metric("全部任务", task_stats.get("total", 0))
    top_cols[1].metric("待治理审批", task_stats.get("pending_approval", 0))
    top_cols[2].metric("发送失败", task_stats.get("send_failed", 0))
except Exception as exc:
    st.warning(f"任务统计不可用: {exc}")

left, right = st.columns([1.1, 1])

with left:
    st.subheader("DLP 待审批任务")
    task_filter = st.selectbox("任务过滤", ["pending_approval", "send_failed", "delivery_deferred", "dead_letter", "all"], index=0)
    try:
        tasks = load_tasks(task_filter)
    except Exception as exc:
        st.error(f"加载任务失败: {exc}")
        tasks = []

    if not tasks:
        st.info("当前没有匹配任务。")
    for task in tasks[:30]:
        risk = str(task.get("risk_level") or "")
        title = f"{task.get('task_id')} · {task_status_label(str(task.get('status') or ''))} · {task.get('destination_email') or 'no recipient'}"
        with st.expander(title, expanded=str(task.get("status")) == "pending_approval"):
            st.markdown(
                f"risk: {risk_html(risk)}  \n"
                f"delivery: `{task.get('delivery_status') or 'not_sent'}`  \n"
                f"tenant/user/workspace: `{task.get('tenant_id') or '-'}` / `{task.get('user_id') or '-'}` / `{task.get('workspace_id') or '-'}`",
                unsafe_allow_html=True,
            )
            st.text_area("脱敏预览", value=str(task.get("message_redacted") or task.get("message_raw") or ""), height=120, disabled=True)
            if task.get("delivery_error"):
                st.error(str(task.get("delivery_error")))
            if task.get("next_recommended_action"):
                st.info(str(task.get("next_recommended_action")))
            action_cols = st.columns(2)
            if str(task.get("status")) == "pending_approval":
                with action_cols[0]:
                    if st.button("批准并进入发送队列", key=f"approve_{task.get('task_id')}"):
                        try:
                            api_post(f"/tasks/{task.get('task_id')}/approve", {"actor": "governance_admin"})
                            st.success("已批准并尝试入队。")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"批准失败: {exc}")
                with action_cols[1]:
                    reason = st.text_input("驳回原因", value="governance rejected sensitive outbound", key=f"reason_{task.get('task_id')}")
                    if st.button("驳回并终止", key=f"reject_{task.get('task_id')}"):
                        try:
                            api_post(f"/tasks/{task.get('task_id')}/reject", {"actor": "governance_admin", "reason": reason})
                            st.success("已驳回。")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"驳回失败: {exc}")
            with st.expander("审计事件 / Timeline", expanded=False):
                for event in task.get("audit_events") or []:
                    st.markdown(f"- `{event.get('created_at')}` **{event.get('event_type')}**: {event.get('details_json')}")

with right:
    st.subheader("Provider / Queue Health")
    health_cols = st.columns(2)
    try:
        queue = api_get("/admin/queue-health")
        queue_status = (queue or {}).get("queue_status", {}) if isinstance(queue, dict) else {}
        health_cols[0].metric("Queue OK", "yes" if queue_status.get("ok") else "no")
        with st.expander("Queue depth", expanded=True):
            st.json(queue_status)
    except Exception as exc:
        health_cols[0].metric("Queue OK", "unknown")
        st.warning(f"Queue health unavailable: {exc}")
    try:
        provider = api_get("/admin/mail-provider-health")
        provider_health = (provider or {}).get("provider_health", {}) if isinstance(provider, dict) else {}
        health_cols[1].metric("Mail Provider", provider_health.get("status", "unknown"))
        with st.expander("Provider observation", expanded=True):
            st.json((provider or {}).get("observation", {}))
    except Exception as exc:
        health_cols[1].metric("Mail Provider", "unknown")
        st.warning(f"Provider health unavailable: {exc}")

    st.subheader("DLQ / Recovery")
    try:
        dlq = api_get("/admin/mail-dlq", params={"limit": 20})
        entries = (dlq or {}).get("entries", []) if isinstance(dlq, dict) else []
    except Exception as exc:
        st.error(f"DLQ 加载失败: {exc}")
        entries = []
    if not entries:
        st.info("暂无 dead-letter 任务。")
    for entry in entries:
        label = f"{entry.get('dlq_id')} · {entry.get('operation')} · {entry.get('replay_status')}"
        with st.expander(label, expanded=str(entry.get("replay_status")) == "pending"):
            st.markdown(
                f"task: `{entry.get('task_id')}`  \n"
                f"safe replay: `{entry.get('safe_replay_allowed')}`  \n"
                f"attempts: `{entry.get('attempt_count')}`"
            )
            if entry.get("last_error"):
                st.error(str(entry.get("last_error")))
            if entry.get("recovery_hint"):
                st.info(str(entry.get("recovery_hint")))
            if entry.get("safe_replay_allowed") and entry.get("replay_status") in {"pending", "replay_queued", "enqueue_failed"}:
                if st.button("安全重放并入队", key=f"replay_{entry.get('dlq_id')}"):
                    try:
                        result = api_post(f"/admin/mail-dlq/{entry.get('dlq_id')}/replay")
                        if result.get("ok"):
                            st.success("已重放并入队。")
                        else:
                            st.warning("重放未完成，请查看 observation。")
                        st.json(result.get("observation", {}))
                        st.rerun()
                    except Exception as exc:
                        st.error(f"重放失败: {exc}")
            with st.expander("DLQ payload", expanded=False):
                st.json(entry)

    st.subheader("Harness Results")
    try:
        harness = api_get("/admin/mail-harness-summary")
        for item in (harness or {}).get("harnesses", []):
            st.markdown(f"- **{item.get('name')}**: {item.get('scope')}")
            st.code(item.get("docker_command", ""), language="bash")
    except Exception as exc:
        st.warning(f"Harness summary unavailable: {exc}")

st.caption(f"API: `{EXTERNAL_API_URL}` · Prometheus: `{EXTERNAL_PROMETHEUS_URL}`")
