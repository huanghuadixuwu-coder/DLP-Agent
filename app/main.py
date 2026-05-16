from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from contextlib import asynccontextmanager
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from redis import Redis

from app.config import get_settings
from app.conversation_memory import build_memory_context, compact_text, infer_title, write_merged_summary, write_turn_summary
from app.conversation_store import (
    append_exchange,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_merge,
    get_turns,
    init_conversation_store,
    list_conversations,
    save_merge,
    update_conversation_summary,
)
from app.corpus import list_problem_summaries, problem_lookup
from app.dlp_entry import classify_dlp_entry, extract_destination_email as dlp_extract_destination_email, looks_like_outbound_action as dlp_looks_like_outbound_action
from app.dlp_scenarios import build_status_path, evaluate_scenario_task, get_dlp_scenario, list_dlp_scenarios, normalize_fault_injection
from app.enterprise_rag.core.service import answer_enterprise_question
from app.enterprise_rag.eval.benchmark_runner import run_benchmark_sample
from app.enterprise_rag.eval.casebook import build_casebook
from app.enterprise_rag.ingestion.indexer import ingest_enterprise_rag_bench
from app.graph import execute_confirmed_plan, get_llm, preview_plan, run_agent
from app.hermes_memory import init_workspace_memory_index
from app.hermes_dynamic_memory import (
    get_recent_compactions,
    get_structured_turn_summaries,
    get_user_memory_context,
    get_workspace_memory_context,
    init_hermes_dynamic_memory_store,
    write_dynamic_turn_memory,
)
from app.ingest import ingest_if_needed
from app.inbound_mail import (
    draft_reply_for_message,
    generate_daily_mail_digest,
    get_inbound_mail_summary,
    latest_sync_state,
    list_inbound_mail_messages,
    sync_inbound_mail,
)
from app.inbound_mail_store import init_inbound_mail_store, list_notifications
from app.disambiguation_lab import answer_apple_query
from app.labs_long_doc import allocate_context
from app.memory import build_conversation_summary_document
from app.metrics import (
    content_type,
    record_context_budget,
    record_answer_collapse,
    record_conversation_created,
    record_conversation_merge,
    record_conversation_turns,
    record_failure,
    record_lab_request,
    record_memory_retrieval_hits,
    record_merged_summary_write,
    record_privacy_scan,
    record_request,
    record_router,
    record_dlp_scenario_replay,
    record_task_created,
    record_task_degradation,
    record_turn_summary_write,
    record_unified_agent,
    record_unified_evidence_hits,
    record_workflow_approved,
    record_workflow_created,
    record_workflow_email_sent,
    record_workflow_rejected,
    refresh_task_metrics,
    render_metrics,
)
from app.outbound_delivery import (
    build_mail_action_plan,
    build_review_content,
    build_outbound_resolution,
    looks_like_mail_action_request,
    looks_like_pending_draft_edit_request,
    looks_like_send_confirmation,
    patch_pending_mail_plan,
)
from app.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    ConversationCreateRequest,
    ConversationMergeRequest,
    ConversationMergeResponse,
    ConversationSummary,
    ConversationTurn,
    DlpScenarioDefinition,
    DlpScenarioReplayRequest,
    DisambiguationQueryRequest,
    DisambiguationQueryResponse,
    DlpTaskApprovalRequest,
    DlpTaskResponse,
    DlpTaskCreateRequest,
    DlpTaskSupplementRequest,
    EnterpriseRagBenchmarkResponse,
    EnterpriseRagIngestRequest,
    EnterpriseRagIngestResponse,
    EnterpriseRagQueryRequest,
    EnterpriseRagQueryResponse,
    ExecuteRequest,
    FrameworkCompareRequest,
    FrameworkCompareResponse,
    HealthResponse,
    IngestResponse,
    InboundDraftReplyResponse,
    InboundMailMessage,
    InboundMailSummaryResponse,
    InboundMailSyncRequest,
    InboundMailSyncResponse,
    NotificationOutboxItem,
    OutboundMailSummaryResponse,
    LongDocQueryRequest,
    LongDocQueryResponse,
    PlanRequest,
    PlanResponse,
    PrivacyScanRequest,
    PrivacyScanResponse,
    ProblemSummary,
    RetrievalPreview,
    SensitiveWorkflowApprovalRequest,
    SensitiveWorkflowCreateRequest,
    SensitiveWorkflowResponse,
    UnifiedAgentRequest,
    UnifiedAgentResponse,
)
from app.observability import configure_observability
from app.orchestration import orchestrate_agent_request
from app.orchestration.fast_router import route_agent_request
from app.orchestration.final_renderer import render_final_answer, render_mail_authoring
from app.orchestration.registry import build_tool_executor_map
from app.orchestration.types import OrchestrationContext
from app.privacy_lab import scan_sensitive_message
from app.raw_vs_langgraph import compare_raw_llm_and_langgraph
from app.session_store import append_turn, delete_plan, load_plan, save_plan
from app.sensitive_workflow import run_sensitive_outbound_workflow
from app.task_events import build_task_event, publish_task_event, task_event_iterator
from app.task_queue import EMAIL_QUEUE, MAIL_QUEUE, RISK_QUEUE, enqueue_daily_mail_digest, enqueue_dlp_risk_task, enqueue_email_send_task, enqueue_inbound_mail_sync
from app.task_store import (
    add_task_event,
    approve_task,
    create_dlp_task,
    get_dlp_task,
    get_latest_recoverable_task,
    get_sent_mail_stats,
    get_task_approvals,
    get_task_events,
    get_task_stats,
    init_task_store,
    list_dlp_tasks,
    reject_task,
    update_task,
)
from app.upload_analysis import build_upload_context, classify_upload_request, infer_upload_task_type, refers_to_recent_upload
from app.upload_blob_store import save_upload_blob
from app.vectorstore import count_collection, upsert_documents
from app.workflow_store import (
    add_audit_event,
    approve_sensitive_workflow,
    create_sensitive_workflow,
    get_audit_events,
    get_sensitive_workflow,
    init_workflow_store,
    list_sensitive_workflows,
    reject_sensitive_workflow,
    send_sensitive_workflow_email,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_observability()
    init_conversation_store()
    init_workflow_store()
    init_task_store()
    init_inbound_mail_store()
    init_workspace_memory_index()
    init_hermes_dynamic_memory_store()
    try:
        written = ingest_if_needed(force=False)
        if written:
            logger.info("Seeded Chroma with %s documents.", written)
    except Exception as exc:  # pragma: no cover - startup best effort
        logger.warning("Startup ingest skipped: %s", exc)
    yield


app = FastAPI(title="Secure Enterprise Mail Agent", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8511",
        "http://127.0.0.1:8511",
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_DLP_EMAIL = "17388861183@163.com"
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
OUTBOUND_REFERENCE_TOKENS = (
    "这个文档",
    "这个文件",
    "这个内容",
    "这个总结",
    "上面的总结",
    "刚才的总结",
    "这份总结",
    "this document",
    "this file",
    "this content",
    "this summary",
    "that summary",
)
OUTBOUND_SUMMARY_REFERENCE_TOKENS = ("总结", "摘要", "summary", "recap")
OUTBOUND_SEND_BOTH_TOKENS = ("都发", "一起发", "both", "send both")
OUTBOUND_NON_CONTENT_ANSWERS = {
    "当前没有生成可展示结果。",
    "当前没有可回顾历史",
    "当前没有可用历史",
}


def _workflow_response(workflow: dict) -> SensitiveWorkflowResponse:
    return SensitiveWorkflowResponse(**workflow, audit_events=get_audit_events(str(workflow["workflow_id"])))


def _task_response(task: dict) -> DlpTaskResponse:
    audit_events = get_task_events(str(task["task_id"]))
    status_path = build_status_path(audit_events)
    scenario_evaluation: dict = {}
    if task.get("lab_run"):
        scenario_evaluation = evaluate_scenario_task(
            task,
            expected_outcome=dict(task.get("expected_outcome") or {}),
            status_path=status_path,
        )
    return DlpTaskResponse(
        **task,
        audit_events=audit_events,
        approvals=get_task_approvals(str(task["task_id"])),
        status_path=status_path,
        scenario_evaluation=scenario_evaluation,
    )


def _source_preview(text: str, limit: int = 800) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def _looks_like_outbound_action(message: str) -> bool:
    lowered = message.lower()
    chinese_send_tokens = ("外发", "发送", "发给", "发到", "邮箱", "邮件", "寄给", "转发", "同步给")
    chinese_content_tokens = ("总结", "摘要", "整理", "如下", "文段", "内容", "文本", "这段", "这一段", "附件", "文件", "日志")
    has_chinese_send_intent = any(token in message for token in chinese_send_tokens)
    has_chinese_payload = any(token in message for token in chinese_content_tokens)
    if has_chinese_send_intent and (has_chinese_payload or bool(EMAIL_PATTERN.search(message or ""))):
        return True
    send_tokens = (
        "send",
        "email",
        "mail",
        "forward",
        "外发",
        "发送",
        "发给",
        "发到",
        "邮箱",
        "供应商",
        "外部",
        "伙伴",
    )
    content_tokens = (
        "summary",
        "summarize",
        "总结",
        "摘要",
        "整理",
        "转发",
        "发送",
        "如下",
        "文段",
        "内容",
        "文本",
        "这段",
        "这一段",
        "附件",
        "文件",
    )
    has_send_intent = any(token in lowered or token in message for token in send_tokens)
    has_content_payload = any(token in lowered or token in message for token in content_tokens)
    has_email_target = bool(EMAIL_PATTERN.search(message or ""))
    return has_send_intent and (has_content_payload or has_email_target)


def _extract_destination_email(message: str) -> str:
    match = EMAIL_PATTERN.search(message or "")
    return match.group(0) if match else DEFAULT_DLP_EMAIL


def _build_outbound_message(message: str, uploaded_text: str = "", uploaded_filename: str = "") -> str:
    text = message.strip()
    if uploaded_text.strip():
        header = f"\n\n[上传文件: {uploaded_filename or 'uploaded_text'}]\n"
        text = f"{text}{header}{uploaded_text.strip()}" if text else uploaded_text.strip()
    return text


def _task_message(message: str, uploaded_text: str = "", uploaded_filename: str = "") -> str:
    return _build_outbound_message(message, uploaded_text, uploaded_filename)


def _normalize_source_parse(uploaded_filename: str, uploaded_text: str, source_parse_status: str, source_parse_error: str) -> tuple[str, str]:
    if not (uploaded_filename or "").strip():
        return "not_provided", ""
    if (source_parse_error or "").strip():
        return source_parse_status or "parse_failed", source_parse_error
    if (uploaded_text or "").strip():
        return "parsed", ""
    return (source_parse_status or "empty"), source_parse_error


def _build_display_message(message: str, uploaded_filename: str = "", source_parse_status: str = "not_provided") -> str:
    text = (message or "").strip()
    filename = (uploaded_filename or "").strip()
    if not filename:
        return text
    status_suffix = " (解析失败)" if source_parse_status in {"parse_failed", "invalid"} else ""
    attachment_line = f"[附件: {filename}{status_suffix}]"
    return f"{text}\n\n{attachment_line}" if text else attachment_line


def _get_latest_upload_context(conversation_id: str) -> dict[str, Any]:
    for turn in reversed(get_turns(conversation_id)):
        if turn.get("role") != "assistant":
            continue
        debug_payload = dict(turn.get("debug_payload") or {})
        upload_context = debug_payload.get("upload_context")
        if not isinstance(upload_context, dict):
            continue
        if not upload_context.get("content_available"):
            continue
        uploaded_text = str(upload_context.get("uploaded_text") or "").strip()
        summary = str(upload_context.get("summary") or "").strip()
        snippets = [
            str(item).strip()
            for item in upload_context.get("key_snippets") or []
            if str(item).strip()
        ]
        if uploaded_text or summary or snippets or str(upload_context.get("upload_blob_id") or "").strip():
            return upload_context
    return {}


def _resolve_upload_context(payload: UnifiedAgentRequest, conversation_id: str) -> tuple[dict[str, Any], bool]:
    upload_blob_id = ""
    if (payload.uploaded_file_base64 or "").strip() and (payload.uploaded_filename or "").strip():
        try:
            blob_meta = save_upload_blob(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                filename=payload.uploaded_filename,
                content_type=payload.uploaded_content_type,
                data_base64=payload.uploaded_file_base64,
            )
            upload_blob_id = str(blob_meta.get("blob_id") or "")
        except Exception:
            upload_blob_id = ""
    current_upload_context = build_upload_context(
        uploaded_filename=payload.uploaded_filename,
        uploaded_content_type=payload.uploaded_content_type,
        uploaded_text=payload.uploaded_text,
        upload_blob_id=upload_blob_id,
        source_parse_status=payload.source_parse_status,
        source_parse_error=payload.source_parse_error,
    )
    if current_upload_context.get("content_available"):
        return current_upload_context, False
    if refers_to_recent_upload(payload.message):
        recalled = _get_latest_upload_context(conversation_id)
        if recalled:
            recalled_copy = dict(recalled)
            recalled_copy["recalled"] = True
            return recalled_copy, True
    return current_upload_context, False


def _looks_like_referential_outbound_request(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    if not _looks_like_outbound_action(text):
        return False
    if any(token in text or token in lowered for token in OUTBOUND_REFERENCE_TOKENS):
        return True
    return bool(re.search(r"\b(send|forward)\s+(this|it|that)\b", lowered))


def _looks_like_send_both_request(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    return any(token in text or token in lowered for token in OUTBOUND_SEND_BOTH_TOKENS)


def _looks_like_summary_reference(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    if any(token in text or token in lowered for token in ("上面的总结", "刚才的总结", "summary", "摘要", "总结")):
        return True
    return False


def _looks_like_contextual_outbound_request(message: str) -> bool:
    text = (message or "").strip()
    lowered = text.lower()
    has_email_target = bool(EMAIL_PATTERN.search(text))
    send_markers = ("发送", "发给", "发到", "外发", "转发", "send", "forward", "mail", "email")
    has_send_intent = any(marker in text or marker in lowered for marker in send_markers)
    has_context_reference = _looks_like_referential_outbound_request(text) or _looks_like_summary_reference(text) or _looks_like_send_both_request(text)
    return has_send_intent and (has_email_target or has_context_reference)


def _sanitize_recent_content(text: str) -> str:
    cleaned = compact_text(str(text or ""), 1200).strip()
    return "" if cleaned in OUTBOUND_NON_CONTENT_ANSWERS else cleaned


def _collect_outbound_candidates(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    upload_context: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    context_snapshot = dict(upload_context or {})

    def _append_candidate(
        *,
        kind: str,
        content: str,
        label: str,
        filename: str = "",
        content_type: str = "",
        supports_attachment: bool = False,
        upload_blob_id: str = "",
    ) -> None:
        normalized = compact_text(content, 1200).strip()
        if not normalized:
            if supports_attachment and filename:
                normalized = f"[binary attachment: {filename}]"
            else:
                return
        dedupe_key = (kind, normalized.casefold())
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        candidates.append(
            {
                "kind": kind,
                "label": label,
                "content": normalized,
                "filename": filename,
                "content_type": content_type,
                "supports_attachment": supports_attachment,
                "upload_blob_id": upload_blob_id,
            }
        )

    current_upload_text = _sanitize_recent_content(str(payload.uploaded_text or ""))
    if current_upload_text:
        _append_candidate(
            kind="uploaded_text",
            content=current_upload_text,
            label=f"当前上传内容（{payload.uploaded_filename or '文本'}）",
            filename=str(payload.uploaded_filename or ""),
            content_type=str(payload.uploaded_content_type or ""),
            supports_attachment=True,
            upload_blob_id=str(context_snapshot.get("upload_blob_id") or ""),
        )
    elif context_snapshot.get("content_available") and (
        str(context_snapshot.get("uploaded_text") or "").strip() or str(context_snapshot.get("upload_blob_id") or "").strip()
    ):
        _append_candidate(
            kind="uploaded_text",
            content=_sanitize_recent_content(str(context_snapshot.get("uploaded_text") or "")),
            label=f"最近上传内容（{context_snapshot.get('filename') or '文本'}）",
            filename=str(context_snapshot.get("filename") or ""),
            content_type=str(context_snapshot.get("content_type") or ""),
            supports_attachment=True,
            upload_blob_id=str(context_snapshot.get("upload_blob_id") or ""),
        )
    else:
        recalled_upload = _get_latest_upload_context(conversation_id)
        if recalled_upload.get("content_available") and (
            str(recalled_upload.get("uploaded_text") or "").strip() or str(recalled_upload.get("upload_blob_id") or "").strip()
        ):
            _append_candidate(
                kind="uploaded_text",
                content=_sanitize_recent_content(str(recalled_upload.get("uploaded_text") or "")),
                label=f"最近上传内容（{recalled_upload.get('filename') or '文本'}）",
                filename=str(recalled_upload.get("filename") or ""),
                content_type=str(recalled_upload.get("content_type") or ""),
                supports_attachment=True,
                upload_blob_id=str(recalled_upload.get("upload_blob_id") or ""),
            )

    turns = get_turns(conversation_id, limit=10)
    for turn in reversed(turns):
        role = str(turn.get("role") or "").lower()
        debug_payload = dict(turn.get("debug_payload") or {})
        if role == "assistant" and (
            bool(debug_payload.get("needs_clarification"))
            or str(debug_payload.get("mode_used") or "") in {"outbound_resolution", "task_queue"}
            or str(debug_payload.get("termination_reason") or "") == "needs_clarification"
        ):
            continue
        content = _sanitize_recent_content(str(turn.get("answer_summary") or turn.get("redacted_content") or turn.get("content") or ""))
        if role == "assistant" and content:
            _append_candidate(kind="assistant_last_answer", content=content, label="刚才生成的总结/回答")
            break
    for turn in reversed(turns):
        role = str(turn.get("role") or "").lower()
        content = _sanitize_recent_content(str(turn.get("redacted_content") or turn.get("content") or ""))
        if role == "user" and len(content) >= 120 and not _looks_like_outbound_action(content):
            _append_candidate(kind="user_recent_text", content=content, label="你最近粘贴/输入的长文本")
            break
    return candidates


def _build_outbound_clarification_question(message: str, candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "我已经识别到你想外发内容，但“这个文档/这个内容”目前没有明确指向。请直接回复要发送的是“刚才的总结”，或者重新上传/粘贴要发送的正文。"
    options = []
    for index, item in enumerate(candidates, start=1):
        preview = compact_text(str(item.get("content") or ""), 80)
        options.append(f"{index}. {item.get('label')}: {preview}")
    return (
        "我已经识别到这是指代型外发请求，但还不能安全判断你要发送哪一份内容。"
        "请直接回复要发送的对象，例如“发刚才的总结”或“发原始上传内容”。\n\n可选对象：\n"
        + "\n".join(options)
    )


def _build_outbound_resolution(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    upload_context: dict[str, Any],
) -> dict[str, Any]:
    candidates = _collect_outbound_candidates(payload, conversation_id, upload_context)
    return build_outbound_resolution(
        message=str(payload.message or ""),
        request_message=str(payload.message or ""),
        candidates=candidates,
        destination_email=_extract_destination_email(payload.message),
        referential_request=_looks_like_referential_outbound_request(payload.message),
        explicit_summary=_looks_like_summary_reference(payload.message),
        send_both=_looks_like_send_both_request(payload.message),
    )



def _task_answer_text(task: dict[str, Any]) -> str:
    status = str(task.get("status", ""))
    task_id = str(task.get("task_id", ""))
    if status in {"needs_clarification", "input_invalid"}:
        return str(task.get("clarification_question") or "我已保留你的外发请求，但还需要补充信息后才能继续处理。")
    if status == "queued":
        attachment_note = ""
        if str(task.get("attachment_strategy", "")) == "attach_original_upload":
            attachment_name = str(task.get("attachment_filename") or "原始内容")
            attachment_note = f" 邮件正文会按你的要求生成，原始上传内容会作为附件（{attachment_name}）一起发送。"
        return (
            f"已创建 DLP 外发任务 `{task_id}`，系统正在异步执行风险判断。"
            " 你可以在左侧任务台查看状态；如果命中敏感信息，任务会自动转入待审批。"
            f"{attachment_note}"
        )
    if status == "pending_approval":
        return f"检测到敏感外发风险，任务 `{task_id}` 已挂起，等待审批通过后再继续发送。"
    if status == "delivery_deferred":
        return f"任务 `{task_id}` 的邮件发送遇到临时问题，系统已延后重试。请在任务台查看错误与建议动作。"
    if status == "send_failed":
        return f"任务 `{task_id}` 的邮件发送失败。请在任务台查看错误详情与下一步建议。"
    if status == "sent":
        return str(task.get("final_result") or task.get("delivery_result") or "外发已完成。")
    return str(task.get("final_result") or f"任务 `{task_id}` 已创建。")

    return str(task.get("final_result") or f"任务 `{task_id}` 已创建。")


def _mail_provider_capabilities() -> dict[str, bool]:
    return {
        "can_send": True,
        "can_reply": False,
        "can_forward": False,
        "can_search_inbox": True,
        "can_read_full_message": True,
        "can_recall": False,
        "can_track_delivery": True,
        "can_manage_drafts": True,
    }


def _build_task_status_observation(task: dict[str, Any]) -> dict[str, Any]:
    answer_hint = _task_answer_text(task)
    payload = {
        "task_id": str(task.get("task_id") or ""),
        "status": str(task.get("status") or ""),
        "risk_level": str(task.get("risk_level") or ""),
        "delivery_status": str(task.get("delivery_status") or ""),
        "delivery_result": str(task.get("delivery_result") or ""),
        "delivery_error": str(task.get("delivery_error") or ""),
        "clarification_question": str(task.get("clarification_question") or ""),
        "next_step": compact_text(answer_hint, 240),
        "delivery_preview": {
            "subject": str(task.get("delivery_subject") or ""),
            "body": str(task.get("delivery_body") or ""),
            "attachment_filename": str(task.get("attachment_filename") or ""),
        },
        "task": task,
    }
    return {
        "observation_type": "task_status_result",
        "source": "task_queue",
        "grounding_kind": "tool",
        "summary": compact_text(answer_hint, 220),
        "payload": payload,
        "citations": [],
        "confidence": 0.98,
    }


def _build_mail_plan_observation(
    mail_plan: dict[str, Any],
    *,
    source: str = "mail_action",
    summary: str = "",
    draft_mode: str = "",
    observation_type: str = "mail_plan_result",
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_body = str(mail_plan.get("resolved_body") or "")
    resolved_subject = str(mail_plan.get("resolved_subject") or "")
    patch_kind_value = str((extra_payload or {}).get("patch_kind") or mail_plan.get("patch_kind") or "")
    confirmation_required = bool(
        mail_plan.get("requires_confirmation")
        or str(mail_plan.get("status") or "") == "pending_confirmation"
        or draft_mode == "confirmation_required"
    )
    draft_state = "patch" if patch_kind_value else "confirm" if confirmation_required else "pending_draft"
    payload = {
        "draft_id": str(mail_plan.get("draft_id") or ""),
        "draft_status": str(mail_plan.get("status") or ""),
        "draft_state": draft_state,
        "patch_kind": patch_kind_value,
        "confirmation_required": confirmation_required,
        "mail_action_type": str(mail_plan.get("mail_action_type") or ""),
        "recipients": list(mail_plan.get("resolved_recipients") or []),
        "subject": resolved_subject,
        "body": resolved_body,
        "attachments": list(mail_plan.get("resolved_attachments") or []),
        "source_refs": list(mail_plan.get("source_refs") or []),
        "body_constraints": dict(mail_plan.get("body_constraints") or {}),
        "body_sources": list(mail_plan.get("body_sources") or []),
        "source_policy": dict(mail_plan.get("source_policy") or {}),
        "draft_mode": draft_mode or str(mail_plan.get("mail_action_type") or ""),
        "provider_capabilities": _mail_provider_capabilities(),
        "mail_plan": mail_plan,
    }
    if extra_payload:
        payload.update(extra_payload)
    return {
        "observation_type": observation_type,
        "source": source,
        "grounding_kind": "tool",
        "summary": compact_text(summary or resolved_body or resolved_subject or "已生成邮件计划。", 220),
        "payload": payload,
        "citations": [],
        "confidence": 0.95,
    }


def _build_mailbox_summary_observation(
    *,
    source: str,
    summary_text: str,
    payload: dict[str, Any],
    confidence: float = 0.92,
) -> dict[str, Any]:
    return {
        "observation_type": "mailbox_summary_result",
        "source": source,
        "grounding_kind": "tool",
        "summary": compact_text(summary_text, 220),
        "payload": payload,
        "citations": [],
        "confidence": confidence,
    }


def _build_compound_observation(
    *,
    question: str,
    observations: list[dict[str, Any]],
    subtasks: list[dict[str, Any]],
) -> dict[str, Any]:
    summaries = [compact_text(str(item.get("summary") or ""), 120) for item in observations if str(item.get("summary") or "").strip()]
    return {
        "observation_type": "compound_result",
        "source": "compound_plan_execute",
        "grounding_kind": "mixed",
        "summary": compact_text("；".join(summaries[:4]) or question, 240),
        "payload": {
            "question": question,
            "subtasks": subtasks,
            "observation_summaries": [
                {
                    "observation_type": str(item.get("observation_type") or ""),
                    "source": str(item.get("source") or ""),
                    "summary": compact_text(str(item.get("summary") or ""), 160),
                }
                for item in observations
            ],
            "grounding_sources": [str(item.get("source") or "") for item in observations if str(item.get("source") or "").strip()],
        },
        "citations": [citation for item in observations for citation in list(item.get("citations") or [])][:8],
        "confidence": 0.9,
    }


def _build_task_agent_response(
    *,
    session_id: str,
    conversation_id: str,
    outbound_message: str,
    display_message: str,
    task: dict[str, Any],
    routing_reason: str,
) -> UnifiedAgentResponse:
    task_id = str(task["task_id"])
    status = str(task["status"])
    answer_text = _task_answer_text(task)
    needs_clarification = status in {"needs_clarification", "input_invalid"}
    task_observation = _build_task_status_observation(task)
    renderer = {"answer": answer_text, "token_in": 0, "token_out": 0, "estimated_cost": 0.0, "used_fallback": False}
    if not needs_clarification:
        renderer = render_final_answer(
            question=outbound_message,
            current_goal="privacy_alert",
            observations=[task_observation],
            working_memory=[str(task_observation["summary"])],
            conservative=status not in {"sent", "delivery_deferred"},
        )
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": task_id,
        "message": outbound_message,
        "safe_message": str(task.get("message_redacted") or outbound_message),
        "display_message": display_message,
        "answer": str(renderer.get("answer") or answer_text),
        "intent": "privacy_alert",
        "routing_source": "rule",
        "routing_confidence": 0.99,
        "routing_reason": routing_reason,
        "candidate_intents": ["privacy_alert"],
        "mode_used": "task_queue",
        "tool_calls": [
            {
                "tool_name": "govern_dlp_task" if needs_clarification else "enqueue_dlp_risk_task",
                "success": True,
                "status": status,
                "error": "",
            }
        ],
        "retrieved_evidence": list(task.get("retrieved_evidence", [])),
        "needs_clarification": needs_clarification,
        "clarification_question": task.get("clarification_question") or None,
        "privacy": {
            "redacted": bool(task.get("redactions")),
            "risk_level": str(task.get("risk_level", "")),
            "redactions": task.get("redactions", []),
        },
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "node_latencies_ms": {"total": 0.0},
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    record_request(
        mode="task_queue",
        latency_ms=0.0,
        token_in=int(renderer.get("token_in", 0)),
        token_out=int(renderer.get("token_out", 0)),
        estimated_cost=float(renderer.get("estimated_cost", 0.0)),
        retrieval_hits=0,
    )
    record_unified_agent(
        intent="privacy_alert",
        tool_calls=synthetic_result["tool_calls"],
        needs_clarification=needs_clarification,
        privacy_guardrail=bool(synthetic_result["privacy"].get("redacted")),
        context_budget_used=None,
    )
    record_router("rule", 0.99)
    record_unified_evidence_hits(synthetic_result["retrieved_evidence"])
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=str(synthetic_result["answer"]),
        intent="privacy_alert",
        routing_source="rule",
        routing_confidence=0.99,
        routing_reason=routing_reason,
        candidate_intents=["privacy_alert"],
        mode_used="task_queue",
        tool_calls=synthetic_result["tool_calls"],
        retrieved_evidence=synthetic_result["retrieved_evidence"],
        needs_clarification=needs_clarification,
        clarification_question=synthetic_result["clarification_question"],
        privacy=synthetic_result["privacy"],
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        upload_context={},
        final_answer_source="task_queue_clarification" if needs_clarification else ("task_queue_renderer_fallback" if renderer.get("used_fallback") else "task_queue_renderer"),
        tool_observations=[task_observation],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=task_id,
        task_status=status,
        task_risk_level=str(task.get("risk_level", "")),
        delivery_status=str(task.get("delivery_status") or "not_sent"),
        delivery_result=str(task.get("delivery_result") or ""),
        delivery_error=str(task.get("delivery_error") or ""),
        trace_id=task_id,
        latency_ms=0.0,
        token_in=0,
        token_out=0,
        estimated_cost=0.0,
    )


def _should_apply_recoverable_supplement(task: dict[str, Any] | None, payload: UnifiedAgentRequest, conversation_id: str) -> bool:
    if not task:
        return False
    if str(task.get("conversation_id", "")) != conversation_id:
        return False
    if str(task.get("status", "")) not in {"needs_clarification", "input_invalid"}:
        return False
    if (payload.uploaded_filename or "").strip():
        return True
    if (payload.uploaded_text or "").strip():
        return True
    message = (payload.message or "").strip()
    if not message:
        return False
    missing_fields = {str(item) for item in task.get("missing_fields", [])}
    if "destination_email" in missing_fields and dlp_extract_destination_email(message):
        return True
    if "content" in missing_fields and message:
        return True
    return str(task.get("entry_issue_type", "")) == "file_parse_failed"


def _create_async_dlp_task(payload: DlpTaskCreateRequest) -> dict:
    combined_message = (payload.review_content or "").strip() or (payload.resolved_outbound_content or "").strip() or _task_message(
        payload.message,
        payload.uploaded_text,
        payload.uploaded_filename,
    )
    request_message = (payload.request_message or payload.message or "").strip()
    delivery_subject = (payload.delivery_subject or "").strip()
    delivery_body = (payload.delivery_body or "").strip() or (payload.resolved_outbound_content or "").strip()
    delivery_plan_kind = (payload.delivery_plan_kind or "").strip()
    attachment_blob_id = str(payload.attachment_blob_id or "").strip()
    if not attachment_blob_id and (payload.uploaded_file_base64 or "").strip() and (payload.uploaded_filename or "").strip():
        try:
            blob_meta = save_upload_blob(
                session_id=payload.session_id,
                conversation_id=payload.conversation_id,
                filename=payload.uploaded_filename,
                content_type=payload.uploaded_content_type,
                data_base64=payload.uploaded_file_base64,
            )
            if str(payload.attachment_strategy or "") == "attach_original_upload":
                attachment_blob_id = str(blob_meta.get("blob_id") or "")
        except Exception:
            attachment_blob_id = attachment_blob_id or ""
    fault_injection = normalize_fault_injection(payload.fault_injection)
    source_parse_status, source_parse_error = _normalize_source_parse(
        payload.uploaded_filename,
        payload.uploaded_text,
        payload.source_parse_status,
        payload.source_parse_error,
    )
    resolved_target_ready = bool(combined_message.strip()) and bool((payload.destination_email or "").strip())
    decision = None if (payload.lab_run or resolved_target_ready) else classify_dlp_entry(
        message=combined_message,
        destination_email=payload.destination_email,
        uploaded_text=payload.uploaded_text,
        uploaded_filename=payload.uploaded_filename,
        source_parse_status=source_parse_status,
        source_parse_error=source_parse_error,
    )
    destination_email = payload.destination_email.strip() or (decision.destination_email if decision else "")
    if payload.lab_run or resolved_target_ready:
        status = "queued"
        entry_issue_type = ""
        missing_fields: list[str] = []
        clarification_question = ""
    elif decision.route == "non_dlp":
        if source_parse_status in {"parse_failed", "empty", "invalid"} or source_parse_error:
            status = "input_invalid"
            entry_issue_type = "file_parse_failed"
            missing_fields = ["content"] if not destination_email else []
            clarification_question = "我已保留你的外发请求，但上传文件暂时无法解析。请重新上传文件或直接粘贴正文。"
        else:
            status = "needs_clarification"
            entry_issue_type = "missing_content"
            missing_fields = ["content"]
            clarification_question = "我已保留你的外发请求，但还需要更明确的外发内容或说明后才能继续处理。"
    else:
        status = "queued" if decision.route == "normal_outbound" else decision.route
        entry_issue_type = decision.entry_issue_type
        missing_fields = decision.missing_fields
        clarification_question = decision.clarification_question
    task = create_dlp_task(
        session_id=payload.session_id,
        conversation_id=payload.conversation_id,
        message_raw=combined_message,
        request_message=request_message,
        delivery_subject=delivery_subject,
        delivery_body=delivery_body,
        delivery_plan_kind=delivery_plan_kind,
        resolved_source_kind=str(payload.resolved_source_kind or ""),
        attachment_strategy=str(payload.attachment_strategy or "none"),
        attachment_content=str(payload.attachment_content or ""),
        attachment_filename=str(payload.attachment_filename or ""),
        attachment_content_type=str(payload.attachment_content_type or ""),
        attachment_blob_id=attachment_blob_id,
        destination_email=destination_email,
        source_filename=payload.uploaded_filename,
        source_content_type=payload.uploaded_content_type,
        source_parse_status=source_parse_status,
        source_parse_error=source_parse_error,
        requested_action=payload.requested_action,
        status=status,
        entry_issue_type=entry_issue_type,
        missing_fields=missing_fields,
        clarification_question=clarification_question,
        lab_run=payload.lab_run,
        scenario_id=payload.scenario_id,
        scenario_name=payload.scenario_name,
        fault_injection=fault_injection,
        expected_outcome=dict(payload.expected_outcome or {}),
    )
    record_task_created()
    if payload.lab_run and payload.scenario_id:
        record_dlp_scenario_replay(payload.scenario_id)
    if status == "queued":
        enqueue_dlp_risk_task(str(task["task_id"]))
        publish_task_event(
            build_task_event(
                task_id=str(task["task_id"]),
                status=str(task["status"]),
                event_type="queued",
                message="Task queued for asynchronous DLP processing.",
                delivery_status=str(task.get("delivery_status", "not_sent")),
            )
        )
    else:
        publish_task_event(
            build_task_event(
                task_id=str(task["task_id"]),
                status=str(task["status"]),
                event_type=status,
                message=str(task.get("clarification_question") or "Task is waiting for additional input."),
                delivery_status=str(task.get("delivery_status", "not_sent")),
            )
        )
    return task


def _merge_supplement_message(existing_message: str, message: str, uploaded_text: str = "", uploaded_filename: str = "") -> str:
    supplement = _task_message(message, uploaded_text, uploaded_filename).strip()
    if not supplement:
        return existing_message
    if not existing_message.strip():
        return supplement
    return f"{existing_message.rstrip()}\n\n[Supplement]\n{supplement}"


def _build_supplement_outbound_resolution(task: dict[str, Any], payload: DlpTaskSupplementRequest) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    uploaded_text = str(payload.uploaded_text or "").strip()
    if uploaded_text:
        candidates.append(
            {
                "kind": "uploaded_text",
                "label": f"当前补充上传内容（{payload.uploaded_filename or '文本'}）",
                "content": uploaded_text,
                "filename": str(payload.uploaded_filename or task.get("source_filename") or ""),
                "content_type": str(payload.uploaded_content_type or task.get("source_content_type") or "text/plain"),
                "upload_blob_id": "",
            }
        )
    typed_message = str(payload.message or "").strip()
    if typed_message and not _looks_like_outbound_action(typed_message):
        candidates.append(
            {
                "kind": "user_recent_text",
                "label": "用户补充正文",
                "content": typed_message,
                "filename": "",
                "content_type": "text/plain",
                "upload_blob_id": "",
            }
        )
    if not candidates:
        existing_body = str(task.get("delivery_body") or "").strip()
        if existing_body:
            candidates.append(
                {
                    "kind": "assistant_last_answer",
                    "label": "已有外发正文",
                    "content": existing_body,
                    "filename": "",
                    "content_type": "text/plain",
                    "upload_blob_id": "",
                }
            )
    return build_outbound_resolution(
        message=str(payload.message or task.get("request_message") or task.get("message_raw") or ""),
        request_message=str(task.get("request_message") or task.get("message_raw") or ""),
        candidates=candidates,
        destination_email=str(payload.destination_email or task.get("destination_email") or ""),
        referential_request=_looks_like_referential_outbound_request(str(payload.message or "")),
        explicit_summary=_looks_like_summary_reference(str(payload.message or "")),
        send_both=_looks_like_send_both_request(str(payload.message or "")),
    )


def _build_mail_action_plan(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    upload_context: dict[str, Any],
) -> dict[str, Any]:
    candidates = _collect_outbound_candidates(payload, conversation_id, upload_context)
    return build_mail_action_plan(
        message=str(payload.message or ""),
        request_message=str(payload.message or ""),
        candidates=candidates,
        destination_email=_extract_destination_email(payload.message),
        referential_request=_looks_like_referential_outbound_request(payload.message),
        explicit_summary=_looks_like_summary_reference(payload.message),
        send_both=_looks_like_send_both_request(payload.message),
        conversation_id=conversation_id,
    )


def _get_latest_pending_mail_confirmation(conversation_id: str) -> dict[str, Any]:
    for turn in reversed(get_turns(conversation_id, limit=12)):
        if str(turn.get("role") or "").lower() != "assistant":
            continue
        debug_payload = dict(turn.get("debug_payload") or {})
        confirmation_payload = dict(debug_payload.get("confirmation_payload") or {})
        mail_plan = dict(confirmation_payload.get("mail_plan") or {})
        if confirmation_payload and mail_plan and str(mail_plan.get("mail_action_type") or "").startswith(("send_", "compose_")):
            return confirmation_payload
    return {}


def _get_latest_pending_mail_draft(conversation_id: str) -> dict[str, Any]:
    confirmation_payload = _get_latest_pending_mail_confirmation(conversation_id)
    return dict(confirmation_payload.get("mail_plan") or {})


def _render_mail_plan_with_llm(
    *,
    message: str,
    mail_plan: dict[str, Any],
    render_mode: str,
    observations: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rendered_plan = dict(mail_plan or {})
    render_result = render_mail_authoring(
        question=message,
        render_mode=render_mode,
        mail_plan=rendered_plan,
        observations=observations,
        candidates=candidates,
    )
    body_for_sending = str(render_result.get("body_for_sending") or "").strip()
    if body_for_sending:
        rendered_plan["resolved_body"] = body_for_sending
        rendered_plan["review_content"] = build_review_content(
            body_for_sending,
            dict(rendered_plan.get("selected_candidate") or {}) or None,
            dict(rendered_plan.get("attachment_candidate") or {}) or None,
        )
    return rendered_plan, render_result


def _build_mail_clarification_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    mail_plan: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=dict(mail_plan or {}),
        render_mode="clarification",
        candidates=list(candidates or []),
    )
    clarification_question = str(render_result.get("clarification_question") or render_result.get("user_message") or "").strip()
    mail_observation = _build_mail_plan_observation(
        rendered_plan,
        source="mail_action_resolve",
        summary=clarification_question,
        draft_mode="clarification",
        extra_payload={"candidates": list(candidates or [])},
    )
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "display_message": display_message or message,
        "answer": clarification_question,
        "intent": "action_or_draft",
        "routing_source": "mail_action",
        "routing_confidence": 0.98,
        "routing_reason": "Mail action resolution requires additional information before continuing.",
        "candidate_intents": ["action_or_draft"],
        "mode_used": "mail_action",
        "tool_calls": [
            {
                "tool_name": "mail_action_resolve",
                "success": True,
                "status": "clarification_required",
                "error": "",
                "result": {"mail_plan": rendered_plan, "candidates": list(candidates or [])},
            }
        ],
        "retrieved_evidence": [],
        "needs_clarification": True,
        "clarification_question": clarification_question,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "task_plan": {"mail_plan": rendered_plan, "candidates": list(candidates or [])},
        "node_latencies_ms": {"total": 0.0},
        "token_in": int(render_result.get("token_in", 0)),
        "token_out": int(render_result.get("token_out", 0)),
        "estimated_cost": float(render_result.get("estimated_cost", 0.0)),
        "tool_observations": [mail_observation],
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=clarification_question,
        intent="action_or_draft",
        routing_source="mail_action",
        routing_confidence=0.98,
        routing_reason=str(synthetic_result["routing_reason"]),
        candidate_intents=["action_or_draft"],
        mode_used="mail_action",
        tool_calls=synthetic_result["tool_calls"],
        retrieved_evidence=[],
        needs_clarification=True,
        clarification_question=clarification_question,
        privacy={},
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        task_plan=synthetic_result["task_plan"],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=0.0,
        tool_observations=[mail_observation],
        token_in=int(render_result.get("token_in", 0)),
        token_out=int(render_result.get("token_out", 0)),
        estimated_cost=float(render_result.get("estimated_cost", 0.0)),
    )


def _build_mail_confirmation_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    mail_plan: dict[str, Any],
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=mail_plan,
        render_mode="confirmation",
    )
    confirmation_payload = {
        "action_name": "send_mail_plan",
        "title": "请确认邮件发送计划",
        "message": str(render_result.get("user_message") or ""),
        "mail_plan": rendered_plan,
    }
    mail_observation = _build_mail_plan_observation(
        rendered_plan,
        source="mail_action_resolve",
        summary=str(confirmation_payload["message"]),
        draft_mode="confirmation_required",
        observation_type="mail_confirmation_result",
    )
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "display_message": display_message or message,
        "answer": confirmation_payload["message"],
        "intent": "action_or_draft",
        "routing_source": "mail_action",
        "routing_confidence": 0.99,
        "routing_reason": "Mail send request was resolved into a confirmation-ready mail plan.",
        "candidate_intents": ["action_or_draft"],
        "mode_used": "mail_action",
        "tool_calls": [
            {
                "tool_name": "mail_action_resolve",
                "success": True,
                "status": "confirmation_required",
                "error": "",
                "result": {"mail_plan": rendered_plan},
            }
        ],
        "retrieved_evidence": [],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "task_plan": {"mail_plan": rendered_plan},
        "pending_confirmation": confirmation_payload,
        "confirmation_payload": confirmation_payload,
        "final_answer_source": "mail_confirmation",
        "termination_reason": "needs_confirmation",
        "node_latencies_ms": {"total": 0.0},
        "token_in": int(render_result.get("token_in", 0)),
        "token_out": int(render_result.get("token_out", 0)),
        "estimated_cost": float(render_result.get("estimated_cost", 0.0)),
        "tool_observations": [mail_observation],
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=str(confirmation_payload["message"]),
        intent="action_or_draft",
        routing_source="mail_action",
        routing_confidence=0.99,
        routing_reason=str(synthetic_result["routing_reason"]),
        candidate_intents=["action_or_draft"],
        mode_used="mail_action",
        tool_calls=synthetic_result["tool_calls"],
        retrieved_evidence=[],
        needs_clarification=False,
        clarification_question=None,
        privacy={},
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        task_plan=synthetic_result["task_plan"],
        pending_confirmation=confirmation_payload,
        confirmation_payload=confirmation_payload,
        final_answer_source="mail_confirmation",
        termination_reason="needs_confirmation",
        tool_observations=[mail_observation],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=0.0,
        token_in=int(render_result.get("token_in", 0)),
        token_out=int(render_result.get("token_out", 0)),
        estimated_cost=float(render_result.get("estimated_cost", 0.0)),
    )


def _build_mail_patch_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    mail_plan: dict[str, Any],
    patch_kind: str,
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=mail_plan,
        render_mode="patch",
    )
    confirmation_payload = {
        "action_name": "send_mail_plan",
        "title": "请确认更新后的邮件发送计划",
        "message": str(render_result.get("user_message") or ""),
        "mail_plan": rendered_plan,
    }
    patch_observation = _build_mail_plan_observation(
        rendered_plan,
        source="mail_patch_plan",
        summary=str(render_result.get("user_message") or ""),
        draft_mode=patch_kind,
        observation_type="mail_patch_result",
        extra_payload={"patch_kind": patch_kind},
    )
    answer = str(render_result.get("user_message") or "")
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "display_message": display_message or message,
        "answer": answer,
        "intent": "action_or_draft",
        "routing_source": "mail_action",
        "routing_confidence": 0.99,
        "routing_reason": "Pending mail draft was patched in place and remains confirmation-gated.",
        "candidate_intents": ["action_or_draft"],
        "mode_used": "mail_action",
        "tool_calls": [
            {
                "tool_name": "mail_patch_plan",
                "success": True,
                "status": "confirmation_required",
                "error": "",
                "result": {"mail_plan": rendered_plan, "patch_kind": patch_kind},
            }
        ],
        "retrieved_evidence": [],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "task_plan": {"mail_plan": rendered_plan, "patch_kind": patch_kind},
        "pending_confirmation": confirmation_payload,
        "confirmation_payload": confirmation_payload,
        "final_answer_source": "mail_patch_confirmation",
        "termination_reason": "needs_confirmation",
        "node_latencies_ms": {"total": 0.0},
        "token_in": int(render_result.get("token_in", 0)),
        "token_out": int(render_result.get("token_out", 0)),
        "estimated_cost": float(render_result.get("estimated_cost", 0.0)),
        "tool_observations": [patch_observation],
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=answer,
        intent="action_or_draft",
        routing_source="mail_action",
        routing_confidence=0.99,
        routing_reason=str(synthetic_result["routing_reason"]),
        candidate_intents=["action_or_draft"],
        mode_used="mail_action",
        tool_calls=synthetic_result["tool_calls"],
        retrieved_evidence=[],
        needs_clarification=False,
        clarification_question=None,
        privacy={},
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        task_plan=synthetic_result["task_plan"],
        pending_confirmation=confirmation_payload,
        confirmation_payload=confirmation_payload,
        final_answer_source="mail_patch_confirmation",
        termination_reason="needs_confirmation",
        tool_observations=[patch_observation],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=0.0,
        token_in=int(render_result.get("token_in", 0)),
        token_out=int(render_result.get("token_out", 0)),
        estimated_cost=float(render_result.get("estimated_cost", 0.0)),
    )


def _build_mail_draft_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    mail_plan: dict[str, Any],
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=mail_plan,
        render_mode="draft",
    )
    draft_observation = _build_mail_plan_observation(
        rendered_plan,
        source="mail_authoring_plan",
        summary=str(render_result.get("user_message") or rendered_plan.get("resolved_body") or ""),
        draft_mode=str(mail_plan.get("mail_action_type") or "draft_only"),
        observation_type="mail_draft_result",
    )
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "display_message": display_message or message,
        "answer": str(render_result.get("user_message") or ""),
        "intent": "action_or_draft",
        "routing_source": "mail_action",
        "routing_confidence": 0.98,
        "routing_reason": "Mail authoring request was resolved into a draft-only mail plan.",
        "candidate_intents": ["action_or_draft"],
        "mode_used": "mail_action",
        "tool_calls": [
            {
                "tool_name": "mail_authoring_plan",
                "success": True,
                "status": "draft_ready",
                "error": "",
                "result": {"mail_plan": rendered_plan},
            }
        ],
        "retrieved_evidence": [],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "task_plan": {"mail_plan": rendered_plan},
        "final_answer_source": "mail_authoring",
        "node_latencies_ms": {"total": 0.0},
        "token_in": int(render_result.get("token_in", 0)),
        "token_out": int(render_result.get("token_out", 0)),
        "estimated_cost": float(render_result.get("estimated_cost", 0.0)),
        "tool_observations": [draft_observation],
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=str(synthetic_result["answer"]),
        intent="action_or_draft",
        routing_source="mail_action",
        routing_confidence=0.98,
        routing_reason=str(synthetic_result["routing_reason"]),
        candidate_intents=["action_or_draft"],
        mode_used="mail_action",
        tool_calls=synthetic_result["tool_calls"],
        retrieved_evidence=[],
        needs_clarification=False,
        clarification_question=None,
        privacy={},
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        task_plan={"mail_plan": rendered_plan},
        final_answer_source="mail_authoring_renderer_fallback" if render_result.get("used_fallback") else "mail_authoring_renderer",
        tool_observations=[draft_observation],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=0.0,
        token_in=int(render_result.get("token_in", 0)),
        token_out=int(render_result.get("token_out", 0)),
        estimated_cost=float(render_result.get("estimated_cost", 0.0)),
    )


def _build_mail_unsupported_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    mail_plan: dict[str, Any],
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=mail_plan,
        render_mode="unsupported",
    )
    answer = str(render_result.get("user_message") or "")
    mail_observation = _build_mail_plan_observation(
        rendered_plan,
        source="mail_provider_capabilities",
        summary=answer,
        draft_mode="unsupported",
        extra_payload={
            "unsupported_reason": str(rendered_plan.get("unsupported_reason") or ""),
            "unsupported_code": str(rendered_plan.get("unsupported_code") or ""),
        },
    )
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "display_message": display_message or message,
        "answer": answer,
        "intent": "unsupported_capability",
        "routing_source": "mail_action",
        "routing_confidence": 0.99,
        "routing_reason": "Mail action was recognized but is unsupported by the current provider capabilities.",
        "candidate_intents": ["unsupported_capability"],
        "mode_used": "mail_action",
        "tool_calls": [
            {
                "tool_name": "mail_provider_capabilities",
                "success": True,
                "status": "unsupported",
                "error": "",
                "result": {"mail_plan": rendered_plan},
            }
        ],
        "retrieved_evidence": [],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "task_plan": {"mail_plan": rendered_plan},
        "final_answer_source": "mail_capability_guardrail_fallback" if render_result.get("used_fallback") else "mail_capability_guardrail_renderer",
        "node_latencies_ms": {"total": 0.0},
        "token_in": int(render_result.get("token_in", 0)),
        "token_out": int(render_result.get("token_out", 0)),
        "estimated_cost": float(render_result.get("estimated_cost", 0.0)),
        "tool_observations": [mail_observation],
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=answer,
        intent="unsupported_capability",
        routing_source="mail_action",
        routing_confidence=0.99,
        routing_reason=str(synthetic_result["routing_reason"]),
        candidate_intents=["unsupported_capability"],
        mode_used="mail_action",
        tool_calls=synthetic_result["tool_calls"],
        retrieved_evidence=[],
        needs_clarification=False,
        clarification_question=None,
        privacy={},
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        task_plan=synthetic_result["task_plan"],
        final_answer_source=str(synthetic_result["final_answer_source"]),
        tool_observations=[mail_observation],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=0.0,
        token_in=int(render_result.get("token_in", 0)),
        token_out=int(render_result.get("token_out", 0)),
        estimated_cost=float(render_result.get("estimated_cost", 0.0)),
    )


def _build_fast_path_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    route_decision: dict[str, Any],
    result: dict[str, Any],
    latency_ms: float,
) -> UnifiedAgentResponse:
    observations = list(result.get("observations") or result.get("tool_observations") or [])
    renderer = {"answer": str(result.get("answer") or ""), "token_in": 0, "token_out": 0, "estimated_cost": 0.0, "used_fallback": False}
    if not bool(result.get("needs_clarification", False)) and not bool(result.get("skip_renderer", False)):
        renderer = render_final_answer(
            question=message,
            current_goal=str(result.get("intent") or route_decision.get("intent") or "fast_path"),
            observations=observations,
            working_memory=list(result.get("working_memory") or []),
            conservative=bool(result.get("conservative", False)),
        )
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "display_message": display_message or message,
        "answer": str(renderer.get("answer") or result.get("answer") or ""),
        "intent": str(result.get("intent") or route_decision.get("intent") or "fast_path"),
        "routing_source": str(route_decision.get("routing_source") or "fast_router"),
        "routing_confidence": float(route_decision.get("confidence") or 0.0),
        "routing_reason": str(route_decision.get("router_reason") or ""),
        "candidate_intents": [str(route_decision.get("intent") or "fast_path")],
        "mode_used": "fast_path",
        "tool_calls": list(result.get("tool_calls") or []),
        "retrieved_evidence": list(result.get("retrieved_evidence") or []),
        "needs_clarification": bool(result.get("needs_clarification", False)),
        "clarification_question": result.get("clarification_question"),
        "privacy": dict(result.get("privacy") or {}),
        "context_budget": {},
        "citations": list(result.get("citations") or []),
        "memory_context": dict(result.get("memory_context") or {}),
        "memory_hits": int(result.get("memory_hits", 0)),
        "merged_memory_hits": int(result.get("merged_memory_hits", 0)),
        "context_sources": list(result.get("context_sources") or []),
        "workspace_memory_hits": int(result.get("workspace_memory_hits", 0)),
        "transcript_hits": int(result.get("transcript_hits", 0)),
        "user_model_used": bool(result.get("user_model_used", False)),
        "reflection_notes": None,
        "upload_context": dict(result.get("upload_context") or {}),
        "route_mode": "fast",
        "router_intent": str(route_decision.get("intent") or ""),
        "router_reason": str(route_decision.get("router_reason") or ""),
        "required_grounding": str(route_decision.get("required_grounding") or "none"),
        "fast_path_used": True,
        "degraded_from": str(route_decision.get("degraded_from") or "none"),
        "planner_type": "fast_router",
        "task_plan": {
            "route_mode": "fast",
            "router_intent": str(route_decision.get("intent") or ""),
            "required_grounding": str(route_decision.get("required_grounding") or "none"),
            "recommended_tool": str(route_decision.get("recommended_tool") or ""),
        },
        "subtask_results": list(result.get("tool_calls") or []),
        "aggregation_strategy": "fast_path",
        "partial_failures": list(result.get("partial_failures") or []),
        "react_trace": [],
        "loop_step_count": 0,
        "termination_reason": str(result.get("termination_reason") or "direct_answer"),
        "pending_confirmation": {},
        "confirmation_payload": {},
        "final_answer_source": str(
            result.get("final_answer_source")
            or ("fast_path_renderer_fallback" if renderer.get("used_fallback") else "fast_path_renderer")
        ),
        "memory_reads": list(result.get("memory_reads") or []),
        "tool_observations": list(result.get("tool_observations") or []),
        "node_latencies_ms": {"total": round(latency_ms, 2)},
        "token_in": int(result.get("token_in", 0)) + int(renderer.get("token_in", 0)),
        "token_out": int(result.get("token_out", 0)) + int(renderer.get("token_out", 0)),
        "estimated_cost": float(result.get("estimated_cost", 0.0)) + float(renderer.get("estimated_cost", 0.0)),
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    record_request(
        mode="fast_path",
        latency_ms=latency_ms,
        token_in=int(result.get("token_in", 0)),
        token_out=int(result.get("token_out", 0)),
        estimated_cost=float(result.get("estimated_cost", 0.0)),
        retrieval_hits=len(list(synthetic_result.get("citations", []))),
        memory_hits=int(synthetic_result.get("memory_hits", 0)),
        used_conversation_memory=bool(synthetic_result.get("memory_hits", 0) or synthetic_result.get("merged_memory_hits", 0)),
    )
    record_router(str(route_decision.get("routing_source") or "fast_router"), float(route_decision.get("confidence") or 0.0))
    record_unified_evidence_hits(synthetic_result["retrieved_evidence"])
    record_memory_retrieval_hits(int(synthetic_result["memory_hits"]), int(synthetic_result["merged_memory_hits"]))
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=str(synthetic_result["answer"]),
        intent=str(synthetic_result["intent"]),
        routing_source=str(synthetic_result["routing_source"]),
        routing_confidence=float(synthetic_result["routing_confidence"]),
        routing_reason=str(synthetic_result["routing_reason"]),
        candidate_intents=list(synthetic_result["candidate_intents"]),
        mode_used="fast_path",
        tool_calls=list(synthetic_result["tool_calls"]),
        retrieved_evidence=list(synthetic_result["retrieved_evidence"]),
        needs_clarification=bool(synthetic_result["needs_clarification"]),
        clarification_question=synthetic_result["clarification_question"],
        privacy=dict(synthetic_result["privacy"]),
        context_budget={},
        citations=list(synthetic_result["citations"]),
        memory_written=memory_written,
        memory_hits=int(synthetic_result["memory_hits"]),
        merged_memory_hits=int(synthetic_result["merged_memory_hits"]),
        memory_context=dict(synthetic_result["memory_context"]),
        context_sources=list(synthetic_result["context_sources"]),
        workspace_memory_hits=int(synthetic_result["workspace_memory_hits"]),
        transcript_hits=int(synthetic_result["transcript_hits"]),
        user_model_used=bool(synthetic_result["user_model_used"]),
        answer_collapsed=False,
        reflection_notes=None,
        upload_context=dict(synthetic_result["upload_context"]),
        route_mode="fast",
        router_intent=str(synthetic_result["router_intent"]),
        router_reason=str(synthetic_result["router_reason"]),
        required_grounding=str(synthetic_result["required_grounding"]),
        fast_path_used=True,
        degraded_from=str(synthetic_result["degraded_from"]),
        planner_type="fast_router",
        task_plan=dict(synthetic_result["task_plan"]),
        subtask_results=list(synthetic_result["subtask_results"]),
        aggregation_strategy="fast_path",
        partial_failures=list(synthetic_result["partial_failures"]),
        react_trace=[],
        loop_step_count=0,
        termination_reason=str(synthetic_result["termination_reason"]),
        pending_confirmation={},
        confirmation_payload={},
        final_answer_source=str(synthetic_result["final_answer_source"]),
        memory_reads=list(synthetic_result["memory_reads"]),
        tool_observations=list(synthetic_result["tool_observations"]),
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=round(latency_ms, 2),
        token_in=int(synthetic_result["token_in"]),
        token_out=int(synthetic_result["token_out"]),
        estimated_cost=float(synthetic_result["estimated_cost"]),
    )


def _fast_memory_scope(message: str, route_decision: dict[str, Any]) -> str:
    preferred = str(route_decision.get("recommended_tool") or "")
    if preferred in {"conversation_recent", "conversation_summary", "workspace_memory", "user_model"}:
        return preferred
    lowered = message.lower()
    if any(token in message or token in lowered for token in ("偏好", "默认", "remember my preference", "default behavior")):
        return "user_model"
    if any(token in message or token in lowered for token in ("docker", "todolist", "readme", "项目约定", "workspace")):
        return "workspace_memory"
    if any(token in message for token in ("之前聊了什么", "之前讨论", "总结一下之前")):
        return "conversation_summary"
    return "conversation_recent"


def _fast_contextual_memory_result(*, session_id: str, conversation_id: str, message: str, route_decision: dict[str, Any]) -> dict[str, Any]:
    scope = _fast_memory_scope(message, route_decision)
    memory_reads: list[dict[str, Any]] = []
    memory_context: dict[str, Any] = {}
    observations: list[dict[str, Any]] = []
    working_memory: list[str] = []
    memory_hits = 0
    merged_memory_hits = 0
    workspace_memory_hits = 0
    transcript_hits = 0
    user_model_used = False
    context_sources: list[str] = []

    if scope == "user_model":
        user_memory = get_user_memory_context(session_id=session_id, conversation_id=conversation_id, limit=8)
        summary_text = str(user_memory.get("summary") or "")
        facts = [str(item).strip() for item in user_memory.get("facts") or [] if str(item).strip()]
        memory_reads.append({"kind": "user_model", "hits": int(user_memory.get("hits", 0)), "summary": summary_text})
        memory_context["user_model"] = user_memory
        user_model_used = bool(user_memory.get("hits"))
        observation = {
            "observation_type": "user_model",
            "source": "user_model",
            "grounding_kind": "memory",
            "memory_boundary": "context_only",
            "enterprise_citation_required": True,
            "summary": compact_text(summary_text or "User preference memory was checked.", 220),
            "payload": {"facts": facts, "summary": summary_text, "reflection_candidates": list(user_memory.get("reflection_candidates") or [])},
            "citations": [],
            "confidence": 0.92,
        }
        observations.append(observation)
        if observation["summary"]:
            working_memory.append(str(observation["summary"]))
    elif scope == "workspace_memory":
        workspace = get_workspace_memory_context(message, top_k=6)
        items = list(workspace.get("items") or [])
        summary_text = str(workspace.get("summary") or "")
        memory_reads.append({"kind": "workspace_memory", "hits": int(workspace.get("hits", 0)), "summary": summary_text})
        memory_context["workspace_memory"] = workspace
        workspace_memory_hits = int(workspace.get("hits", 0))
        context_sources = [str(item.get("file_path") or item.get("title") or "") for item in items[:4] if str(item.get("file_path") or item.get("title") or "").strip()]
        observation = {
            "observation_type": "workspace_memory",
            "source": "workspace_memory",
            "grounding_kind": "memory",
            "memory_boundary": "context_only",
            "enterprise_citation_required": True,
            "summary": compact_text(summary_text or "Workspace memory was checked.", 220),
            "payload": {"items": items[:4], "summary": summary_text},
            "citations": [],
            "confidence": 0.9,
        }
        observations.append(observation)
        if observation["summary"]:
            working_memory.append(str(observation["summary"]))
    else:
        turns = get_turns(conversation_id, limit=8)
        transcript_hits = len(turns)
        memory_context["recent_turns"] = turns
        recent_summary = "\n".join(
            f"{turn.get('role')}: {compact_text(str(turn.get('redacted_content') or turn.get('content') or ''), 100)}"
            for turn in turns[-4:]
        )
        memory_reads.append({"kind": "conversation_recent", "hits": len(turns), "summary": recent_summary})
        recent_observation = {
            "observation_type": "conversation_recent",
            "source": "conversation_recent",
            "grounding_kind": "memory",
            "memory_boundary": "context_only",
            "enterprise_citation_required": True,
            "summary": compact_text(recent_summary or "Recent conversation memory was checked.", 220),
            "payload": {"turns": turns},
            "citations": [],
            "confidence": 0.88,
        }
        observations.append(recent_observation)
        if recent_observation["summary"]:
            working_memory.append(str(recent_observation["summary"]))
        if scope == "conversation_summary":
            summary_memory = build_memory_context(session_id=session_id, conversation_id=conversation_id, question=message)
            compactions = get_recent_compactions(session_id=session_id, conversation_id=conversation_id, limit=3)
            structured = get_structured_turn_summaries(session_id=session_id, conversation_id=conversation_id, limit=6)
            summary_text = str(summary_memory.get("memory_context") or "")
            memory_context["conversation_summary"] = summary_memory
            memory_context["compactions"] = compactions
            memory_context["structured_turn_summaries"] = structured
            memory_reads.append(
                {
                    "kind": "conversation_summary",
                    "hits": int(summary_memory.get("memory_hits", 0)) + int(summary_memory.get("merged_memory_hits", 0)) + len(compactions) + len(structured),
                    "summary": summary_text,
                }
            )
            memory_hits = int(summary_memory.get("memory_hits", 0))
            merged_memory_hits = int(summary_memory.get("merged_memory_hits", 0))
            summary_observation = {
                "observation_type": "conversation_summary",
                "source": "conversation_summary",
                "grounding_kind": "memory",
                "memory_boundary": "context_only",
                "enterprise_citation_required": True,
                "summary": compact_text(summary_text or "Conversation summary memory was checked.", 220),
                "payload": {
                    "summary": summary_text,
                    "compactions": compactions,
                    "structured_turn_summaries": structured,
                },
                "citations": [],
                "confidence": 0.9,
            }
            observations.append(summary_observation)
            if summary_observation["summary"]:
                working_memory.append(str(summary_observation["summary"]))

    return {
        "intent": "contextual_qa",
        "tool_calls": [],
        "observations": observations,
        "tool_observations": observations,
        "working_memory": working_memory,
        "memory_context": memory_context,
        "memory_hits": memory_hits,
        "merged_memory_hits": merged_memory_hits,
        "workspace_memory_hits": workspace_memory_hits,
        "transcript_hits": transcript_hits,
        "user_model_used": user_model_used,
        "memory_reads": memory_reads,
        "context_sources": context_sources,
        "final_answer_source": "fast_memory_renderer",
        "termination_reason": "direct_answer",
    }


def _execute_fast_path(
    *,
    payload: UnifiedAgentRequest,
    conversation_id: str,
    display_message: str,
    upload_context: dict[str, Any],
    route_decision: dict[str, Any],
) -> UnifiedAgentResponse:
    started = perf_counter()
    intent = str(route_decision.get("intent") or "mixed")
    recommended_tool = str(route_decision.get("recommended_tool") or "")
    executors = build_tool_executor_map()
    context = OrchestrationContext(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        message=payload.message,
        safe_message=payload.message,
        display_message=display_message or payload.message,
        upload_context=dict(upload_context or {}),
    )

    if intent == "upload_analysis":
        task_type = str((route_decision.get("recommended_tool_input") or {}).get("task_type") or infer_upload_task_type(payload.message))
        payload_dict = executors["uploaded_content_analyze"](
            {
                "content_kind": str(upload_context.get("kind") or "document"),
                "task_type": task_type,
                "message": payload.message,
                "uploaded_filename": str(upload_context.get("filename") or payload.uploaded_filename),
                "uploaded_content_type": str(upload_context.get("content_type") or payload.uploaded_content_type),
                "uploaded_text": str(upload_context.get("uploaded_text") or payload.uploaded_text),
                "source_parse_status": str(upload_context.get("parse_status") or payload.source_parse_status),
                "source_parse_error": str(upload_context.get("parse_error") or payload.source_parse_error),
            },
            context,
            {},
        )
        observation = {
            "observation_type": "upload_analysis_result",
            "source": "uploaded_content_analyze",
            "grounding_kind": "none",
            "summary": compact_text(str(payload_dict.get("answer") or ""), 220),
            "payload": payload_dict,
            "citations": [],
            "confidence": 0.9,
        }
        result = {
            "intent": "uploaded_content_analyze",
            "tool_calls": [{"tool_name": "uploaded_content_analyze", "success": True, "status": "completed", "result": payload_dict}],
            "tool_observations": [observation],
            "observations": [observation],
            "working_memory": [str(observation["summary"])],
            "upload_context": dict(payload_dict.get("upload_context") or upload_context),
            "final_answer_source": f"fast_upload_{task_type}_renderer",
            "termination_reason": "direct_answer",
        }
        return _build_fast_path_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            route_decision=route_decision,
            result=result,
            latency_ms=(perf_counter() - started) * 1000.0,
        )

    if intent == "persona":
        payload_dict = executors["persona_or_chitchat"]({"message": payload.message}, context, {})
        observation = {
            "observation_type": "persona_result",
            "source": "persona_or_chitchat",
            "grounding_kind": "none",
            "summary": compact_text(str(payload_dict.get("answer") or ""), 220),
            "payload": payload_dict,
            "citations": [],
            "confidence": 0.86,
        }
        result = {
            "intent": "persona_or_chitchat",
            "tool_calls": [{"tool_name": "persona_or_chitchat", "success": True, "status": "completed", "result": payload_dict}],
            "tool_observations": [observation],
            "observations": [observation],
            "working_memory": [str(observation["summary"])],
            "final_answer_source": "fast_persona_renderer",
            "termination_reason": "direct_answer",
        }
        return _build_fast_path_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            route_decision=route_decision,
            result=result,
            latency_ms=(perf_counter() - started) * 1000.0,
        )

    if intent == "enterprise_fact":
        payload_dict = answer_enterprise_question(
            payload.message,
            top_k=8,
            session_id=payload.session_id,
            conversation_id=conversation_id,
        )
        observation = {
            "observation_type": "enterprise_rag_result",
            "source": "enterprise_rag_query",
            "grounding_kind": "retrieval",
            "summary": compact_text(str(payload_dict.get("answer") or ""), 240),
            "payload": payload_dict,
            "citations": list(payload_dict.get("citations") or []),
            "confidence": float(payload_dict.get("confidence", 0.0) or 0.0),
        }
        result = {
            "intent": "enterprise_rag_query",
            "tool_calls": [{"tool_name": "enterprise_rag_query", "success": True, "status": "completed", "result": payload_dict}],
            "tool_observations": [observation],
            "observations": [observation],
            "working_memory": [str(observation["summary"])],
            "retrieved_evidence": list(payload_dict.get("supporting_fact_details") or []),
            "citations": list(payload_dict.get("citations") or []),
            "context_sources": list(payload_dict.get("context_sources") or []),
            "workspace_memory_hits": int(payload_dict.get("workspace_memory_hits", 0)),
            "transcript_hits": int(payload_dict.get("transcript_hits", 0)),
            "user_model_used": bool(payload_dict.get("user_model_used", False)),
            "memory_context": dict(payload_dict.get("memory_context") or {}),
            "final_answer_source": "fast_enterprise_rag_renderer",
            "termination_reason": "direct_answer",
        }
        return _build_fast_path_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            route_decision=route_decision,
            result=result,
            latency_ms=(perf_counter() - started) * 1000.0,
        )

    if intent == "mail_status":
        if recommended_tool == "outbound_mail_summary":
            since, until = _local_day_window()
            summary = {"since": since, "until": until, **get_sent_mail_stats(since=since, until=until, session_id=payload.session_id)}
            observation = {
                "observation_type": "mail_status_result",
                "source": "outbound_mail_summary",
                "grounding_kind": "tool",
                "summary": "已获取当前时间范围内的外发统计。",
                "payload": summary,
                "citations": [],
                "confidence": 0.92,
            }
            result = {
                "intent": "outbound_mail_assistant",
                "tool_calls": [{"tool_name": "outbound_mail_summary", "success": True, "status": "completed", "result": summary}],
                "tool_observations": [observation],
                "observations": [observation],
                "working_memory": [str(observation["summary"])],
                "final_answer_source": "fast_mail_status_renderer",
                "termination_reason": "direct_answer",
            }
        elif recommended_tool == "governance_task_context_fetch":
            task = get_latest_recoverable_task(payload.session_id, conversation_id)
            if task:
                answer = f"当前最近的治理任务状态是：`{task.get('status', 'unknown')}`。风险级别：`{task.get('risk_level', 'unknown')}`。任务 ID：`{task.get('task_id', '')}`。"
            else:
                answer = "当前会话里没有待恢复或待处理的治理任务。"
            result = {
                "intent": "governance_task_status",
                "tool_calls": [{"tool_name": "governance_task_context_fetch", "success": True, "status": "completed", "result": {"task": task or {}}}],
                "tool_observations": [
                    {
                        "observation_type": "task_status_result",
                        "source": "governance_task_context_fetch",
                        "grounding_kind": "tool",
                        "summary": "已获取当前会话最近的治理任务状态。" if task else "当前会话没有可恢复或待处理的治理任务。",
                        "payload": {"task": task or {}},
                        "citations": [],
                        "confidence": 0.9,
                    }
                ],
                "observations": [
                    {
                        "observation_type": "task_status_result",
                        "source": "governance_task_context_fetch",
                        "grounding_kind": "tool",
                        "summary": "已获取当前会话最近的治理任务状态。" if task else "当前会话没有可恢复或待处理的治理任务。",
                        "payload": {"task": task or {}},
                        "citations": [],
                        "confidence": 0.9,
                    }
                ],
                "working_memory": ["已获取当前会话最近的治理任务状态。" if task else "当前会话没有可恢复或待处理的治理任务。"],
                "final_answer_source": "fast_mail_status_renderer",
                "termination_reason": "direct_answer",
            }
        else:
            summary = get_inbound_mail_summary()
            result = {
                "intent": "inbound_mail_assistant",
                "tool_calls": [{"tool_name": "inbound_mail_summary", "success": True, "status": "completed", "result": summary}],
                "tool_observations": [
                    {
                        "observation_type": "mail_status_result",
                        "source": "inbound_mail_summary",
                        "grounding_kind": "tool",
                        "summary": "已获取当前收件箱摘要和同步状态。",
                        "payload": {"summary": summary, "sync_state": latest_sync_state()},
                        "citations": [],
                        "confidence": 0.92,
                    }
                ],
                "observations": [
                    {
                        "observation_type": "mail_status_result",
                        "source": "inbound_mail_summary",
                        "grounding_kind": "tool",
                        "summary": "已获取当前收件箱摘要和同步状态。",
                        "payload": {"summary": summary, "sync_state": latest_sync_state()},
                        "citations": [],
                        "confidence": 0.92,
                    }
                ],
                "working_memory": ["已获取当前收件箱摘要和同步状态。"],
                "final_answer_source": "fast_mail_status_renderer",
                "termination_reason": "direct_answer",
            }
        return _build_fast_path_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            route_decision=route_decision,
            result=result,
            latency_ms=(perf_counter() - started) * 1000.0,
        )

    if intent == "contextual_memory":
        result = _fast_contextual_memory_result(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            route_decision=route_decision,
        )
        return _build_fast_path_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            route_decision=route_decision,
            result=result,
            latency_ms=(perf_counter() - started) * 1000.0,
        )

    raise ValueError(f"Unsupported fast path intent: {intent}")


def _supplement_dlp_task(task: dict[str, Any], payload: DlpTaskSupplementRequest) -> dict:
    merged_message = _merge_supplement_message(
        str(task.get("message_raw", "")),
        payload.message,
        payload.uploaded_text,
        payload.uploaded_filename,
    )
    source_filename = payload.uploaded_filename or str(task.get("source_filename", ""))
    source_content_type = payload.uploaded_content_type or str(task.get("source_content_type", ""))
    source_parse_status, source_parse_error = _normalize_source_parse(
        source_filename,
        payload.uploaded_text,
        payload.source_parse_status if payload.uploaded_filename else str(task.get("source_parse_status", "not_provided")),
        payload.source_parse_error if payload.uploaded_filename else str(task.get("source_parse_error", "")),
    )
    decision = classify_dlp_entry(
        message=merged_message,
        destination_email=payload.destination_email or str(task.get("destination_email", "")),
        uploaded_text=payload.uploaded_text,
        uploaded_filename=source_filename,
        source_parse_status=source_parse_status,
        source_parse_error=source_parse_error,
    )
    destination_email = payload.destination_email.strip() or decision.destination_email or str(task.get("destination_email", ""))
    if decision.route == "non_dlp":
        if source_parse_status in {"parse_failed", "empty", "invalid"} or source_parse_error:
            next_status = "input_invalid"
            entry_issue_type = "file_parse_failed"
            missing_fields = ["content"] if not destination_email else []
            clarification_question = "我已保留你的外发请求，但上传文件暂时无法解析。请重新上传文件或直接粘贴正文。"
        else:
            next_status = "needs_clarification"
            entry_issue_type = "missing_content"
            missing_fields = ["content"]
            clarification_question = "我已保留你的外发请求，但还需要更明确的外发内容或说明后才能继续处理。"
    else:
        next_status = "queued" if decision.route == "normal_outbound" else decision.route
        entry_issue_type = decision.entry_issue_type
        missing_fields = decision.missing_fields
        clarification_question = decision.clarification_question
    resolution = _build_supplement_outbound_resolution(task, payload) if next_status == "queued" else {}
    updated_task = update_task(
        str(task["task_id"]),
        message_raw=str(resolution.get("review_content") or merged_message),
        request_message=str(task.get("request_message") or task.get("message_raw") or ""),
        delivery_subject=str(resolution.get("delivery_subject") or task.get("delivery_subject") or ""),
        delivery_body=str(resolution.get("delivery_body") or task.get("delivery_body") or ""),
        delivery_plan_kind=str(resolution.get("delivery_plan_kind") or task.get("delivery_plan_kind") or ""),
        resolved_source_kind=str(resolution.get("resolved_source_kind") or task.get("resolved_source_kind") or ""),
        attachment_strategy=str(resolution.get("attachment_strategy") or task.get("attachment_strategy") or "none"),
        attachment_content=str(resolution.get("attachment_content") or task.get("attachment_content") or ""),
        attachment_filename=str(resolution.get("attachment_filename") or task.get("attachment_filename") or ""),
        attachment_content_type=str(resolution.get("attachment_content_type") or task.get("attachment_content_type") or ""),
        attachment_blob_id=str(resolution.get("attachment_blob_id") or task.get("attachment_blob_id") or ""),
        destination_email=destination_email,
        source_filename=source_filename,
        source_content_type=source_content_type,
        source_parse_status=source_parse_status,
        source_parse_error=source_parse_error,
        entry_issue_type=entry_issue_type,
        missing_fields=missing_fields,
        clarification_question=clarification_question,
        delivery_error="" if next_status == "queued" else str(task.get("delivery_error", "")),
        delivery_result="" if next_status == "queued" else str(task.get("delivery_result", "")),
        final_result="" if next_status == "queued" else str(task.get("final_result", "")),
        status=next_status,
    )
    if not updated_task:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    add_task_event(
        str(task["task_id"]),
        "supplemented",
        "api",
        {
            "message": "Supplemental information was attached to the task.",
            "status": next_status,
            "destination_email": destination_email,
            "missing_fields": missing_fields,
        },
    )
    if next_status == "queued":
        add_task_event(
            str(task["task_id"]),
            "queued",
            "api",
            {"message": "Task resumed after supplemental information was received.", "status": "queued"},
        )
        publish_task_event(
            build_task_event(
                task_id=str(task["task_id"]),
                status="queued",
                event_type="queued",
                message="Supplemental information received. Task resumed for DLP processing.",
                delivery_status=str(updated_task.get("delivery_status", "not_sent")),
            )
        )
        enqueue_dlp_risk_task(str(task["task_id"]))
    else:
        publish_task_event(
            build_task_event(
                task_id=str(task["task_id"]),
                status=next_status,
                event_type=next_status,
                message=str(updated_task.get("clarification_question") or "Task is waiting for additional input."),
                delivery_status=str(updated_task.get("delivery_status", "not_sent")),
            )
        )
    return updated_task


def _refresh_async_metrics() -> None:
    stats = get_task_stats()
    settings = get_settings()
    backlog = {RISK_QUEUE: 0, EMAIL_QUEUE: 0, MAIL_QUEUE: 0}
    average_latency_ms = 0.0
    try:
        with Redis.from_url(settings.redis_url, decode_responses=True) as redis_client:
            backlog[RISK_QUEUE] = int(redis_client.llen(RISK_QUEUE))
            backlog[EMAIL_QUEUE] = int(redis_client.llen(EMAIL_QUEUE))
            backlog[MAIL_QUEUE] = int(redis_client.llen(MAIL_QUEUE))
    except Exception:
        backlog = {RISK_QUEUE: 0, EMAIL_QUEUE: 0, MAIL_QUEUE: 0}
    terminal_tasks = [
        item
        for item in list_dlp_tasks()
        if item.get("status") in {"sent", "rejected", "send_failed", "failed"}
    ]
    if terminal_tasks:
        total_ms = 0.0
        for item in terminal_tasks:
            try:
                created_ts = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00")).timestamp()
                updated_ts = datetime.fromisoformat(str(item["updated_at"]).replace("Z", "+00:00")).timestamp()
                total_ms += max(updated_ts - created_ts, 0.0) * 1000
            except Exception:
                continue
        average_latency_ms = total_ms / len(terminal_tasks) if terminal_tasks else 0.0
    refresh_task_metrics(
        total=stats["total"],
        inflight=stats["inflight"],
        by_status=stats["by_status"],
        backlog=backlog,
        average_latency_ms=average_latency_ms,
    )


def _looks_like_inbound_mail_query(message: str) -> bool:
    text = (message or "").lower()
    mail_tokens = ("邮件", "邮箱", "收件", "来信", "mail", "email", "inbox")
    query_tokens = ("收到", "收了", "多少", "重要", "今天", "昨日", "昨天", "早报", "summary", "digest", "unread")
    outbound_query_tokens = ("发送了多少", "发了多少", "发出多少", "sent how many", "how many sent")
    if any(token in message or token in text for token in outbound_query_tokens):
        return False
    return any(token in message or token in text for token in mail_tokens) and any(
        token in message or token in text for token in query_tokens
    )


def _looks_like_outbound_mail_summary_query(message: str) -> bool:
    text = (message or "").lower()
    mail_tokens = ("邮件", "邮箱", "mail", "email")
    outbound_tokens = ("发送", "发出", "发了", "sent", "send")
    summary_tokens = ("多少", "几封", "统计", "数量", "count", "today", "今天", "昨日", "昨天")
    return (
        any(token in message or token in text for token in mail_tokens)
        and any(token in message or token in text for token in outbound_tokens)
        and any(token in message or token in text for token in summary_tokens)
    )


def _looks_like_mail_draft_request(message: str) -> bool:
    text = (message or "").lower()
    return any(token in message or token in text for token in ("起草回复", "草拟回复", "回复草稿", "draft reply"))


def _build_inbound_mail_agent_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    answer: str,
    tool_name: str,
    payload: dict[str, Any],
    intent: str = "inbound_mail_assistant",
    mode_used: str = "mail_inbound",
    routing_reason: str = "Detected inbound mail summary or reply drafting request.",
    observations: list[dict[str, Any]] | None = None,
) -> UnifiedAgentResponse:
    normalized_observations = list(observations or [])
    if not normalized_observations:
        normalized_observations = [
            {
                "observation_type": "mail_read_result" if "reply" in tool_name else "mail_status_result",
                "source": tool_name,
                "grounding_kind": "tool",
                "summary": compact_text(answer, 220),
                "payload": payload,
                "citations": [],
                "confidence": 0.94,
            }
        ]
    renderer = render_final_answer(
        question=message,
        current_goal=intent,
        observations=normalized_observations,
        working_memory=[str(item.get("summary") or "") for item in normalized_observations if str(item.get("summary") or "").strip()],
        conservative=False,
    )
    result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "answer": str(renderer.get("answer") or answer),
        "intent": intent,
        "routing_source": "rule",
        "routing_confidence": 0.95,
        "routing_reason": routing_reason,
        "candidate_intents": [intent],
        "mode_used": mode_used,
        "tool_calls": [{"tool_name": tool_name, "success": True, "status": "completed", "result": payload}],
        "retrieved_evidence": [],
        "needs_clarification": False,
        "clarification_question": None,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "node_latencies_ms": {"total": 0.0},
        "token_in": int(renderer.get("token_in", 0)),
        "token_out": int(renderer.get("token_out", 0)),
        "estimated_cost": float(renderer.get("estimated_cost", 0.0)),
        "tool_observations": normalized_observations,
    }
    turn_id, memory_written = _write_unified_conversation_memory(result, conversation_id)
    record_request(
        mode=mode_used,
        latency_ms=0.0,
        token_in=int(result["token_in"]),
        token_out=int(result["token_out"]),
        estimated_cost=float(result["estimated_cost"]),
        retrieval_hits=0,
    )
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=str(result["answer"]),
        intent=intent,
        routing_source="rule",
        routing_confidence=0.95,
        routing_reason=routing_reason,
        candidate_intents=[intent],
        mode_used=mode_used,
        tool_calls=result["tool_calls"],
        retrieved_evidence=[],
        needs_clarification=False,
        clarification_question=None,
        privacy={},
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        final_answer_source="rule_renderer_fallback" if renderer.get("used_fallback") else "rule_renderer",
        tool_observations=normalized_observations,
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(result["request_id"]),
        latency_ms=0.0,
        token_in=int(result["token_in"]),
        token_out=int(result["token_out"]),
        estimated_cost=float(result["estimated_cost"]),
    )


def _inbound_summary_answer(summary: dict[str, Any], state: dict[str, Any]) -> str:
    lines = [
        f"昨日到现在共收到 {summary.get('total', 0)} 封邮件，其中未读 {summary.get('unread', 0)} 封。",
        f"重要邮件候选 {summary.get('important_count', 0)} 封。",
    ]
    if state.get("last_error"):
        lines.append(f"最近一次收件同步异常：{state['last_error']}")
    important = summary.get("important_messages", [])[:5]
    if important:
        lines.append("")
        lines.append("重要邮件摘要：")
        for item in important:
            sender = _format_sender_label(item.get("sender", ""))
            lines.append(f"- {item.get('subject') or '(无主题)'} | {sender}: {item.get('summary') or item.get('snippet', '')}")
    return "\n".join(lines)


def _format_sender_label(sender: str) -> str:
    value = (sender or "").strip()
    if not value:
        return "未知发件人"
    if value.endswith("@exmail.weixin.qq.com"):
        return "系统通知"
    return f"`{value}`"


def _local_day_window() -> tuple[str, str]:
    now_local = datetime.now(LOCAL_TZ)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=LOCAL_TZ)
    return start_local.astimezone(timezone.utc).isoformat(), now_local.astimezone(timezone.utc).isoformat()


def _outbound_summary_answer(summary: dict[str, Any]) -> str:
    total_sent = int(summary.get("total_sent", 0))
    lines = [f"今天通过 Agent 成功发送了 {total_sent} 封邮件。"]
    recent_sent = list(summary.get("recent_sent") or [])
    if recent_sent:
        lines.append("")
        lines.append("最近发送：")
        for item in recent_sent[:5]:
            recipient = (item.get("destination_email") or "").strip() or "未填写"
            sent_at = str(item.get("sent_at") or "")
            lines.append(f"- `{sent_at}` -> `{recipient}`")
    else:
        lines.append("当前统计范围内还没有成功发送记录。")
    return "\n".join(lines)


def _looks_like_enterprise_rag_query(message: str) -> bool:
    text = (message or "").lower()
    hints = (
        "enterpriserag",
        "enterprise rag",
        "企业知识",
        "企业资料",
        "项目文档",
        "客户",
        "供应商",
        "合同",
        "工单",
        "jira",
        "linear",
        "github",
        "slack",
        "confluence",
        "hubspot",
        "google drive",
    )
    return any(hint in text or hint in message for hint in hints)


def _enterprise_rag_answer_text(result: dict[str, Any]) -> str:
    lines = [str(result.get("answer") or "当前企业知识库中没有检索到足够证据。")]
    citations = list(result.get("citations") or [])[:5]
    if citations:
        lines.append("")
        lines.append("引用证据：")
        for item in citations:
            title = item.get("title") or "(untitled)"
            doc_id = item.get("doc_id") or ""
            source_type = item.get("source_type") or ""
            lines.append(f"- {title} [{source_type}] `{doc_id}`")
    return "\n".join(lines)


@dataclass
class _CompoundSubtask:
    task_id: str
    capability: str
    input: dict[str, Any]


@dataclass
class _CompoundTaskPlan:
    subtasks: list[_CompoundSubtask]


def plan_compound_tasks(message: str) -> _CompoundTaskPlan:
    subtasks: list[_CompoundSubtask] = []
    lowered = (message or "").lower()

    if _looks_like_inbound_mail_query(message) or any(token in lowered for token in ("inbox", "mailbox", "收件箱")):
        subtasks.append(
            _CompoundSubtask(
                task_id="compound_inbound_mail_summary",
                capability="inbound_mail_summary",
                input={},
            )
        )
    if _looks_like_outbound_mail_summary_query(message) or any(token in lowered for token in ("sent mail", "outbound", "发了多少", "外发")):
        subtasks.append(
            _CompoundSubtask(
                task_id="compound_outbound_mail_summary",
                capability="outbound_mail_summary",
                input={},
            )
        )
    if _looks_like_enterprise_rag_query(message) or any(token in lowered for token in ("风险", "risk", "meeting", "onboarding", "文档")):
        subtasks.append(
            _CompoundSubtask(
                task_id="compound_enterprise_rag",
                capability="enterprise_rag_query",
                input={"question": message, "source_types": []},
            )
        )

    return _CompoundTaskPlan(subtasks=subtasks)


def _handle_compound_agent_request(payload: UnifiedAgentRequest, conversation_id: str) -> UnifiedAgentResponse | None:
    task_plan = plan_compound_tasks(payload.message)
    if len(task_plan.subtasks) < 2:
        return None
    since, until = _local_day_window()
    tool_results: dict[str, Any] = {"subtasks": []}
    observations: list[dict[str, Any]] = []
    for subtask in task_plan.subtasks:
        if subtask.capability == "outbound_mail_summary":
            result = {"since": since, "until": until, **get_sent_mail_stats(since=since, until=until, session_id=payload.session_id)}
            observations.append(
                {
                    "observation_type": "mail_status_result",
                    "source": "outbound_mail_summary",
                    "grounding_kind": "tool",
                    "summary": "已获取当前时间范围内的外发统计。",
                    "payload": result,
                    "citations": [],
                    "confidence": 0.92,
                }
            )
        elif subtask.capability == "inbound_mail_summary":
            result = get_inbound_mail_summary()
            observations.append(
                {
                    "observation_type": "mail_status_result",
                    "source": "inbound_mail_summary",
                    "grounding_kind": "tool",
                    "summary": "已获取当前收件箱摘要和同步状态。",
                    "payload": {"summary": result, "sync_state": latest_sync_state()},
                    "citations": [],
                    "confidence": 0.92,
                }
            )
        elif subtask.capability == "enterprise_rag_query":
            result = answer_enterprise_question(
                str(subtask.input.get("question") or payload.message),
                source_types=list(subtask.input.get("source_types") or []),
            )
            observations.append(
                {
                    "observation_type": "enterprise_rag_result",
                    "source": "enterprise_rag_query",
                    "grounding_kind": "retrieval",
                    "summary": compact_text(str(result.get("answer") or ""), 240),
                    "payload": result,
                    "citations": list(result.get("citations") or []),
                    "confidence": float(result.get("confidence", 0.0) or 0.0),
                }
            )
        else:
            result = {"error": f"Unsupported capability: {subtask.capability}"}
        tool_results["subtasks"].append({"task_id": subtask.task_id, "capability": subtask.capability, "result": result})
        tool_calls.append({"tool_name": subtask.capability, "success": "error" not in result, "status": "completed", "result": result})
    answer = "\n\n".join(answer_sections)
    return _build_inbound_mail_agent_response(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        message=payload.message,
        answer=answer,
        tool_name="compound_task_plan",
        payload={"task_plan": [asdict(subtask) for subtask in task_plan.subtasks], **tool_results},
        intent="compound_enterprise_assistant",
        mode_used="compound_plan_execute",
        routing_reason="Detected a compound enterprise/mail request and executed multiple subtasks.",
        observations=observations,
    )


def _handle_compound_agent_request(payload: UnifiedAgentRequest, conversation_id: str) -> UnifiedAgentResponse | None:
    task_plan = plan_compound_tasks(payload.message)
    if len(task_plan.subtasks) < 2:
        return None

    since, until = _local_day_window()
    tool_results: dict[str, Any] = {"subtasks": []}
    observations: list[dict[str, Any]] = []

    for subtask in task_plan.subtasks:
        if subtask.capability == "outbound_mail_summary":
            result = {"since": since, "until": until, **get_sent_mail_stats(since=since, until=until, session_id=payload.session_id)}
            observations.append(
                _build_mailbox_summary_observation(
                    source="outbound_mail_summary",
                    summary_text="已获取当前时间范围内的外发统计。",
                    payload=result,
                )
            )
        elif subtask.capability == "inbound_mail_summary":
            summary = get_inbound_mail_summary()
            sync_state = latest_sync_state()
            result = {"summary": summary, "sync_state": sync_state}
            observations.append(
                _build_mailbox_summary_observation(
                    source="inbound_mail_summary",
                    summary_text="已获取当前收件箱摘要和同步状态。",
                    payload=result,
                )
            )
        elif subtask.capability == "enterprise_rag_query":
            result = answer_enterprise_question(
                str(subtask.input.get("question") or payload.message),
                source_types=list(subtask.input.get("source_types") or []),
            )
            observations.append(
                {
                    "observation_type": "enterprise_rag_result",
                    "source": "enterprise_rag_query",
                    "grounding_kind": "retrieval",
                    "summary": compact_text(str(result.get("answer") or ""), 240),
                    "payload": result,
                    "citations": list(result.get("citations") or []),
                    "confidence": float(result.get("confidence", 0.0) or 0.0),
                }
            )
        else:
            result = {"error": f"Unsupported capability: {subtask.capability}"}

        tool_results["subtasks"].append(
            {"task_id": subtask.task_id, "capability": subtask.capability, "result": result}
        )

    observations.append(
        _build_compound_observation(
            question=payload.message,
            observations=observations,
            subtasks=[asdict(subtask) for subtask in task_plan.subtasks],
        )
    )

    return _build_inbound_mail_agent_response(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        message=payload.message,
        answer="",
        tool_name="compound_task_plan",
        payload={"task_plan": [asdict(subtask) for subtask in task_plan.subtasks], **tool_results},
        intent="compound_enterprise_assistant",
        mode_used="compound_plan_execute",
        routing_reason="Detected a compound enterprise/mail request and executed multiple subtasks.",
        observations=observations,
    )


def _parse_optional_datetime(value: str | None, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}; expected ISO datetime.") from exc


def _handle_async_outbound_agent_request(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    outbound_message: str,
    display_message: str,
    upload_context: dict[str, Any] | None = None,
    ) -> UnifiedAgentResponse:
    resolution = _build_outbound_resolution(payload, conversation_id, dict(upload_context or {}))
    if not resolution.get("ok"):
        return _build_rule_clarification_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            clarification_question=str(
                resolution.get("clarification_question")
                or "我已经识别到外发意图，但还不能安全判断要发送的具体内容。请先明确发送对象。"
            ),
            routing_reason="Detected a referential outbound request and required explicit content resolution before creating a DLP task.",
            candidates=list(resolution.get("candidates") or []),
        )

    selected_candidate = dict(resolution.get("selected_candidate") or {})
    task_payload = DlpTaskCreateRequest(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        message=payload.message,
        request_message=str(resolution.get("request_message") or payload.message),
        review_content=str(resolution.get("review_content") or ""),
        resolved_outbound_content=str(resolution.get("resolved_outbound_content") or outbound_message),
        resolved_source_kind=str(resolution.get("resolved_source_kind") or ""),
        delivery_subject=str(resolution.get("delivery_subject") or ""),
        delivery_body=str(resolution.get("delivery_body") or ""),
        delivery_plan_kind=str(resolution.get("delivery_plan_kind") or ""),
        attachment_strategy=str(resolution.get("attachment_strategy") or "none"),
        attachment_content=str(resolution.get("attachment_content") or ""),
        attachment_filename=str(resolution.get("attachment_filename") or ""),
        attachment_content_type=str(resolution.get("attachment_content_type") or ""),
        attachment_blob_id=str(resolution.get("attachment_blob_id") or ""),
        destination_email=str(resolution.get("destination_email") or _extract_destination_email(payload.message)),
        uploaded_filename=str(selected_candidate.get("filename") or payload.uploaded_filename),
        uploaded_content_type=str(selected_candidate.get("content_type") or payload.uploaded_content_type),
        uploaded_text=str(selected_candidate.get("content") or payload.uploaded_text),
        uploaded_file_base64=payload.uploaded_file_base64,
        source_parse_status=payload.source_parse_status,
        source_parse_error=payload.source_parse_error,
    )
    task = _create_async_dlp_task(task_payload)
    return _build_task_agent_response(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        outbound_message=str(resolution.get("resolved_outbound_content") or outbound_message),
        display_message=display_message,
        task=task,
        routing_reason="Resolved outbound content and created a governed DLP task.",
    )


def _create_dlp_task_from_mail_plan(
    *,
    session_id: str,
    conversation_id: str,
    request_message: str,
    mail_plan: dict[str, Any],
) -> dict[str, Any]:
    attachment = dict((mail_plan.get("resolved_attachments") or [{}])[0] or {}) if mail_plan.get("resolved_attachments") else {}
    selected_candidate = dict(mail_plan.get("selected_candidate") or {})
    return _create_async_dlp_task(
        DlpTaskCreateRequest(
            session_id=session_id,
            conversation_id=conversation_id,
            message=str(request_message or ""),
            request_message=str(mail_plan.get("request_message") or request_message or ""),
            review_content=str(mail_plan.get("review_content") or ""),
            resolved_outbound_content=str(mail_plan.get("resolved_body") or ""),
            resolved_source_kind=str(mail_plan.get("target_object") or ""),
            delivery_subject=str(mail_plan.get("resolved_subject") or ""),
            delivery_body=str(mail_plan.get("resolved_body") or ""),
            delivery_plan_kind=str(mail_plan.get("resolution_rule") or ""),
            attachment_strategy=str(attachment.get("attachment_strategy") or "none"),
            attachment_content=str(((mail_plan.get("attachment_candidate") or {}) if isinstance(mail_plan.get("attachment_candidate"), dict) else {}).get("content") or ""),
            attachment_filename=str(attachment.get("filename") or ""),
            attachment_content_type=str(attachment.get("content_type") or "text/plain"),
            attachment_blob_id=str(attachment.get("upload_blob_id") or ""),
            destination_email=str((mail_plan.get("resolved_recipients") or [""])[0] or ""),
            uploaded_filename=str(selected_candidate.get("filename") or attachment.get("filename") or ""),
            uploaded_content_type=str(selected_candidate.get("content_type") or attachment.get("content_type") or ""),
            uploaded_text=str(selected_candidate.get("content") or ""),
            requested_action=str(mail_plan.get("mail_action_type") or "send_message"),
        )
    )


def _build_scenario_task_payload(
    scenario: dict,
    payload: DlpScenarioReplayRequest,
) -> DlpTaskCreateRequest:
    chosen_faults = normalize_fault_injection(payload.fault_injection or scenario.get("fault_injection"))
    destination_email = payload.destination_email or str(scenario.get("destination_email") or DEFAULT_DLP_EMAIL)
    return DlpTaskCreateRequest(
        session_id=payload.session_id,
        conversation_id=payload.conversation_id,
        message=str(scenario.get("message", "")),
        destination_email=destination_email,
        uploaded_filename=str(scenario.get("uploaded_filename", "")),
        uploaded_content_type=str(scenario.get("uploaded_content_type", "")),
        uploaded_text=str(scenario.get("uploaded_text", "")),
        requested_action=str(scenario.get("requested_action", "summarize_and_send")),
        lab_run=True,
        scenario_id=str(scenario.get("scenario_id", "")),
        scenario_name=str(scenario.get("name", "")),
        fault_injection=chosen_faults,
        expected_outcome=dict(scenario.get("expected_outcome") or {}),
    )


def _create_dlp_workflow_from_payload(payload: SensitiveWorkflowCreateRequest) -> dict:
    result = run_sensitive_outbound_workflow(
        message=payload.message,
        business_context=payload.business_context,
        recipient_type=payload.recipient_type,
        destination_email=payload.destination_email,
        source_filename=payload.source_filename,
        source_content_type=payload.source_content_type,
        requested_action=payload.requested_action,
        context_budget=payload.context_budget,
    )
    workflow = create_sensitive_workflow(
        session_id=payload.session_id,
        conversation_id=payload.conversation_id,
        message=payload.message,
        redacted_text=str(result.get("redacted_text", "")),
        business_context=payload.business_context,
        recipient_type=payload.recipient_type,
        destination_email=payload.destination_email,
        source_filename=payload.source_filename,
        source_content_type=payload.source_content_type,
        source_text_preview=_source_preview(payload.message),
        requested_action=payload.requested_action,
        risk_level=str(result.get("risk_level", "low")),
        risk_reasons=result.get("risk_reasons", []),
        redactions=result.get("redactions", []),
        proposed_action=str(result.get("proposed_action", "")),
        final_result=str(result.get("final_result", "")),
        draft_summary=str(result.get("draft_summary", "")),
        simulated_delivery_result=str(result.get("simulated_delivery_result", "")),
        approval_required=bool(result.get("approval_required", False)),
        status=str(result.get("status", "completed")),
    )
    for event in result.get("audit_events", []):
        add_audit_event(str(workflow["workflow_id"]), str(event.get("event_type", "workflow_event")), "agent", event)
    record_workflow_created(str(workflow["risk_level"]), bool(workflow["approval_required"]))
    if not workflow["approval_required"]:
        workflow = send_sensitive_workflow_email(str(workflow["workflow_id"]), "agent") or workflow
        record_workflow_email_sent(str(workflow.get("status")) == "sent")
    return workflow


def _validate_problem(problem_id: str) -> None:
    if problem_id not in problem_lookup():
        raise HTTPException(status_code=404, detail="Unknown problem_id")


def _build_retrieval_preview(payload: dict) -> RetrievalPreview:
    preview = payload.get("retrieval_preview") or {"retrieval_hits": 0, "snippets": []}
    return RetrievalPreview(
        retrieval_hits=int(preview.get("retrieval_hits", 0)),
        snippets=[Citation(**snippet) for snippet in preview.get("snippets", [])],
    )


def _should_write_summary(answer: str) -> bool:
    text = (answer or "").strip()
    if not text:
        return False
    if "证据不足" in text:
        return False
    return True


def _persist_conversation_memory(result: dict) -> bool:
    final_answer = str(result.get("final_answer") or result.get("answer") or "")
    if not _should_write_summary(final_answer):
        return False

    try:
        session_id = str(result["session_id"])
        problem_id = str(result["problem_id"])
        problem = problem_lookup()[problem_id]
        citations = result.get("citations", [])

        turn_index = append_turn(
            session_id,
            problem_id,
            {
                "question": result["question"],
                "answer": final_answer,
                "draft_answer": result.get("draft_answer"),
                "reflection_notes": result.get("reflection_notes"),
                "final_answer": final_answer,
                "citations": citations,
                "query_type": result.get("query_type", ""),
            },
            max_turns=5,
        )

        summary_doc = build_conversation_summary_document(
            session_id=session_id,
            problem_id=problem_id,
            problem_title=problem.title,
            question=str(result["question"]),
            final_answer=final_answer,
            query_type=str(result.get("query_type", "")),
            turn_index=turn_index,
            citations=citations,
        )
        upsert_documents([summary_doc])
        return True
    except Exception as exc:  # pragma: no cover - best effort memory write
        logger.warning("Conversation memory write skipped: %s", exc)
        return False


def _build_chat_response(result: dict) -> ChatResponse:
    return ChatResponse(
        session_id=str(result["session_id"]),
        answer=str(result["final_answer"]),
        citations=[Citation(**citation) for citation in result.get("citations", [])],
        trace_id=str(result["request_id"]),
        latency_ms=float(result["node_latencies_ms"]["total"]),
        token_in=int(result["token_in"]),
        token_out=int(result["token_out"]),
        estimated_cost=float(result["estimated_cost"]),
        mode_used=result["mode_used"],
        query_type=str(result["query_type"]),
        draft_answer=result.get("draft_answer"),
        reflection_notes=result.get("reflection_notes"),
        final_answer=result.get("final_answer"),
        plan_steps=result.get("plan_steps"),
        retrieval_preview=_build_retrieval_preview(result),
        requires_confirmation=bool(result.get("requires_confirmation", False)),
        memory_hits=int(result.get("memory_hits", 0)),
        used_conversation_memory=bool(result.get("used_conversation_memory", False)),
        conversation_summary_written=bool(result.get("conversation_summary_written", False)),
    )


def _record_success_metrics(result: dict) -> None:
    record_request(
        mode=str(result["mode_used"]),
        latency_ms=float(result["node_latencies_ms"]["total"]),
        token_in=int(result["token_in"]),
        token_out=int(result["token_out"]),
        estimated_cost=float(result["estimated_cost"]),
        retrieval_hits=int(result["retrieval_hits"]),
        node_latencies_ms=result.get("node_latencies_ms"),
        memory_hits=int(result.get("memory_hits", 0)),
        used_conversation_memory=bool(result.get("used_conversation_memory", False)),
        conversation_summary_written=bool(result.get("conversation_summary_written", False)),
    )


def _ensure_conversation(session_id: str, conversation_id: str | None) -> tuple[dict, bool]:
    if conversation_id:
        existing = get_conversation(conversation_id)
        if existing:
            if existing["session_id"] != session_id:
                raise HTTPException(status_code=403, detail="conversation_id does not belong to this session_id")
            return existing, False
    created = create_conversation(session_id)
    record_conversation_created()
    return created, True


def _build_debug_snapshot(result: dict, conversation_id: str) -> dict:
    answer_text = str(result.get("answer", ""))
    return {
        "conversation_id": conversation_id,
        "intent": str(result.get("intent", "unknown")),
        "routing_source": str(result.get("routing_source", "unknown")),
        "routing_confidence": float(result.get("routing_confidence", 0.0)),
        "routing_reason": str(result.get("routing_reason", "")),
        "candidate_intents": result.get("candidate_intents", []),
        "tool_calls": result.get("tool_calls", []),
        "retrieved_evidence": result.get("retrieved_evidence", []),
        "needs_clarification": bool(result.get("needs_clarification", False)),
        "clarification_question": result.get("clarification_question"),
        "privacy": result.get("privacy", {}) or {},
        "context_budget": result.get("context_budget", {}) or {},
        "memory_hits": int(result.get("memory_hits", 0)),
        "merged_memory_hits": int(result.get("merged_memory_hits", 0)),
        "memory_context": result.get("memory_context", {}) or {},
        "context_sources": result.get("context_sources", []) or [],
        "workspace_memory_hits": int(result.get("workspace_memory_hits", 0)),
        "transcript_hits": int(result.get("transcript_hits", 0)),
        "user_model_used": bool(result.get("user_model_used", False)),
        "upload_context": result.get("upload_context", {}) or {},
        "route_mode": str(result.get("route_mode", "")),
        "router_intent": str(result.get("router_intent", "")),
        "required_grounding": str(result.get("required_grounding", "")),
        "fast_path_used": bool(result.get("fast_path_used", False)),
        "degraded_from": str(result.get("degraded_from", "none")),
        "reflection_notes": result.get("reflection_notes"),
        "planner_type": str(result.get("planner_type", "")),
        "task_plan": result.get("task_plan", {}) or {},
        "subtask_results": result.get("subtask_results", []) or [],
        "aggregation_strategy": str(result.get("aggregation_strategy", "")),
        "partial_failures": result.get("partial_failures", []) or [],
        "react_trace": result.get("react_trace", []) or [],
        "loop_step_count": int(result.get("loop_step_count", 0)),
        "termination_reason": str(result.get("termination_reason", "")),
        "pending_confirmation": result.get("pending_confirmation", {}) or {},
        "confirmation_payload": result.get("confirmation_payload", {}) or {},
        "final_answer_source": str(result.get("final_answer_source", "")),
        "memory_reads": result.get("memory_reads", []) or [],
        "tool_observations": result.get("tool_observations", []) or [],
        "latency_ms": float(result.get("node_latencies_ms", {}).get("total", 0.0)),
        "token_in": int(result.get("token_in", 0)),
        "token_out": int(result.get("token_out", 0)),
        "estimated_cost": float(result.get("estimated_cost", 0.0)),
        "answer_collapsed": len(answer_text) > 1200 or answer_text.count("\n") > 12,
    }


def _write_unified_conversation_memory(result: dict, conversation_id: str) -> tuple[str | None, bool]:
    answer = str(result.get("answer", "")).strip()
    if not answer:
        return None, False

    question = str(result.get("display_message") or result.get("message", ""))
    safe_question = str(result.get("display_message") or result.get("safe_message") or question)
    answer_summary = compact_text(answer, 1200)
    debug_payload = _build_debug_snapshot(result, conversation_id)
    exchange = append_exchange(
        session_id=str(result["session_id"]),
        conversation_id=conversation_id,
        question=question,
        answer=answer,
        redacted_question=safe_question,
        answer_summary=answer_summary,
        intent=str(result.get("intent", "")),
        tool_calls=result.get("tool_calls", []),
        citations=result.get("citations", []),
        debug_payload=debug_payload,
    )
    record_conversation_turns()

    user_turn = exchange["user_turn"]
    assistant_turn = exchange["assistant_turn"]
    written = False
    dynamic_memory_result: dict[str, Any] = {}
    try:
        dynamic_memory_result = write_dynamic_turn_memory(
            result=result,
            conversation_id=conversation_id,
            user_turn_id=str(user_turn["turn_id"]),
            assistant_turn_id=str(assistant_turn["turn_id"]),
            question=question,
            answer=answer,
        )
    except Exception as exc:  # pragma: no cover - best effort dynamic memory write
        logger.warning("Hermes dynamic memory write skipped: %s", exc)
    try:
        dynamic_identifiers = dict(dynamic_memory_result.get("identifiers") or {})
        written = write_turn_summary(
            session_id=str(result["session_id"]),
            conversation_id=conversation_id,
            user_turn_id=str(user_turn["turn_id"]),
            assistant_turn_id=str(assistant_turn["turn_id"]),
            question=question,
            safe_question=safe_question,
            answer=answer,
            answer_summary=answer_summary,
            intent=str(result.get("intent", "")),
            citations=result.get("citations", []),
            upload_context=result.get("upload_context", {}) or {},
            dynamic_memory={
                "summary": str(dynamic_memory_result.get("summary") or ""),
                "intent": str(dynamic_memory_result.get("intent") or result.get("intent") or ""),
                "entities": dynamic_memory_result.get("entities") or [],
                "files_uploaded": dynamic_memory_result.get("files_uploaded") or [],
                "risk_level": str(dynamic_memory_result.get("risk_level") or "low"),
                "user_goal": question,
                "outcome": answer_summary,
                "failure_reason": _summarize_memory_failure_for_doc(result),
                "memory_scope": str(dynamic_memory_result.get("memory_scope") or "session"),
                "key_files": dynamic_identifiers.get("files") or [],
                "recipients": dynamic_identifiers.get("emails") or [],
                "task_ids": dynamic_identifiers.get("task_ids") or [],
            },
        )
        if written:
            record_turn_summary_write()
    except Exception as exc:  # pragma: no cover - best effort memory write
        logger.warning("Unified turn memory write skipped: %s", exc)

    conversation = get_conversation(conversation_id)
    if conversation:
        turn_count = len(get_turns(conversation_id))
        if not conversation.get("is_merged") and turn_count <= 2:
            update_conversation_summary(conversation_id, answer_summary, title=infer_title(question))
        elif not conversation.get("summary"):
            update_conversation_summary(conversation_id, answer_summary)
    return str(assistant_turn["turn_id"]), written


def _summarize_memory_failure_for_doc(result: dict[str, Any]) -> str:
    partial_failures = list(result.get("partial_failures") or [])
    if partial_failures:
        first = dict(partial_failures[0] or {})
        return compact_text(f"{first.get('tool_name', 'tool')}: {first.get('error', 'unknown error')}", 260)
    termination = str(result.get("termination_reason") or "")
    if termination in {"budget_exhausted", "fatal_tool_failure", "abort_with_reason"}:
        return termination
    return ""


def _parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("No JSON object found")


def _merge_with_llm(conversation_ids: list[str], turns: list[dict]) -> dict:
    source_text = "\n\n".join(
        [
            f"turn_id={turn['turn_id']} role={turn['role']} content={compact_text(turn.get('redacted_content') or turn.get('content', ''), 700)}"
            for turn in turns
        ]
    )
    prompt = """Merge multiple conversations into structured memory. Return JSON only.
Use Chinese for every string value. Keep the summary concise but traceable.
{
  "summary": "...",
  "topics": ["..."],
  "decisions": ["..."],
  "open_questions": ["..."],
  "important_context": ["..."]
}
"""
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(content=f"conversation_ids={conversation_ids}\n\n{source_text}"),
            ]
        )
        parsed = _parse_json_object(str(response.content))
    except Exception:
        parsed = {
            "summary": compact_text(source_text, 1600),
            "topics": ["conversation_merge"],
            "decisions": [],
            "open_questions": [],
            "important_context": [],
        }
    return {
        "summary": str(parsed.get("summary", "")),
        "topics": [str(item) for item in parsed.get("topics", [])],
        "decisions": [str(item) for item in parsed.get("decisions", [])],
        "open_questions": [str(item) for item in parsed.get("open_questions", [])],
        "important_context": [str(item) for item in parsed.get("important_context", [])],
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    chroma_ok = True
    try:
        count_collection()
    except Exception:
        chroma_ok = False
    return HealthResponse(
        status="ok" if chroma_ok else "degraded",
        chroma_ok=chroma_ok,
        problems_loaded=len(list_problem_summaries()),
        langsmith_enabled=settings.langsmith_enabled,
    )


@app.get("/problems", response_model=list[ProblemSummary])
def problems() -> list[ProblemSummary]:
    return list_problem_summaries()


@app.get("/conversations", response_model=list[ConversationSummary])
def conversations(session_id: str) -> list[ConversationSummary]:
    return [ConversationSummary(**item) for item in list_conversations(session_id)]


@app.post("/conversations", response_model=ConversationSummary)
def create_conversation_api(payload: ConversationCreateRequest) -> ConversationSummary:
    conversation = create_conversation(payload.session_id, payload.title)
    record_conversation_created()
    return ConversationSummary(**conversation)


@app.post("/conversations/merge", response_model=ConversationMergeResponse)
def merge_conversations_api(payload: ConversationMergeRequest) -> ConversationMergeResponse:
    source_conversations = []
    for conversation_id in payload.conversation_ids:
        conversation = get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail=f"Unknown conversation_id: {conversation_id}")
        if conversation["session_id"] != payload.session_id:
            raise HTTPException(status_code=403, detail="All conversations must belong to the same session_id")
        source_conversations.append(conversation)

    turns: list[dict] = []
    for conversation in source_conversations:
        turns.extend(get_turns(conversation["conversation_id"]))
    if not turns:
        raise HTTPException(status_code=400, detail="Selected conversations have no turns to merge")

    source_turn_ids = [str(turn["turn_id"]) for turn in turns]
    merged = _merge_with_llm(payload.conversation_ids, turns)
    merged_title = payload.merge_name or infer_title(merged["summary"], fallback="合并对话")
    merged_conversation = create_conversation(
        payload.session_id,
        merged_title,
        is_merged=True,
        source_conversation_ids=payload.conversation_ids,
    )
    merged_id = str(merged_conversation["conversation_id"])
    update_conversation_summary(merged_id, merged["summary"], title=merged_title)
    save_merge(
        merged_conversation_id=merged_id,
        source_conversation_ids=payload.conversation_ids,
        merge_summary=merged["summary"],
        topics=merged["topics"],
        decisions=merged["decisions"],
        open_questions=merged["open_questions"],
        source_turn_ids=source_turn_ids,
    )
    record_conversation_created()
    record_conversation_merge()

    written = False
    try:
        written = write_merged_summary(
            session_id=payload.session_id,
            merged_conversation_id=merged_id,
            source_conversation_ids=payload.conversation_ids,
            source_turn_ids=source_turn_ids,
            summary=merged["summary"],
            topics=merged["topics"],
        )
        if written:
            record_merged_summary_write()
    except Exception as exc:  # pragma: no cover - best effort memory write
        logger.warning("Merged memory write skipped: %s", exc)

    return ConversationMergeResponse(
        merged_conversation_id=merged_id,
        summary=merged["summary"],
        topics=merged["topics"],
        decisions=merged["decisions"],
        open_questions=merged["open_questions"],
        source_conversations=payload.conversation_ids,
        source_turn_ids=source_turn_ids,
        written_to_vectorstore=written,
    )


@app.get("/conversations/{conversation_id}", response_model=ConversationSummary)
def get_conversation_api(conversation_id: str) -> ConversationSummary:
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Unknown conversation_id")
    return ConversationSummary(**conversation)


@app.delete("/conversations/{conversation_id}")
def delete_conversation_api(conversation_id: str, session_id: str) -> dict:
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Unknown conversation_id")
    if conversation["session_id"] != session_id:
        raise HTTPException(status_code=403, detail="conversation_id does not belong to this session_id")

    deleted = delete_conversation(conversation_id)
    return {
        "deleted": bool(deleted),
        "conversation_id": conversation_id,
        "title": (deleted or {}).get("title", ""),
    }


@app.get("/conversations/{conversation_id}/turns", response_model=list[ConversationTurn])
def get_conversation_turns_api(conversation_id: str) -> list[ConversationTurn]:
    if not get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Unknown conversation_id")
    return [ConversationTurn(**turn) for turn in get_turns(conversation_id)]


@app.get("/conversations/{conversation_id}/summary")
def get_conversation_summary_api(conversation_id: str) -> dict:
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Unknown conversation_id")
    merge = get_merge(conversation_id)
    return {"conversation": conversation, "merge": merge}


@app.get("/tasks", response_model=list[DlpTaskResponse])
def list_dlp_tasks_api(
    session_id: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
) -> list[DlpTaskResponse]:
    return [_task_response(item) for item in list_dlp_tasks(session_id=session_id, status=status, risk_level=risk_level)]


@app.post("/mail/inbound/sync", response_model=InboundMailSyncResponse)
def sync_inbound_mail_api(payload: InboundMailSyncRequest | None = None) -> InboundMailSyncResponse:
    request = payload or InboundMailSyncRequest()
    since = _parse_optional_datetime(request.since, "since")
    until = _parse_optional_datetime(request.until, "until")
    return InboundMailSyncResponse(**sync_inbound_mail(since=since, until=until, limit=request.limit))


@app.post("/mail/inbound/sync/async")
def enqueue_inbound_mail_sync_api() -> dict[str, str]:
    return {"task_id": enqueue_inbound_mail_sync(), "queue": MAIL_QUEUE}


@app.post("/mail/inbound/digest")
def generate_daily_mail_digest_api() -> dict[str, Any]:
    return generate_daily_mail_digest()


@app.post("/mail/inbound/digest/async")
def enqueue_daily_mail_digest_api() -> dict[str, str]:
    return {"task_id": enqueue_daily_mail_digest(), "queue": MAIL_QUEUE}


@app.get("/mail/inbound/summary", response_model=InboundMailSummaryResponse)
def inbound_mail_summary_api(since: str | None = None, until: str | None = None) -> InboundMailSummaryResponse:
    return InboundMailSummaryResponse(**get_inbound_mail_summary(since, until))


@app.get("/mail/outbound/summary", response_model=OutboundMailSummaryResponse)
def outbound_mail_summary_api(
    since: str | None = None,
    until: str | None = None,
    session_id: str | None = None,
) -> OutboundMailSummaryResponse:
    window_since, window_until = since, until
    if not window_since or not window_until:
        window_since, window_until = _local_day_window()
    summary = get_sent_mail_stats(since=window_since, until=window_until, session_id=session_id)
    return OutboundMailSummaryResponse(since=window_since, until=window_until, **summary)


@app.post("/enterprise-rag/query", response_model=EnterpriseRagQueryResponse)
def enterprise_rag_query_api(payload: EnterpriseRagQueryRequest) -> EnterpriseRagQueryResponse:
    return EnterpriseRagQueryResponse(
        **answer_enterprise_question(
            payload.question,
            source_types=payload.source_types,
            top_k=payload.top_k,
            session_id=payload.session_id,
            conversation_id=payload.conversation_id,
        )
    )


@app.post("/enterprise-rag/ingest", response_model=EnterpriseRagIngestResponse)
def enterprise_rag_ingest_api(payload: EnterpriseRagIngestRequest) -> EnterpriseRagIngestResponse:
    try:
        return EnterpriseRagIngestResponse(
            **ingest_enterprise_rag_bench(
                mode=payload.mode,
                documents_path=payload.documents_path,
                questions_path=payload.questions_path,
                limit=payload.limit,
                reset=payload.reset,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/enterprise-rag/casebook")
def enterprise_rag_casebook_api(questions_path: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    try:
        return build_casebook(questions_path=questions_path, limit=max(1, min(limit, 200)))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/enterprise-rag/benchmark", response_model=EnterpriseRagBenchmarkResponse)
def enterprise_rag_benchmark_api(
    questions_path: str | None = None,
    limit: int = 20,
    top_k: int = 8,
) -> EnterpriseRagBenchmarkResponse:
    try:
        return EnterpriseRagBenchmarkResponse(
            **run_benchmark_sample(
                questions_path=questions_path,
                limit=max(1, min(limit, 100)),
                top_k=max(1, min(top_k, 30)),
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/mail/inbound/messages", response_model=list[InboundMailMessage])
def inbound_mail_messages_api(
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[InboundMailMessage]:
    bounded_limit = max(1, min(limit, 200))
    return [
        InboundMailMessage(**item)
        for item in list_inbound_mail_messages(since=since, until=until, limit=bounded_limit, offset=max(0, offset))
    ]


@app.post("/mail/inbound/{message_id}/draft-reply", response_model=InboundDraftReplyResponse)
def draft_inbound_mail_reply_api(message_id: str) -> InboundDraftReplyResponse:
    try:
        return InboundDraftReplyResponse(**draft_reply_for_message(message_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown message_id") from exc


@app.get("/notifications/outbox", response_model=list[NotificationOutboxItem])
def notification_outbox_api(
    status: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[NotificationOutboxItem]:
    return [
        NotificationOutboxItem(**item)
        for item in list_notifications(status=status, event_type=event_type, limit=max(1, min(limit, 200)))
    ]


@app.get("/labs/dlp/scenarios", response_model=list[DlpScenarioDefinition])
def list_dlp_scenarios_api() -> list[DlpScenarioDefinition]:
    return [DlpScenarioDefinition(**item) for item in list_dlp_scenarios()]


@app.post("/labs/dlp/scenarios/{scenario_id}/replay", response_model=DlpTaskResponse)
def replay_dlp_scenario_api(scenario_id: str, payload: DlpScenarioReplayRequest) -> DlpTaskResponse:
    conversation = get_conversation(payload.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Unknown conversation_id")
    if conversation["session_id"] != payload.session_id:
        raise HTTPException(status_code=403, detail="conversation_id does not belong to this session_id")

    scenario = get_dlp_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="Unknown scenario_id")

    task = _create_async_dlp_task(_build_scenario_task_payload(scenario, payload))
    return _task_response(task)


@app.post("/tasks/{task_id}/supplement", response_model=DlpTaskResponse)
def supplement_dlp_task_api(task_id: str, payload: DlpTaskSupplementRequest) -> DlpTaskResponse:
    conversation = get_conversation(payload.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Unknown conversation_id")
    if conversation["session_id"] != payload.session_id:
        raise HTTPException(status_code=403, detail="conversation_id does not belong to this session_id")
    task = get_dlp_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    if str(task.get("conversation_id", "")) != payload.conversation_id:
        raise HTTPException(status_code=403, detail="task_id does not belong to this conversation_id")
    if str(task.get("status", "")) not in {"needs_clarification", "input_invalid"}:
        raise HTTPException(status_code=409, detail="Only governance tasks can be supplemented")
    task = _supplement_dlp_task(task, payload)
    return _task_response(task)


@app.get("/tasks/{task_id}", response_model=DlpTaskResponse)
def get_dlp_task_api(task_id: str) -> DlpTaskResponse:
    task = get_dlp_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    return _task_response(task)


@app.post("/tasks/{task_id}/approve", response_model=DlpTaskResponse)
def approve_dlp_task_api(task_id: str, payload: DlpTaskApprovalRequest) -> DlpTaskResponse:
    task = approve_task(task_id, payload.actor)
    if not task:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    if str(task["status"]) == "approved":
        publish_task_event(
            build_task_event(
                task_id=task_id,
                status=str(task["status"]),
                event_type="approved",
                message="Task approved and queued for outbound email sending.",
                risk_level=str(task.get("risk_level", "")),
                delivery_status=str(task.get("delivery_status", "not_sent")),
            )
        )
        enqueue_email_send_task(task_id)
    return _task_response(task)


@app.post("/tasks/{task_id}/reject", response_model=DlpTaskResponse)
def reject_dlp_task_api(task_id: str, payload: DlpTaskApprovalRequest) -> DlpTaskResponse:
    task = reject_task(task_id, payload.actor, payload.reason)
    if not task:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    publish_task_event(
        build_task_event(
            task_id=task_id,
            status=str(task["status"]),
            event_type="rejected",
            message="Task rejected and terminated.",
            risk_level=str(task.get("risk_level", "")),
            delivery_status=str(task.get("delivery_status", "rejected")),
        )
    )
    return _task_response(task)


@app.websocket("/ws/tasks/{task_id}")
async def task_status_stream(task_id: str, websocket: WebSocket) -> None:
    task = get_dlp_task(task_id)
    if not task:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    await websocket.send_json(
        build_task_event(
            task_id=task_id,
            status=str(task["status"]),
            event_type="snapshot",
            message="Current task snapshot.",
            risk_level=str(task.get("risk_level", "")),
            delivery_error=str(task.get("delivery_error", "")),
            delivery_status=str(task.get("delivery_status", "")),
        )
    )
    try:
        async for event in task_event_iterator(task_id):
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        with suppress(Exception):
            await websocket.close(code=1011)


@app.get("/workflows/sensitive-outbound", response_model=list[SensitiveWorkflowResponse])
def list_sensitive_workflows_api(
    session_id: str | None = None,
    status: str | None = None,
) -> list[SensitiveWorkflowResponse]:
    return [_workflow_response(item) for item in list_sensitive_workflows(session_id=session_id, status=status)]


@app.post("/workflows/sensitive-outbound", response_model=SensitiveWorkflowResponse)
def create_sensitive_workflow_api(payload: SensitiveWorkflowCreateRequest) -> SensitiveWorkflowResponse:
    if payload.conversation_id:
        conversation = get_conversation(payload.conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Unknown conversation_id")
        if conversation["session_id"] != payload.session_id:
            raise HTTPException(status_code=403, detail="conversation_id does not belong to this session_id")

    workflow = _create_dlp_workflow_from_payload(payload)
    return _workflow_response(workflow)


@app.get("/workflows/sensitive-outbound/{workflow_id}", response_model=SensitiveWorkflowResponse)
def get_sensitive_workflow_api(workflow_id: str) -> SensitiveWorkflowResponse:
    workflow = get_sensitive_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Unknown workflow_id")
    return _workflow_response(workflow)


@app.post("/workflows/sensitive-outbound/{workflow_id}/approve", response_model=SensitiveWorkflowResponse)
def approve_sensitive_workflow_api(
    workflow_id: str,
    payload: SensitiveWorkflowApprovalRequest,
) -> SensitiveWorkflowResponse:
    workflow = approve_sensitive_workflow(workflow_id, payload.actor)
    if not workflow:
        raise HTTPException(status_code=404, detail="Unknown workflow_id")
    record_workflow_approved()
    record_workflow_email_sent(str(workflow.get("status")) == "sent")
    return _workflow_response(workflow)


@app.post("/workflows/sensitive-outbound/{workflow_id}/reject", response_model=SensitiveWorkflowResponse)
def reject_sensitive_workflow_api(
    workflow_id: str,
    payload: SensitiveWorkflowApprovalRequest,
) -> SensitiveWorkflowResponse:
    workflow = reject_sensitive_workflow(workflow_id, payload.actor, payload.reason)
    if not workflow:
        raise HTTPException(status_code=404, detail="Unknown workflow_id")
    record_workflow_rejected()
    return _workflow_response(workflow)


@app.post("/ingest", response_model=IngestResponse)
def ingest(force: bool = False) -> IngestResponse:
    written = ingest_if_needed(force=force)
    settings = get_settings()
    return IngestResponse(documents_written=written, collection_name=settings.chroma_collection)


@app.post("/plan", response_model=PlanResponse)
def plan(payload: PlanRequest) -> PlanResponse:
    _validate_problem(payload.problem_id)

    try:
        result = preview_plan(payload.session_id, payload.problem_id, payload.question, payload.mode)
    except Exception as exc:
        record_failure()
        logger.exception("Plan preview failed")
        raise HTTPException(status_code=500, detail=f"Plan preview failed: {exc}") from exc

    save_plan(
        str(result["request_id"]),
        {
            "session_id": payload.session_id,
            "problem_id": payload.problem_id,
            "question": payload.question,
            "mode": result["mode_used"],
            "plan_steps": result.get("plan_steps", ""),
        },
    )

    return PlanResponse(
        session_id=payload.session_id,
        trace_id=str(result["request_id"]),
        mode_suggested=result["mode_used"],
        query_type=str(result["query_type"]),
        retrieval_preview=_build_retrieval_preview(result),
        plan_steps=str(result.get("plan_steps", "")),
        requires_confirmation=bool(result.get("requires_confirmation", False)),
        memory_hits=int(result.get("memory_hits", 0)),
        used_conversation_memory=bool(result.get("used_conversation_memory", False)),
    )


@app.post("/execute", response_model=ChatResponse)
def execute(payload: ExecuteRequest) -> ChatResponse:
    stored = load_plan(payload.request_id)
    if stored is None:
        record_failure()
        raise HTTPException(status_code=404, detail="Unknown or expired request_id")
    if stored["session_id"] != payload.session_id:
        record_failure()
        raise HTTPException(status_code=403, detail="session_id does not match the planned request")
    if not payload.approved_plan:
        delete_plan(payload.request_id)
        raise HTTPException(status_code=400, detail="Plan was not approved")

    try:
        result = execute_confirmed_plan(
            stored["session_id"],
            stored["problem_id"],
            stored["question"],
            stored["mode"],
            plan_steps=stored.get("plan_steps"),
            request_id=payload.request_id,
        )
        result["conversation_summary_written"] = _persist_conversation_memory(result)
    except Exception as exc:
        record_failure()
        logger.exception("Execution failed")
        raise HTTPException(status_code=500, detail=f"Execution failed: {exc}") from exc
    finally:
        delete_plan(payload.request_id)

    _record_success_metrics(result)
    return _build_chat_response(result)


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    _validate_problem(payload.problem_id)

    if payload.mode == "plan_execute":
        raise HTTPException(status_code=409, detail="plan_execute requires POST /plan followed by POST /execute")

    try:
        result = run_agent(payload.session_id, payload.problem_id, payload.question, payload.mode)
        result["conversation_summary_written"] = _persist_conversation_memory(result)
    except Exception as exc:
        record_failure()
        logger.exception("Chat request failed")
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {exc}") from exc

    _record_success_metrics(result)
    return _build_chat_response(result)


@app.post("/agent/chat", response_model=UnifiedAgentResponse)
def agent_chat(payload: UnifiedAgentRequest) -> UnifiedAgentResponse:
    if payload.problem_id:
        _validate_problem(payload.problem_id)

    conversation, _ = _ensure_conversation(payload.session_id, payload.conversation_id)
    conversation_id = str(conversation["conversation_id"])

    outbound_message = _build_outbound_message(payload.message, payload.uploaded_text, payload.uploaded_filename)
    display_message = _build_display_message(payload.message, payload.uploaded_filename, payload.source_parse_status)
    upload_context, recalled_upload = _resolve_upload_context(payload, conversation_id)
    if recalled_upload and upload_context.get("filename"):
        display_message = _build_display_message(
            payload.message,
            f"{upload_context.get('filename')} (沿用上次上传)",
            str(upload_context.get("parse_status") or "parsed"),
        )
    if looks_like_send_confirmation(payload.message):
        pending_confirmation = _get_latest_pending_mail_confirmation(conversation_id)
        pending_mail_plan = dict(pending_confirmation.get("mail_plan") or {})
        if pending_mail_plan:
            task = _create_dlp_task_from_mail_plan(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                request_message=str(pending_mail_plan.get("request_message") or payload.message),
                mail_plan=pending_mail_plan,
            )
            return _build_task_agent_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                outbound_message=str(pending_mail_plan.get("resolved_body") or payload.message),
                display_message=display_message,
                task=task,
                routing_reason="Confirmed a pending mail plan and created a governed DLP task.",
            )
    recoverable_task = get_latest_recoverable_task(payload.session_id, conversation_id)
    if _should_apply_recoverable_supplement(recoverable_task, payload, conversation_id):
        supplemented = _supplement_dlp_task(
            recoverable_task,
            DlpTaskSupplementRequest(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                destination_email="",
                uploaded_filename=payload.uploaded_filename,
                uploaded_content_type=payload.uploaded_content_type,
                uploaded_text=payload.uploaded_text,
                source_parse_status=payload.source_parse_status,
                source_parse_error=payload.source_parse_error,
            ),
        )
        return _build_task_agent_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            outbound_message=outbound_message or str(supplemented.get("message_raw", "")),
            display_message=display_message,
            task=supplemented,
            routing_reason="Applied supplemental information to a governed DLP task.",
        )
    if looks_like_pending_draft_edit_request(payload.message):
        pending_mail_plan = _get_latest_pending_mail_draft(conversation_id)
        if pending_mail_plan:
            patched_mail_plan = patch_pending_mail_plan(
                message=payload.message,
                request_message=payload.message,
                mail_plan=pending_mail_plan,
            )
            if patched_mail_plan.get("needs_clarification"):
                return _build_mail_clarification_response(
                    session_id=payload.session_id,
                    conversation_id=conversation_id,
                    message=payload.message,
                    display_message=display_message,
                    mail_plan=dict(patched_mail_plan.get("mail_plan") or {}),
                    candidates=[],
                )
            if patched_mail_plan.get("ok"):
                return _build_mail_patch_response(
                    session_id=payload.session_id,
                    conversation_id=conversation_id,
                    message=payload.message,
                    display_message=display_message,
                    mail_plan=dict(patched_mail_plan.get("mail_plan") or {}),
                    patch_kind=str(patched_mail_plan.get("patch_kind") or "edit_pending_draft"),
                )
        return _build_mail_clarification_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            mail_plan={"missing_fields": ["pending_mail_draft"], "mail_action_type": "edit_pending_draft"},
            candidates=[],
        )
    if looks_like_mail_action_request(payload.message) and not (
        _looks_like_outbound_mail_summary_query(payload.message) or _looks_like_inbound_mail_query(payload.message)
    ):
        mail_action_plan = _build_mail_action_plan(payload, conversation_id, dict(upload_context or {}))
        if mail_action_plan.get("needs_clarification"):
            return _build_mail_clarification_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
                candidates=list(mail_action_plan.get("candidates") or []),
            )
        if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "unsupported":
            return _build_mail_unsupported_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
            )
        if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "draft_only":
            return _build_mail_draft_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
            )
        if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "confirmation_required":
            return _build_mail_confirmation_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
            )
    upload_decision = classify_upload_request(message=payload.message, upload_context=upload_context)
    if upload_decision.route == "outbound":
        return _handle_async_outbound_agent_request(payload, conversation_id, outbound_message, display_message, upload_context)
    if (
        upload_decision.route in {"none", "analyze"}
        and not (_looks_like_outbound_mail_summary_query(payload.message) or _looks_like_inbound_mail_query(payload.message))
        and (_looks_like_outbound_action(payload.message) or _looks_like_contextual_outbound_request(payload.message))
    ):
        return _handle_async_outbound_agent_request(payload, conversation_id, outbound_message, display_message, upload_context)

    compound_response = _handle_compound_agent_request(payload, conversation_id)
    if compound_response is not None:
        return compound_response

    route_decision = route_agent_request(
        message=payload.message,
        safe_message=payload.message,
        upload_context=upload_context,
    )
    if str(route_decision.get("route_mode") or "slow") == "fast":
        try:
            return _execute_fast_path(
                payload=payload,
                conversation_id=conversation_id,
                display_message=display_message,
                upload_context=upload_context,
                route_decision=route_decision,
            )
        except Exception:
            logger.exception("Fast path execution failed; degrading to slow path")
            route_decision = {**route_decision, "route_mode": "slow", "degraded_from": "router"}
    else:
        route_decision = {**route_decision, "route_mode": "slow"}

    try:
        result = orchestrate_agent_request(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            safe_message=payload.message,
            display_message=display_message,
            upload_context=upload_context,
            router_intent=str(route_decision.get("intent") or ""),
            required_grounding=str(route_decision.get("required_grounding") or "none"),
            recommended_tool=str(route_decision.get("recommended_tool") or ""),
            router_reason=str(route_decision.get("router_reason") or ""),
            degraded_from=str(route_decision.get("degraded_from") or "none"),
        )
    except Exception:
        logger.exception("Orchestration request failed; falling back to unified agent")
        record_failure()
        fallback_intent = str(route_decision.get("intent") or "")
        if fallback_intent in {"upload_analysis", "contextual_memory", "mail_status", "enterprise_fact", "persona"}:
            try:
                fallback_route_decision = {
                    **route_decision,
                    "route_mode": "fast",
                    "degraded_from": "react_think",
                }
                return _execute_fast_path(
                    payload=payload,
                    conversation_id=conversation_id,
                    display_message=display_message,
                    upload_context=upload_context,
                    route_decision=fallback_route_decision,
                )
            except Exception:
                logger.exception("Safe fast fallback failed after orchestration failure")
        return _build_rule_clarification_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            clarification_question="当前规划阶段失败了。为了避免直接返回错误，你可以把问题再聚焦一点，或让我先基于当前内容给出保守回答。",
            routing_reason="The orchestration path failed before a grounded answer could be safely produced.",
        )

    turn_id, memory_written = _write_unified_conversation_memory(result, conversation_id)
    latency_ms = float(result["node_latencies_ms"]["total"])
    context_budget = result.get("context_budget", {}) or {}
    used_budget = context_budget.get("used") or context_budget.get("packed_tokens")
    tool_calls = result.get("tool_calls", [])
    privacy = result.get("privacy", {}) or {}

    record_request(
        mode=str(result.get("mode_used", payload.mode)),
        latency_ms=latency_ms,
        token_in=int(result.get("token_in", 0)),
        token_out=int(result.get("token_out", 0)),
        estimated_cost=float(result.get("estimated_cost", 0.0)),
        retrieval_hits=len(result.get("citations", [])),
        node_latencies_ms=result.get("node_latencies_ms"),
    )
    record_unified_agent(
        intent=str(result.get("intent", "unknown")),
        tool_calls=tool_calls,
        needs_clarification=bool(result.get("needs_clarification", False)),
        privacy_guardrail=bool(privacy.get("redacted")),
        context_budget_used=int(used_budget) if used_budget is not None else None,
    )
    record_router(str(result.get("routing_source", "unknown")), float(result.get("routing_confidence", 0.0)))
    record_unified_evidence_hits(result.get("retrieved_evidence", []))
    record_memory_retrieval_hits(int(result.get("memory_hits", 0)), int(result.get("merged_memory_hits", 0)))
    answer_text = str(result.get("answer", ""))
    answer_collapsed = len(answer_text) > 1200 or answer_text.count("\n") > 12
    if answer_collapsed:
        record_answer_collapse()

    return UnifiedAgentResponse(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=answer_text,
        intent=str(result.get("intent", "unknown")),
        routing_source=str(result.get("routing_source", "unknown")),
        routing_confidence=float(result.get("routing_confidence", 0.0)),
        routing_reason=str(result.get("routing_reason", "")),
        candidate_intents=result.get("candidate_intents", []),
        mode_used=str(result.get("mode_used", payload.mode)),
        tool_calls=tool_calls,
        retrieved_evidence=result.get("retrieved_evidence", []),
        needs_clarification=bool(result.get("needs_clarification", False)),
        clarification_question=result.get("clarification_question"),
        privacy=privacy,
        context_budget=context_budget,
        citations=result.get("citations", []),
        memory_written=memory_written,
        memory_hits=int(result.get("memory_hits", 0)),
        merged_memory_hits=int(result.get("merged_memory_hits", 0)),
        memory_context=result.get("memory_context", {}),
        context_sources=result.get("context_sources", []) or [],
        workspace_memory_hits=int(result.get("workspace_memory_hits", 0)),
        transcript_hits=int(result.get("transcript_hits", 0)),
        user_model_used=bool(result.get("user_model_used", False)),
        answer_collapsed=answer_collapsed,
        reflection_notes=result.get("reflection_notes"),
        upload_context=result.get("upload_context", {}) or {},
        route_mode=str(result.get("route_mode", "slow")),
        router_intent=str(result.get("router_intent", route_decision.get("intent") or "")),
        router_reason=str(result.get("router_reason", route_decision.get("router_reason") or "")),
        required_grounding=str(result.get("required_grounding", route_decision.get("required_grounding") or "none")),
        fast_path_used=bool(result.get("fast_path_used", False)),
        degraded_from=str(result.get("degraded_from", route_decision.get("degraded_from") or "none")),
        planner_type=str(result.get("planner_type", "")),
        task_plan=result.get("task_plan", {}) or {},
        subtask_results=result.get("subtask_results", []) or [],
        aggregation_strategy=str(result.get("aggregation_strategy", "")),
        partial_failures=result.get("partial_failures", []) or [],
        react_trace=result.get("react_trace", []) or [],
        loop_step_count=int(result.get("loop_step_count", 0)),
        termination_reason=str(result.get("termination_reason", "")),
        pending_confirmation=result.get("pending_confirmation", {}) or {},
        confirmation_payload=result.get("confirmation_payload", {}) or {},
        final_answer_source=str(result.get("final_answer_source", "")),
        memory_reads=result.get("memory_reads", []) or [],
        tool_observations=result.get("tool_observations", []) or [],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        trace_id=str(result["request_id"]),
        latency_ms=latency_ms,
        token_in=int(result.get("token_in", 0)),
        token_out=int(result.get("token_out", 0)),
        estimated_cost=float(result.get("estimated_cost", 0.0)),
    )


def _build_rule_clarification_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    clarification_question: str,
    routing_reason: str,
    candidates: list[dict[str, Any]] | None = None,
) -> UnifiedAgentResponse:
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "display_message": display_message or message,
        "answer": clarification_question,
        "intent": "action_or_draft",
        "routing_source": "rule",
        "routing_confidence": 0.97,
        "routing_reason": routing_reason,
        "candidate_intents": ["action_or_draft"],
        "mode_used": "outbound_resolution",
        "tool_calls": [
            {
                "tool_name": "resolve_outbound_target",
                "success": True,
                "status": "clarification_required",
                "error": "",
                "result": {"candidates": list(candidates or [])},
            }
        ],
        "retrieved_evidence": [],
        "needs_clarification": True,
        "clarification_question": clarification_question,
        "privacy": {},
        "context_budget": {},
        "citations": [],
        "memory_context": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "reflection_notes": None,
        "node_latencies_ms": {"total": 0.0},
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
        "task_plan": {"outbound_resolution_candidates": list(candidates or [])},
    }
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    record_request(
        mode="outbound_resolution",
        latency_ms=0.0,
        token_in=0,
        token_out=0,
        estimated_cost=0.0,
        retrieval_hits=0,
    )
    record_unified_agent(
        intent="action_or_draft",
        tool_calls=synthetic_result["tool_calls"],
        needs_clarification=True,
        privacy_guardrail=False,
        context_budget_used=None,
    )
    record_router("rule", 0.97)
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=clarification_question,
        intent="action_or_draft",
        routing_source="rule",
        routing_confidence=0.97,
        routing_reason=routing_reason,
        candidate_intents=["action_or_draft"],
        mode_used="outbound_resolution",
        tool_calls=synthetic_result["tool_calls"],
        retrieved_evidence=[],
        needs_clarification=True,
        clarification_question=clarification_question,
        privacy={},
        context_budget={},
        citations=[],
        memory_written=memory_written,
        memory_hits=0,
        merged_memory_hits=0,
        memory_context={},
        answer_collapsed=False,
        reflection_notes=None,
        task_plan=synthetic_result["task_plan"],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=None,
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=0.0,
        token_in=0,
        token_out=0,
        estimated_cost=0.0,
    )


@app.post("/labs/long-doc/query", response_model=LongDocQueryResponse)
def long_doc_query(payload: LongDocQueryRequest) -> LongDocQueryResponse:
    result = allocate_context(
        payload.question,
        payload.context_budget,
        payload.retrieval_layer_preference,
    )
    record_lab_request("long_doc")
    record_context_budget("long_doc", int(result["used_tokens"]), int(result["budget_tokens"]))
    return LongDocQueryResponse(**result)


@app.post("/labs/privacy/scan", response_model=PrivacyScanResponse)
def privacy_scan(payload: PrivacyScanRequest) -> PrivacyScanResponse:
    result = scan_sensitive_message(payload.message, payload.context_budget)
    record_lab_request("privacy")
    record_privacy_scan(str(result["risk_level"]), result["redactions"])
    context_pack = result["context_pack"]
    record_context_budget(
        "privacy",
        int(context_pack["packed_tokens"]),
        payload.context_budget,
        int(context_pack["truncated_segments"]),
    )
    return PrivacyScanResponse(**result)


@app.post("/labs/disambiguation/query", response_model=DisambiguationQueryResponse)
def disambiguation_query(payload: DisambiguationQueryRequest) -> DisambiguationQueryResponse:
    result = answer_apple_query(payload.query)
    record_lab_request("disambiguation")
    return DisambiguationQueryResponse(**result)


@app.post("/labs/framework/compare", response_model=FrameworkCompareResponse)
def framework_compare(payload: FrameworkCompareRequest) -> FrameworkCompareResponse:
    result = compare_raw_llm_and_langgraph(payload.task)
    record_lab_request("framework_compare")
    return FrameworkCompareResponse(**result)


@app.get("/metrics")
def metrics() -> Response:
    _refresh_async_metrics()
    return Response(content=render_metrics(), media_type=content_type())
