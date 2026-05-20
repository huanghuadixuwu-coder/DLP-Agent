from __future__ import annotations

import json
import os
import uuid
import base64

import httpx
import streamlit as st
import streamlit.components.v1 as components


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
EXTERNAL_API_URL = "http://localhost:8010"
EXTERNAL_PROMETHEUS_URL = "http://localhost:9091"
DEFAULT_DESTINATION_EMAIL = ""
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


st.set_page_config(page_title="安全外发与邮件协作 Agent", page_icon="📨", layout="wide")

st.markdown(
    """
    <style>
    .gradient-card {
        border-radius: 14px;
        padding: 0.75rem 0.9rem;
        color: white;
        font-weight: 700;
        margin: 0.8rem 0 0.45rem 0;
        box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
    }
    .conversation-gradient { background: linear-gradient(135deg, #0f766e, #2563eb); }
    .dlp-gradient { background: linear-gradient(135deg, #dc2626, #f59e0b); }
    .settings-gradient { background: linear-gradient(135deg, #334155, #7c3aed); }
    .small-muted {
        color: #64748b;
        font-size: 0.86rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("安全外发与邮件协作 Agent")
st.caption("面向企业外部沟通治理：读取邮件早报、总结会议纪要/日志/客户沟通，外发前经过 DLP 检测、审批和真实邮件发送。")


def friendly_api_error(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "API service unavailable. Check `docker compose ps` and the `api` logs."
    if isinstance(exc, httpx.TimeoutException):
        return "API request timed out. Check whether the `api` service is healthy."
    if isinstance(exc, httpx.HTTPStatusError):
        detail = exc.response.text.strip()
        return f"API request failed with HTTP {exc.response.status_code}.{f' {detail}' if detail else ''}"
    return str(exc)


def auth_headers() -> dict[str, str]:
    token = str(st.session_state.get("auth_session_token") or "").strip()
    if not token:
        return {}
    return {"X-Auth-Session": token}


def api_get(path: str, params: dict | None = None, timeout: float = 10.0):
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{API_BASE_URL}{path}", params=params, headers=auth_headers())
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise RuntimeError(friendly_api_error(exc)) from exc


def api_post(path: str, payload: dict | None = None, params: dict | None = None, timeout: float = 120.0):
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{API_BASE_URL}{path}", json=payload, params=params, headers=auth_headers())
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise RuntimeError(friendly_api_error(exc)) from exc


def api_delete(path: str, params: dict | None = None, timeout: float = 30.0):
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.delete(f"{API_BASE_URL}{path}", params=params, headers=auth_headers())
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise RuntimeError(friendly_api_error(exc)) from exc


def request_exmail_login_code(email: str) -> dict:
    return api_post("/auth/exmail/request-code", {"email": email}, timeout=60.0)


def verify_exmail_login_code(email: str, code: str) -> dict:
    return api_post("/auth/exmail/verify-code", {"email": email, "code": code}, timeout=60.0)


def load_auth_me() -> dict:
    return api_get("/auth/me", timeout=30.0)


def logout_auth_session() -> dict:
    return api_post("/auth/logout", {}, timeout=30.0)


@st.cache_data(ttl=10, show_spinner=False)
def load_conversations(session_id: str, auth_token: str = "") -> list[dict]:
    try:
        return api_get("/conversations", params={"session_id": session_id})
    except Exception:
        return []


@st.cache_data(ttl=2, show_spinner=False)
def load_turns(conversation_id: str | None, auth_token: str = "") -> list[dict]:
    if not conversation_id:
        return []
    try:
        return api_get(f"/conversations/{conversation_id}/turns")
    except Exception:
        return []


@st.cache_data(ttl=10, show_spinner=False)
def load_summary(conversation_id: str | None, auth_token: str = "") -> dict:
    if not conversation_id:
        return {}
    try:
        return api_get(f"/conversations/{conversation_id}/summary")
    except Exception:
        return {}


def create_conversation(session_id: str, title: str | None = None) -> dict:
    return api_post("/conversations", {"session_id": session_id, "title": title})


def merge_conversations(session_id: str, conversation_ids: list[str], merge_name: str | None) -> dict:
    return api_post(
        "/conversations/merge",
        {"session_id": session_id, "conversation_ids": conversation_ids, "merge_name": merge_name},
        timeout=180.0,
    )


def delete_conversation(session_id: str, conversation_id: str) -> dict:
    return api_delete(f"/conversations/{conversation_id}", params={"session_id": session_id})


@st.cache_data(ttl=10, show_spinner=False)
def list_sensitive_workflows(session_id: str, auth_token: str = "") -> list[dict]:
    try:
        return api_get("/tasks", params={"session_id": session_id})
    except Exception:
        return []


def supplement_dlp_task(
    task_id: str,
    session_id: str,
    conversation_id: str,
    message: str,
    destination_email: str,
    source_filename: str = "",
    source_content_type: str = "",
    uploaded_text: str = "",
    uploaded_file_base64: str = "",
    source_parse_status: str = "not_provided",
    source_parse_error: str = "",
) -> dict:
    return api_post(
        f"/tasks/{task_id}/supplement",
        {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": message,
            "destination_email": destination_email,
            "uploaded_filename": source_filename,
            "uploaded_content_type": source_content_type,
            "uploaded_text": uploaded_text,
            "uploaded_file_base64": uploaded_file_base64,
            "source_parse_status": source_parse_status,
            "source_parse_error": source_parse_error,
        },
        timeout=60.0,
    )


def list_dlp_scenarios() -> list[dict]:
    try:
        return api_get("/labs/dlp/scenarios")
    except Exception:
        return []


def load_dlp_scenarios() -> tuple[list[dict], str | None]:
    try:
        return api_get("/labs/dlp/scenarios"), None
    except Exception as exc:
        return [], friendly_api_error(exc)


def replay_dlp_scenario(
    scenario_id: str,
    session_id: str,
    conversation_id: str,
    destination_email: str,
    fault_injection: dict,
) -> dict:
    return api_post(
        f"/labs/dlp/scenarios/{scenario_id}/replay",
        {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "destination_email": destination_email,
            "fault_injection": fault_injection,
        },
        timeout=60.0,
    )


def sync_inbound_mailbox() -> dict:
    return api_post("/mail/inbound/sync", {}, timeout=120.0)


def generate_daily_mail_digest() -> dict:
    return api_post("/mail/inbound/digest", {}, timeout=60.0)


@st.cache_data(ttl=20, show_spinner=False)
def load_inbound_mail_summary(auth_token: str = "") -> dict:
    return api_get("/mail/inbound/summary", timeout=30.0)


@st.cache_data(ttl=20, show_spinner=False)
def load_inbound_mail_messages(limit: int = 5, auth_token: str = "") -> list[dict]:
    return api_get("/mail/inbound/messages", params={"limit": limit}, timeout=30.0)


@st.cache_data(ttl=20, show_spinner=False)
def load_notification_outbox(limit: int = 5, auth_token: str = "") -> list[dict]:
    return api_get("/notifications/outbox", params={"limit": limit}, timeout=30.0)


def approve_sensitive_workflow(workflow_id: str, actor: str) -> dict:
    return api_post(f"/tasks/{workflow_id}/approve", {"actor": actor}, timeout=60.0)


def reject_sensitive_workflow(workflow_id: str, actor: str, reason: str) -> dict:
    return api_post(f"/tasks/{workflow_id}/reject", {"actor": actor, "reason": reason})


def trigger_ingest(force: bool) -> str:
    payload = api_post("/ingest", params={"force": force}, timeout=120.0)
    return f"写入文档数: {payload['documents_written']}, collection: {payload['collection_name']}"


def run_unified_agent(
    session_id: str,
    conversation_id: str | None,
    message: str,
    mode: str,
    show_steps: bool,
    uploaded_filename: str = "",
    uploaded_content_type: str = "",
    uploaded_text: str = "",
    uploaded_file_base64: str = "",
    source_parse_status: str = "not_provided",
    source_parse_error: str = "",
) -> dict:
    return api_post(
        "/agent/chat",
        {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": message,
            "mode": mode,
            "problem_id": None,
            "show_steps": show_steps,
            "uploaded_filename": uploaded_filename,
            "uploaded_content_type": uploaded_content_type,
            "uploaded_text": uploaded_text,
            "uploaded_file_base64": uploaded_file_base64,
            "source_parse_status": source_parse_status,
            "source_parse_error": source_parse_error,
        },
    )


def get_query_param(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def sync_url_state(session_id: str, conversation_id: str | None = None) -> None:
    st.query_params["session_id"] = session_id
    if conversation_id:
        st.query_params["conversation_id"] = conversation_id
    elif "conversation_id" in st.query_params:
        del st.query_params["conversation_id"]


def should_collapse(text: str) -> bool:
    return len(text) > 1200 or text.count("\n") > 12


def render_answer(content: str, auto_collapse: bool) -> None:
    if auto_collapse and should_collapse(content):
        st.caption("回答较长，已自动折叠。")
        with st.expander("展开完整回答", expanded=False):
            st.markdown(content)
    else:
        st.markdown(content)


def render_message(turn: dict, auto_collapse: bool) -> None:
    role = "assistant" if turn["role"] == "assistant" else "user"
    with st.chat_message(role):
        content = turn.get("content", "")
        if role == "assistant":
            render_answer(content, auto_collapse)
        else:
            st.markdown(content)


def latest_debug_from_turns(turns: list[dict]) -> dict | None:
    for turn in reversed(turns):
        if turn.get("role") != "assistant":
            continue
        payload = turn.get("debug_payload") or {}
        if payload:
            payload.setdefault("turn_id", turn.get("turn_id"))
            payload.setdefault("conversation_id", turn.get("conversation_id"))
            return payload
    return None


def decode_uploaded_file(uploaded_file) -> tuple[str, str, str, str]:
    if uploaded_file is None:
        return "", "not_provided", "", ""
    raw = uploaded_file.getvalue()
    raw_base64 = base64.b64encode(raw).decode("ascii") if raw else ""
    if len(raw) > MAX_UPLOAD_BYTES:
        return "", "parse_failed", "上传文件超过 2MB，当前仅支持较小的文本、日志和表格文件。", raw_base64
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            decoded = raw.decode(encoding)
            if decoded.strip():
                return decoded, "parsed", "", raw_base64
            return "", "empty", "上传文件为空或没有可解析的文本内容。", raw_base64
        except UnicodeDecodeError:
            continue
    return "", "parse_failed", "文件解码失败。请上传 UTF-8、GBK 或 GB18030 编码的文本类文件。", raw_base64


def workflow_sort_key(item: dict) -> tuple[int, str]:
    rank = {
        "needs_clarification": 0,
        "input_invalid": 1,
        "pending_approval": 2,
        "delivery_deferred": 3,
        "send_failed": 4,
        "processing": 5,
        "queued": 6,
        "queued_for_send": 7,
        "sending": 8,
        "sent": 9,
        "approved": 10,
        "rejected": 11,
        "completed": 12,
        "failed": 13,
    }
    return (rank.get(item.get("status", ""), 99), item.get("updated_at", ""))


def set_task_panel_focus(task_id: str | None, status: str | None = None, *, filter_name: str | None = None) -> None:
    st.session_state.task_panel_preferred_task_id = task_id
    if filter_name is not None:
        st.session_state.task_panel_filter = filter_name
    elif status == "pending_approval":
        st.session_state.task_panel_filter = "pending_approval"
    else:
        st.session_state.task_panel_filter = "all"
    st.session_state.task_panel_focus_nonce = str(uuid.uuid4())


def apply_task_panel_filter(filter_name: str, *, task_id: str | None = None) -> None:
    st.session_state.task_panel_filter = filter_name
    st.session_state.task_panel_preferred_task_id = task_id
    st.session_state.task_panel_focus_nonce = str(uuid.uuid4())


def gradient_header(label: str, css_class: str) -> None:
    st.markdown(f"<div class='gradient-card {css_class}'>{label}</div>", unsafe_allow_html=True)


def render_realtime_task_panel(
    session_id: str,
    api_url: str,
    height: int = 820,
    *,
    preferred_task_id: str | None = None,
    preferred_filter: str = "all",
    focus_nonce: str = "",
) -> None:
    payload = {
        "sessionId": session_id,
        "apiBase": api_url,
        "defaultEmail": DEFAULT_DESTINATION_EMAIL,
        "preferredTaskId": preferred_task_id,
        "preferredFilter": preferred_filter,
        "focusNonce": focus_nonce,
    }
    component_html = """
    <div id="dlp-task-panel"></div>
    <script>
    const cfg = __PAYLOAD__;
    const root = document.getElementById("dlp-task-panel");
    const state = {{
      tasks: [],
      selectedTaskId: null,
      filter: "all",
      sockets: {{}},
      refreshTimer: null,
      wsScheme: cfg.apiBase.startsWith("https") ? "wss" : "ws",
    }};

    const rank = {{
      needs_clarification: 0,
      input_invalid: 1,
      delivery_deferred: 2,
      pending_approval: 3,
      processing: 4,
      queued_for_send: 5,
      sending: 6,
      send_failed: 7,
      queued: 8,
      sent: 9,
      approved: 10,
      rejected: 11,
      failed: 12
    }};

    function escapeHtml(value) {{
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }}

    function taskStatusLabel(status) {{
      const map = {{
        needs_clarification: "寰呰ˉ鍏呬俊鎭?,
        input_invalid: "杈撳叆鏃犳晥",
        delivery_deferred: "寤跺悗閲嶈瘯",
        queued: "鎺掗槦涓?,
        processing: "椋庨櫓鍒ゆ柇涓?,
        pending_approval: "寰呭鎵?,
        approved: "宸叉壒鍑?,
        rejected: "宸查┏鍥?,
        queued_for_send: "寰呭彂閫?,
        sending: "鍙戦€佷腑",
        sent: "宸插彂閫?,
        send_failed: "鍙戦€佸け璐?,
        failed: "鎵ц澶辫触"
      }};
      return map[status] || status || "unknown";
    }}

    function filterTasks() {{
      const tasks = [...state.tasks].sort((a, b) => {{
        const rankDiff = (rank[a.status] ?? 99) - (rank[b.status] ?? 99);
        if (rankDiff !== 0) return rankDiff;
        return (b.updated_at || "").localeCompare(a.updated_at || "");
      }});
      if (state.filter === "all") return tasks;
      return tasks.filter((task) => task.status === state.filter);
    }}

    function count(status) {{
      return state.tasks.filter((task) => task.status === status).length;
    }}

    function getSelectedTask() {{
      return state.tasks.find((task) => task.task_id === state.selectedTaskId) || filterTasks()[0] || null;
    }}

    async function fetchJson(path, options = undefined) {{
      const response = await fetch(cfg.apiBase + path, {{
        headers: {{ "Content-Type": "application/json" }},
        ...(options || {{}})
      }});
      if (!response.ok) {{
        const text = await response.text();
        throw new Error(text || `HTTP ${{response.status}}`);
      }}
      return await response.json();
    }}

    async function loadTasks() {{
      const tasks = await fetchJson(`/tasks?session_id=${{encodeURIComponent(cfg.sessionId)}}`);
      state.tasks = tasks;
      if (!state.selectedTaskId && tasks.length) {{
        state.selectedTaskId = tasks[0].task_id;
      }}
      if (state.selectedTaskId && !tasks.some((task) => task.task_id === state.selectedTaskId)) {{
        state.selectedTaskId = tasks[0]?.task_id || null;
      }}
      syncSockets();
      render();
    }}

    async function refreshTask(taskId) {{
      const task = await fetchJson(`/tasks/${{encodeURIComponent(taskId)}}`);
      const idx = state.tasks.findIndex((item) => item.task_id === taskId);
      if (idx >= 0) {{
        state.tasks[idx] = task;
      }} else {{
        state.tasks.unshift(task);
      }}
      render();
    }}

    function closeSocket(taskId) {{
      const socket = state.sockets[taskId];
      if (socket) {{
        try {{ socket.close(); }} catch (err) {{}}
        delete state.sockets[taskId];
      }}
    }}

    function openSocket(taskId) {{
      if (!taskId || state.sockets[taskId]) return;
      const url = cfg.apiBase.replace(/^http/, state.wsScheme) + `/ws/tasks/${{encodeURIComponent(taskId)}}`;
      const socket = new WebSocket(url);
      socket.onmessage = async (event) => {{
        try {{
          const payload = JSON.parse(event.data);
          if (payload?.task_id) {{
            await refreshTask(payload.task_id);
          }}
        }} catch (err) {{
          console.error("task ws parse error", err);
        }}
      }};
      socket.onclose = () => {{
        delete state.sockets[taskId];
      }};
      socket.onerror = () => {{
        delete state.sockets[taskId];
      }};
      state.sockets[taskId] = socket;
    }}

    function syncSockets() {{
      const keep = new Set(state.tasks.slice(0, 20).map((task) => task.task_id));
      Object.keys(state.sockets).forEach((taskId) => {{
        if (!keep.has(taskId)) closeSocket(taskId);
      }});
      keep.forEach((taskId) => openSocket(taskId));
    }}

    async function approveTask(taskId) {{
      await fetchJson(`/tasks/${{encodeURIComponent(taskId)}}/approve`, {{
        method: "POST",
        body: JSON.stringify({{ actor: "browser_reviewer" }})
      }});
      await refreshTask(taskId);
    }}

    async function rejectTask(taskId) {{
      const reason = prompt("璇疯緭鍏ラ┏鍥炲師鍥?, "contains sensitive outbound data");
      if (reason === null) return;
      await fetchJson(`/tasks/${{encodeURIComponent(taskId)}}/reject`, {{
        method: "POST",
        body: JSON.stringify({{ actor: "browser_reviewer", reason }})
      }});
      await refreshTask(taskId);
    }}

    function render() {{
      const selected = getSelectedTask();
      if (selected && state.selectedTaskId !== selected.task_id) {{
        state.selectedTaskId = selected.task_id;
      }}
      const tasks = filterTasks();
      const selectedEvents = (selected?.audit_events || []).slice(-8).reverse();
      root.innerHTML = `
        <style>
          .task-root {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: #0f172a;
          }}
          .task-toolbar {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
            margin-bottom: 10px;
          }}
          .task-chip {{
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 10px 8px;
            background: #fff;
            text-align: center;
          }}
          .task-chip strong {{
            display: block;
            font-size: 0.95rem;
          }}
          .task-filters {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-bottom: 12px;
          }}
          .task-filter {{
            border: 1px solid #cbd5e1;
            background: #fff;
            color: #334155;
            border-radius: 999px;
            padding: 6px 10px;
            cursor: pointer;
            font-size: 0.78rem;
          }}
          .task-filter.active {{
            background: linear-gradient(135deg, #dc2626, #f59e0b);
            color: #fff;
            border-color: transparent;
          }}
          .task-list {{
            display: grid;
            gap: 10px;
            max-height: 280px;
            overflow: auto;
            margin-bottom: 12px;
          }}
          .task-card {{
            border: 1px solid #e2e8f0;
            border-radius: 14px;
            background: #fff;
            padding: 10px;
            cursor: pointer;
            box-shadow: 0 8px 16px rgba(15, 23, 42, 0.05);
          }}
          .task-card.active {{
            border-color: #f97316;
            box-shadow: 0 10px 22px rgba(249, 115, 22, 0.16);
          }}
          .task-card-header {{
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: center;
            margin-bottom: 6px;
          }}
          .task-status {{
            font-size: 0.75rem;
            border-radius: 999px;
            padding: 4px 8px;
            color: #fff;
            background: #475569;
            white-space: nowrap;
          }}
          .task-status.pending_approval {{ background: #dc2626; }}
          .task-status.processing, .task-status.sending {{ background: #2563eb; }}
          .task-status.sent {{ background: #16a34a; }}
          .task-status.send_failed, .task-status.failed {{ background: #7f1d1d; }}
          .task-status.approved, .task-status.queued_for_send {{ background: #7c3aed; }}
          .task-status.rejected {{ background: #475569; }}
          .task-meta {{
            color: #64748b;
            font-size: 0.76rem;
            line-height: 1.45;
          }}
          .task-detail {{
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            background: #fff;
            padding: 12px;
          }}
          .task-detail h4 {{
            margin: 0 0 10px 0;
            font-size: 0.98rem;
          }}
          .task-detail pre {{
            white-space: pre-wrap;
            word-break: break-word;
            background: #f8fafc;
            border-radius: 12px;
            padding: 10px;
            font-size: 0.77rem;
            border: 1px solid #e2e8f0;
          }}
          .task-actions {{
            display: flex;
            gap: 8px;
            margin-top: 10px;
          }}
          .task-actions button {{
            flex: 1;
            border: none;
            border-radius: 10px;
            padding: 10px 12px;
            cursor: pointer;
            font-weight: 600;
          }}
          .approve-btn {{ background: #16a34a; color: #fff; }}
          .reject-btn {{ background: #dc2626; color: #fff; }}
          .detail-grid {{
            display: grid;
            gap: 8px;
            margin-bottom: 10px;
            font-size: 0.8rem;
          }}
          .detail-label {{ color: #64748b; }}
          .event-list {{
            display: grid;
            gap: 8px;
            margin-top: 12px;
          }}
          .event-item {{
            border-left: 3px solid #f97316;
            background: #fff7ed;
            padding: 8px 10px;
            border-radius: 8px;
            font-size: 0.76rem;
          }}
          .task-empty {{
            border: 1px dashed #cbd5e1;
            border-radius: 14px;
            padding: 16px;
            color: #64748b;
            text-align: center;
            background: #f8fafc;
          }}
        </style>
        <div class="task-root">
          <div class="task-toolbar">
            <div class="task-chip"><strong>${{count("pending_approval")}}</strong><span>寰呭鎵?/span></div>
            <div class="task-chip"><strong>${{count("processing") + count("queued") + count("queued_for_send") + count("sending")}}</strong><span>澶勭悊涓?/span></div>
            <div class="task-chip"><strong>${{count("send_failed")}}</strong><span>鍙戦€佸け璐?/span></div>
          </div>
          <div class="task-filters">
            ${["all", "pending_approval", "processing", "send_failed", "sent"].map((key) => `
              <button class="task-filter ${{state.filter === key ? "active" : ""}}" data-filter="${{key}}">
                ${{key === "all" ? "鍏ㄩ儴" : taskStatusLabel(key)}}
              </button>
            `).join("")}
          </div>
          <div class="task-list">
            ${tasks.length ? tasks.map((task) => `
              <div class="task-card ${{state.selectedTaskId === task.task_id ? "active" : ""}}" data-task-id="${{task.task_id}}">
                <div class="task-card-header">
                  <strong>${{escapeHtml(task.task_id)}}</strong>
                  <span class="task-status ${{escapeHtml(task.status)}}">${{escapeHtml(taskStatusLabel(task.status))}}</span>
                </div>
                <div class="task-meta">
                  risk: ${{escapeHtml(task.risk_level || "-")}}<br/>
                  to: ${{escapeHtml(task.destination_email || cfg.defaultEmail)}}<br/>
                  file: ${{escapeHtml(task.source_filename || "text")}}
                </div>
              </div>
            `).join("") : `<div class="task-empty">鏆傛棤浠诲姟锛屽垱寤哄鍙戜换鍔″悗浼氳嚜鍔ㄥ嚭鐜板湪杩欓噷銆?/div>`}
          </div>
          <div class="task-detail">
            ${selected ? `
              <h4>浠诲姟璇︽儏</h4>
              <div class="detail-grid">
                <div><span class="detail-label">task_id:</span> ${{escapeHtml(selected.task_id)}}</div>
                <div><span class="detail-label">status:</span> ${{escapeHtml(taskStatusLabel(selected.status))}}</div>
                <div><span class="detail-label">risk:</span> ${{escapeHtml(selected.risk_level || "-")}}</div>
                <div><span class="detail-label">delivery:</span> ${{escapeHtml(selected.delivery_status || "not_sent")}}</div>
              </div>
              <div class="detail-label">鑴辨晱棰勮</div>
              <pre>${{escapeHtml(selected.message_redacted || selected.message_raw || "")}}</pre>
              <div class="detail-label">鎽樿鑽夌</div>
              <pre>${{escapeHtml(selected.draft_summary || "")}}</pre>
              ${selected.delivery_result ? `<div class="detail-label">鍙戦€佺粨鏋?/div><pre>${{escapeHtml(selected.delivery_result)}}</pre>` : ""}
              ${selected.delivery_error ? `<div class="detail-label">閿欒</div><pre>${{escapeHtml(selected.delivery_error)}}</pre>` : ""}
              ${selected.status === "pending_approval" ? `
                <div class="task-actions">
                  <button class="approve-btn" data-action="approve" data-task-id="${{selected.task_id}}">鎵瑰噯骞剁湡瀹炲彂閫?/button>
                  <button class="reject-btn" data-action="reject" data-task-id="${{selected.task_id}}">椹冲洖骞剁粓姝?/button>
                </div>
              ` : ""}
              <div class="detail-label" style="margin-top:12px;">鏈€杩戜簨浠?/div>
              <div class="event-list">
                ${selectedEvents.map((event) => `
                  <div class="event-item">
                    <strong>${{escapeHtml(event.event_type)}}</strong><br/>
                    <span>${{escapeHtml(event.details_json?.message || "")}}</span><br/>
                    <span style="color:#64748b;">${{escapeHtml(event.created_at || "")}}</span>
                  </div>
                `).join("")}
              </div>
            ` : `<div class="task-empty">璇烽€夋嫨涓€涓换鍔℃煡鐪嬭鎯呫€?/div>`}
          </div>
        </div>
      `;

      root.querySelectorAll("[data-filter]").forEach((button) => {{
        button.addEventListener("click", () => {{
          state.filter = button.getAttribute("data-filter");
          render();
        }});
      }});
      root.querySelectorAll("[data-task-id]").forEach((card) => {{
        if (card.getAttribute("data-action")) return;
        card.addEventListener("click", () => {{
          state.selectedTaskId = card.getAttribute("data-task-id");
          render();
        }});
      }});
      root.querySelectorAll("[data-action='approve']").forEach((button) => {{
        button.addEventListener("click", async () => {{
          button.disabled = true;
          try {{
            await approveTask(button.getAttribute("data-task-id"));
          }} catch (err) {{
            alert(`鎵瑰噯澶辫触: ${{err.message}}`);
          }} finally {{
            button.disabled = false;
          }}
        }});
      }});
      root.querySelectorAll("[data-action='reject']").forEach((button) => {{
        button.addEventListener("click", async () => {{
          button.disabled = true;
          try {{
            await rejectTask(button.getAttribute("data-task-id"));
          }} catch (err) {{
            alert(`椹冲洖澶辫触: ${{err.message}}`);
          }} finally {{
            button.disabled = false;
          }}
        }});
      }});
    }}

    async function init() {{
      await loadTasks();
      if (state.refreshTimer) clearInterval(state.refreshTimer);
      state.refreshTimer = setInterval(loadTasks, 5000);
    }}

    init().catch((err) => {{
      root.innerHTML = `<div style="padding:12px;border:1px solid #fecaca;background:#fff1f2;border-radius:12px;color:#9f1239;">浠诲姟鍙板垵濮嬪寲澶辫触: ${{escapeHtml(err.message)}}</div>`;
    }});
    </script>
    """
    component_html = component_html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    components.html(component_html, height=height, scrolling=True)


def render_realtime_task_panel_v2(
    session_id: str,
    conversation_id: str | None,
    api_url: str,
    height: int = 820,
    *,
    auth_token: str = "",
    preferred_task_id: str | None = None,
    preferred_filter: str = "all",
    focus_nonce: str = "",
) -> None:
    payload = {
        "sessionId": session_id,
        "conversationId": conversation_id or "",
        "apiBase": api_url,
        "authToken": auth_token,
        "preferredTaskId": preferred_task_id,
        "preferredFilter": preferred_filter,
        "focusNonce": focus_nonce,
    }
    component_html = """
    <div id="dlp-task-panel-v2"></div>
    <script>
    const cfg = __PAYLOAD__;
    const root = document.getElementById("dlp-task-panel-v2");
    const state = {
      tasks: [],
      selectedTaskId: null,
      filter: cfg.preferredFilter || "all",
      sockets: {},
      refreshTimer: null,
      wsScheme: cfg.apiBase.startsWith("https") ? "wss" : "ws",
      appliedFocusNonce: null,
      supplementDrafts: {},
      lastTasksSignature: ""
    };

    const rank = {
      needs_clarification: 0,
      input_invalid: 1,
      delivery_deferred: 2,
      pending_approval: 3,
      send_failed: 4,
      processing: 5,
      queued: 6,
      queued_for_send: 7,
      sending: 8,
      sent: 9,
      approved: 10,
      rejected: 11,
      completed: 12,
      failed: 13
    };

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function escapeAttr(value) {
      return escapeHtml(value).replace(/'/g, "&#39;");
    }

    function taskStatusLabel(status) {
      const map = {
        needs_clarification: "待补充",
        input_invalid: "输入无效",
        delivery_deferred: "延后发送",
        queued: "排队中",
        processing: "风险判断中",
        pending_approval: "待审批",
        approved: "已批准",
        rejected: "已驳回",
        queued_for_send: "待发送",
        sending: "发送中",
        sent: "已发送",
        send_failed: "发送失败",
        failed: "执行失败",
        completed: "已完成"
      };
      return map[status] || status || "unknown";
    }

    function taskDetailHint(status) {
      if (status === "pending_approval") return "下一步：审批或驳回";
      if (status === "needs_clarification" || status === "input_invalid") return "下一步：补充信息并恢复任务";
      if (status === "send_failed" || status === "delivery_deferred") return "下一步：查看错误并决定是否重试或人工接管";
      return "下一步：查看任务进展";
    }

    function getDraft(taskId) {
      if (!state.supplementDrafts[taskId]) {
        state.supplementDrafts[taskId] = { email: "", message: "" };
      }
      return state.supplementDrafts[taskId];
    }

    function captureInputFocus() {
      const active = document.activeElement;
      if (!active) return null;
      const textTaskId = active.getAttribute("data-supplement-text");
      if (textTaskId) {
        return {
          taskId: textTaskId,
          field: "text",
          start: typeof active.selectionStart === "number" ? active.selectionStart : null,
          end: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
        };
      }
      const emailTaskId = active.getAttribute("data-supplement-email");
      if (emailTaskId) {
        return {
          taskId: emailTaskId,
          field: "email",
          start: typeof active.selectionStart === "number" ? active.selectionStart : null,
          end: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
        };
      }
      return null;
    }

    function restoreInputFocus(focusSnapshot) {
      if (!focusSnapshot?.taskId) return;
      const selector = focusSnapshot.field === "text"
        ? `[data-supplement-text='${CSS.escape(focusSnapshot.taskId)}']`
        : `[data-supplement-email='${CSS.escape(focusSnapshot.taskId)}']`;
      const node = root.querySelector(selector);
      if (!node) return;
      node.focus();
      if (
        typeof focusSnapshot.start === "number" &&
        typeof focusSnapshot.end === "number" &&
        typeof node.setSelectionRange === "function"
      ) {
        try {
          node.setSelectionRange(focusSnapshot.start, focusSnapshot.end);
        } catch (err) {}
      }
    }

    function filterTasks() {
      const tasks = [...state.tasks].sort((a, b) => {
        const rankDiff = (rank[a.status] ?? 99) - (rank[b.status] ?? 99);
        if (rankDiff !== 0) return rankDiff;
        return (b.updated_at || "").localeCompare(a.updated_at || "");
      });
      if (state.filter === "all") return tasks;
      return tasks.filter((task) => task.status === state.filter);
    }

    function getSelectedTask() {
      return state.tasks.find((task) => task.task_id === state.selectedTaskId) || filterTasks()[0] || null;
    }

    function count(status) {
      return state.tasks.filter((task) => task.status === status).length;
    }

    function taskSignature(tasks) {
      return JSON.stringify(
        (tasks || []).map((task) => ({
          task_id: task.task_id,
          status: task.status,
          updated_at: task.updated_at,
          delivery_status: task.delivery_status
        }))
      );
    }

    function applyExternalFocus(tasks) {
      if (cfg.preferredFilter && state.filter !== cfg.preferredFilter) {
        state.filter = cfg.preferredFilter;
      }
      if (cfg.focusNonce && state.appliedFocusNonce !== cfg.focusNonce) {
        state.appliedFocusNonce = cfg.focusNonce;
        if (cfg.preferredFilter) {
          state.filter = cfg.preferredFilter;
        }
        if (cfg.preferredTaskId && tasks.some((task) => task.task_id === cfg.preferredTaskId)) {
          state.selectedTaskId = cfg.preferredTaskId;
          return;
        }
        state.selectedTaskId = filterTasks()[0]?.task_id || tasks[0]?.task_id || null;
        return;
      }
      if (!state.selectedTaskId && cfg.preferredTaskId && tasks.some((task) => task.task_id === cfg.preferredTaskId)) {
        state.selectedTaskId = cfg.preferredTaskId;
        return;
      }
      if (state.selectedTaskId && tasks.some((task) => task.task_id === state.selectedTaskId)) {
        return;
      }
      state.selectedTaskId = filterTasks()[0]?.task_id || tasks[0]?.task_id || null;
    }

    async function fetchJson(path, options = undefined) {
      const headers = { "Content-Type": "application/json" };
      if (cfg.authToken) {
        headers["X-Auth-Session"] = cfg.authToken;
      }
      const response = await fetch(cfg.apiBase + path, {
        headers,
        ...(options || {})
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return await response.json();
    }

    async function loadTasks() {
      const tasks = await fetchJson(`/tasks?session_id=${encodeURIComponent(cfg.sessionId)}`);
      const signature = taskSignature(tasks);
      if (signature === state.lastTasksSignature) {
        syncSockets();
        return;
      }
      state.tasks = tasks;
      state.lastTasksSignature = signature;
      applyExternalFocus(tasks);
      syncSockets();
      render();
    }

    async function refreshTask(taskId) {
      const task = await fetchJson(`/tasks/${encodeURIComponent(taskId)}`);
      const idx = state.tasks.findIndex((item) => item.task_id === taskId);
      if (idx >= 0) {
        state.tasks[idx] = task;
      } else {
        state.tasks.unshift(task);
      }
      state.lastTasksSignature = taskSignature(state.tasks);
      applyExternalFocus(state.tasks);
      render();
    }

    function closeSocket(taskId) {
      const socket = state.sockets[taskId];
      if (socket) {
        try { socket.close(); } catch (err) {}
        delete state.sockets[taskId];
      }
    }

    function openSocket(taskId) {
      if (!taskId || state.sockets[taskId]) return;
      const url = cfg.apiBase.replace(/^http/, state.wsScheme) + `/ws/tasks/${encodeURIComponent(taskId)}`;
      const socket = new WebSocket(url);
      socket.onmessage = async (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.task_id) {
            await refreshTask(payload.task_id);
          }
        } catch (err) {
          console.error("task ws parse error", err);
        }
      };
      socket.onclose = () => {
        delete state.sockets[taskId];
      };
      socket.onerror = () => {
        delete state.sockets[taskId];
      };
      state.sockets[taskId] = socket;
    }

    function syncSockets() {
      const keep = new Set(state.tasks.slice(0, 6).map((task) => task.task_id));
      if (state.selectedTaskId) {
        keep.add(state.selectedTaskId);
      }
      Object.keys(state.sockets).forEach((taskId) => {
        if (!keep.has(taskId)) closeSocket(taskId);
      });
      keep.forEach((taskId) => openSocket(taskId));
    }

    async function approveTask(taskId) {
      await fetchJson(`/tasks/${encodeURIComponent(taskId)}/approve`, {
        method: "POST",
        body: JSON.stringify({ actor: "browser_reviewer" })
      });
      await refreshTask(taskId);
    }

    async function rejectTask(taskId) {
      const reason = prompt("请输入驳回原因", "contains sensitive outbound data");
      if (reason === null) return;
      await fetchJson(`/tasks/${encodeURIComponent(taskId)}/reject`, {
        method: "POST",
        body: JSON.stringify({ actor: "browser_reviewer", reason })
      });
      await refreshTask(taskId);
    }

    async function supplementTask(taskId) {
      const emailNode = root.querySelector(`[data-supplement-email='${CSS.escape(taskId)}']`);
      const textNode = root.querySelector(`[data-supplement-text='${CSS.escape(taskId)}']`);
      const fileNode = root.querySelector(`[data-supplement-file='${CSS.escape(taskId)}']`);
      const draft = getDraft(taskId);
      const task = state.tasks.find((item) => item.task_id === taskId) || {};
      const payload = {
        session_id: cfg.sessionId,
        conversation_id: String(task.conversation_id || cfg.conversationId || "").trim(),
        message: (textNode?.value ?? draft.message ?? "").trim(),
        destination_email: (emailNode?.value ?? draft.email ?? "").trim(),
        uploaded_filename: "",
        uploaded_content_type: "",
        uploaded_text: "",
        uploaded_file_base64: "",
        source_parse_status: "empty",
        source_parse_error: ""
      };
      if (fileNode?.files?.length) {
        const file = fileNode.files[0];
        payload.uploaded_filename = file.name || "";
        payload.uploaded_content_type = file.type || "";
        try {
          const arrayBuffer = await file.arrayBuffer();
          const bytes = new Uint8Array(arrayBuffer);
          let binary = "";
          const chunkSize = 0x8000;
          for (let i = 0; i < bytes.length; i += chunkSize) {
            binary += String.fromCharCode(...bytes.slice(i, i + chunkSize));
          }
          payload.uploaded_file_base64 = btoa(binary);
          payload.uploaded_text = await file.text();
          payload.source_parse_status = payload.uploaded_text ? "parsed" : "empty";
        } catch (err) {
          payload.source_parse_status = "parse_failed";
          payload.source_parse_error = String(err?.message || err || "file parse failed");
        }
      }
      await fetchJson(`/tasks/${encodeURIComponent(taskId)}/supplement`, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      state.supplementDrafts[taskId] = { email: "", message: "" };
      await refreshTask(taskId);
    }

    function render() {
      const focusSnapshot = captureInputFocus();
      const selected = getSelectedTask();
      if (selected && state.selectedTaskId !== selected.task_id) {
        state.selectedTaskId = selected.task_id;
      }
      const tasks = filterTasks();
      const selectedEvents = (selected?.audit_events || []).slice(-8).reverse();
      const hasEmptyFilter = !tasks.length && state.filter !== "all";
      const selectedDraft = selected ? getDraft(selected.task_id) : { email: "", message: "" };
      const selectedEmailValue = selectedDraft.email || selected?.destination_email || "";
      const selectedMessageValue = selectedDraft.message || "";

      root.innerHTML = `
        <style>
          .task-root { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #0f172a; }
          .task-toolbar { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 10px; }
          .task-chip { border: 1px solid #e2e8f0; border-radius: 12px; padding: 10px 8px; background: #fff; text-align: center; cursor: pointer; }
          .task-chip strong { display: block; font-size: 0.95rem; }
          .task-chip small { color: #64748b; }
          .task-filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
          .task-filter { border: 1px solid #cbd5e1; background: #fff; color: #334155; border-radius: 999px; padding: 6px 10px; cursor: pointer; font-size: 0.78rem; }
          .task-filter.active { background: linear-gradient(135deg, #dc2626, #f59e0b); color: #fff; border-color: transparent; }
          .task-list { display: grid; gap: 10px; max-height: 280px; overflow: auto; margin-bottom: 12px; }
          .task-card { border: 1px solid #e2e8f0; border-radius: 14px; background: #fff; padding: 10px; cursor: pointer; box-shadow: 0 8px 16px rgba(15, 23, 42, 0.05); }
          .task-card.active { border-color: #f97316; box-shadow: 0 10px 22px rgba(249, 115, 22, 0.16); }
          .task-card-header { display: flex; justify-content: space-between; gap: 10px; align-items: center; margin-bottom: 6px; }
          .task-status { font-size: 0.75rem; border-radius: 999px; padding: 4px 8px; color: #fff; background: #475569; white-space: nowrap; }
          .task-status.pending_approval { background: #dc2626; }
          .task-status.processing, .task-status.sending { background: #2563eb; }
          .task-status.sent { background: #16a34a; }
          .task-status.send_failed, .task-status.failed { background: #7f1d1d; }
          .task-status.approved, .task-status.queued_for_send, .task-status.delivery_deferred { background: #7c3aed; }
          .task-status.rejected { background: #475569; }
          .task-meta { color: #64748b; font-size: 0.76rem; line-height: 1.45; }
          .task-detail { border: 1px solid #e2e8f0; border-radius: 16px; background: #fff; padding: 12px; }
          .task-detail h4 { margin: 0 0 6px 0; font-size: 0.98rem; }
          .detail-hint { margin: 0 0 10px 0; color: #9a3412; font-size: 0.8rem; font-weight: 600; }
          .task-detail pre { white-space: pre-wrap; word-break: break-word; background: #f8fafc; border-radius: 12px; padding: 10px; font-size: 0.77rem; border: 1px solid #e2e8f0; }
          .task-actions { display: flex; gap: 8px; margin-top: 10px; }
          .task-actions button { flex: 1; border: none; border-radius: 10px; padding: 10px 12px; cursor: pointer; font-weight: 600; }
          .approve-btn { background: #16a34a; color: #fff; }
          .reject-btn { background: #dc2626; color: #fff; }
          .secondary-btn { background: #e2e8f0; color: #0f172a; }
          .detail-grid { display: grid; gap: 8px; margin-bottom: 10px; font-size: 0.8rem; }
          .detail-label { color: #64748b; }
          .event-list { display: grid; gap: 8px; margin-top: 12px; }
          .event-item { border-left: 3px solid #f97316; background: #fff7ed; padding: 8px 10px; border-radius: 8px; font-size: 0.76rem; }
          .task-empty { border: 1px dashed #cbd5e1; border-radius: 14px; padding: 16px; color: #64748b; text-align: center; background: #f8fafc; }
          .task-empty button { margin-top: 8px; border: none; border-radius: 10px; padding: 8px 12px; background: #f97316; color: #fff; cursor: pointer; }
          .supplement-grid { display: grid; gap: 8px; margin-top: 12px; }
          .supplement-grid input, .supplement-grid textarea { width: 100%; box-sizing: border-box; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; font-size: 0.8rem; font-family: inherit; }
          .supplement-grid textarea { min-height: 110px; resize: vertical; }
        </style>
        <div class="task-root">
          <div class="task-toolbar">
            <div class="task-chip" data-filter="pending_approval"><strong>${count("pending_approval")}</strong><small>待审批</small></div>
            <div class="task-chip" data-filter="send_failed"><strong>${count("send_failed")}</strong><small>发送失败</small></div>
            <div class="task-chip" data-filter="all"><strong>${state.tasks.length}</strong><small>全部任务</small></div>
          </div>
          <div class="task-filters">
            ${["all", "pending_approval", "needs_clarification", "send_failed", "sent"].map((key) => `
              <button class="task-filter ${state.filter === key ? "active" : ""}" data-filter="${key}">
                ${key === "all" ? "全部" : taskStatusLabel(key)}
              </button>
            `).join("")}
          </div>
          <div class="task-list">
            ${tasks.length ? tasks.map((task) => `
              <div class="task-card ${state.selectedTaskId === task.task_id ? "active" : ""}" data-task-id="${escapeAttr(task.task_id)}">
                <div class="task-card-header">
                  <strong>${escapeHtml(task.task_id)}</strong>
                  <span class="task-status ${escapeHtml(task.status)}">${escapeHtml(taskStatusLabel(task.status))}</span>
                </div>
                <div class="task-meta">
                  risk: ${escapeHtml(task.risk_level || "-")}<br/>
                  to: ${escapeHtml(task.destination_email || "未填写")}<br/>
                  file: ${escapeHtml(task.source_filename || "text")}
                </div>
              </div>
            `).join("") : `<div class="task-empty">${hasEmptyFilter ? "当前筛选下没有任务。" : "暂无任务，创建外发任务后会出现在这里。"}${hasEmptyFilter ? '<br/><button data-action="reset-filter">返回全部任务</button>' : ''}</div>`}
          </div>
          <div class="task-detail">
            ${selected ? `
              <h4>任务详情</h4>
              <div class="detail-hint">${escapeHtml(taskDetailHint(selected.status))}</div>
              <div class="detail-grid">
                <div><span class="detail-label">task_id:</span> ${escapeHtml(selected.task_id)}</div>
                <div><span class="detail-label">status:</span> ${escapeHtml(taskStatusLabel(selected.status))}</div>
                <div><span class="detail-label">risk:</span> ${escapeHtml(selected.risk_level || "-")}</div>
                <div><span class="detail-label">delivery:</span> ${escapeHtml(selected.delivery_status || "not_sent")}</div>
              </div>
              <div class="detail-label">脱敏预览</div>
              <pre>${escapeHtml(selected.message_redacted || selected.message_raw || "")}</pre>
              <div class="detail-label">摘要草稿</div>
              <pre>${escapeHtml(selected.draft_summary || "")}</pre>
              ${selected.clarification_question ? `<div class="detail-label">补充说明</div><pre>${escapeHtml(selected.clarification_question)}</pre>` : ""}
              ${selected.delivery_result ? `<div class="detail-label">发送结果</div><pre>${escapeHtml(selected.delivery_result)}</pre>` : ""}
              ${selected.delivery_error ? `<div class="detail-label">错误</div><pre>${escapeHtml(selected.delivery_error)}</pre>` : ""}
              ${(selected.status === "needs_clarification" || selected.status === "input_invalid") ? `
                <div class="supplement-grid">
                  <input type="text" placeholder="补充收件邮箱" data-supplement-email="${escapeAttr(selected.task_id)}" value="${escapeAttr(selectedEmailValue)}" />
                  <input type="file" accept=".txt,.log,.md,.json,.csv" data-supplement-file="${escapeAttr(selected.task_id)}" />
                  <textarea placeholder="补充正文或说明" data-supplement-text="${escapeAttr(selected.task_id)}">${escapeHtml(selectedMessageValue)}</textarea>
                </div>
                <div class="task-actions">
                  <button class="secondary-btn" data-action="supplement" data-task-id="${escapeAttr(selected.task_id)}">补充并恢复任务</button>
                </div>
              ` : ""}
              ${selected.status === "pending_approval" ? `
                <div class="task-actions">
                  <button class="approve-btn" data-action="approve" data-task-id="${escapeAttr(selected.task_id)}">批准并真实发送</button>
                  <button class="reject-btn" data-action="reject" data-task-id="${escapeAttr(selected.task_id)}">驳回并终止</button>
                </div>
              ` : ""}
              <div class="detail-label" style="margin-top:12px;">最近事件</div>
              <div class="event-list">
                ${selectedEvents.map((event) => `
                  <div class="event-item">
                    <strong>${escapeHtml(event.event_type)}</strong><br/>
                    <span>${escapeHtml(event.details_json?.message || "")}</span><br/>
                    <span style="color:#64748b;">${escapeHtml(event.created_at || "")}</span>
                  </div>
                `).join("")}
              </div>
            ` : `<div class="task-empty">请选择一个任务查看详情。</div>`}
          </div>
        </div>
      `;

      root.querySelectorAll("[data-filter]").forEach((button) => {
        button.addEventListener("click", () => {
          state.filter = button.getAttribute("data-filter");
          state.selectedTaskId = filterTasks()[0]?.task_id || state.selectedTaskId;
          render();
        });
      });
      root.querySelectorAll("[data-task-id]").forEach((card) => {
        if (card.getAttribute("data-action")) return;
        card.addEventListener("click", () => {
          state.selectedTaskId = card.getAttribute("data-task-id");
          render();
        });
      });
      root.querySelectorAll("[data-action='approve']").forEach((button) => {
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await approveTask(button.getAttribute("data-task-id"));
          } catch (err) {
            alert(`批准失败: ${err.message}`);
          } finally {
            button.disabled = false;
          }
        });
      });
      root.querySelectorAll("[data-action='reject']").forEach((button) => {
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await rejectTask(button.getAttribute("data-task-id"));
          } catch (err) {
            alert(`驳回失败: ${err.message}`);
          } finally {
            button.disabled = false;
          }
        });
      });
      root.querySelectorAll("[data-action='supplement']").forEach((button) => {
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await supplementTask(button.getAttribute("data-task-id"));
          } catch (err) {
            alert(`补充失败: ${err.message}`);
          } finally {
            button.disabled = false;
          }
        });
      });
      root.querySelectorAll("[data-action='reset-filter']").forEach((button) => {
        button.addEventListener("click", () => {
          state.filter = "all";
          state.selectedTaskId = filterTasks()[0]?.task_id || null;
          render();
        });
      });
      root.querySelectorAll("[data-supplement-email]").forEach((input) => {
        input.addEventListener("input", () => {
          const taskId = input.getAttribute("data-supplement-email");
          if (!taskId) return;
          getDraft(taskId).email = input.value;
        });
      });
      root.querySelectorAll("[data-supplement-text]").forEach((textarea) => {
        textarea.addEventListener("input", () => {
          const taskId = textarea.getAttribute("data-supplement-text");
          if (!taskId) return;
          getDraft(taskId).message = textarea.value;
        });
      });

      restoreInputFocus(focusSnapshot);
    }

    async function init() {
      await loadTasks();
      if (state.refreshTimer) clearInterval(state.refreshTimer);
      state.refreshTimer = setInterval(loadTasks, 15000);
    }

    init().catch((err) => {
      root.innerHTML = `<div style="padding:12px;border:1px solid #fecaca;background:#fff1f2;border-radius:12px;color:#9f1239;">任务台初始化失败: ${escapeHtml(err.message)}</div>`;
    });
    </script>
    """
    component_html = component_html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    components.html(component_html, height=height, scrolling=True)

def render_legacy_task_inspector(workflows: list[dict]) -> None:
    if not workflows:
        st.caption("暂无任务可供调试查看。")
        return

    workflow_labels = [
        f"{item['status']} | {item.get('risk_level', '')} | {item.get('destination_email') or '未填写'} | {item.get('source_filename') or 'text'} | {item['task_id']}"
        for item in workflows[:30]
    ]
    workflow_map = {label: workflows[index] for index, label in enumerate(workflow_labels)}
    selected_workflow_label = st.selectbox("任务调试选择", workflow_labels, key="legacy_task_inspector_select")
    selected_workflow = workflow_map[selected_workflow_label]
    st.write(f"status: `{selected_workflow['status']}`")
    st.write(f"risk: `{selected_workflow['risk_level']}`")
    st.write(f"destination: `{selected_workflow.get('destination_email') or '未填写'}`")
    st.write(f"delivery: `{selected_workflow.get('delivery_status', 'not_sent')}`")
    st.json(
        {
            "task_id": selected_workflow.get("task_id"),
            "status_path": selected_workflow.get("status_path", []),
            "scenario_evaluation": selected_workflow.get("scenario_evaluation", {}),
            "entry_governance": {
                "entry_issue_type": selected_workflow.get("entry_issue_type", ""),
                "missing_fields": selected_workflow.get("missing_fields", []),
                "clarification_question": selected_workflow.get("clarification_question", ""),
            },
            "governance": {
                "degradation_mode": selected_workflow.get("degradation_mode", ""),
                "fallback_reason": selected_workflow.get("fallback_reason", ""),
                "last_error_category": selected_workflow.get("last_error_category", ""),
                "retrieval_status": selected_workflow.get("retrieval_status", ""),
                "summary_status": selected_workflow.get("summary_status", ""),
            },
            "audit_events": selected_workflow.get("audit_events", []),
        }
    )


if "session_id" not in st.session_state:
    st.session_state.session_id = get_query_param("session_id") or str(uuid.uuid4())
if "auth_session_token" not in st.session_state:
    st.session_state.auth_session_token = ""
if "auth_user" not in st.session_state:
    st.session_state.auth_user = {}
if "login_email" not in st.session_state:
    st.session_state.login_email = ""
if "login_code_sent" not in st.session_state:
    st.session_state.login_code_sent = False
if "current_conversation_id" not in st.session_state:
    st.session_state.current_conversation_id = get_query_param("conversation_id")
if "last_debug" not in st.session_state:
    st.session_state.last_debug = None
if "auto_collapse_answers" not in st.session_state:
    st.session_state.auto_collapse_answers = True
if "last_debug_conversation_id" not in st.session_state:
    st.session_state.last_debug_conversation_id = None
if "task_panel_preferred_task_id" not in st.session_state:
    st.session_state.task_panel_preferred_task_id = None
if "task_panel_filter" not in st.session_state:
    st.session_state.task_panel_filter = "all"
if "task_panel_focus_nonce" not in st.session_state:
    st.session_state.task_panel_focus_nonce = ""
if "chat_upload_nonce" not in st.session_state:
    st.session_state.chat_upload_nonce = 0
if "visible_turn_limit" not in st.session_state:
    st.session_state.visible_turn_limit = 30


if st.session_state.auth_session_token:
    try:
        auth_payload = load_auth_me()
        st.session_state.auth_user = dict(auth_payload.get("user") or {})
        if not get_query_param("session_id") and not str(st.session_state.session_id).startswith("exmail-"):
            email = str(st.session_state.auth_user.get("email") or "")
            stable = email.replace("@", "_at_").replace(".", "_")
            st.session_state.session_id = f"exmail-{stable}" if stable else st.session_state.session_id
    except Exception:
        st.session_state.auth_session_token = ""
        st.session_state.auth_user = {}
        st.cache_data.clear()

if not st.session_state.auth_session_token:
    st.subheader("腾讯企业邮箱登录")
    st.caption("使用腾讯企业邮箱验证码绑定身份。登录后，系统会从服务端会话生成 tenant / user / workspace / roles。")
    email_value = st.text_input("企业邮箱", value=st.session_state.login_email, placeholder="name@company.com")
    col_send, col_verify = st.columns([1, 1])
    with col_send:
        if st.button("发送验证码", type="primary"):
            try:
                result = request_exmail_login_code(email_value)
                st.session_state.login_email = email_value.strip()
                st.session_state.login_code_sent = True
                st.success(f"验证码已发送到 {result.get('email_masked', '')}")
            except Exception as exc:
                st.error(f"发送失败: {exc}")
    with col_verify:
        code_value = st.text_input("验证码", value="", max_chars=12)
        if st.button("登录"):
            try:
                result = verify_exmail_login_code(email_value or st.session_state.login_email, code_value)
                st.session_state.auth_session_token = str(result.get("session_token") or "")
                st.session_state.auth_user = dict(result.get("user") or {})
                email = str(st.session_state.auth_user.get("email") or "")
                stable = email.replace("@", "_at_").replace(".", "_")
                st.session_state.session_id = f"exmail-{stable}" if stable else str(uuid.uuid4())
                st.session_state.current_conversation_id = None
                st.session_state.last_debug = None
                st.session_state.last_debug_conversation_id = None
                st.cache_data.clear()
                sync_url_state(st.session_state.session_id, None)
                st.rerun()
            except Exception as exc:
                st.error(f"登录失败: {exc}")
    st.stop()


auth_token = str(st.session_state.auth_session_token or "")
conversations = load_conversations(st.session_state.session_id, auth_token)
if not conversations:
    try:
        created = create_conversation(st.session_state.session_id)
        st.session_state.current_conversation_id = created["conversation_id"]
        conversations = load_conversations(st.session_state.session_id, auth_token)
    except Exception:
        conversations = []

conversation_options = {
    f"{'[鍚堝苟] ' if item.get('is_merged') else ''}{item['title']} ({item['conversation_id']})": item["conversation_id"]
    for item in conversations
}
reverse_conversation_options = {value: key for key, value in conversation_options.items()}
valid_conversation_ids = set(conversation_options.values())
if conversations and st.session_state.current_conversation_id not in valid_conversation_ids:
    st.session_state.current_conversation_id = conversations[0]["conversation_id"]
sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)


with st.sidebar:
    gradient_header("企业身份", "settings-gradient")
    user = dict(st.session_state.auth_user or {})
    st.caption(str(user.get("email") or ""))
    st.caption(f"tenant: {user.get('tenant_id', '')}")
    st.caption(f"workspace: {user.get('workspace_id', '')}")
    roles_text = ", ".join(str(role) for role in user.get("roles", []))
    st.caption(f"roles: {roles_text}")
    if st.button("退出登录", type="secondary"):
        try:
            logout_auth_session()
        except Exception:
            pass
        st.session_state.auth_session_token = ""
        st.session_state.auth_user = {}
        st.session_state.current_conversation_id = None
        st.session_state.last_debug = None
        st.session_state.last_debug_conversation_id = None
        st.cache_data.clear()
        st.rerun()

    gradient_header("对话工作区", "conversation-gradient")
    st.caption("当前 session")
    st.code(st.session_state.session_id, language=None)

    create_col, delete_col = st.columns(2)
    with create_col:
        if st.button("新建对话"):
            created = create_conversation(st.session_state.session_id)
            st.cache_data.clear()
            st.session_state.current_conversation_id = created["conversation_id"]
            st.session_state.visible_turn_limit = 30
            st.session_state.last_debug = None
            st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
            sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
            st.rerun()
    with delete_col:
        if st.session_state.current_conversation_id and st.button("删除对话", type="secondary"):
            try:
                delete_conversation(st.session_state.session_id, st.session_state.current_conversation_id)
                st.cache_data.clear()
                st.session_state.last_debug = None
                st.session_state.last_debug_conversation_id = None
                remaining = load_conversations(st.session_state.session_id, auth_token)
                if remaining:
                    st.session_state.current_conversation_id = remaining[0]["conversation_id"]
                else:
                    created = create_conversation(st.session_state.session_id)
                    st.session_state.current_conversation_id = created["conversation_id"]
                st.session_state.visible_turn_limit = 30
                sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
                st.success("对话已删除")
                st.rerun()
            except Exception as exc:
                st.error(f"删除失败: {exc}")

    if conversation_options:
        current_label = reverse_conversation_options.get(st.session_state.current_conversation_id)
        labels = list(conversation_options.keys())
        selected_label = st.selectbox(
            "当前对话",
            labels,
            index=labels.index(current_label) if current_label in labels else 0,
        )
        selected_conversation_id = conversation_options[selected_label]
        if selected_conversation_id != st.session_state.current_conversation_id:
            st.session_state.current_conversation_id = selected_conversation_id
            st.session_state.visible_turn_limit = 30
            st.session_state.last_debug_conversation_id = selected_conversation_id
            sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
        st.caption("当前 conversation_id")
        st.code(st.session_state.current_conversation_id, language=None)

    merge_choices = st.multiselect("选择要合并的对话", list(conversation_options.keys()))
    merge_name = st.text_input("合并后的名称", value="")
    if st.button("合并所选对话", disabled=len(merge_choices) < 2):
        try:
            result = merge_conversations(
                st.session_state.session_id,
                [conversation_options[label] for label in merge_choices],
                merge_name.strip() or None,
            )
            st.cache_data.clear()
            st.session_state.current_conversation_id = result["merged_conversation_id"]
            st.session_state.visible_turn_limit = 30
            st.session_state.last_debug = {"merge_result": result}
            st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
            sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
            st.success("合并完成")
            st.rerun()
        except Exception as exc:
            st.error(f"合并失败: {exc}")

    summary_payload = load_summary(st.session_state.current_conversation_id, auth_token)
    conversation_summary = (summary_payload.get("conversation") or {}).get("summary", "")
    merge_summary = summary_payload.get("merge") or {}
    if conversation_summary:
        with st.expander("当前对话摘要", expanded=False):
            st.write(conversation_summary)
    if merge_summary:
        with st.expander("合并来源", expanded=False):
            st.json(merge_summary)

    gradient_header("安全外发治理台", "dlp-gradient")
    workflows = sorted(list_sensitive_workflows(st.session_state.session_id, auth_token), key=workflow_sort_key)
    pending_count = len([item for item in workflows if item.get("status") == "pending_approval"])
    failed_count = len([item for item in workflows if item.get("status") == "send_failed"])
    count_cols = st.columns(3)
    with count_cols[0]:
        if st.button(f"待审批: {pending_count}", key="task_panel_filter_pending", use_container_width=True):
            apply_task_panel_filter("pending_approval")
            st.rerun()
    with count_cols[1]:
        if st.button(f"发送失败: {failed_count}", key="task_panel_filter_failed", use_container_width=True):
            apply_task_panel_filter("send_failed")
            st.rerun()
    with count_cols[2]:
        if st.button(f"全部任务: {len(workflows)}", key="task_panel_filter_all", use_container_width=True):
            apply_task_panel_filter("all")
            st.rerun()
    st.caption(f"待审批: {pending_count} / 发送失败: {failed_count} / 全部任务: {len(workflows)}")

    st.caption("实时任务台会持续刷新外发治理状态，并支持审批、驳回和补充恢复。")
    render_realtime_task_panel_v2(
        st.session_state.session_id,
        st.session_state.current_conversation_id,
        EXTERNAL_API_URL,
        auth_token=auth_token,
        preferred_task_id=st.session_state.task_panel_preferred_task_id,
        preferred_filter=st.session_state.task_panel_filter,
        focus_nonce=st.session_state.task_panel_focus_nonce,
    )

    gradient_header("Agent 设置 / 调试", "settings-gradient")
    mode = st.selectbox(
        "执行模式",
        options=[("auto", "Auto"), ("react", "ReAct"), ("plan_execute", "Plan-and-Execute"), ("reflection", "Reflection")],
        format_func=lambda option: option[1],
    )[0]
    show_steps = st.checkbox("显示工具调用过程", value=True)
    st.session_state.auto_collapse_answers = st.checkbox(
        "自动折叠长回复",
        value=st.session_state.auto_collapse_answers,
    )
    force_ingest = st.checkbox("强制重建/补写语料", value=False)
    if st.button("初始化统一 RAG 语料"):
        try:
            st.success(trigger_ingest(force_ingest))
            st.cache_data.clear()
        except Exception as exc:
            st.error(f"初始化失败: {exc}")
    st.markdown(f"- API: `{EXTERNAL_API_URL}`\n- Prometheus: `{EXTERNAL_PROMETHEUS_URL}`")


left, right = st.columns([2, 1])

with left:
    st.subheader("对话")
    turns = load_turns(st.session_state.current_conversation_id, auth_token)
    current_turn_debug = latest_debug_from_turns(turns)
    if current_turn_debug and (
        not st.session_state.last_debug
        or st.session_state.last_debug_conversation_id != st.session_state.current_conversation_id
    ):
        st.session_state.last_debug = current_turn_debug
        st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
    if not turns:
        st.caption("这个对话还没有消息。")
    hidden_turn_count = max(0, len(turns) - st.session_state.visible_turn_limit)
    if hidden_turn_count:
        info_col, action_col = st.columns([3, 1])
        with info_col:
            st.caption(f"当前仅渲染最近 {st.session_state.visible_turn_limit} 条消息，已隐藏更早的 {hidden_turn_count} 条，以保证页面流畅。")
        with action_col:
            if st.button("加载更早消息", key=f"load_more_{st.session_state.current_conversation_id}"):
                st.session_state.visible_turn_limit += 20
                st.rerun()
    visible_turns = turns[-st.session_state.visible_turn_limit :]
    for turn in visible_turns:
        render_message(turn, st.session_state.auto_collapse_answers)

    st.markdown(
        "<span class='small-muted'>可选：把日志、JSON、CSV 或 Markdown 上传给 Agent 一起处理。</span>",
        unsafe_allow_html=True,
    )
    chat_uploaded = st.file_uploader(
        "上传给本轮对话的文件",
        key=f"chat_upload_{st.session_state.current_conversation_id}_{st.session_state.chat_upload_nonce}",
    )
    chat_uploaded_text, chat_source_parse_status, chat_upload_error, chat_uploaded_base64 = decode_uploaded_file(chat_uploaded)
    chat_source_parse_error = chat_upload_error
    if chat_upload_error:
        st.warning(chat_upload_error)
    elif chat_uploaded:
        st.caption(f"已准备文件: {chat_uploaded.name}，发送下一条消息时会一起提交。")

    prompt = st.chat_input("输入问题，例如：帮我把如下文段发送到 1136732521@qq.com")
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
            if chat_uploaded:
                st.caption(f"附件: {chat_uploaded.name}")
        with st.chat_message("assistant"):
            with st.spinner("Agent 正在处理请求；如涉及外发，会进入 DLP 检测与审批链路..."):
                try:
                    result = run_unified_agent(
                        st.session_state.session_id,
                        st.session_state.current_conversation_id,
                        prompt,
                        mode,
                        show_steps,
                        chat_uploaded.name if chat_uploaded else "",
                        chat_uploaded.type if chat_uploaded else "",
                        chat_uploaded_text,
                        chat_uploaded_base64,
                        chat_source_parse_status,
                        chat_source_parse_error,
                    )
                    st.cache_data.clear()
                    st.session_state.current_conversation_id = result["conversation_id"]
                    st.session_state.visible_turn_limit = 30
                    st.session_state.last_debug = result
                    st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
                    if result.get("task_id"):
                        set_task_panel_focus(result.get("task_id"), result.get("task_status"))
                    st.session_state.chat_upload_nonce += 1
                    sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
                    render_answer(result["answer"], st.session_state.auto_collapse_answers)
                    st.rerun()
                except Exception as exc:
                    st.error(f"请求失败: {exc}")

with right:
    st.subheader("调试面板")
    with st.expander("邮件早报 / 收件状态（调试）", expanded=False):
        st.caption("只读企业邮箱 IMAP 收件能力。这里仅用于手动同步和查看 notification outbox，不做强提醒弹窗。")
        mail_cols = st.columns(2)
        with mail_cols[0]:
            if st.button("同步收件箱", use_container_width=True):
                try:
                    sync_result = sync_inbound_mailbox()
                    if sync_result.get("enabled") is False:
                        st.info(sync_result.get("state", {}).get("last_error") or "IMAP 未启用。")
                    else:
                        st.success(f"同步完成：拉取 {sync_result.get('synced', 0)} / 新增 {sync_result.get('new', 0)}")
                except Exception as exc:
                    st.error(f"同步失败: {exc}")
        with mail_cols[1]:
            if st.button("生成早报事件", use_container_width=True):
                try:
                    digest_result = generate_daily_mail_digest()
                    notification = digest_result.get("notification") or {}
                    st.success(f"早报事件已生成: {notification.get('notification_id', 'created')}")
                except Exception as exc:
                    st.error(f"生成失败: {exc}")
        try:
            mail_summary = load_inbound_mail_summary(auth_token)
            state = mail_summary.get("sync_state") or {}
            metric_cols = st.columns(3)
            metric_cols[0].metric("时间窗邮件", mail_summary.get("total", 0))
            metric_cols[1].metric("未读估计", mail_summary.get("unread", 0))
            metric_cols[2].metric("重要提示", mail_summary.get("important_count", 0))
            if state.get("last_sync_at"):
                st.caption(f"最近同步: {state.get('last_sync_at')}")
            if state.get("last_error"):
                st.warning(state.get("last_error"))
            recent_messages = load_inbound_mail_messages(limit=5, auth_token=auth_token)
            if recent_messages:
                st.write("最近邮件")
                for message in recent_messages:
                    st.markdown(
                        f"- `{message.get('received_at', '')}` "
                        f"**{message.get('subject') or '(no subject)'}** "
                        f"from `{message.get('sender', '')}`"
                    )
            else:
                st.caption("暂无已同步邮件。开启 `IMAP_ENABLED=true` 并配置当前邮箱的 IMAP 凭证后可同步。")
            notifications = load_notification_outbox(limit=5, auth_token=auth_token)
            if notifications:
                with st.expander("最近通知事件", expanded=False):
                    for item in notifications:
                        st.markdown(f"- `{item.get('event_type')}` {item.get('title')} ({item.get('status')})")
        except Exception as exc:
            st.info(f"邮件状态暂不可用: {exc}")
    with st.expander("任务调试视图", expanded=False):
        render_legacy_task_inspector(workflows)
    debug = st.session_state.last_debug
    if not debug:
        st.caption("发起一次对话、合并或 DLP 任务后，这里会显示路由、工具、RAG 证据、任务和记忆命中。")
    elif "merge_result" in debug:
        st.metric("Merged", debug["merge_result"]["merged_conversation_id"])
        st.metric("Source conversations", len(debug["merge_result"]["source_conversations"]))
        st.metric("Source turns", len(debug["merge_result"]["source_turn_ids"]))
        with st.expander("合并结果", expanded=True):
            st.json(debug["merge_result"])
    elif "workflow_result" in debug:
        workflow_result = debug["workflow_result"]
        st.metric("Task", workflow_result["task_id"])
        st.metric("Status", workflow_result["status"])
        st.metric("Risk", workflow_result["risk_level"])
        st.metric("Delivery", workflow_result.get("delivery_status", "not_sent"))
        with st.expander("Workflow result", expanded=True):
            st.json(workflow_result)
    else:
        st.metric("Intent", debug.get("intent", "unknown"))
        st.metric("Route", debug.get("routing_source", "unknown"))
        st.metric("Memory", debug.get("memory_hits", 0))
        st.metric("Merged Memory", debug.get("merged_memory_hits", 0))
        st.metric("Latency", f"{debug.get('latency_ms', 0):.0f} ms")
        if debug.get("task_id"):
            st.metric("Task", debug["task_id"])
            st.metric("Task Status", debug.get("task_status", ""))
            st.metric("Delivery", debug.get("delivery_status", ""))
            if debug.get("delivery_error"):
                st.error(debug["delivery_error"])
        if show_steps:
            with st.expander("Tool Calls", expanded=True):
                st.json(debug.get("tool_calls", []))
            with st.expander("Unified RAG Evidence", expanded=False):
                st.json(debug.get("retrieved_evidence", []))
            with st.expander("Privacy / Workflow", expanded=False):
                st.json(
                    {
                        "privacy": debug.get("privacy", {}),
                        "task_id": debug.get("task_id"),
                        "task_status": debug.get("task_status"),
                        "task_risk_level": debug.get("task_risk_level"),
                        "delivery_status": debug.get("delivery_status"),
                        "delivery_result": debug.get("delivery_result"),
                        "delivery_error": debug.get("delivery_error"),
                    }
                )
            with st.expander("Memory Context", expanded=False):
                st.json(
                    {
                        "memory_context": debug.get("memory_context", {}),
                        "upload_context": debug.get("upload_context", {}),
                    }
                )


