from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.orchestration.types import AgentTaskPlan, ToolResult


def aggregate_results(plan: AgentTaskPlan, results: list[ToolResult]) -> dict[str, Any]:
    visible_results = [item for item in results if _is_visible(plan, item.task_id)]
    answer_sections: list[str] = []
    citations: list[dict[str, Any]] = []
    partial_failures: list[dict[str, Any]] = []
    retrieved_evidence: list[dict[str, Any]] = []
    upload_context: dict[str, Any] = {}
    context_sources: list[str] = []
    workspace_memory_hits = 0
    transcript_hits = 0
    user_model_used = False
    memory_context: dict[str, Any] = {}

    for item in results:
        if item.error:
            partial_failures.append({"task_id": item.task_id, "capability": item.capability, "error": item.error})
        if item.payload.get("citations"):
            citations.extend(list(item.payload.get("citations") or []))
        if item.capability in {"enterprise_rag_query", "enterprise_search"} and item.payload:
            retrieved_evidence.extend(list(item.payload.get("citations") or item.payload.get("evidence", {}).get("citations", [])))
        if not upload_context and isinstance(item.payload.get("upload_context"), dict):
            upload_context = dict(item.payload.get("upload_context") or {})
        if item.payload.get("context_sources"):
            context_sources = list(item.payload.get("context_sources") or [])
            workspace_memory_hits = int(item.payload.get("workspace_memory_hits", 0))
            transcript_hits = int(item.payload.get("transcript_hits", 0))
            user_model_used = bool(item.payload.get("user_model_used", False))
        if item.payload.get("memory_context"):
            memory_context = dict(item.payload.get("memory_context") or {})

    for item in visible_results:
        section = _section_for_result(item)
        if section:
            answer_sections.append(section)

    if not answer_sections and partial_failures:
        answer_sections.append("部分子任务执行失败，当前没有可展示的成功结果。")
    if partial_failures:
        answer_sections.append(_render_partial_failures(partial_failures))

    answer = "\n\n".join(section for section in answer_sections if section).strip()
    if not answer:
        answer = "当前没有生成可展示结果。"
    return {
        "answer": answer,
        "citations": citations[:10],
        "retrieved_evidence": retrieved_evidence[:10],
        "partial_failures": partial_failures,
        "subtask_results": [asdict(item) for item in results],
        "upload_context": upload_context,
        "context_sources": context_sources,
        "workspace_memory_hits": workspace_memory_hits,
        "transcript_hits": transcript_hits,
        "user_model_used": user_model_used,
        "memory_context": memory_context,
    }


def _is_visible(plan: AgentTaskPlan, task_id: str) -> bool:
    for item in plan.subtasks:
        if item.task_id == task_id:
            return item.user_visible
    return True


def _section_for_result(result: ToolResult) -> str:
    if result.error:
        return ""
    if result.capability == "outbound_mail_summary":
        return _outbound_summary_answer(result.payload)
    if result.capability == "inbound_mail_summary":
        return _inbound_summary_answer(result.payload)
    if result.capability == "enterprise_rag_query":
        return _enterprise_answer_text(result.payload)
    if result.capability == "inbound_reply_draft":
        return _reply_draft_answer(result.payload)
    if result.capability in {"uploaded_content_analyze", "unsupported_capability", "persona_or_chitchat"}:
        return str(result.payload.get("answer") or result.payload.get("observation_summary") or "")
    if result.capability == "inbound_message_read":
        message = result.payload.get("message") or {}
        if not isinstance(message, dict):
            return ""
        return f"已读取邮件：{message.get('subject') or '(无主题)'}"
    return ""


def _inbound_summary_answer(summary: dict[str, Any]) -> str:
    state = dict(summary.get("sync_state") or {})
    lines = [
        f"昨日到现在共收到 {summary.get('total', 0)} 封邮件，其中未读 {summary.get('unread', 0)} 封。",
        f"重要邮件候选 {summary.get('important_count', 0)} 封。",
    ]
    if state.get("last_error"):
        lines.append(f"最近一次收件同步异常：{state['last_error']}")
    important = list(summary.get("important_messages") or [])[:5]
    if important:
        lines.append("")
        lines.append("重要邮件摘要：")
        for item in important:
            sender = _format_sender_label(str(item.get("sender") or ""))
            lines.append(f"- {item.get('subject') or '(无主题)'} | {sender}: {item.get('summary') or item.get('snippet', '')}")
    return "\n".join(lines)


def _outbound_summary_answer(summary: dict[str, Any]) -> str:
    lines = [f"今天通过 Agent 成功发送了 {int(summary.get('total_sent', 0))} 封邮件。"]
    recent_sent = list(summary.get("recent_sent") or [])
    if recent_sent:
        lines.append("")
        lines.append("最近发送：")
        for item in recent_sent[:5]:
            recipient = str(item.get("destination_email") or "").strip() or "未填写"
            sent_at = str(item.get("sent_at") or "")
            lines.append(f"- `{sent_at}` -> `{recipient}`")
    return "\n".join(lines)


def _enterprise_answer_text(payload: dict[str, Any]) -> str:
    answer = str(payload.get("answer") or "当前企业知识库中没有检索到足够证据。")
    lines = [answer]
    citations = list((payload.get("answer_debug") or {}).get("deduped_citations") or [])[:3]
    if not citations:
        seen_doc_ids: set[str] = set()
        seen_titles: set[str] = set()
        citations = []
        for item in list(payload.get("citations") or []):
            doc_id = str(item.get("doc_id") or "").strip()
            title = str(item.get("title") or "").strip().lower()
            if doc_id and doc_id in seen_doc_ids:
                continue
            if not doc_id and title and title in seen_titles:
                continue
            if doc_id:
                seen_doc_ids.add(doc_id)
            if title:
                seen_titles.add(title)
            citations.append(item)
            if len(citations) >= 3:
                break
    if citations:
        lines.append("")
        lines.append("引用证据：")
        for item in citations:
            lines.append(f"- {item.get('title') or '(untitled)'} [{item.get('source_type') or 'unknown'}] `{item.get('doc_id') or ''}`")
    return "\n".join(lines)


def _reply_draft_answer(payload: dict[str, Any]) -> str:
    message = dict(payload.get("message") or {})
    draft_reply = str(payload.get("draft_reply") or "").strip()
    if not draft_reply:
        return "未能生成回复草稿。"
    subject = str(message.get("subject") or "(无主题)")
    sender = _format_sender_label(str(message.get("sender") or ""))
    return (
        f"已基于同步邮件生成回复草稿。\n"
        f"邮件：{subject} | {sender}\n\n"
        f"{draft_reply}\n\n"
        "若你要真正发给对方，请再明确给出发送指令，系统会进入 DLP 外发治理链路。"
    )


def _render_partial_failures(partial_failures: list[dict[str, Any]]) -> str:
    lines = ["部分子任务未成功："]
    for item in partial_failures:
        lines.append(f"- `{item.get('capability')}`：{item.get('error') or 'unknown error'}")
    return "\n".join(lines)


def _format_sender_label(sender: str) -> str:
    value = sender.strip()
    if not value:
        return "未知发件人"
    if value.endswith("@exmail.weixin.qq.com"):
        return "系统通知"
    return f"`{value}`"
