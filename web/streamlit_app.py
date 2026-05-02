from __future__ import annotations

import os
import uuid

import httpx
import streamlit as st


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
EXTERNAL_API_URL = "http://localhost:8010"
EXTERNAL_PROMETHEUS_URL = "http://localhost:9091"
DEFAULT_DESTINATION_EMAIL = "17388861183@163.com"
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


st.set_page_config(page_title="DLP 外发审批 Agent", page_icon="🛡️", layout="wide")

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

st.title("DLP 外发审批 Agent")
st.caption("一个企业流程型 Agent：中间对话触发外发、上传文本文件、检测敏感信息、人工审批后通过 MCP 邮件工具真实发送。")


def api_get(path: str, params: dict | None = None, timeout: float = 10.0):
    with httpx.Client(timeout=timeout) as client:
        response = client.get(f"{API_BASE_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


def api_post(path: str, payload: dict | None = None, params: dict | None = None, timeout: float = 120.0):
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{API_BASE_URL}{path}", json=payload, params=params)
        response.raise_for_status()
        return response.json()


def api_delete(path: str, params: dict | None = None, timeout: float = 30.0):
    with httpx.Client(timeout=timeout) as client:
        response = client.delete(f"{API_BASE_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


def load_conversations(session_id: str) -> list[dict]:
    try:
        return api_get("/conversations", params={"session_id": session_id})
    except Exception:
        return []


def load_turns(conversation_id: str | None) -> list[dict]:
    if not conversation_id:
        return []
    try:
        return api_get(f"/conversations/{conversation_id}/turns")
    except Exception:
        return []


def load_summary(conversation_id: str | None) -> dict:
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


def list_sensitive_workflows(session_id: str) -> list[dict]:
    try:
        return api_get("/workflows/sensitive-outbound", params={"session_id": session_id})
    except Exception:
        return []


def create_sensitive_workflow(
    session_id: str,
    conversation_id: str | None,
    message: str,
    destination_email: str,
    source_filename: str = "",
    source_content_type: str = "",
) -> dict:
    return api_post(
        "/workflows/sensitive-outbound",
        {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": message,
            "business_context": "manual_sidebar_dlp_outbound",
            "recipient_type": "email",
            "destination_email": destination_email or DEFAULT_DESTINATION_EMAIL,
            "source_filename": source_filename,
            "source_content_type": source_content_type,
            "requested_action": "summarize_and_send",
        },
    )


def approve_sensitive_workflow(workflow_id: str, actor: str) -> dict:
    return api_post(f"/workflows/sensitive-outbound/{workflow_id}/approve", {"actor": actor}, timeout=60.0)


def reject_sensitive_workflow(workflow_id: str, actor: str, reason: str) -> dict:
    return api_post(f"/workflows/sensitive-outbound/{workflow_id}/reject", {"actor": actor, "reason": reason})


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
        st.caption("回答较长，已折叠。")
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


def decode_uploaded_file(uploaded_file) -> tuple[str, str | None]:
    if uploaded_file is None:
        return "", None
    raw = uploaded_file.getvalue()
    if len(raw) > MAX_UPLOAD_BYTES:
        return "", "上传文件超过 2MB，首版只支持小型日志或文本文件。"
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return "", "文件解码失败。请上传 UTF-8、GBK 或 GB18030 编码的文本类文件。"


def workflow_sort_key(item: dict) -> tuple[int, str]:
    rank = {"pending_approval": 0, "send_failed": 1, "sent": 2, "approved": 3, "rejected": 4, "completed": 5}
    return (rank.get(item.get("status", ""), 9), item.get("updated_at", ""))


def gradient_header(label: str, css_class: str) -> None:
    st.markdown(f"<div class='gradient-card {css_class}'>{label}</div>", unsafe_allow_html=True)


if "session_id" not in st.session_state:
    st.session_state.session_id = get_query_param("session_id") or str(uuid.uuid4())
if "current_conversation_id" not in st.session_state:
    st.session_state.current_conversation_id = get_query_param("conversation_id")
if "last_debug" not in st.session_state:
    st.session_state.last_debug = None
if "auto_collapse_answers" not in st.session_state:
    st.session_state.auto_collapse_answers = True
if "last_debug_conversation_id" not in st.session_state:
    st.session_state.last_debug_conversation_id = None


conversations = load_conversations(st.session_state.session_id)
if not conversations:
    try:
        created = create_conversation(st.session_state.session_id)
        st.session_state.current_conversation_id = created["conversation_id"]
        conversations = load_conversations(st.session_state.session_id)
    except Exception:
        conversations = []

conversation_options = {
    f"{'[合并] ' if item.get('is_merged') else ''}{item['title']} ({item['conversation_id']})": item["conversation_id"]
    for item in conversations
}
reverse_conversation_options = {value: key for key, value in conversation_options.items()}
valid_conversation_ids = set(conversation_options.values())
if conversations and st.session_state.current_conversation_id not in valid_conversation_ids:
    st.session_state.current_conversation_id = conversations[0]["conversation_id"]
sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)


with st.sidebar:
    gradient_header("对话工作区", "conversation-gradient")
    st.caption("当前 session")
    st.code(st.session_state.session_id, language=None)

    create_col, delete_col = st.columns(2)
    with create_col:
        if st.button("新建对话"):
            created = create_conversation(st.session_state.session_id)
            st.session_state.current_conversation_id = created["conversation_id"]
            st.session_state.last_debug = None
            st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
            sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
            st.rerun()
    with delete_col:
        if st.session_state.current_conversation_id and st.button("删除对话", type="secondary"):
            try:
                delete_conversation(st.session_state.session_id, st.session_state.current_conversation_id)
                st.session_state.last_debug = None
                st.session_state.last_debug_conversation_id = None
                remaining = load_conversations(st.session_state.session_id)
                if remaining:
                    st.session_state.current_conversation_id = remaining[0]["conversation_id"]
                else:
                    created = create_conversation(st.session_state.session_id)
                    st.session_state.current_conversation_id = created["conversation_id"]
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
            index=labels.index(current_label) if current_label in conversation_options else 0,
        )
        selected_conversation_id = conversation_options[selected_label]
        if selected_conversation_id != st.session_state.current_conversation_id:
            st.session_state.current_conversation_id = selected_conversation_id
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
            st.session_state.current_conversation_id = result["merged_conversation_id"]
            st.session_state.last_debug = {"merge_result": result}
            st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
            sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
            st.success("合并完成")
            st.rerun()
        except Exception as exc:
            st.error(f"合并失败: {exc}")

    summary_payload = load_summary(st.session_state.current_conversation_id)
    conversation_summary = (summary_payload.get("conversation") or {}).get("summary", "")
    merge_summary = summary_payload.get("merge") or {}
    if conversation_summary:
        with st.expander("当前对话摘要", expanded=False):
            st.write(conversation_summary)
    if merge_summary:
        with st.expander("合并来源", expanded=False):
            st.json(merge_summary)

    gradient_header("DLP 外发审批 Agent", "dlp-gradient")
    workflows = sorted(list_sensitive_workflows(st.session_state.session_id), key=workflow_sort_key)
    pending_count = len([item for item in workflows if item.get("status") == "pending_approval"])
    failed_count = len([item for item in workflows if item.get("status") == "send_failed"])
    st.caption(f"待审批: {pending_count} / 发送失败: {failed_count} / 全部任务: {len(workflows)}")

    with st.expander("手动创建外发任务", expanded=False):
        manual_destination = st.text_input("收件邮箱", value=DEFAULT_DESTINATION_EMAIL)
        manual_uploaded = st.file_uploader("上传日志或文本文件", type=["txt", "log", "md", "json", "csv"], key="manual_upload")
        manual_uploaded_text, manual_upload_error = decode_uploaded_file(manual_uploaded)
        if manual_upload_error:
            st.error(manual_upload_error)
        manual_text = st.text_area(
            "粘贴文本或补充说明",
            value="帮我把这段日志总结一下，并发给邮箱。User: 张三, Phone: 13800000000, API_KEY: sk-123456, 数据库报错连接超时。",
            height=130,
        )
        if st.button("创建 DLP 外发任务"):
            if manual_upload_error:
                st.error("上传文件未通过校验，未创建任务。")
            else:
                combined_text = manual_text.strip()
                if manual_uploaded_text:
                    combined_text = (
                        f"{combined_text}\n\n[上传文件: {manual_uploaded.name}]\n{manual_uploaded_text}"
                        if combined_text
                        else manual_uploaded_text
                    )
                if not combined_text.strip():
                    st.error("请粘贴文本或上传文件。")
                else:
                    try:
                        created_workflow = create_sensitive_workflow(
                            st.session_state.session_id,
                            st.session_state.current_conversation_id,
                            combined_text,
                            manual_destination,
                            manual_uploaded.name if manual_uploaded else "",
                            manual_uploaded.type if manual_uploaded else "",
                        )
                        st.session_state.last_debug = {"workflow_result": created_workflow}
                        st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
                        st.success(f"任务已创建: {created_workflow['status']}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"创建任务失败: {exc}")

    if workflows:
        workflow_labels = [
            f"{item['status']} | {item['risk_level']} | {item.get('destination_email')} | {item.get('source_filename') or 'text'} | {item['workflow_id']}"
            for item in workflows[:30]
        ]
        workflow_map = {label: workflows[index] for index, label in enumerate(workflow_labels)}
        selected_workflow_label = st.selectbox("工作流任务", workflow_labels)
        selected_workflow = workflow_map[selected_workflow_label]
        with st.expander("任务详情", expanded=selected_workflow.get("status") in {"pending_approval", "send_failed"}):
            st.write(f"status: `{selected_workflow['status']}`")
            st.write(f"risk: `{selected_workflow['risk_level']}`")
            st.write(f"destination: `{selected_workflow.get('destination_email', DEFAULT_DESTINATION_EMAIL)}`")
            st.write(f"delivery: `{selected_workflow.get('delivery_status', 'not_sent')}`")
            if selected_workflow.get("source_filename"):
                st.write(f"file: `{selected_workflow['source_filename']}`")
            st.write("脱敏预览")
            st.code(selected_workflow.get("redacted_text", ""), language=None)
            st.write("摘要草稿")
            st.write(selected_workflow.get("draft_summary", ""))
            if selected_workflow.get("delivery_result"):
                st.write("真实发送结果")
                st.success(selected_workflow["delivery_result"])
            if selected_workflow.get("delivery_error"):
                st.write("发送失败原因")
                st.error(selected_workflow["delivery_error"])
            if selected_workflow.get("final_result"):
                st.write("最终结果")
                st.write(selected_workflow["final_result"])
            st.json(
                {
                    "risk_reasons": selected_workflow.get("risk_reasons", []),
                    "redactions": selected_workflow.get("redactions", []),
                    "audit_events": selected_workflow.get("audit_events", []),
                }
            )
            if selected_workflow.get("status") == "pending_approval":
                reviewer = st.text_input("审批人", value="local_reviewer", key="workflow_reviewer")
                reject_reason = st.text_input("驳回原因", value="contains sensitive outbound data", key="workflow_reject_reason")
                approve_col, reject_col = st.columns(2)
                with approve_col:
                    if st.button("批准并真实发送", key=f"approve_{selected_workflow['workflow_id']}"):
                        try:
                            result = approve_sensitive_workflow(selected_workflow["workflow_id"], reviewer)
                            st.session_state.last_debug = {"workflow_result": result}
                            st.success("审批完成，已尝试真实发送")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"批准失败: {exc}")
                with reject_col:
                    if st.button("驳回并终止", key=f"reject_{selected_workflow['workflow_id']}"):
                        try:
                            result = reject_sensitive_workflow(selected_workflow["workflow_id"], reviewer, reject_reason)
                            st.session_state.last_debug = {"workflow_result": result}
                            st.warning("已驳回并终止")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"驳回失败: {exc}")

    gradient_header("Agent 设置 / 调试", "settings-gradient")
    mode = st.selectbox(
        "执行模式",
        options=[("auto", "Auto"), ("react", "ReAct"), ("plan_execute", "Plan-and-Execute"), ("reflection", "Reflection")],
        format_func=lambda option: option[1],
    )[0]
    show_steps = st.checkbox("展示工具调用过程", value=True)
    st.session_state.auto_collapse_answers = st.checkbox(
        "自动折叠长回答",
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
    turns = load_turns(st.session_state.current_conversation_id)
    current_turn_debug = latest_debug_from_turns(turns)
    if current_turn_debug and (
        not st.session_state.last_debug
        or st.session_state.last_debug_conversation_id != st.session_state.current_conversation_id
    ):
        st.session_state.last_debug = current_turn_debug
        st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
    if not turns:
        st.caption("这个对话还没有消息。")
    for turn in turns:
        render_message(turn, st.session_state.auto_collapse_answers)

    st.markdown("<span class='small-muted'>可选：把日志、JSON、CSV 或 Markdown 上传给 Agent 一起处理。</span>", unsafe_allow_html=True)
    chat_uploaded = st.file_uploader(
        "上传给本轮对话的文件",
        type=["txt", "log", "md", "json", "csv"],
        key=f"chat_upload_{st.session_state.current_conversation_id}",
    )
    chat_uploaded_text, chat_upload_error = decode_uploaded_file(chat_uploaded)
    if chat_upload_error:
        st.error(chat_upload_error)
    elif chat_uploaded:
        st.caption(f"已准备文件: {chat_uploaded.name}，发送下一条消息时会一并提交。")

    prompt = st.chat_input("输入问题，例如：帮我总结这份日志并外发到 17388861183@163.com")
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
            if chat_uploaded:
                st.caption(f"附件: {chat_uploaded.name}")
        with st.chat_message("assistant"):
            if chat_upload_error:
                st.error("上传文件未通过校验，本轮未提交。")
            else:
                with st.spinner("Agent 正在判断是否需要 DLP 审批、检索记忆或调用邮件工具..."):
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
                        )
                        st.session_state.current_conversation_id = result["conversation_id"]
                        st.session_state.last_debug = result
                        st.session_state.last_debug_conversation_id = st.session_state.current_conversation_id
                        sync_url_state(st.session_state.session_id, st.session_state.current_conversation_id)
                        render_answer(result["answer"], st.session_state.auto_collapse_answers)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"请求失败: {exc}")

with right:
    st.subheader("调试面板")
    debug = st.session_state.last_debug
    if not debug:
        st.caption("发起一次对话、合并或 DLP 任务后，这里会展示路由、工具、RAG 证据、workflow 和 memory 命中。")
    elif "merge_result" in debug:
        st.metric("Merged", debug["merge_result"]["merged_conversation_id"])
        st.metric("Source conversations", len(debug["merge_result"]["source_conversations"]))
        st.metric("Source turns", len(debug["merge_result"]["source_turn_ids"]))
        with st.expander("合并结果", expanded=True):
            st.json(debug["merge_result"])
    elif "workflow_result" in debug:
        workflow_result = debug["workflow_result"]
        st.metric("Workflow", workflow_result["workflow_id"])
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
        if debug.get("workflow_id"):
            st.metric("Workflow", debug["workflow_id"])
            st.metric("Workflow Status", debug.get("workflow_status", ""))
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
                        "workflow_id": debug.get("workflow_id"),
                        "workflow_status": debug.get("workflow_status"),
                        "workflow_risk_level": debug.get("workflow_risk_level"),
                        "delivery_status": debug.get("delivery_status"),
                        "delivery_result": debug.get("delivery_result"),
                        "delivery_error": debug.get("delivery_error"),
                    }
                )
            with st.expander("Memory Context", expanded=False):
                st.json(debug.get("memory_context", {}))
