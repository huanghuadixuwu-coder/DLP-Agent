from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from contextlib import asynccontextmanager
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, timezone
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from redis import Redis

from app.actor_context import ActorContext, build_actor_context, permission_observation, require_permission
from app.auth_store import create_login_code, init_auth_store, mask_email, resolve_session_token, revoke_session_token, verify_login_code
from app.backpressure import check_rate_limit
from app.communication.brief_service import assemble_communication_brief_observation
from app.communication.brief_store import get_latest_brief_for_thread, init_communication_brief_store
from app.communication.thread_store import (
    get_active_communication_thread,
    get_communication_thread,
    init_communication_thread_store,
    list_communication_threads,
    set_active_communication_thread,
)
from app.config import get_settings
from app.continuation_state import (
    PendingObject,
    pending_object_from_confirmation,
    pending_object_from_mail_clarification,
    pending_object_from_record,
    pending_object_from_source_clarification,
    resolve_continuation,
)
from app.conversation_memory import build_memory_context, compact_text, infer_title, write_merged_summary
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
from app.dlp_entry import classify_dlp_entry, extract_destination_email as dlp_extract_destination_email, looks_like_outbound_action as dlp_looks_like_outbound_action
from app.dlp_scenarios import build_status_path, evaluate_scenario_task, get_dlp_scenario, list_dlp_scenarios, normalize_fault_injection
from app.email_sender import send_email_smtp
from app.enterprise_rag.core.index_contract import audit_active_index_parity, build_active_index_contract
from app.enterprise_rag.core.service import answer_enterprise_question, build_enterprise_answer_observation, build_public_enterprise_query_payload
from app.enterprise_rag.eval.benchmark_runner import run_benchmark_sample
from app.enterprise_rag.eval.casebook import build_casebook
from app.enterprise_rag.ingestion.manifest_store import load_manifest
from app.enterprise_rag.ingestion.indexer import ingest_enterprise_rag_bench
from app.graph import get_llm
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
from app.inbound_mail_store import init_inbound_mail_store, list_notifications, list_recent_inbound_threads
from app.mail.current_provider import CurrentImapSmtpMailProvider
from app.mail.access import is_inbound_mail_tool, is_mail_read_authorized, mark_mail_read_authorized
from app.mail.draft_store import (
    bind_mail_draft_task,
    build_persisted_confirmation_payload,
    cancel_mail_draft,
    get_latest_active_mail_draft,
    init_mail_draft_store,
    upsert_mail_draft,
)
from app.mail.content_parser import parse_message_content_candidates
from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND
from app.mail.source_resolver import resolve_mail_source_request
from app.disambiguation_lab import answer_apple_query
from app.labs_long_doc import allocate_context
from app.pending_object_store import (
    consume_pending_object,
    get_latest_active_pending_object,
    init_pending_object_store,
    list_active_pending_objects,
    upsert_pending_object,
)
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
    record_unified_agent,
    record_unified_evidence_hits,
    record_queue_backlog,
    record_mail_dlq_replay,
    refresh_rag_index_metrics,
    refresh_task_metrics,
    render_metrics,
)
from app.outbound_delivery import (
    build_mail_action_plan,
    build_review_content,
    build_outbound_resolution,
    looks_like_mail_action_request,
    patch_pending_mail_plan,
)
from app.models import (
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
    FrameworkCompareRequest,
    FrameworkCompareResponse,
    HealthResponse,
    InboundDraftReplyResponse,
    InboundMailMessage,
    InboundMailSummaryResponse,
    InboundMailSyncRequest,
    InboundMailSyncResponse,
    NotificationOutboxItem,
    OutboundMailSummaryResponse,
    LongDocQueryRequest,
    LongDocQueryResponse,
    PrivacyScanRequest,
    PrivacyScanResponse,
    UnifiedAgentRequest,
    UnifiedAgentResponse,
)
from app.observability import configure_observability
from app.orchestration import orchestrate_agent_request
from app.orchestration.fast_router import route_agent_request
from app.orchestration.final_renderer import render_final_answer, render_mail_authoring
from app.orchestration.multi_agent_planner import plan_multi_agent_dag_request
from app.orchestration.observations import make_typed_observation
from app.orchestration.registry import build_tool_executor_map
from app.orchestration.trace_evaluator import attach_trace_evaluation
from app.orchestration.types import OrchestrationContext
from app.privacy_lab import load_active_privacy_policy, scan_sensitive_message
from app.raw_vs_langgraph import compare_raw_llm_and_langgraph
from app.resilience import make_failure_observation
from app.task_events import build_task_event, publish_task_event, task_event_iterator
from app.task_queue import (
    EMAIL_QUEUE,
    MAIL_QUEUE,
    RISK_QUEUE,
    enqueue_daily_mail_digest,
    enqueue_conversation_memory_summary,
    enqueue_dlp_risk_task,
    enqueue_email_send_task,
    enqueue_enterprise_benchmark,
    enqueue_enterprise_ingest,
    enqueue_enterprise_query,
    enqueue_meeting_task,
    enqueue_inbound_mail_sync,
    get_queue_health,
    get_enterprise_task_status,
)
from app.task_store import (
    add_task_event,
    approve_task,
    create_dlp_task,
    get_dlp_task,
    get_dlp_task_by_idempotency_key,
    get_latest_recoverable_task,
    get_sent_mail_stats,
    get_task_approvals,
    get_task_events,
    get_task_stats,
    init_task_store,
    list_dlp_tasks,
    list_mail_dlq_entries,
    mark_mail_dlq_replay,
    replay_mail_dlq_entry,
    reject_task,
    update_task,
)
from app.upload_analysis import build_upload_context, classify_upload_request, infer_upload_task_type, refers_to_recent_upload
from app.upload_blob_store import save_upload_blob
from app.vectorstore import count_collection, upsert_conversation_memory_documents


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _permission_denied(actor: ActorContext, action: str, resource: str = "") -> HTTPException:
    decision = require_permission(actor, action, resource)
    return HTTPException(status_code=403, detail=permission_observation(actor, decision))


def _ensure_permission(actor: ActorContext, action: str, resource: str = "") -> dict[str, Any]:
    decision = require_permission(actor, action, resource)
    if not decision.allowed:
        raise _permission_denied(actor, action, resource)
    return decision.to_dict()


def _ensure_rate_limit(actor: ActorContext, resource: str) -> dict[str, Any]:
    decision = check_rate_limit(actor, resource)
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "observation_type": "rate_limited",
                "ok": False,
                "actor_context": actor.to_dict(),
                "rate_limit_decision": decision.to_dict(),
            },
        )
    return decision.to_dict()


def _ensure_authenticated_actor_access(
    request: Request,
    actor: ActorContext,
    *,
    resource: str,
    local_mode: str = "local_dev",
) -> dict[str, Any]:
    if actor.is_local_dev:
        return {"allowed": True, "mode": local_mode, "resource": resource}
    token = request.headers.get("x-auth-session") or request.headers.get("X-Auth-Session") or ""
    session = resolve_session_token(token)
    if not session:
        raise HTTPException(status_code=401, detail=f"authenticated session required for {resource}")
    if (
        str(session.get("tenant_id") or "") != actor.tenant_id
        or str(session.get("workspace_id") or "") != actor.workspace_id
        or str(session.get("user_id") or "") != actor.user_id
    ):
        raise HTTPException(status_code=403, detail="auth session does not match actor context")
    return {
        "allowed": True,
        "mode": "authenticated_session",
        "resource": resource,
        "email": str(session.get("email") or ""),
    }


def _try_authenticated_actor_access(
    request: Request,
    actor: ActorContext,
    *,
    resource: str,
    local_mode: str = "local_dev",
) -> dict[str, Any] | None:
    try:
        return _ensure_authenticated_actor_access(
            request,
            actor,
            resource=resource,
            local_mode=local_mode,
        )
    except HTTPException:
        return None


def _ensure_internal_communication_thread_access(request: Request, actor: ActorContext) -> dict[str, Any]:
    return _ensure_authenticated_actor_access(
        request,
        actor,
        resource="communication thread access",
        local_mode="local_dev_internal",
    )


def _ensure_inbound_mail_access(request: Request, actor: ActorContext, resource: str) -> dict[str, Any]:
    return _ensure_authenticated_actor_access(
        request,
        actor,
        resource=resource,
        local_mode="local_dev_mail",
    )


def _agent_chat_can_mark_mail_read(request: Request, actor: ActorContext) -> bool:
    if not _try_authenticated_actor_access(
        request,
        actor,
        resource="agent chat inbound mail",
        local_mode="local_dev_mail",
    ):
        return False
    return require_permission(actor, "mail.read", "agent_chat_inbound_mail").allowed


def _agent_chat_mark_mail_read_if_authorized(
    request: Request,
    actor: ActorContext,
    actor_context: dict[str, Any],
) -> dict[str, Any]:
    if _agent_chat_can_mark_mail_read(request, actor):
        return mark_mail_read_authorized(actor_context)
    return dict(actor_context or {})


def _ensure_agent_chat_inbound_mail_access(
    request: Request,
    actor: ActorContext,
    actor_context: dict[str, Any],
    resource: str = "agent_chat_inbound_mail",
) -> dict[str, Any]:
    _ensure_inbound_mail_access(request, actor, "agent chat inbound mail")
    _ensure_permission(actor, "mail.read", resource)
    return mark_mail_read_authorized(actor_context)


def _attach_landing_context(
    response: UnifiedAgentResponse,
    *,
    actor: ActorContext,
    permission_decision: dict[str, Any],
    rate_limit_decision: dict[str, Any],
    queue_status: dict[str, Any] | None = None,
    task_mode: str = "sync",
) -> UnifiedAgentResponse:
    response.actor_context = actor.to_dict()
    response.permission_decision = dict(permission_decision or {})
    response.rate_limit_decision = dict(rate_limit_decision or {})
    response.queue_status = dict(queue_status or {})
    response.task_mode = task_mode
    return response


def _refresh_queue_metrics() -> dict[str, Any]:
    health = get_queue_health()
    try:
        record_queue_backlog({str(queue): int(count) for queue, count in dict(health.get("queues") or {}).items()})
    except Exception:
        logger.debug("Queue metrics refresh failed", exc_info=True)
    return health


def _mark_task_enqueue_failed(
    task: dict[str, Any],
    *,
    service: str,
    operation: str,
    error: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observation = make_failure_observation(
        service=service,
        operation=operation,
        error=error,
        fallback_strategy="mark_task_failed_and_return_recovery_observation",
        retry_count=0,
        actor_context=actor_context,
        severity="high",
    )
    task_id = str(task.get("task_id") or "")
    updated = update_task(
        task_id,
        status="failed",
        delivery_status="failed",
        delivery_error=error,
        final_result="The task could not be queued for asynchronous worker execution.",
        last_error_category="transient_infra",
        manual_handover_required=True,
        next_recommended_action="Check Redis/Celery worker health, then retry the governed action.",
        domain_result={
            **dict(task.get("domain_result") or {}),
            "ok": False,
            "error": error,
            "recovery_observation": observation,
        },
    ) or task
    add_task_event(
        task_id,
        "enqueue_failed",
        "api",
        {"message": "Task enqueue failed; returning recovery observation.", "error": error, "operation": operation},
    )
    publish_task_event(
        build_task_event(
            task_id=task_id,
            status="failed",
            event_type="enqueue_failed",
            message="Task enqueue failed; worker did not receive the job.",
            delivery_status="failed",
            delivery_error=error,
        )
    )
    return updated


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_observability()
    init_conversation_store()
    init_task_store()
    init_mail_draft_store()
    init_pending_object_store()
    init_auth_store()
    init_inbound_mail_store()
    init_communication_thread_store()
    init_communication_brief_store()
    init_workspace_memory_index()
    init_hermes_dynamic_memory_store()
    try:
        written = ingest_if_needed(force=False)
        if written:
            logger.info("Seeded Chroma with %s documents.", written)
    except Exception as exc:  # pragma: no cover - startup best effort
        logger.warning("Startup ingest skipped: %s", exc)
    yield


settings = get_settings()
cors_allow_origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]

app = FastAPI(title="Secure Enterprise Mail Agent", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_DLP_EMAIL = "17388861183@163.com"
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
OUTBOUND_REFERENCE_TOKENS = (
    "上述",
    "上面的",
    "上文",
    "刚才的",
    "前面的",
    "上述方案",
    "上述解决方案",
    "上面的回答",
    "刚才的回答",
    "刚才的内容",
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
    return match.group(0) if match else ""


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


def _resolve_upload_context(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
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
        _persist_upload_artifact_object(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            upload_context=current_upload_context,
            actor_context=actor_context,
        )
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


def _extract_inline_mail_body(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    parsed_candidates = parse_message_content_candidates(text)
    for candidate in parsed_candidates:
        if str(candidate.get("source_type") or "") == "user_inline_text":
            body = str(candidate.get("content") or "").strip()
            if body:
                return body
    # Current-turn outbound body extraction must only come from ContentCandidate.
    # State-specific body clarification can still treat the whole follow-up as a body.
    return ""


def _mail_thread_candidate_content(thread: dict[str, Any]) -> str:
    messages: list[dict[str, Any]] = []
    for item in list(thread.get("messages") or [])[:5]:
        body = str(item.get("summary") or item.get("body_preview") or item.get("snippet") or item.get("body_text") or "")
        messages.append(
            {
                "message_id": str(item.get("message_id") or ""),
                "sender": str(item.get("sender") or ""),
                "recipients": str(item.get("recipients") or ""),
                "subject": str(item.get("subject") or ""),
                "received_at": str(item.get("received_at") or ""),
                "summary": compact_text(body, 500),
            }
        )
    return json.dumps(
        {
            "thread_id": str(thread.get("thread_id") or ""),
            "provider_thread_id": str(thread.get("provider_thread_id") or ""),
            "subject": str(thread.get("subject") or ""),
            "latest_received_at": str(thread.get("latest_received_at") or ""),
            "messages": messages,
        },
        ensure_ascii=False,
    )


def _answer_artifact_candidate_label(summary: str, citations: list[dict[str, Any]] | None = None) -> str:
    for item in list(citations or []):
        title = str(item.get("title") or item.get("source_title") or item.get("doc_id") or "").strip()
        if title:
            return f"之前的回答：{compact_text(title, 72)}"
    preview = compact_text(summary, 72).strip()
    return f"之前的回答：{preview or '可复用的处理方案'}"


def _collect_outbound_candidates(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    upload_context: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    context_snapshot = dict(upload_context or {})

    def _communication_brief_candidate_content(brief_payload: dict[str, Any]) -> str:
        thread_ref = dict(brief_payload.get("thread_ref") or {})
        return json.dumps(
            {
                "brief_id": str(brief_payload.get("brief_id") or ""),
                "conversation_id": str(brief_payload.get("conversation_id") or ""),
                "thread_ref": {
                    "thread_id": str(thread_ref.get("thread_id") or ""),
                    "source": str(thread_ref.get("source") or ""),
                    "subject": str(thread_ref.get("subject") or ""),
                    "participants": list(thread_ref.get("participants") or []),
                    "last_message_at": str(thread_ref.get("last_message_at") or ""),
                },
                "employee_goal": str(brief_payload.get("employee_goal") or ""),
                "customer_context_summary": str(brief_payload.get("customer_context_summary") or ""),
                "grounding_refs": list(brief_payload.get("grounding_refs") or []),
                "must_include": list(brief_payload.get("must_include") or []),
                "must_avoid": list(brief_payload.get("must_avoid") or []),
                "open_questions": list(brief_payload.get("open_questions") or []),
                "recommended_next_action": str(brief_payload.get("recommended_next_action") or ""),
                "source_observation_ids": list(brief_payload.get("source_observation_ids") or []),
                "confidence": float(brief_payload.get("confidence") or 0.0),
                "brief_persistence_source": str(brief_payload.get("brief_persistence_source") or ""),
                "brief_version": int(brief_payload.get("brief_version") or 1),
                "refresh_reason": str(brief_payload.get("refresh_reason") or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _append_candidate(
        *,
        kind: str,
        content: str,
        label: str,
        candidate_id: str = "",
        source_turn_id: str = "",
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
                "candidate_id": candidate_id or f"{kind}:{len(candidates)}",
                "source_turn_id": source_turn_id,
                "label": label,
                "content": normalized,
                "filename": filename,
                "content_type": content_type,
                "supports_attachment": supports_attachment,
                "upload_blob_id": upload_blob_id,
            }
        )

    inline_content_candidates = parse_message_content_candidates(str(payload.message or ""))
    if inline_content_candidates:
        for parsed in inline_content_candidates:
            if str(parsed.get("source_type") or "") != "user_inline_text":
                continue
            inline_mail_body = str(parsed.get("content") or "").strip()
            if not inline_mail_body:
                continue
            span = list(parsed.get("provenance_span") or [])
            span_id = "-".join(str(item) for item in span[:2]) if span else str(len(candidates))
            _append_candidate(
                kind="user_inline_text",
                candidate_id=f"user-inline:{span_id}",
                content=inline_mail_body,
                label="用户本轮明确提供的正文",
                content_type="text/plain",
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

    if actor_context:
        for artifact in list_active_pending_objects(
            conversation_id=conversation_id,
            actor_context=actor_context,
            object_types=["answer_artifact"],
            limit=12,
        ):
            payload_data = dict(artifact.get("payload") or {})
            turn_id = str(payload_data.get("turn_id") or payload_data.get("resource_id") or "").strip()
            answer_summary = _sanitize_recent_content(str(payload_data.get("answer_summary") or ""))
            if not turn_id or not answer_summary:
                continue
            citations = [dict(item) for item in list(payload_data.get("citations") or [])]
            _append_candidate(
                kind="assistant_last_answer",
                candidate_id=str(artifact.get("object_id") or f"assistant-turn:{turn_id}"),
                source_turn_id=turn_id,
                content=answer_summary,
                label=_answer_artifact_candidate_label(answer_summary, citations),
                content_type="text/plain",
            )

    if actor_context:
        completed_meeting_task = _latest_completed_meeting_task(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            actor_context=actor_context,
        )
        if completed_meeting_task:
            details = _meeting_result_details(completed_meeting_task)
            meeting_content = json.dumps(
                {
                    "communication_role": "escalation_provider",
                    "communication_input_kind": "meeting_result",
                    "task_id": str(completed_meeting_task.get("task_id") or ""),
                    "subject": details.get("subject") or "",
                    "meeting_id": details.get("meeting_id") or "",
                    "meeting_code": details.get("meeting_code") or "",
                    "meeting_url": details.get("meeting_url") or "",
                    "start_time": details.get("start_time") or "",
                    "end_time": details.get("end_time") or "",
                },
                ensure_ascii=False,
            )
            _append_candidate(
                kind="meeting_result",
                candidate_id=f"meeting-task:{completed_meeting_task.get('task_id')}",
                content=meeting_content,
                label=str(details.get("subject") or "最近创建的腾讯会议结果"),
                content_type="application/json",
            )

    if actor_context and is_mail_read_authorized(actor_context):
        with suppress(Exception):
            for thread in list_recent_inbound_threads(limit=3, messages_per_thread=5, actor_context=actor_context):
                thread_id = str(thread.get("thread_id") or thread.get("provider_thread_id") or "").strip()
                if not thread_id:
                    continue
                subject = str(thread.get("subject") or "recent inbound mail thread")
                _append_candidate(
                    kind="mail_thread",
                    candidate_id=f"mail-thread:{thread_id}",
                    content=_mail_thread_candidate_content(thread),
                    label=f"mail thread: {subject}",
                    content_type="application/json",
                )

    if actor_context:
        with suppress(Exception):
            active_thread = get_active_communication_thread(actor_context=actor_context)
            active_thread_id = str(dict(active_thread or {}).get("thread_id") or "").strip()
            latest_brief = get_latest_brief_for_thread(active_thread_id, actor_context=actor_context) if active_thread_id else None
            brief_payload = dict(dict(latest_brief or {}).get("brief") or {})
            brief_id = str(brief_payload.get("brief_id") or "").strip()
            if brief_id:
                thread_ref = dict(brief_payload.get("thread_ref") or {})
                label_subject = str(thread_ref.get("subject") or brief_payload.get("employee_goal") or "communication brief")
                _append_candidate(
                    kind=COMMUNICATION_BRIEF_SOURCE_KIND,
                    candidate_id=f"communication-brief:{brief_id}",
                    content=_communication_brief_candidate_content(
                        {
                            **brief_payload,
                            "brief_persistence_source": str(dict(latest_brief or {}).get("persistence_source") or "communication_briefs"),
                            "brief_version": int(dict(latest_brief or {}).get("version") or 1),
                            "refresh_reason": str(dict(latest_brief or {}).get("refresh_reason") or ""),
                        }
                    ),
                    label=f"communication brief: {compact_text(label_subject, 72)}",
                    content_type="application/json",
                )

    turns = get_turns(conversation_id, limit=10)
    for turn in reversed(turns):
        if str(turn.get("role") or "").lower() != "assistant":
            continue
        turn_id = str(turn.get("turn_id") or "")
        for observation in list(dict(turn.get("debug_payload") or {}).get("tool_observations") or []):
            if str(observation.get("observation_type") or observation.get("kind") or "") != COMMUNICATION_BRIEF_SOURCE_KIND:
                continue
            brief_payload = dict(observation.get("payload") or {})
            brief_id = str(brief_payload.get("brief_id") or "").strip()
            if not brief_id:
                continue
            thread_ref = dict(brief_payload.get("thread_ref") or {})
            label_subject = str(thread_ref.get("subject") or brief_payload.get("employee_goal") or "communication brief")
            _append_candidate(
                kind=COMMUNICATION_BRIEF_SOURCE_KIND,
                candidate_id=f"communication-brief:{brief_id}",
                source_turn_id=turn_id,
                content=_communication_brief_candidate_content(brief_payload),
                label=f"communication brief: {compact_text(label_subject, 72)}",
                content_type="application/json",
            )
            break
    assistant_candidate_count = 0
    for turn in reversed(turns):
        role = str(turn.get("role") or "").lower()
        debug_payload = dict(turn.get("debug_payload") or {})
        if role == "assistant" and (
            bool(debug_payload.get("needs_clarification"))
            or str(debug_payload.get("mode_used") or "") in {"outbound_resolution", "task_queue"}
            or str(debug_payload.get("termination_reason") or "") in {"needs_clarification", "needs_confirmation"}
        ):
            continue
        content = _sanitize_recent_content(str(turn.get("answer_summary") or turn.get("redacted_content") or turn.get("content") or ""))
        if role == "assistant" and content:
            turn_id = str(turn.get("turn_id") or f"recent-{assistant_candidate_count}")
            _append_candidate(
                kind="assistant_last_answer",
                candidate_id=f"assistant-turn:{turn_id}",
                source_turn_id=turn_id,
                content=content,
                label="最近生成的总结/回答",
            )
            assistant_candidate_count += 1
            if assistant_candidate_count >= 5:
                break
    for turn in reversed(turns):
        role = str(turn.get("role") or "").lower()
        content = _sanitize_recent_content(str(turn.get("redacted_content") or turn.get("content") or ""))
        if role == "user" and len(content) >= 120 and not _looks_like_outbound_action(content):
            _append_candidate(kind="user_recent_text", content=content, label="你最近粘贴/输入的长文本")
            break
    return candidates


def _build_outbound_resolution(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    upload_context: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = _collect_outbound_candidates(payload, conversation_id, upload_context, actor_context=actor_context)
    return build_outbound_resolution(
        message=str(payload.message or ""),
        request_message=str(payload.message or ""),
        candidates=candidates,
        destination_email=_extract_destination_email(payload.message),
        referential_request=_looks_like_referential_outbound_request(payload.message),
        explicit_summary=_looks_like_summary_reference(payload.message),
        send_both=_looks_like_send_both_request(payload.message),
    )


def _contains_inbound_mail_candidates(candidates: list[dict[str, Any]] | None) -> bool:
    return any(str(item.get("kind") or "") == "mail_thread" for item in list(candidates or []))


def _looks_like_contextual_communication_request(message: str) -> bool:
    text = str(message or "").strip()
    lowered = text.lower()
    if _looks_like_contextual_outbound_request(text) or looks_like_mail_action_request(text):
        return True
    markers = (
        "active thread",
        "customer thread",
        "communication thread",
        "latest brief",
        "brief",
        "reply draft",
        "draft a reply",
        "customer",
        "thread",
        "reply",
        "email",
        "mail",
    )
    return any(marker in lowered for marker in markers)


def _communication_thread_payload(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "thread_id": str(thread.get("thread_id") or ""),
        "source": str(thread.get("source") or ""),
        "subject": str(thread.get("subject") or ""),
        "participants": list(thread.get("participants") or []),
        "last_message_at": str(thread.get("last_message_at") or ""),
        "status": str(thread.get("status") or ""),
        "source_message_ids": list(thread.get("source_message_ids") or []),
        "latest_summary": str(thread.get("latest_summary") or ""),
        "risk_hint": str(thread.get("risk_hint") or ""),
        "updated_at": str(thread.get("updated_at") or ""),
    }


def _resolve_agent_chat_context(
    payload: UnifiedAgentRequest,
    *,
    conversation_id: str,
    actor_context: dict[str, Any],
) -> dict[str, Any]:
    requested_thread_id = str(getattr(payload, "thread_id", "") or "").strip()
    global_mode = bool(getattr(payload, "global_mode", False))
    provenance_base = {
        "route": "/agent/chat",
        "conversation_id": conversation_id,
        "requested_thread_id": requested_thread_id,
    }
    if global_mode:
        observation = make_typed_observation(
            observation_type="global_entry",
            source="agent_chat_context_resolver",
            grounding_kind="state",
            summary="The request explicitly entered global ask mode.",
            payload={"global_mode": True, "thread_id": "", "brief_id": ""},
            provenance={**provenance_base, "source": "request_payload"},
            confidence=1.0,
            actor_context=actor_context,
        )
        return {
            "global_mode": True,
            "thread": {},
            "brief": {},
            "observations": [observation],
            "context": {"global_mode": True, "thread_id": "", "brief_id": ""},
        }

    thread: dict[str, Any] | None = None
    resolution_source = "active_thread_store"
    if requested_thread_id:
        thread = set_active_communication_thread(requested_thread_id, actor_context=actor_context)
        resolution_source = "request_thread_id"
        if thread is None:
            thread = get_communication_thread(requested_thread_id, actor_context=actor_context, refresh=False)
    else:
        thread = get_active_communication_thread(actor_context=actor_context)

    observations: list[dict[str, Any]] = []
    context: dict[str, Any] = {"global_mode": False}
    if thread:
        thread_payload = _communication_thread_payload(dict(thread))
        context["thread_id"] = thread_payload["thread_id"]
        context["active_thread"] = thread_payload
        observations.append(
            make_typed_observation(
                observation_type="active_communication_thread",
                source="communication_copilot",
                grounding_kind="state",
                summary="Resolved the active communication thread for this chat turn.",
                payload=thread_payload,
                provenance={**provenance_base, "source": resolution_source},
                confidence=1.0,
                actor_context=actor_context,
            )
        )
        latest_brief = get_latest_brief_for_thread(thread_payload["thread_id"], actor_context=actor_context)
        brief_payload = dict(dict(latest_brief or {}).get("brief") or {})
        brief_id = str(brief_payload.get("brief_id") or "").strip()
        if brief_id:
            context["brief_id"] = brief_id
            context["latest_brief"] = brief_payload
            observations.append(
                make_typed_observation(
                    observation_type=COMMUNICATION_BRIEF_SOURCE_KIND,
                    source="communication_copilot",
                    grounding_kind="state",
                    summary="Resolved the latest communication brief for the active thread.",
                    payload={
                        **brief_payload,
                        "brief_persistence_source": str(dict(latest_brief or {}).get("persistence_source") or "communication_briefs"),
                        "brief_version": int(dict(latest_brief or {}).get("version") or 1),
                        "refresh_reason": str(dict(latest_brief or {}).get("refresh_reason") or ""),
                    },
                    provenance={**provenance_base, "source": "communication_brief_store"},
                    confidence=float(brief_payload.get("confidence") or 0.86),
                    actor_context=actor_context,
                )
            )
    elif requested_thread_id or _looks_like_contextual_communication_request(payload.message):
        observations.append(
            make_typed_observation(
                observation_type="active_object_resolution_failed",
                source="agent_chat_context_resolver",
                status="failed",
                grounding_kind="state",
                summary="No active communication thread could be resolved for this contextual chat turn.",
                payload={
                    "requested_thread_id": requested_thread_id,
                    "active_thread_found": False,
                    "reason": "requested_thread_not_found" if requested_thread_id else "no_active_thread",
                },
                provenance={**provenance_base, "source": resolution_source},
                confidence=1.0,
                actor_context=actor_context,
                success=False,
            )
        )
        context["resolution_failed"] = True

    return {
        "global_mode": False,
        "thread": dict(thread or {}),
        "brief": dict(context.get("latest_brief") or {}),
        "observations": observations,
        "context": context,
    }


def _prepend_observations_once(
    existing: list[dict[str, Any]] | None,
    additions: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    result = [dict(item) for item in list(existing or []) if isinstance(item, dict)]
    seen = {
        (
            str(item.get("observation_type") or ""),
            str(dict(item.get("payload") or {}).get("thread_id") or ""),
            str(dict(item.get("payload") or {}).get("brief_id") or ""),
        )
        for item in result
    }
    prefix: list[dict[str, Any]] = []
    for item in [dict(value) for value in list(additions or []) if isinstance(value, dict)]:
        key = (
            str(item.get("observation_type") or ""),
            str(dict(item.get("payload") or {}).get("thread_id") or ""),
            str(dict(item.get("payload") or {}).get("brief_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        prefix.append(item)
    return [*prefix, *result]



def _task_answer_text(task: dict[str, Any]) -> str:
    status = str(task.get("status", ""))
    task_id = str(task.get("task_id", ""))
    task_type = str(task.get("task_type") or "")
    if task_type.startswith("domain_"):
        action = str(task.get("domain_action") or "domain action")
        if status == "queued":
            return f"已创建受控异步任务 `{task_id}`，将执行 `{action}`。系统会通过队列执行并记录结果。"
        if status == "processing":
            return f"任务 `{task_id}` 正在执行 `{action}`。"
        if status == "completed":
            return str(task.get("final_result") or task.get("delivery_result") or f"任务 `{task_id}` 已完成。")
        if status == "failed":
            return str(task.get("final_result") or f"任务 `{task_id}` 执行失败，请查看任务台错误详情。")
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


def _build_task_status_observation(task: dict[str, Any], actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
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
    return make_typed_observation(
        observation_type="task_status_result",
        source="task_queue",
        grounding_kind="tool",
        summary=compact_text(answer_hint, 220),
        payload=payload,
        citations=[],
        confidence=0.98,
        actor_context=actor_context,
    )


def _build_mail_plan_observation(
    mail_plan: dict[str, Any],
    *,
    source: str = "mail_action",
    summary: str = "",
    draft_mode: str = "",
    observation_type: str = "mail_plan_result",
    extra_payload: dict[str, Any] | None = None,
    actor_context: dict[str, Any] | None = None,
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
        "source_resolution": dict(mail_plan.get("source_resolution") or {}),
        "draft_mode": draft_mode or str(mail_plan.get("mail_action_type") or ""),
        "provider_capabilities": _mail_provider_capabilities(),
        "mail_plan": mail_plan,
    }
    if extra_payload:
        payload.update(extra_payload)
    return make_typed_observation(
        observation_type=observation_type,
        source=source,
        grounding_kind="tool",
        summary=compact_text(summary or resolved_body or resolved_subject or "已生成邮件计划。", 220),
        payload=payload,
        citations=[],
        confidence=0.95,
        actor_context=actor_context,
    )


def _build_mail_task_created_observation(
    *,
    task: dict[str, Any],
    mail_plan: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    summary = (
        f"已基于当前确认的邮件内容创建治理任务 `{task_id}`，系统正在执行 DLP 风险判断。"
        if task_id
        else "已基于当前确认的邮件内容创建治理任务，系统正在执行 DLP 风险判断。"
    )
    return _build_mail_plan_observation(
        mail_plan,
        source="mail_confirmation",
        summary=summary,
        draft_mode="task_created",
        observation_type="governed_mail_task_created",
        extra_payload={
            "task_id": task_id,
            "task_status": str(task.get("status") or ""),
            "risk_level": str(task.get("risk_level") or ""),
            "delivery_status": str(task.get("delivery_status") or ""),
            "next_actions": ["wait_for_dlp", "check_task_progress", "revise_if_blocked"],
        },
        actor_context=actor_context,
    )


def _build_mailbox_summary_observation(
    *,
    source: str,
    summary_text: str,
    payload: dict[str, Any],
    confidence: float = 0.92,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return make_typed_observation(
        observation_type="mailbox_summary_result",
        source=source,
        grounding_kind="tool",
        summary=compact_text(summary_text, 220),
        payload=payload,
        citations=[],
        confidence=confidence,
        actor_context=actor_context,
    )


def _build_compound_observation(
    *,
    question: str,
    observations: list[dict[str, Any]],
    subtasks: list[dict[str, Any]],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summaries = [compact_text(str(item.get("summary") or ""), 120) for item in observations if str(item.get("summary") or "").strip()]
    payload = {
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
    }
    return make_typed_observation(
        observation_type="compound_result",
        source="compound_plan_execute",
        grounding_kind="mixed",
        summary=compact_text("；".join(summaries[:4]) or question, 240),
        payload=payload,
        citations=[citation for item in observations for citation in list(item.get("citations") or [])][:8],
        confidence=0.9,
        actor_context=actor_context,
    )


def _build_task_agent_response(
    *,
    session_id: str,
    conversation_id: str,
    outbound_message: str,
    display_message: str,
    task: dict[str, Any],
    routing_reason: str,
    intent: str = "privacy_alert",
    current_goal: str = "privacy_alert",
    routing_source: str = "rule",
    routing_confidence: float = 0.99,
    candidate_intents: list[str] | None = None,
    mode_used: str = "task_queue",
    extra_observations: list[dict[str, Any]] | None = None,
    final_answer_source: str = "task_queue_renderer",
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    task_id = str(task["task_id"])
    status = str(task["status"])
    answer_text = _task_answer_text(task)
    needs_clarification = status in {"needs_clarification", "input_invalid"}
    task_observation = _build_task_status_observation(task)
    observations = [dict(item) for item in list(extra_observations or [])]
    observations.append(task_observation)
    renderer = {"answer": answer_text, "token_in": 0, "token_out": 0, "estimated_cost": 0.0, "used_fallback": False}
    if not needs_clarification:
        renderer = render_final_answer(
            question=outbound_message,
            current_goal=current_goal,
            observations=observations,
            working_memory=[str(item.get("summary") or "") for item in observations if str(item.get("summary") or "").strip()],
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
        "intent": intent,
        "routing_source": routing_source,
        "routing_confidence": routing_confidence,
        "routing_reason": routing_reason,
        "candidate_intents": list(candidate_intents or [intent]),
        "mode_used": mode_used,
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
        "tool_observations": observations,
    }
    synthetic_result = attach_trace_evaluation(synthetic_result, actor_context=actor_context)
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    record_request(
        mode=mode_used,
        latency_ms=0.0,
        token_in=int(renderer.get("token_in", 0)),
        token_out=int(renderer.get("token_out", 0)),
        estimated_cost=float(renderer.get("estimated_cost", 0.0)),
        retrieval_hits=0,
    )
    record_unified_agent(
        intent=intent,
        tool_calls=synthetic_result["tool_calls"],
        needs_clarification=needs_clarification,
        privacy_guardrail=bool(synthetic_result["privacy"].get("redacted")),
        context_budget_used=None,
    )
    record_router(routing_source, routing_confidence)
    record_unified_evidence_hits(synthetic_result["retrieved_evidence"])
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=str(synthetic_result["answer"]),
        intent=intent,
        routing_source=routing_source,
        routing_confidence=routing_confidence,
        routing_reason=routing_reason,
        candidate_intents=list(candidate_intents or [intent]),
        mode_used=mode_used,
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
        final_answer_source="task_queue_clarification" if needs_clarification else (f"{final_answer_source}_fallback" if renderer.get("used_fallback") else final_answer_source),
        tool_observations=list(synthetic_result.get("tool_observations") or observations),
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


def _create_async_dlp_task(payload: DlpTaskCreateRequest, actor_context: dict[str, Any] | None = None) -> dict:
    actor = build_actor_context(payload=payload, session_id=payload.session_id, conversation_id=payload.conversation_id)
    if actor_context:
        actor = ActorContext(**{**actor.to_dict(), **dict(actor_context or {})})
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
        mail_draft_id=str(payload.mail_draft_id or ""),
        idempotency_key=str(payload.idempotency_key or ""),
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        workspace_id=actor.workspace_id,
    )
    record_task_created()
    if payload.lab_run and payload.scenario_id:
        record_dlp_scenario_replay(payload.scenario_id)
    if status == "queued":
        try:
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
        except Exception as exc:
            task = _mark_task_enqueue_failed(
                task,
                service="celery",
                operation="enqueue_dlp_risk_task",
                error=str(exc),
                actor_context=actor.to_dict(),
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
    _persist_task_registry_object(task, actor_context=actor.to_dict())
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
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = _collect_outbound_candidates(payload, conversation_id, upload_context, actor_context=actor_context)
    referential_request = _looks_like_referential_outbound_request(payload.message)
    explicit_summary = _looks_like_summary_reference(payload.message)
    source_resolution = resolve_mail_source_request(
        message=str(payload.message or ""),
        candidates=candidates,
        legacy_referential_request=referential_request,
        explicit_summary=explicit_summary,
    )
    result = build_mail_action_plan(
        message=str(payload.message or ""),
        request_message=str(payload.message or ""),
        candidates=candidates,
        destination_email=_extract_destination_email(payload.message),
        referential_request=referential_request,
        explicit_summary=explicit_summary,
        send_both=_looks_like_send_both_request(payload.message),
        conversation_id=conversation_id,
        source_resolution=source_resolution,
    )
    return _normalize_communication_brief_mail_plan_result(result)


def _normalize_communication_brief_mail_plan_result(result: dict[str, Any]) -> dict[str, Any]:
    mail_plan = dict(result.get("mail_plan") or {})
    selected_candidate = dict(mail_plan.get("selected_candidate") or {})
    if str(selected_candidate.get("kind") or "") != COMMUNICATION_BRIEF_SOURCE_KIND:
        return result

    source_resolution = dict(mail_plan.get("source_resolution") or {})
    compose_mode = str(source_resolution.get("compose_mode") or mail_plan.get("compose_mode") or "recipient_ready_summary")
    if compose_mode == "direct_body":
        compose_mode = "recipient_ready_summary"
    reference_source = {
        "candidate_id": str(selected_candidate.get("candidate_id") or ""),
        "source_turn_id": str(selected_candidate.get("source_turn_id") or ""),
        "role": COMMUNICATION_BRIEF_SOURCE_KIND,
        "policy": compose_mode,
        "content": str(selected_candidate.get("content") or ""),
    }
    artifact = {
        "role": "selected_source",
        "kind": COMMUNICATION_BRIEF_SOURCE_KIND,
        "candidate_id": str(selected_candidate.get("candidate_id") or ""),
        "source_turn_id": str(selected_candidate.get("source_turn_id") or ""),
        "filename": str(selected_candidate.get("filename") or ""),
    }
    mail_plan.update(
        {
            "resolved_body": "" if compose_mode != "verbatim_copy" else str(mail_plan.get("resolved_body") or ""),
            "review_content": "" if compose_mode != "verbatim_copy" else str(mail_plan.get("review_content") or ""),
            "compose_mode": compose_mode,
            "reference_sources": [reference_source],
            "body_sources": [
                {
                    "kind": "reference_source",
                    "role": COMMUNICATION_BRIEF_SOURCE_KIND,
                    "policy": compose_mode,
                }
            ],
            "source_refs": list(dict.fromkeys([*list(mail_plan.get("source_refs") or []), COMMUNICATION_BRIEF_SOURCE_KIND])),
            "source_policy": {
                **dict(mail_plan.get("source_policy") or {}),
                "communication_brief_source": "renderer_reference_only",
            },
            "source_resolution": {
                **source_resolution,
                "source_mode": COMMUNICATION_BRIEF_SOURCE_KIND,
                "compose_mode": compose_mode,
            },
            "source_artifacts": [artifact],
            "provenance_refs": [artifact],
        }
    )
    return {**result, "mail_plan": mail_plan}


def _persist_mail_plan(
    *,
    session_id: str,
    conversation_id: str,
    mail_plan: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    stored = upsert_mail_draft(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=mail_plan,
        actor_context=actor_context,
        status=status,
    )
    persisted_plan = dict(stored.get("mail_plan") or mail_plan or {})
    persisted_plan["draft_id"] = str(stored.get("draft_id") or persisted_plan.get("draft_id") or "")
    persisted_plan["status"] = str(stored.get("status") or persisted_plan.get("status") or "")
    persisted_plan["draft_version"] = int(stored.get("version") or persisted_plan.get("draft_version") or 1)
    if stored.get("confirmation_id"):
        persisted_plan["confirmation_id"] = str(stored.get("confirmation_id") or "")
    if stored.get("confirmation_key"):
        persisted_plan["idempotency_key"] = str(stored.get("confirmation_key") or "")
    _persist_mail_draft_registry_object(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=persisted_plan,
        actor_context=actor_context,
    )
    return persisted_plan


def _persist_mail_draft_registry_object(
    *,
    session_id: str,
    conversation_id: str,
    mail_plan: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft_id = str(mail_plan.get("draft_id") or "").strip()
    if not draft_id:
        return {}
    resource_status = str(mail_plan.get("status") or "draft")
    continuations = ["patch", "cancel"]
    if resource_status == "pending_confirmation":
        continuations.insert(0, "confirm")
    return upsert_pending_object(
        object_type="mail_draft",
        object_id=f"mail-draft:{draft_id}",
        session_id=session_id,
        conversation_id=conversation_id,
        payload={
            "resource_type": "mail_draft",
            "resource_id": draft_id,
            "resource_status": resource_status,
            "mail_plan": dict(mail_plan),
        },
        actor_context=actor_context,
        status="active",
        salience=0.84,
        allowed_continuations=continuations,
        source_observation_ids=[
            str(item.get("candidate_id") or item.get("source_turn_id") or "")
            for item in list(mail_plan.get("provenance_refs") or [])
            if str(item.get("candidate_id") or item.get("source_turn_id") or "")
        ],
        supersede_same_type=True,
    )


def _persist_task_registry_object(
    task: dict[str, Any],
    *,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        return {}
    task_type = str(task.get("task_type") or "dlp_outbound")
    object_type = "domain_task" if task_type.startswith("domain_") else "dlp_task"
    task_actor = {
        "tenant_id": str(task.get("tenant_id") or ""),
        "user_id": str(task.get("user_id") or ""),
        "workspace_id": str(task.get("workspace_id") or ""),
        "session_id": str(task.get("session_id") or ""),
        "conversation_id": str(task.get("conversation_id") or ""),
        **dict(actor_context or {}),
    }
    task_status = str(task.get("status") or "queued")
    continuations = ["show_status"]
    if task_status in {"needs_clarification", "input_invalid"}:
        continuations.append("provide_missing_field")
    return upsert_pending_object(
        object_type=object_type,
        object_id=task_id,
        session_id=str(task.get("session_id") or ""),
        conversation_id=str(task.get("conversation_id") or ""),
        payload={
            "resource_type": object_type,
            "resource_id": task_id,
            "resource_status": task_status,
            "task_type": task_type,
            "task_id": task_id,
        },
        actor_context=task_actor,
        status="active",
        salience=0.58,
        allowed_continuations=continuations,
        supersede_same_type=False,
    )


def _persist_upload_artifact_object(
    *,
    session_id: str,
    conversation_id: str,
    upload_context: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blob_id = str(upload_context.get("upload_blob_id") or "").strip()
    if not blob_id:
        return {}
    return upsert_pending_object(
        object_type="upload_artifact",
        object_id=blob_id,
        session_id=session_id,
        conversation_id=conversation_id,
        payload={
            "resource_type": "upload_artifact",
            "resource_id": blob_id,
            "resource_status": "available",
            "filename": str(upload_context.get("filename") or ""),
            "content_type": str(upload_context.get("content_type") or ""),
            "parse_status": str(upload_context.get("parse_status") or ""),
        },
        actor_context=actor_context,
        status="active",
        salience=0.42,
        allowed_continuations=["reference", "attach"],
        supersede_same_type=False,
    )


def _persist_answer_artifact_object(
    *,
    session_id: str,
    conversation_id: str,
    turn_id: str,
    answer_summary: str,
    intent: str,
    citations: list[dict[str, Any]],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not turn_id or not answer_summary or intent in {"action_or_draft", "pending_object_cancelled"}:
        return {}
    return upsert_pending_object(
        object_type="answer_artifact",
        object_id=f"assistant-turn:{turn_id}",
        session_id=session_id,
        conversation_id=conversation_id,
        payload={
            "resource_type": "answer_artifact",
            "resource_id": turn_id,
            "resource_status": "available",
            "turn_id": turn_id,
            "intent": intent,
            "answer_summary": answer_summary,
            "citations": list(citations or []),
        },
        actor_context=actor_context,
        status="active",
        salience=0.36,
        allowed_continuations=["reference"],
        source_observation_ids=[str(item.get("doc_id") or "") for item in citations if str(item.get("doc_id") or "")],
        supersede_same_type=False,
    )


def _consume_mail_draft_registry_object(draft_id: str, actor_context: dict[str, Any]) -> None:
    if draft_id:
        consume_pending_object(f"mail-draft:{draft_id}", actor_context=actor_context)


def _active_registry_objects(
    conversation_id: str,
    actor_context: dict[str, Any],
) -> list[PendingObject]:
    objects = [
        pending
        for pending in (
            pending_object_from_record(item)
            for item in list_active_pending_objects(
                conversation_id=conversation_id,
                actor_context=actor_context,
            )
        )
        if pending is not None
    ]
    if not any(item.object_type == "mail_draft" for item in objects):
        draft = get_latest_active_mail_draft(conversation_id, actor_context=actor_context)
        if draft:
            _persist_mail_draft_registry_object(
                session_id=str(draft.get("session_id") or actor_context.get("session_id") or ""),
                conversation_id=conversation_id,
                mail_plan=dict(draft.get("mail_plan") or {}),
                actor_context=actor_context,
            )
            objects = [
                pending
                for pending in (
                    pending_object_from_record(item)
                    for item in list_active_pending_objects(
                        conversation_id=conversation_id,
                        actor_context=actor_context,
                    )
                )
                if pending is not None
            ]
    return objects


def _safe_pending_object_snapshot(
    item: dict[str, Any],
    *,
    actor_context: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(item.get("payload") or {})
    object_type = str(item.get("object_type") or "")
    resource_status = str(payload.get("resource_status") or item.get("status") or "")
    resource: dict[str, Any] = {}
    if object_type == "mail_draft":
        mail_plan = dict(payload.get("mail_plan") or {})
        resource = {
            "draft_id": str(mail_plan.get("draft_id") or payload.get("resource_id") or ""),
            "draft_state": str(mail_plan.get("draft_state") or ""),
            "status": str(mail_plan.get("status") or resource_status),
            "recipients": list(mail_plan.get("resolved_recipients") or []),
            "subject": str(mail_plan.get("resolved_subject") or ""),
            "missing_fields": list(mail_plan.get("missing_fields") or []),
            "confirmation_required": bool(mail_plan.get("requires_confirmation")),
        }
        resource_status = str(resource.get("status") or resource_status)
    elif object_type in {"dlp_task", "domain_task"}:
        task_id = str(payload.get("task_id") or payload.get("resource_id") or item.get("object_id") or "")
        task = get_dlp_task(task_id) or {}
        if task and (
            str(task.get("tenant_id") or "") == str(actor_context.get("tenant_id") or "")
            and str(task.get("workspace_id") or "") == str(actor_context.get("workspace_id") or "")
            and str(task.get("user_id") or "") == str(actor_context.get("user_id") or "")
        ):
            resource_status = str(task.get("status") or resource_status)
            resource = {
                "task_id": task_id,
                "task_type": str(task.get("task_type") or ""),
                "status": resource_status,
                "risk_level": str(task.get("risk_level") or ""),
                "delivery_status": str(task.get("delivery_status") or ""),
                "missing_fields": list(task.get("missing_fields") or []),
            }
    elif object_type == "upload_artifact":
        resource = {
            "blob_id": str(payload.get("resource_id") or item.get("object_id") or ""),
            "filename": str(payload.get("filename") or ""),
            "content_type": str(payload.get("content_type") or ""),
            "parse_status": str(payload.get("parse_status") or ""),
        }
    elif object_type == "answer_artifact":
        resource = {
            "turn_id": str(payload.get("turn_id") or payload.get("resource_id") or ""),
            "intent": str(payload.get("intent") or ""),
            "summary": compact_text(str(payload.get("answer_summary") or ""), 280),
        }
    else:
        resource = {
            "resource_id": str(payload.get("resource_id") or item.get("object_id") or ""),
            "missing_fields": list((payload.get("mail_plan") or {}).get("missing_fields") or []),
        }
    return {
        "object_id": str(item.get("object_id") or ""),
        "object_type": object_type,
        "status": str(item.get("status") or ""),
        "resource_status": resource_status,
        "allowed_continuations": list(item.get("allowed_continuations") or []),
        "salience": float(item.get("salience") or 0.0),
        "expires_at": str(item.get("expires_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "resource": resource,
    }


def _get_latest_pending_mail_confirmation(
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persisted = get_latest_active_mail_draft(
        conversation_id,
        actor_context=actor_context,
        pending_confirmation_only=True,
    )
    if persisted:
        return build_persisted_confirmation_payload(persisted)
    for turn in reversed(get_turns(conversation_id, limit=12)):
        if str(turn.get("role") or "").lower() != "assistant":
            continue
        debug_payload = dict(turn.get("debug_payload") or {})
        confirmation_payload = dict(debug_payload.get("confirmation_payload") or {})
        mail_plan = dict(confirmation_payload.get("mail_plan") or {})
        if confirmation_payload and mail_plan and str(mail_plan.get("mail_action_type") or "").startswith(("send_", "compose_")):
            return confirmation_payload
    return {}


def _persist_confirmation_object(
    *,
    session_id: str,
    conversation_id: str,
    confirmation_payload: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(confirmation_payload or {})
    if not payload:
        return {}
    kind = "mail" if dict(payload.get("mail_plan") or {}) else "domain"
    pending = pending_object_from_confirmation({"kind": kind, "payload": payload})
    if pending is None or not pending.object_id:
        return {}
    return upsert_pending_object(
        object_type=pending.object_type,
        object_id=pending.object_id,
        session_id=session_id,
        conversation_id=conversation_id,
        payload={"kind": kind, "confirmation_payload": payload},
        actor_context=actor_context,
        status="pending_confirmation",
        salience=pending.salience,
        allowed_continuations=list(pending.allowed_continuations),
        supersede_same_type=True,
    )


def _get_latest_registry_confirmation(
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidates = [
        item
        for item in (
            get_latest_active_pending_object(
                conversation_id=conversation_id,
                object_type="mail_confirmation",
                actor_context=actor_context,
            ),
            get_latest_active_pending_object(
                conversation_id=conversation_id,
                object_type="domain_confirmation",
                actor_context=actor_context,
            ),
        )
        if item
    ]
    if not candidates:
        return {}
    stored = max(candidates, key=lambda item: (str(item.get("updated_at") or ""), float(item.get("salience") or 0.0)))
    payload = dict(stored.get("payload") or {})
    confirmation_payload = dict(payload.get("confirmation_payload") or {})
    kind = str(payload.get("kind") or ("mail" if confirmation_payload.get("mail_plan") else "domain"))
    if not confirmation_payload or kind not in {"mail", "domain"}:
        return {}
    return {
        "kind": kind,
        "payload": confirmation_payload,
        "object_id": str(stored.get("object_id") or ""),
        "persistence_source": "pending_objects",
        "registry_status": str(stored.get("status") or ""),
    }


def _get_latest_pending_confirmation(
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persisted_registry = _get_latest_registry_confirmation(conversation_id, actor_context)
    if persisted_registry:
        return persisted_registry
    persisted_mail = _get_latest_pending_mail_confirmation(conversation_id, actor_context)
    for turn in reversed(get_turns(conversation_id, limit=12)):
        if str(turn.get("role") or "").lower() != "assistant":
            continue
        debug_payload = dict(turn.get("debug_payload") or {})
        confirmation_payload = dict(debug_payload.get("confirmation_payload") or {})
        if not confirmation_payload:
            continue
        mail_plan = dict(confirmation_payload.get("mail_plan") or {})
        if mail_plan and str(mail_plan.get("mail_action_type") or "").startswith(("send_", "compose_")):
            return {"kind": "mail", "payload": persisted_mail or confirmation_payload}
        tool_name = str(confirmation_payload.get("tool_name") or confirmation_payload.get("action_name") or "")
        if tool_name.startswith(("meeting_", "calendar_")):
            return {"kind": "domain", "payload": confirmation_payload}
    if persisted_mail:
        return {"kind": "mail", "payload": persisted_mail}
    return {}


def _persist_source_clarification_object(
    *,
    session_id: str,
    conversation_id: str,
    mail_plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_resolution = dict(mail_plan.get("source_resolution") or {})
    if not source_resolution.get("needs_clarification") or not candidates:
        return {}
    object_id = f"source-clarification:{mail_plan.get('draft_id') or uuid.uuid4().hex[:16]}"
    payload = {
        "mail_plan": dict(mail_plan),
        "candidates": list(candidates),
        "source_resolution": source_resolution,
        "request_message": str(mail_plan.get("request_message") or ""),
    }
    return upsert_pending_object(
        object_type="source_clarification",
        object_id=object_id,
        session_id=session_id,
        conversation_id=conversation_id,
        payload=payload,
        actor_context=actor_context,
        status="needs_clarification",
        salience=0.92,
        allowed_continuations=["choose_source", "cancel"],
        supersede_same_type=True,
    )


def _mail_field_clarification_type(mail_plan: dict[str, Any]) -> str:
    missing_fields = {str(field) for field in list(mail_plan.get("missing_fields") or []) if str(field)}
    if "recipient" in missing_fields:
        return "recipient_clarification"
    if missing_fields.intersection({"content", "content_or_attachment", "body_source", "recipient_ready_body"}):
        return "body_clarification"
    return ""


def _persist_mail_field_clarification_object(
    *,
    session_id: str,
    conversation_id: str,
    mail_plan: dict[str, Any],
    candidates: list[dict[str, Any]],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if dict(mail_plan.get("source_resolution") or {}).get("needs_clarification") and candidates:
        return {}
    object_type = _mail_field_clarification_type(mail_plan)
    if not object_type:
        return {}
    object_id = f"{object_type}:{mail_plan.get('draft_id') or uuid.uuid4().hex[:16]}"
    payload = {
        "object_type": object_type,
        "mail_plan": dict(mail_plan),
        "candidates": list(candidates),
        "request_message": str(mail_plan.get("request_message") or ""),
    }
    return upsert_pending_object(
        object_type=object_type,
        object_id=object_id,
        session_id=session_id,
        conversation_id=conversation_id,
        payload=payload,
        actor_context=actor_context,
        status="needs_clarification",
        salience=0.91 if object_type == "recipient_clarification" else 0.88,
        allowed_continuations=["provide_missing_field", "cancel"],
        supersede_same_type=True,
    )


def _get_latest_pending_source_clarification(
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persisted = get_latest_active_pending_object(
        conversation_id=conversation_id,
        object_type="source_clarification",
        actor_context=actor_context,
    )
    if persisted:
        return {
            "kind": "source_clarification",
            "payload": dict(persisted.get("payload") or {}),
            "object_id": str(persisted.get("object_id") or ""),
            "persistence_source": "pending_objects",
        }
    for turn in reversed(get_turns(conversation_id, limit=12)):
        if str(turn.get("role") or "").lower() != "assistant":
            continue
        debug_payload = dict(turn.get("debug_payload") or {})
        if not bool(debug_payload.get("needs_clarification")):
            continue
        task_plan = dict(debug_payload.get("task_plan") or {})
        mail_plan = dict(task_plan.get("mail_plan") or {})
        source_resolution = dict(mail_plan.get("source_resolution") or {})
        if not source_resolution.get("needs_clarification"):
            continue
        candidates = list(task_plan.get("candidates") or [])
        if not candidates:
            for tool_call in list(debug_payload.get("tool_calls") or []):
                result = dict(tool_call.get("result") or {})
                candidates = list(result.get("candidates") or [])
                if candidates:
                    break
        if not candidates:
            continue
        return {
            "kind": "source_clarification",
            "payload": {
                "mail_plan": mail_plan,
                "candidates": candidates,
                "source_resolution": source_resolution,
                "request_message": str(mail_plan.get("request_message") or ""),
            },
            "object_id": str(mail_plan.get("draft_id") or ""),
            "persistence_source": "conversation_debug",
        }
    return {}


def _get_latest_pending_mail_field_clarification(
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    for object_type in ("recipient_clarification", "body_clarification"):
        persisted = get_latest_active_pending_object(
            conversation_id=conversation_id,
            object_type=object_type,
            actor_context=actor_context,
        )
        if persisted:
            return {
                "kind": object_type,
                "payload": {
                    **dict(persisted.get("payload") or {}),
                    "object_id": str(persisted.get("object_id") or ""),
                    "persistence_source": "pending_objects",
                },
                "object_id": str(persisted.get("object_id") or ""),
                "persistence_source": "pending_objects",
            }
    return {}


def _first_resolved_recipient(mail_plan: dict[str, Any]) -> str:
    recipients = list(mail_plan.get("resolved_recipients") or [])
    if recipients:
        return str(recipients[0] or "").strip()
    return ""


def _remove_missing_fields(mail_plan: dict[str, Any], fields: set[str]) -> None:
    mail_plan["missing_fields"] = [
        field
        for field in list(mail_plan.get("missing_fields") or [])
        if str(field) not in fields
    ]


def _mail_plan_ready_for_confirmation(mail_plan: dict[str, Any]) -> bool:
    return bool(list(mail_plan.get("resolved_recipients") or [])) and bool(str(mail_plan.get("resolved_body") or "").strip()) and not list(mail_plan.get("missing_fields") or [])


def _resolve_pending_mail_field_clarification(
    *,
    user_message: str,
    pending_payload: dict[str, Any],
) -> dict[str, Any]:
    mail_plan = dict(pending_payload.get("mail_plan") or {})
    candidates = list(pending_payload.get("candidates") or [])
    object_type = str(pending_payload.get("object_type") or "")
    if not mail_plan:
        return {"ok": False, "needs_clarification": True, "mail_plan": {"missing_fields": ["mail_plan"]}, "candidates": candidates}
    if object_type == "recipient_clarification":
        recipient = _extract_destination_email(user_message)
        if not recipient:
            mail_plan["missing_fields"] = sorted({*list(mail_plan.get("missing_fields") or []), "recipient"})
            return {"ok": False, "needs_clarification": True, "mail_plan": mail_plan, "candidates": candidates}
        mail_plan["resolved_recipients"] = [recipient]
        _remove_missing_fields(mail_plan, {"recipient"})
    elif object_type == "body_clarification":
        body = _extract_inline_mail_body(user_message) or str(user_message or "").strip()
        if not body:
            mail_plan["missing_fields"] = sorted({*list(mail_plan.get("missing_fields") or []), "content"})
            return {"ok": False, "needs_clarification": True, "mail_plan": mail_plan, "candidates": candidates}
        mail_plan["resolved_body"] = body
        mail_plan["review_content"] = build_review_content(body, dict(mail_plan.get("selected_candidate") or {}) or None, dict(mail_plan.get("attachment_candidate") or {}) or None)
        mail_plan["selected_candidate"] = {
            "kind": "user_inline_text",
            "candidate_id": f"user-inline-continuation:{uuid.uuid4().hex[:8]}",
            "source_turn_id": "",
            "label": "用户补充的正文",
            "content": body,
            "filename": "",
            "content_type": "text/plain",
            "supports_attachment": False,
            "upload_blob_id": "",
        }
        mail_plan["target_object"] = "user_inline_text"
        mail_plan["source_refs"] = ["user_inline_text"]
        _remove_missing_fields(mail_plan, {"content", "content_or_attachment", "body_source", "recipient_ready_body"})
    else:
        return {"ok": False, "needs_clarification": True, "mail_plan": mail_plan, "candidates": candidates}
    if _mail_plan_ready_for_confirmation(mail_plan):
        mail_plan["status"] = "pending_confirmation"
        mail_plan["requires_confirmation"] = True
        return {"ok": True, "mode": "confirmation_required", "mail_plan": mail_plan, "candidates": candidates}
    mail_plan["status"] = "needs_clarification"
    return {"ok": False, "needs_clarification": True, "mail_plan": mail_plan, "candidates": candidates}


def _resolve_pending_mail_source_clarification(
    *,
    user_message: str,
    pending_payload: dict[str, Any],
    conversation_id: str,
) -> dict[str, Any]:
    mail_plan = dict(pending_payload.get("mail_plan") or {})
    candidates = list(pending_payload.get("candidates") or [])
    original_message = str(mail_plan.get("request_message") or pending_payload.get("request_message") or "").strip()
    if not original_message:
        original_message = user_message
    source_resolution = resolve_mail_source_request(
        message=user_message,
        candidates=candidates,
        legacy_referential_request=True,
        explicit_summary=False,
    )
    destination_email = _first_resolved_recipient(mail_plan) or _extract_destination_email(original_message)
    return build_mail_action_plan(
        message=original_message,
        request_message=original_message,
        candidates=candidates,
        destination_email=destination_email,
        referential_request=True,
        explicit_summary=_looks_like_summary_reference(original_message),
        send_both=_looks_like_send_both_request(original_message),
        conversation_id=conversation_id,
        source_resolution=source_resolution,
    )


def _create_domain_task_from_confirmation(
    *,
    session_id: str,
    conversation_id: str,
    request_message: str,
    confirmation_payload: dict[str, Any],
    actor_context: dict[str, Any],
) -> dict[str, Any]:
    action = str(confirmation_payload.get("tool_name") or confirmation_payload.get("action_name") or "").strip()
    tool_input = dict(confirmation_payload.get("tool_input") or {})
    idempotency_key = str(confirmation_payload.get("idempotency_key") or tool_input.get("idempotency_key") or "").strip()
    if not action.startswith("meeting_"):
        raise HTTPException(status_code=400, detail={"error": "unsupported_domain_confirmation", "action": action})
    if idempotency_key:
        for existing in list_dlp_tasks(
            session_id=session_id,
            tenant_id=str(actor_context.get("tenant_id") or ""),
            workspace_id=str(actor_context.get("workspace_id") or ""),
        ):
            if str(existing.get("task_type") or "") != "domain_meeting":
                continue
            payload = dict(existing.get("domain_payload") or {})
            if isinstance(payload.get("tool_input"), dict):
                payload = dict(payload.get("tool_input") or {})
            if str(payload.get("idempotency_key") or "") == idempotency_key:
                return existing
    domain_payload = {
        **tool_input,
        "communication_role": "escalation_provider",
        "communication_input_kind": "meeting_escalation_candidate",
        "_confirmed_action": action,
        "_dag_plan": dict(confirmation_payload.get("dag_plan") or {}),
        "_confirmation_payload": {
            "risk": str(confirmation_payload.get("risk") or ""),
            "confirmation_required": bool(confirmation_payload.get("confirmation_required", True)),
        },
    }

    task = create_dlp_task(
        task_type="domain_meeting",
        session_id=session_id,
        conversation_id=conversation_id,
        message_raw=request_message,
        request_message=request_message,
        destination_email="",
        requested_action=action,
        status="queued",
        domain_action=action,
        domain_payload=domain_payload,
        tenant_id=str(actor_context.get("tenant_id") or ""),
        user_id=str(actor_context.get("user_id") or ""),
        workspace_id=str(actor_context.get("workspace_id") or ""),
    )
    _persist_task_registry_object(task, actor_context=actor_context)
    try:
        enqueue_meeting_task(str(task["task_id"]))
    except Exception as exc:
        task = _mark_task_enqueue_failed(
            task,
            service="celery",
            operation="enqueue_meeting_task",
            error=str(exc),
            actor_context=actor_context,
        )
    _persist_task_registry_object(task, actor_context=actor_context)
    return task


def _get_latest_pending_mail_draft(
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persisted = get_latest_active_mail_draft(conversation_id, actor_context=actor_context)
    if persisted:
        return dict(persisted.get("mail_plan") or {})
    confirmation_payload = _get_latest_pending_mail_confirmation(conversation_id, actor_context)
    if confirmation_payload.get("mail_plan"):
        return dict(confirmation_payload.get("mail_plan") or {})
    for turn in reversed(get_turns(conversation_id, limit=12)):
        if str(turn.get("role") or "").lower() != "assistant":
            continue
        debug_payload = dict(turn.get("debug_payload") or {})
        mail_plan = dict((debug_payload.get("task_plan") or {}).get("mail_plan") or {})
        if mail_plan and str(mail_plan.get("mail_action_type") or "").startswith(("send_", "compose_")):
            return mail_plan
    return {}


def _latest_completed_meeting_task(
    *,
    session_id: str,
    conversation_id: str,
    actor_context: dict[str, Any],
) -> dict[str, Any]:
    for task in list_dlp_tasks(
        session_id=session_id,
        tenant_id=str(actor_context.get("tenant_id") or ""),
        workspace_id=str(actor_context.get("workspace_id") or ""),
        status="completed",
    ):
        if str(task.get("task_type") or "") != "domain_meeting":
            continue
        if str(task.get("conversation_id") or "") != conversation_id:
            continue
        if str(task.get("domain_action") or "") != "meeting_create_tencent_meeting":
            continue
        return task
    return {}


def _meeting_result_details(task: dict[str, Any]) -> dict[str, Any]:
    domain_result = dict(task.get("domain_result") or {})
    result = dict(domain_result.get("result") or {})
    details = {
        "communication_role": str(result.get("communication_role") or "escalation_provider"),
        "communication_input_kind": str(result.get("communication_input_kind") or "meeting_result"),
        "meeting_id": str(result.get("meeting_id") or ""),
        "meeting_code": str(result.get("meeting_code") or ""),
        "meeting_url": str(result.get("meeting_url") or result.get("join_url") or ""),
        "subject": str(result.get("subject") or ""),
        "start_time": str(result.get("start_time") or ""),
        "end_time": str(result.get("end_time") or ""),
        "summary": str(result.get("summary") or result.get("content_text") or task.get("final_result") or ""),
        "provider": str(result.get("provider") or "tencent_meeting_mcp"),
    }
    if details["meeting_id"] and details["meeting_url"]:
        return details
    parsed = _parse_meeting_content_text(str(result.get("content_text") or ""))
    for key, value in parsed.items():
        if not details.get(key):
            details[key] = value
    normalized = dict(result.get("normalized_request") or {})
    if not details["subject"]:
        details["subject"] = str(normalized.get("topic") or "")
    if not details["start_time"]:
        details["start_time"] = str(normalized.get("start_time") or "")
    if not details["end_time"]:
        details["end_time"] = str(normalized.get("end_time") or "")
    return details


def _parse_meeting_content_text(content_text: str) -> dict[str, str]:
    if not content_text:
        return {}
    try:
        outer = json.loads(content_text)
    except Exception:
        return {}
    payload = outer
    body = outer.get("body") if isinstance(outer, dict) else None
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except Exception:
            payload = outer
    if not isinstance(payload, dict):
        return {}
    meetings = payload.get("meeting_info_list")
    meeting = meetings[0] if isinstance(meetings, list) and meetings and isinstance(meetings[0], dict) else payload
    return {
        "meeting_id": str(meeting.get("meeting_id") or ""),
        "meeting_code": str(meeting.get("meeting_code") or ""),
        "meeting_url": str(meeting.get("join_url") or meeting.get("meeting_url") or ""),
        "subject": str(meeting.get("subject") or ""),
        "start_time": str(meeting.get("start_time") or ""),
        "end_time": str(meeting.get("end_time") or ""),
    }


def _meeting_invitation_draft_result(task: dict[str, Any]) -> dict[str, Any]:
    domain_result = dict(task.get("domain_result") or {})
    for item in reversed(list(domain_result.get("post_confirm_results") or [])):
        if not isinstance(item, dict):
            continue
        if str(item.get("action") or "") == "mail_invitation_draft":
            return dict(item.get("result") or {})
    return {}


def _mail_plan_from_meeting_task(
    *,
    task: dict[str, Any],
    conversation_id: str,
    request_message: str,
    recipient: str = "",
) -> dict[str, Any]:
    details = _meeting_result_details(task)
    draft_result = _meeting_invitation_draft_result(task)
    draft_state = dict(draft_result.get("draft_state") or {})
    meeting = dict(draft_state.get("meeting") or {})
    for key, value in details.items():
        if value and not meeting.get(key):
            meeting[key] = value
    subject = str(draft_state.get("subject") or meeting.get("subject") or details.get("subject") or "腾讯会议邀请")
    if "会议邀请" not in subject:
        subject = f"{subject} - 会议邀请"
    meeting_text = json.dumps(
        {
            "communication_role": "escalation_provider",
            "communication_input_kind": "meeting_result",
            "topic": meeting.get("topic") or meeting.get("subject") or details.get("subject") or "",
            "meeting_id": meeting.get("meeting_id") or details.get("meeting_id") or "",
            "meeting_code": meeting.get("meeting_code") or details.get("meeting_code") or "",
            "meeting_url": meeting.get("meeting_url") or details.get("meeting_url") or "",
            "start_time": meeting.get("start_time") or details.get("start_time") or "",
            "end_time": meeting.get("end_time") or details.get("end_time") or "",
            "provider_summary": meeting.get("provider_summary") or details.get("summary") or "",
        },
        ensure_ascii=False,
    )
    recipients = [recipient] if recipient else []
    missing_fields = [] if recipients else ["recipient"]
    return {
        "draft_id": f"meeting-{task.get('task_id')}",
        "conversation_id": conversation_id,
        "status": "pending_confirmation" if recipients else "draft",
        "draft_state": "confirm" if recipients else "draft",
        "patch_kind": "",
        "mail_action_type": "send_meeting_invitation",
        "target_object": "meeting_invitation",
        "communication_role": "escalation_provider",
        "communication_input_kind": "meeting_result",
        "communication_closeout_owner": "mail_agent",
        "resolved_recipients": recipients,
        "resolved_subject": subject,
        "resolved_body": "",
        "resolved_attachments": [],
        "source_refs": [str(task.get("task_id") or ""), "meeting_result"],
        "requires_confirmation": bool(recipients),
        "unsupported_reason": "",
        "unsupported_code": "",
        "missing_fields": missing_fields,
        "review_content": "",
        "request_message": request_message,
        "selected_candidate": {
            "kind": "meeting_result",
            "communication_role": "escalation_provider",
            "communication_input_kind": "meeting_result",
            "candidate_id": f"meeting-task:{task.get('task_id')}",
            "label": subject,
            "filename": "",
            "content_type": "application/json",
            "content": meeting_text,
        },
        "attachment_candidate": None,
        "resolution_rule": "meeting_result_invitation",
        "body_constraints": {
            "source_policy": "meeting_result_only",
            "send_requires_dlp": True,
            "send_requires_confirmation": True,
        },
        "body_sources": [
            {
                "kind": "reference_source",
                "role": "meeting_invitation_details",
                "communication_role": "escalation_provider",
                "communication_input_kind": "meeting_result",
                "policy": "recipient_ready_summary",
                "task_id": str(task.get("task_id") or ""),
            }
        ],
        "compose_mode": "recipient_ready_summary",
        "reference_sources": [
            {
                "candidate_id": f"meeting-task:{task.get('task_id')}",
                "source_turn_id": "",
                "role": "meeting_result",
                "communication_role": "escalation_provider",
                "communication_input_kind": "meeting_result",
                "policy": "recipient_ready_summary",
                "content": meeting_text,
            }
        ],
        "source_artifacts": [
            {
                "role": "meeting_invitation_details",
                "kind": "meeting_result",
                "communication_role": "escalation_provider",
                "communication_input_kind": "meeting_result",
                "candidate_id": f"meeting-task:{task.get('task_id')}",
                "source_turn_id": "",
                "filename": "",
            }
        ],
        "provenance_refs": [
            {
                "role": "meeting_invitation_details",
                "kind": "meeting_result",
                "communication_role": "escalation_provider",
                "communication_input_kind": "meeting_result",
                "candidate_id": f"meeting-task:{task.get('task_id')}",
                "source_turn_id": "",
                "filename": "",
            }
        ],
        "source_policy": {
            "meeting_result": "meeting_result_only",
            "attachment_source": "attachment_only",
            "body_source": "user_explicit_or_meeting_result",
            "closeout_owner": "mail_agent",
        },
        "meeting_result": details,
        "domain_task_id": str(task.get("task_id") or ""),
    }


def _extract_email_from_message(message: str) -> str:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", message or "", re.IGNORECASE)
    return match.group(0) if match else ""


def _looks_like_meeting_result_followup(message: str) -> bool:
    lowered = (message or "").lower()
    return any(
        token in lowered or token in (message or "")
        for token in ("会议结果", "会议链接", "创建好了吗", "结果", "meeting result", "meeting link", "created meeting", "what happened")
    )


def _looks_like_meeting_invitation_continuation(message: str) -> bool:
    text = message or ""
    lowered = text.lower()
    if _extract_email_from_message(text):
        return True
    return any(
        token in lowered or token in text
        for token in (
            "发给",
            "发送给",
            "邮件给",
            "邀请",
            "通知",
            "send to",
            "email to",
            "invite",
            "send invitation",
        )
    )


def _build_pending_object_cancelled_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    pending_object: PendingObject,
    actor_context: dict[str, Any],
) -> UnifiedAgentResponse:
    observation = make_typed_observation(
        observation_type="pending_object_cancelled",
        source="continuation_resolver",
        summary="The active pending object was cancelled by the user.",
        payload={
            "object_id": pending_object.object_id,
            "object_type": pending_object.object_type,
            "status": "cancelled",
            "next_actions": ["start_new_task"],
        },
        provenance={"source": "pending_objects", "object_id": pending_object.object_id},
        confidence=0.99,
        actor_context=actor_context,
    )
    route_decision = {
        "routing_source": "continuation_resolver",
        "intent": "pending_object_cancelled",
        "confidence": 0.99,
        "router_reason": "The user cancelled the active pending object before any new task planning.",
    }
    result = {
        "intent": "pending_object_cancelled",
        "observations": [observation],
        "tool_observations": [observation],
        "tool_calls": [
            {
                "tool_name": "continuation_resolver",
                "success": True,
                "status": "cancelled",
                "error": "",
                "result": dict(observation.get("payload") or {}),
            }
        ],
        "task_plan": {"cancelled_pending_object": pending_object.to_dict()},
        "termination_reason": "pending_object_cancelled",
        "final_answer_source": "pending_object_cancelled_renderer",
    }
    return _build_fast_path_response(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        display_message=display_message,
        route_decision=route_decision,
        result=result,
        latency_ms=0.0,
        actor_context=actor_context,
    )


def _build_continuation_clarification_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    decision: Any,
    pending_objects: list[PendingObject],
    actor_context: dict[str, Any],
) -> UnifiedAgentResponse:
    observation = make_typed_observation(
        observation_type="continuation_resolution_required",
        source="continuation_resolver",
        summary="An active object exists, but the latest message cannot be safely classified as an edit or a new task.",
        payload={
            "decision": decision.to_dict(),
            "active_objects": [
                {
                    "object_id": item.object_id,
                    "object_type": item.object_type,
                    "status": item.status,
                    "allowed_continuations": list(item.allowed_continuations),
                }
                for item in pending_objects[:6]
            ],
            "next_actions": ["clarify_edit_existing_object", "clarify_start_new_task"],
        },
        provenance={"source": "pending_objects", "object_id": str(decision.object_id or "")},
        confidence=0.99,
        actor_context=actor_context,
    )
    route_decision = {
        "routing_source": "continuation_resolver",
        "intent": "continuation_resolution_required",
        "confidence": 0.99,
        "router_reason": "The durable state layer requires clarification before any planner or side effect can continue.",
    }
    result = {
        "intent": "continuation_resolution_required",
        "observations": [observation],
        "tool_observations": [observation],
        "tool_calls": [
            {
                "tool_name": "continuation_resolver",
                "success": True,
                "status": "clarification_required",
                "error": "",
                "result": dict(observation.get("payload") or {}),
            }
        ],
        "needs_clarification": True,
        "task_plan": {"continuation_resolution": dict(observation.get("payload") or {})},
        "termination_reason": "continuation_resolution_required",
        "final_answer_source": "continuation_resolution_renderer",
    }
    return _build_fast_path_response(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        display_message=display_message,
        route_decision=route_decision,
        result=result,
        latency_ms=0.0,
        actor_context=actor_context,
    )


def _enterprise_renderer_observation_payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result.get("enterprise_answer_observation") or build_enterprise_answer_observation(result))
    payload.pop("citations_brief", None)
    payload.pop("retrieval_summary", None)
    return payload


def _enterprise_renderer_citations(result: dict[str, Any]) -> list[dict[str, Any]]:
    payload = dict(result.get("enterprise_answer_observation") or build_enterprise_answer_observation(result))
    return [dict(item) for item in list(payload.get("citations_brief") or [])]


def _enterprise_renderer_observation_summary(payload: dict[str, Any]) -> str:
    facts = list(payload.get("canonical_facts") or [])
    if facts:
        return str(dict(facts[0] or {}).get("normalized_fact") or "")[:240]
    state = dict(payload.get("answer_state") or {})
    if state.get("missing_evidence"):
        return "当前企业知识检索未形成可回答证据。"
    return "已检索企业知识证据并生成结构化事实。"


def _observation_kind(observation: dict[str, Any]) -> str:
    return str(observation.get("observation_type") or observation.get("kind") or "").strip()


def _runtime_brief_skip_reason(result: dict[str, Any]) -> str:
    if bool(result.get("needs_clarification")):
        return "clarification"
    if dict(result.get("pending_confirmation") or result.get("confirmation_payload") or {}):
        return "pending_confirmation"
    intent = str(result.get("intent") or result.get("router_intent") or "")
    mode = str(result.get("mode_used") or "")
    termination = str(result.get("termination_reason") or "")
    final_source = str(result.get("final_answer_source") or "")
    structured_values = {intent, mode, termination, final_source}
    if structured_values & {
        "action_or_draft",
        "mail_action",
        "mail_inbound",
        "outbound_resolution",
        "task_queue",
        "meeting_result",
        "pending_object_cancelled",
        "continuation_resolution_required",
    }:
        return "action_or_mail_flow"
    if any(value.startswith("mail_") or value.startswith("inbound_mail") or value.startswith("outbound_mail") for value in structured_values):
        return "mail_flow"
    if any(value.startswith("task_") or value.startswith("domain_") or value.startswith("dlp_") for value in structured_values):
        return "task_flow"
    task_plan = dict(result.get("task_plan") or {})
    if task_plan.get("mail_plan") or task_plan.get("pending_mail_draft"):
        return "mail_plan"
    for observation in _collect_result_observations(result, task_plan):
        kind = _observation_kind(observation)
        if kind in {
            "mail_status_result",
            "mail_read_result",
            "mail_action_plan",
            "mail_confirmation",
            "mail_draft",
            "meeting_result",
            "continuation_resolution_required",
            "confirmation_required",
        }:
            return "action_or_mail_observation"
    return ""


def _runtime_grounding_observation(result: dict[str, Any]) -> dict[str, Any]:
    task_plan = dict(result.get("task_plan") or {})
    for observation in _collect_result_observations(result, task_plan):
        kind = _observation_kind(observation)
        if kind == "enterprise_answer_observation":
            return dict(observation)
        payload = dict(observation.get("payload") or {})
        if (
            str(payload.get("communication_role") or "") == "grounding_provider"
            or str(payload.get("communication_input_kind") or "") == "grounding_bundle"
        ):
            return dict(observation)

    enterprise_payload = dict(result.get("enterprise_answer_observation") or {})
    if enterprise_payload:
        return {
            "observation_type": "enterprise_answer_observation",
            "source": "enterprise_rag_query",
            "grounding_kind": "retrieval",
            "summary": _enterprise_renderer_observation_summary(enterprise_payload),
            "payload": enterprise_payload,
            "citations": list(enterprise_payload.get("selected_evidence") or enterprise_payload.get("citations_brief") or []),
            "confidence": float(dict(enterprise_payload.get("answer_state") or {}).get("confidence") or 0.0),
        }

    citations = [dict(item) for item in list(result.get("citations") or []) if isinstance(item, dict)]
    retrieved_evidence = [dict(item) for item in list(result.get("retrieved_evidence") or []) if isinstance(item, dict)]
    if not citations and not retrieved_evidence:
        return {}
    evidence_refs = citations or retrieved_evidence
    answer_summary = compact_text(str(result.get("answer") or ""), 320).strip()
    return {
        "observation_type": "enterprise_answer_observation",
        "source": "runtime_grounded_answer",
        "grounding_kind": "retrieval",
        "summary": answer_summary,
        "payload": {
            "communication_role": "grounding_provider",
            "communication_input_kind": "grounding_bundle",
            "answer_state": {
                "answerable": True,
                "missing_evidence": False,
                "confidence": float(result.get("routing_confidence") or 0.72),
                "final_answer_source": str(result.get("final_answer_source") or ""),
            },
            "canonical_facts": [
                {
                    "fact_id": "answer_summary",
                    "fact_type": "runtime_answer",
                    "normalized_fact": answer_summary,
                    "priority": "high",
                    "score": float(result.get("routing_confidence") or 0.72),
                    "source_fact_ids": [],
                }
            ]
            if answer_summary
            else [],
            "evidence_manifest": [
                {
                    "doc_id": str(item.get("doc_id") or item.get("id") or ""),
                    "chunk_id": str(item.get("chunk_id") or ""),
                    "source_type": str(item.get("source_type") or item.get("kind") or ""),
                    "title": str(item.get("title") or item.get("source_title") or ""),
                    "score": float(item.get("score") or 0.0),
                }
                for item in evidence_refs[:8]
            ],
            "selected_evidence": evidence_refs[:8],
        },
        "citations": citations[:8],
        "confidence": float(result.get("routing_confidence") or 0.72),
    }


def _append_runtime_communication_brief(
    result: dict[str, Any],
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> None:
    task_plan = dict(result.get("task_plan") or {})
    observations = _collect_result_observations(result, task_plan)
    if any(_observation_kind(item) == COMMUNICATION_BRIEF_SOURCE_KIND for item in observations):
        return
    if _runtime_brief_skip_reason(result):
        return
    grounding_observation = _runtime_grounding_observation(result)
    if not grounding_observation:
        return
    source_observation_ids = []
    for item in [grounding_observation, *observations]:
        payload = dict(item.get("payload") or {})
        provenance = dict(item.get("provenance") or {})
        source_id = (
            str(provenance.get("correlation_id") or "")
            or str(payload.get("correlation_id") or "")
            or str(payload.get("brief_id") or "")
            or str(item.get("source") or "")
        )
        if source_id and source_id not in source_observation_ids:
            source_observation_ids.append(source_id)
    try:
        _, observation = assemble_communication_brief_observation(
            employee_goal=str(result.get("display_message") or result.get("message") or ""),
            conversation_id=conversation_id,
            grounding_observation=grounding_observation,
            memory_context=dict(result.get("memory_context") or {}),
            actor_context=dict(actor_context or result.get("actor_context") or {}),
            source_observation_ids=source_observation_ids[:8],
            persist_snapshot=True,
            refresh_reason="runtime_closeout",
        )
    except Exception as exc:  # pragma: no cover - closeout should not block answer persistence
        logger.warning("Communication brief closeout skipped: %s", exc)
        return
    result.setdefault("tool_observations", []).append(asdict(observation))


def _build_meeting_result_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    task: dict[str, Any],
    actor_context: dict[str, Any],
) -> UnifiedAgentResponse:
    details = _meeting_result_details(task)
    pending_mail_draft = _mail_plan_from_meeting_task(
        task=task,
        conversation_id=conversation_id,
        request_message=message,
        recipient="",
    )
    observation = make_typed_observation(
        observation_type="meeting_result",
        source="domain_meeting_task",
        summary="A completed meeting creation task is available for the current conversation.",
        payload={
            "communication_role": "escalation_provider",
            "communication_input_kind": "meeting_result",
            "communication_closeout_owner": "mail_agent",
            "task_id": str(task.get("task_id") or ""),
            "task_status": str(task.get("status") or ""),
            "domain_action": str(task.get("domain_action") or ""),
            "meeting": details,
            "pending_mail_draft": pending_mail_draft,
            "next_actions": ["show_meeting_result", "patch_invitation_recipients", "send_invitation_after_dlp"],
            "missing_fields": list(pending_mail_draft.get("missing_fields") or []),
        },
        provenance={
            "source": "task_store",
            "task_id": str(task.get("task_id") or ""),
            "task_type": str(task.get("task_type") or ""),
        },
        confidence=0.95,
        actor_context=actor_context,
    )
    route_decision = {
        "routing_source": "meeting_result_continuation",
        "intent": "meeting_result",
        "confidence": 0.96,
        "router_reason": "The current conversation has a completed meeting task and the user asked for the meeting result.",
    }
    result = {
        "intent": "meeting_result",
        "observations": [observation],
        "tool_observations": [observation],
        "tool_calls": [
            {
                "tool_name": "domain_meeting_task",
                "success": True,
                "status": "completed",
                "error": "",
                "result": {
                    "communication_role": "escalation_provider",
                    "communication_input_kind": "meeting_result",
                    "communication_closeout_owner": "mail_agent",
                    "meeting": details,
                    "pending_mail_draft": pending_mail_draft,
                },
            }
        ],
        "task_plan": {"pending_mail_draft": pending_mail_draft},
        "termination_reason": "meeting_result_ready",
        "final_answer_source": "meeting_result_renderer",
    }
    return _build_fast_path_response(
        session_id=session_id,
        conversation_id=conversation_id,
        message=message,
        display_message=display_message,
        route_decision=route_decision,
        result=result,
        latency_ms=0.0,
        actor_context=actor_context,
    )


def _render_mail_plan_with_llm(
    *,
    message: str,
    mail_plan: dict[str, Any],
    render_mode: str,
    observations: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rendered_plan = dict(mail_plan or {})
    render_candidates = list(candidates or [])
    selected_candidate = dict(rendered_plan.get("selected_candidate") or {})
    if str(selected_candidate.get("kind") or "") == COMMUNICATION_BRIEF_SOURCE_KIND:
        selected_id = str(selected_candidate.get("candidate_id") or "")
        render_candidates = [
            item
            for item in render_candidates
            if str(item.get("candidate_id") or "") == selected_id
        ] or [selected_candidate]
    started = perf_counter()
    render_result = render_mail_authoring(
        question=message,
        render_mode=render_mode,
        mail_plan=rendered_plan,
        observations=observations,
        candidates=render_candidates,
    )
    render_result["latency_ms"] = round((perf_counter() - started) * 1000.0, 2)
    body_for_sending = str(render_result.get("body_for_sending") or "").strip()
    if body_for_sending:
        rendered_plan["resolved_body"] = body_for_sending
        rendered_plan["authoring_status"] = "completed"
        rendered_plan["review_content"] = build_review_content(
            body_for_sending,
            dict(rendered_plan.get("selected_candidate") or {}) or None,
            dict(rendered_plan.get("attachment_candidate") or {}) or None,
        )
    elif str(rendered_plan.get("compose_mode") or "") == "recipient_ready_summary":
        rendered_plan["authoring_status"] = "failed"
        rendered_plan["authoring_failure"] = dict(render_result.get("failure_observation") or {})
        rendered_plan["recovery_hint"] = "Retry recipient-ready mail authoring from the resolved reference source."
    return rendered_plan, render_result


def _mail_render_latency_ms(render_result: dict[str, Any]) -> float:
    latency = float(render_result.get("latency_ms") or 0.0)
    return round(max(latency, 0.01), 2)


def _build_mail_authoring_recovery_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    mail_plan: dict[str, Any],
    render_result: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    rendered_plan = _persist_mail_plan(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=mail_plan,
        actor_context=actor_context,
        status="authoring_failed",
    )
    failure_observation = dict(render_result.get("failure_observation") or rendered_plan.get("authoring_failure") or {})
    if not failure_observation:
        failure_observation = make_failure_observation(
            service="llm",
            operation="mail_authoring",
            error="recipient_ready_body_empty",
            fallback_strategy="return_mail_authoring_recovery_observation",
            actor_context=actor_context,
        )
    latency_ms = _mail_render_latency_ms(render_result)
    answer = "邮件正文整理服务暂时不可用，本次没有创建发送任务。请稍后重试。"
    mail_observation = _build_mail_plan_observation(
        rendered_plan,
        source="mail_authoring_renderer",
        summary=answer,
        draft_mode="authoring_failed",
        observation_type="mail_authoring_recovery",
        extra_payload={"recovery_observation": failure_observation},
        actor_context=actor_context,
    )
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
        "routing_reason": "Recipient-ready mail authoring dependency failed before confirmation.",
        "candidate_intents": ["action_or_draft"],
        "mode_used": "mail_action",
        "tool_calls": [
            {
                "tool_name": "mail_authoring_renderer",
                "success": False,
                "status": "degraded",
                "error": str(dict(failure_observation.get("payload") or {}).get("error") or ""),
                "result": {"mail_plan": rendered_plan, "recovery_observation": failure_observation},
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
        "final_answer_source": "mail_authoring_recovery",
        "termination_reason": "dependency_failure",
        "node_latencies_ms": {"total": latency_ms, "mail_renderer": latency_ms},
        "token_in": int(render_result.get("token_in", 0)),
        "token_out": int(render_result.get("token_out", 0)),
        "estimated_cost": float(render_result.get("estimated_cost", 0.0)),
        "tool_observations": [failure_observation, mail_observation],
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
        final_answer_source="mail_authoring_recovery",
        termination_reason="dependency_failure",
        tool_observations=[failure_observation, mail_observation],
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
        task_id=None,
        task_status=None,
        task_risk_level=None,
        delivery_status=None,
        delivery_result=None,
        delivery_error=str(dict(failure_observation.get("payload") or {}).get("error") or ""),
        trace_id=str(synthetic_result["request_id"]),
        latency_ms=latency_ms,
        token_in=int(render_result.get("token_in", 0)),
        token_out=int(render_result.get("token_out", 0)),
        estimated_cost=float(render_result.get("estimated_cost", 0.0)),
    )


def _build_mail_clarification_response(
    *,
    session_id: str,
    conversation_id: str,
    message: str,
    display_message: str,
    mail_plan: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=dict(mail_plan or {}),
        render_mode="clarification",
        candidates=list(candidates or []),
    )
    rendered_plan = _persist_mail_plan(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=rendered_plan,
        actor_context=actor_context,
        status="needs_clarification",
    )
    pending_source_object = _persist_source_clarification_object(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=rendered_plan,
        candidates=list(candidates or []),
        actor_context=actor_context,
    )
    pending_field_object = _persist_mail_field_clarification_object(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=rendered_plan,
        candidates=list(candidates or []),
        actor_context=actor_context,
    )
    latency_ms = _mail_render_latency_ms(render_result)
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
        "task_plan": {
            "mail_plan": rendered_plan,
            "candidates": list(candidates or []),
            "pending_object": pending_source_object or pending_field_object,
        },
        "node_latencies_ms": {"total": latency_ms, "mail_renderer": latency_ms},
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
        latency_ms=latency_ms,
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
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=mail_plan,
        render_mode="confirmation",
    )
    if str(rendered_plan.get("authoring_status") or "") == "failed":
        return _build_mail_authoring_recovery_response(
            session_id=session_id,
            conversation_id=conversation_id,
            message=message,
            display_message=display_message,
            mail_plan=rendered_plan,
            render_result=render_result,
            actor_context=actor_context,
        )
    if str(rendered_plan.get("compose_mode") or "") == "recipient_ready_summary" and not str(
        rendered_plan.get("resolved_body") or ""
    ).strip():
        return _build_mail_clarification_response(
            session_id=session_id,
            conversation_id=conversation_id,
            message=message,
            display_message=display_message,
            mail_plan=rendered_plan,
            actor_context=actor_context,
        )
    rendered_plan = _persist_mail_plan(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=rendered_plan,
        actor_context=actor_context,
        status="pending_confirmation",
    )
    latency_ms = _mail_render_latency_ms(render_result)
    confirmation_payload = {
        "action_name": "send_mail_plan",
        "title": "请确认邮件发送计划",
        "message": str(render_result.get("user_message") or ""),
        "mail_plan": rendered_plan,
        "draft_id": str(rendered_plan.get("draft_id") or ""),
        "confirmation_id": str(rendered_plan.get("confirmation_id") or ""),
        "idempotency_key": str(rendered_plan.get("idempotency_key") or ""),
        "persistence_source": "mail_drafts",
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
        "node_latencies_ms": {"total": latency_ms, "mail_renderer": latency_ms},
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
        latency_ms=latency_ms,
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
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=mail_plan,
        render_mode="patch",
    )
    if str(rendered_plan.get("authoring_status") or "") == "failed":
        return _build_mail_authoring_recovery_response(
            session_id=session_id,
            conversation_id=conversation_id,
            message=message,
            display_message=display_message,
            mail_plan=rendered_plan,
            render_result=render_result,
            actor_context=actor_context,
        )
    rendered_plan = _persist_mail_plan(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=rendered_plan,
        actor_context=actor_context,
        status="pending_confirmation",
    )
    latency_ms = _mail_render_latency_ms(render_result)
    confirmation_payload = {
        "action_name": "send_mail_plan",
        "title": "请确认更新后的邮件发送计划",
        "message": str(render_result.get("user_message") or ""),
        "mail_plan": rendered_plan,
        "draft_id": str(rendered_plan.get("draft_id") or ""),
        "confirmation_id": str(rendered_plan.get("confirmation_id") or ""),
        "idempotency_key": str(rendered_plan.get("idempotency_key") or ""),
        "persistence_source": "mail_drafts",
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
        "node_latencies_ms": {"total": latency_ms, "mail_renderer": latency_ms},
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
        latency_ms=latency_ms,
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
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    rendered_plan, render_result = _render_mail_plan_with_llm(
        message=message,
        mail_plan=mail_plan,
        render_mode="draft",
    )
    if str(rendered_plan.get("authoring_status") or "") == "failed":
        return _build_mail_authoring_recovery_response(
            session_id=session_id,
            conversation_id=conversation_id,
            message=message,
            display_message=display_message,
            mail_plan=rendered_plan,
            render_result=render_result,
            actor_context=actor_context,
        )
    rendered_plan = _persist_mail_plan(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan=rendered_plan,
        actor_context=actor_context,
        status="draft_ready",
    )
    latency_ms = _mail_render_latency_ms(render_result)
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
        "node_latencies_ms": {"total": latency_ms, "mail_renderer": latency_ms},
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
        latency_ms=latency_ms,
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
    latency_ms = _mail_render_latency_ms(render_result)
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
        "node_latencies_ms": {"total": latency_ms, "mail_renderer": latency_ms},
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
        latency_ms=latency_ms,
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
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    observations = list(result.get("observations") or result.get("tool_observations") or [])
    router_latency_ms = float(route_decision.get("router_latency_ms") or 0.0)
    tool_path_latency_ms = float(latency_ms)
    pre_renderer_latency_ms = router_latency_ms + tool_path_latency_ms
    renderer_started = perf_counter()
    renderer = {"answer": str(result.get("answer") or ""), "token_in": 0, "token_out": 0, "estimated_cost": 0.0, "used_fallback": False}
    if not bool(result.get("skip_renderer", False)):
        renderer = render_final_answer(
            question=message,
            current_goal=str(result.get("intent") or route_decision.get("intent") or "fast_path"),
            observations=observations,
            working_memory=list(result.get("working_memory") or []),
            conservative=bool(result.get("conservative", False)),
            pending_confirmation=dict(result.get("pending_confirmation") or {}),
            actor_context=dict(actor_context or result.get("actor_context") or {}),
        )
        if isinstance(renderer.get("failure_observation"), dict):
            observations.append(dict(renderer["failure_observation"]))
            result.setdefault("tool_observations", []).append(dict(renderer["failure_observation"]))
    renderer_latency_ms = (perf_counter() - renderer_started) * 1000.0
    latency_ms = pre_renderer_latency_ms + renderer_latency_ms
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "correlation_id": str(result.get("correlation_id") or ""),
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
        "clarification_question": (
            str(result.get("clarification_question") or renderer.get("answer") or "")
            if bool(result.get("needs_clarification", False))
            else result.get("clarification_question")
        ),
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
            "verifier_verdict": dict(renderer.get("verifier_verdict") or {}),
            "verifier_rewrite_applied": bool(renderer.get("verifier_rewrite_applied", False)),
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
        "node_latencies_ms": {
            "total": round(latency_ms, 2),
            "router": round(router_latency_ms, 2),
            "tool_path": round(tool_path_latency_ms, 2),
            "pre_renderer": round(pre_renderer_latency_ms, 2),
            "final_renderer": round(renderer_latency_ms, 2),
        },
        "token_in": int(result.get("token_in", 0)) + int(renderer.get("token_in", 0)),
        "token_out": int(result.get("token_out", 0)) + int(renderer.get("token_out", 0)),
        "estimated_cost": float(result.get("estimated_cost", 0.0)) + float(renderer.get("estimated_cost", 0.0)),
    }
    trace_eval_started = perf_counter()
    synthetic_result = attach_trace_evaluation(synthetic_result, actor_context=actor_context)
    trace_eval_latency_ms = (perf_counter() - trace_eval_started) * 1000.0
    memory_persist_started = perf_counter()
    turn_id, memory_written = _write_unified_conversation_memory(synthetic_result, conversation_id)
    memory_persist_latency_ms = (perf_counter() - memory_persist_started) * 1000.0
    latency_ms += trace_eval_latency_ms + memory_persist_latency_ms
    synthetic_result["node_latencies_ms"].update(
        {
            "total": round(latency_ms, 2),
            "trace_evaluation": round(trace_eval_latency_ms, 2),
            "memory_persist_enqueue": round(memory_persist_latency_ms, 2),
        }
    )
    record_request(
        mode="fast_path",
        latency_ms=latency_ms,
        token_in=int(synthetic_result["token_in"]),
        token_out=int(synthetic_result["token_out"]),
        estimated_cost=float(synthetic_result["estimated_cost"]),
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
        correlation_id=str(synthetic_result["correlation_id"]),
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
    actor_context: dict[str, Any] | None = None,
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
        actor_context=dict(actor_context or {}),
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
        observation = make_typed_observation(
            observation_type="upload_analysis_result",
            source="uploaded_content_analyze",
            grounding_kind="tool",
            summary=compact_text(str(payload_dict.get("answer") or ""), 220),
            payload=payload_dict,
            citations=[],
            confidence=0.9,
            actor_context=actor_context,
        )
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
            actor_context=actor_context,
        )

    if intent == "persona":
        payload_dict = executors["persona_or_chitchat"]({"message": payload.message}, context, {})
        observation = make_typed_observation(
            observation_type="persona_result",
            source="persona_or_chitchat",
            grounding_kind="tool",
            summary=compact_text(str(payload_dict.get("answer") or ""), 220),
            payload=payload_dict,
            citations=[],
            confidence=0.86,
            actor_context=actor_context,
        )
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
            actor_context=actor_context,
        )

    if intent == "enterprise_fact":
        payload_dict = answer_enterprise_question(
            payload.message,
            top_k=8,
            session_id=payload.session_id,
            conversation_id=conversation_id,
            actor_context=actor_context,
            compose_answer=False,
        )
        observation_payload = _enterprise_renderer_observation_payload(payload_dict)
        correlation_id = str(payload_dict.get("correlation_id") or "")
        observation = make_typed_observation(
            observation_type="enterprise_answer_observation",
            source="enterprise_rag_query",
            grounding_kind="retrieval",
            summary=_enterprise_renderer_observation_summary(observation_payload),
            payload=observation_payload,
            provenance={"correlation_id": correlation_id},
            citations=_enterprise_renderer_citations(payload_dict),
            confidence=float(payload_dict.get("confidence", 0.0) or 0.0),
            actor_context=actor_context,
        )
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
            "correlation_id": correlation_id,
        }
        return _build_fast_path_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            route_decision=route_decision,
            result=result,
            latency_ms=(perf_counter() - started) * 1000.0,
            actor_context=actor_context,
        )

    if intent == "mail_status":
        if recommended_tool == "outbound_mail_summary":
            since, until = _local_day_window()
            summary = {"since": since, "until": until, **get_sent_mail_stats(since=since, until=until, session_id=payload.session_id)}
            observation = make_typed_observation(
                observation_type="mail_status_result",
                source="outbound_mail_summary",
                grounding_kind="tool",
                summary="已获取当前时间范围内的外发统计。",
                payload=summary,
                citations=[],
                confidence=0.92,
                actor_context=actor_context,
            )
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
            observation = make_typed_observation(
                observation_type="task_status_result",
                source="governance_task_context_fetch",
                grounding_kind="tool",
                summary="已获取当前会话最近的治理任务状态。" if task else "当前会话没有可恢复或待处理的治理任务。",
                payload={"task": task or {}},
                citations=[],
                confidence=0.9,
                actor_context=actor_context,
            )
            result = {
                "intent": "governance_task_status",
                "tool_calls": [{"tool_name": "governance_task_context_fetch", "success": True, "status": "completed", "result": {"task": task or {}}}],
                "tool_observations": [observation],
                "observations": [observation],
                "working_memory": ["已获取当前会话最近的治理任务状态。" if task else "当前会话没有可恢复或待处理的治理任务。"],
                "final_answer_source": "fast_mail_status_renderer",
                "termination_reason": "direct_answer",
            }
        else:
            summary = get_inbound_mail_summary(actor_context=actor_context)
            observation = make_typed_observation(
                observation_type="mail_status_result",
                source="inbound_mail_summary",
                grounding_kind="tool",
                summary="已获取当前收件箱摘要和同步状态。",
                payload={"summary": summary, "sync_state": latest_sync_state(actor_context=actor_context)},
                citations=[],
                confidence=0.92,
                actor_context=actor_context,
            )
            result = {
                "intent": "inbound_mail_assistant",
                "tool_calls": [{"tool_name": "inbound_mail_summary", "success": True, "status": "completed", "result": summary}],
                "tool_observations": [observation],
                "observations": [observation],
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
            actor_context=actor_context,
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
            actor_context=actor_context,
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
        try:
            enqueue_dlp_risk_task(str(task["task_id"]))
        except Exception as exc:
            updated_task = _mark_task_enqueue_failed(
                updated_task,
                service="celery",
                operation="enqueue_dlp_risk_task",
                error=str(exc),
                actor_context={
                    "tenant_id": str(updated_task.get("tenant_id") or ""),
                    "user_id": str(updated_task.get("user_id") or ""),
                    "workspace_id": str(updated_task.get("workspace_id") or ""),
                    "session_id": str(updated_task.get("session_id") or ""),
                    "conversation_id": str(updated_task.get("conversation_id") or ""),
                },
            )
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


def _agent_chat_requests_inbound_mail_access(
    message: str,
    *,
    route_decision: dict[str, Any] | None = None,
    multi_agent_plan: Any | None = None,
) -> bool:
    text = (message or "").lower()
    if _looks_like_inbound_mail_query(message):
        return True
    if any(token in text for token in ("inbox", "mailbox")):
        return True
    route = dict(route_decision or {})
    recommended_tool = str(route.get("recommended_tool") or "")
    intent = str(route.get("intent") or "")
    if is_inbound_mail_tool(recommended_tool):
        return True
    if intent == "mail_status" and recommended_tool not in {
        "outbound_mail_summary",
        "governance_task_context_fetch",
    }:
        return True
    for subtask in list(getattr(multi_agent_plan, "subtasks", []) or []):
        action = str(getattr(subtask, "action", "") or getattr(subtask, "capability", ""))
        if is_inbound_mail_tool(action):
            return True
    return False


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
            make_typed_observation(
                observation_type="mail_read_result" if "reply" in tool_name else "mail_status_result",
                source=tool_name,
                grounding_kind="tool",
                summary=compact_text(answer, 220),
                payload=payload,
                citations=[],
                confidence=0.94,
            )
        ]
    renderer = render_final_answer(
        question=message,
        current_goal=intent,
        observations=normalized_observations,
        working_memory=[str(item.get("summary") or "") for item in normalized_observations if str(item.get("summary") or "").strip()],
        conservative=False,
    )
    if isinstance(renderer.get("failure_observation"), dict):
        normalized_observations.append(dict(renderer["failure_observation"]))
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


def _local_day_window() -> tuple[str, str]:
    now_local = datetime.now(LOCAL_TZ)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=LOCAL_TZ)
    return start_local.astimezone(timezone.utc).isoformat(), now_local.astimezone(timezone.utc).isoformat()


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


def _handle_compound_agent_request(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse | None:
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
            result = get_inbound_mail_summary(actor_context=actor_context)
            observations.append(
                {
                    "observation_type": "mail_status_result",
                    "source": "inbound_mail_summary",
                    "grounding_kind": "tool",
                    "summary": "已获取当前收件箱摘要和同步状态。",
                    "payload": {"summary": result, "sync_state": latest_sync_state(actor_context=actor_context)},
                    "citations": [],
                    "confidence": 0.92,
                }
            )
        elif subtask.capability == "enterprise_rag_query":
            result = answer_enterprise_question(
                str(subtask.input.get("question") or payload.message),
                source_types=list(subtask.input.get("source_types") or []),
                actor_context=actor_context,
                compose_answer=False,
            )
            observation_payload = _enterprise_renderer_observation_payload(result)
            observations.append(
                {
                    "observation_type": "enterprise_answer_observation",
                    "source": "enterprise_rag_query",
                    "grounding_kind": "retrieval",
                    "summary": _enterprise_renderer_observation_summary(observation_payload),
                    "payload": observation_payload,
                    "citations": _enterprise_renderer_citations(result),
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


def _handle_compound_agent_request(
    payload: UnifiedAgentRequest,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse | None:
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
                    actor_context=actor_context,
                )
            )
        elif subtask.capability == "inbound_mail_summary":
            summary = get_inbound_mail_summary(actor_context=actor_context)
            sync_state = latest_sync_state(actor_context=actor_context)
            result = {"summary": summary, "sync_state": sync_state}
            observations.append(
                _build_mailbox_summary_observation(
                    source="inbound_mail_summary",
                    summary_text="已获取当前收件箱摘要和同步状态。",
                    payload=result,
                    actor_context=actor_context,
                )
            )
        elif subtask.capability == "enterprise_rag_query":
            result = answer_enterprise_question(
                str(subtask.input.get("question") or payload.message),
                source_types=list(subtask.input.get("source_types") or []),
                actor_context=actor_context,
                compose_answer=False,
            )
            observation_payload = _enterprise_renderer_observation_payload(result)
            observations.append(
                make_typed_observation(
                    observation_type="enterprise_answer_observation",
                    source="enterprise_rag_query",
                    grounding_kind="retrieval",
                    summary=_enterprise_renderer_observation_summary(observation_payload),
                    payload=observation_payload,
                    citations=_enterprise_renderer_citations(result),
                    confidence=float(result.get("confidence", 0.0) or 0.0),
                    actor_context=actor_context,
                )
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
            actor_context=actor_context,
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
    actor_context: dict[str, Any] | None = None,
) -> UnifiedAgentResponse:
    mail_action_plan = _build_mail_action_plan(
        payload,
        conversation_id,
        dict(upload_context or {}),
        actor_context=actor_context,
    )
    if mail_action_plan.get("needs_clarification"):
        return _build_mail_clarification_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
            candidates=list(mail_action_plan.get("candidates") or []),
            actor_context=actor_context,
        )
    if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "unsupported":
        return _build_mail_unsupported_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
            actor_context=actor_context,
        )
    if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "draft_only":
        return _build_mail_draft_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
            actor_context=actor_context,
        )
    if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "confirmation_required":
        return _build_mail_confirmation_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
            actor_context=actor_context,
        )
    return _build_mail_clarification_response(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        message=payload.message,
        display_message=display_message,
        mail_plan={"missing_fields": ["content_or_attachment"], "request_message": payload.message},
        candidates=[],
        actor_context=actor_context,
    )


def _create_dlp_task_from_mail_plan(
    *,
    session_id: str,
    conversation_id: str,
    request_message: str,
    mail_plan: dict[str, Any],
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attachment = dict((mail_plan.get("resolved_attachments") or [{}])[0] or {}) if mail_plan.get("resolved_attachments") else {}
    selected_candidate = dict(mail_plan.get("selected_candidate") or {})
    draft_id = str(mail_plan.get("draft_id") or "")
    idempotency_key = str(mail_plan.get("idempotency_key") or "")
    existing = get_dlp_task_by_idempotency_key(idempotency_key) if idempotency_key else None
    if existing:
        _persist_task_registry_object(existing, actor_context=actor_context)
        if draft_id:
            bind_mail_draft_task(
                draft_id,
                actor_context=dict(actor_context or {}),
                confirmation_key=idempotency_key,
                task_id=str(existing.get("task_id") or ""),
            )
        return existing
    task = _create_async_dlp_task(
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
            tenant_id=str((actor_context or {}).get("tenant_id") or ""),
            user_id=str((actor_context or {}).get("user_id") or ""),
            workspace_id=str((actor_context or {}).get("workspace_id") or ""),
            roles=list((actor_context or {}).get("roles") or []),
            mail_draft_id=draft_id,
            idempotency_key=idempotency_key,
        ),
        actor_context=actor_context,
    )
    if draft_id and idempotency_key and task.get("task_id"):
        bind_mail_draft_task(
            draft_id,
            actor_context=dict(actor_context or {}),
            confirmation_key=idempotency_key,
            task_id=str(task.get("task_id") or ""),
        )
    return task


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


def _ensure_conversation(
    session_id: str,
    conversation_id: str | None,
    actor_context: dict[str, Any] | None = None,
) -> tuple[dict, bool]:
    actor = ActorContext(**dict(actor_context or {})) if actor_context else ActorContext(session_id=session_id)
    if conversation_id:
        existing = get_conversation(conversation_id)
        if existing:
            if existing["session_id"] != session_id:
                raise HTTPException(status_code=403, detail="conversation_id does not belong to this session_id")
            if not actor.is_local_dev:
                if str(existing.get("tenant_id") or "") != actor.tenant_id:
                    raise HTTPException(status_code=403, detail="conversation_id does not belong to this tenant_id")
                if str(existing.get("user_id") or "") != actor.user_id:
                    raise HTTPException(status_code=403, detail="conversation_id does not belong to this user_id")
                if str(existing.get("workspace_id") or "") != actor.workspace_id:
                    raise HTTPException(status_code=403, detail="conversation_id does not belong to this workspace_id")
            return existing, False
    created = create_conversation(session_id, actor_context=actor.to_dict())
    record_conversation_created()
    return created, True


def _build_debug_snapshot(result: dict, conversation_id: str) -> dict:
    answer_text = str(result.get("answer", ""))
    communication_workspace = _derive_communication_workspace_state(result, conversation_id)
    return {
        "conversation_id": conversation_id,
        "workspace_kind": communication_workspace["workspace_kind"],
        "primary_work_object": communication_workspace["primary_work_object"],
        "communication_context": communication_workspace["communication_context"],
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
        "actor_context": result.get("actor_context", {}) or {},
        "permission_decision": result.get("permission_decision", {}) or {},
        "rate_limit_decision": result.get("rate_limit_decision", {}) or {},
        "queue_status": result.get("queue_status", {}) or {},
        "memory_boundary": "context_only",
        "enterprise_citation_required": True,
        "latency_ms": float(result.get("node_latencies_ms", {}).get("total", 0.0)),
        "token_in": int(result.get("token_in", 0)),
        "token_out": int(result.get("token_out", 0)),
        "estimated_cost": float(result.get("estimated_cost", 0.0)),
        "answer_collapsed": len(answer_text) > 1200 or answer_text.count("\n") > 12,
    }


def _derive_communication_workspace_state(result: dict, conversation_id: str) -> dict[str, Any]:
    task_plan = dict(result.get("task_plan") or {})
    observations = _collect_result_observations(result, task_plan)
    observation_types = [
        str(item.get("observation_type") or "")
        for item in observations
        if str(item.get("observation_type") or "").strip()
    ]
    mail_plan = dict(task_plan.get("mail_plan") or {})
    primary_work_object = _primary_communication_work_object(
        observations=observations,
        mail_plan=mail_plan,
        pending_confirmation=dict(result.get("pending_confirmation") or result.get("confirmation_payload") or {}),
        conversation_id=conversation_id,
    )
    communication_context = {
        "conversation_id": conversation_id,
        "thread_ref": _communication_thread_ref(observations, mail_plan),
        "observation_types": observation_types,
        "backend_roles": {
            "mail": "closeout_owner",
            "dlp": "risk_boundary",
            "enterprise_rag": "grounding_provider",
            "meeting": "escalation_provider",
        },
        "surface_role": "user_workspace_context_view",
        "authority_owner": "backend_observations",
        "governance_surface": "separate_governance_console",
        "high_risk_approval_controls_exposed": False,
    }
    return {
        "workspace_kind": "communication_thread_context",
        "primary_work_object": primary_work_object,
        "communication_context": communication_context,
    }


def _refresh_result_communication_workspace(result: dict, conversation_id: str) -> dict[str, Any]:
    workspace = _derive_communication_workspace_state(result, conversation_id)
    result["task_plan"] = {
        **dict(result.get("task_plan") or {}),
        "communication_workspace": workspace,
    }
    return workspace


def _collect_result_observations(result: dict, task_plan: dict[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for source in (
        result.get("tool_observations"),
        result.get("observations"),
        task_plan.get("tool_observations"),
    ):
        for item in list(source or []):
            if isinstance(item, dict):
                observations.append(item)
    return observations


def _primary_communication_work_object(
    *,
    observations: list[dict[str, Any]],
    mail_plan: dict[str, Any],
    pending_confirmation: dict[str, Any],
    conversation_id: str,
) -> dict[str, Any]:
    for observation_type in ("communication_brief", "communication_thread", "meeting_escalation_candidate"):
        for observation in observations:
            if str(observation.get("observation_type") or "") != observation_type:
                continue
            payload = dict(observation.get("payload") or {})
            thread_ref = dict(payload.get("thread_ref") or payload)
            return {
                "kind": observation_type,
                "id": str(
                    payload.get("brief_id")
                    or payload.get("thread_id")
                    or payload.get("candidate_id")
                    or ""
                ),
                "thread_id": str(thread_ref.get("thread_id") or ""),
                "subject": str(thread_ref.get("subject") or payload.get("topic") or ""),
                "status": str(observation.get("status") or ""),
            }
    if mail_plan:
        return {
            "kind": str(mail_plan.get("target_object") or mail_plan.get("mail_action_type") or "mail_action"),
            "id": str(mail_plan.get("draft_id") or mail_plan.get("task_id") or ""),
            "thread_id": str(dict(mail_plan.get("thread_ref") or {}).get("thread_id") or ""),
            "subject": str(mail_plan.get("resolved_subject") or mail_plan.get("subject") or ""),
            "status": str(mail_plan.get("status") or ""),
        }
    if pending_confirmation:
        return {
            "kind": str(pending_confirmation.get("tool_name") or pending_confirmation.get("action_name") or "pending_confirmation"),
            "id": str(pending_confirmation.get("idempotency_key") or pending_confirmation.get("task_id") or ""),
            "thread_id": "",
            "subject": str(pending_confirmation.get("title") or ""),
            "status": "pending_confirmation",
        }
    return {
        "kind": "conversation_context",
        "id": conversation_id,
        "thread_id": "",
        "subject": "",
        "status": "active",
    }


def _communication_thread_ref(observations: list[dict[str, Any]], mail_plan: dict[str, Any]) -> dict[str, Any]:
    for observation in observations:
        payload = dict(observation.get("payload") or {})
        thread_ref = dict(payload.get("thread_ref") or {})
        if thread_ref:
            return {
                "thread_id": str(thread_ref.get("thread_id") or ""),
                "source": str(thread_ref.get("source") or ""),
                "subject": str(thread_ref.get("subject") or ""),
                "participants": list(thread_ref.get("participants") or []),
                "last_message_at": str(thread_ref.get("last_message_at") or ""),
            }
        if str(observation.get("observation_type") or "") == "communication_thread":
            return {
                "thread_id": str(payload.get("thread_id") or ""),
                "source": str(payload.get("source") or ""),
                "subject": str(payload.get("subject") or ""),
                "participants": list(payload.get("participants") or []),
                "last_message_at": str(payload.get("last_message_at") or ""),
            }
    thread_ref = dict(mail_plan.get("thread_ref") or {})
    if thread_ref:
        return {
            "thread_id": str(thread_ref.get("thread_id") or ""),
            "source": str(thread_ref.get("source") or ""),
            "subject": str(thread_ref.get("subject") or ""),
            "participants": list(thread_ref.get("participants") or []),
            "last_message_at": str(thread_ref.get("last_message_at") or ""),
        }
    return {}


def _write_unified_conversation_memory(
    result: dict,
    conversation_id: str,
    actor_context: dict[str, Any] | None = None,
) -> tuple[str | None, bool]:
    answer = str(result.get("answer", "")).strip()
    if not answer:
        return None, False

    _append_runtime_communication_brief(result, conversation_id, actor_context=actor_context)
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
        actor_context=actor_context or result.get("actor_context") or {},
    )
    record_conversation_turns()

    user_turn = exchange["user_turn"]
    assistant_turn = exchange["assistant_turn"]
    try:
        _persist_answer_artifact_object(
            session_id=str(result["session_id"]),
            conversation_id=conversation_id,
            turn_id=str(assistant_turn["turn_id"]),
            answer_summary=answer_summary,
            intent=str(result.get("intent", "")),
            citations=list(result.get("citations") or []),
            actor_context=actor_context or result.get("actor_context") or {},
        )
    except Exception as exc:  # pragma: no cover - best effort artifact registry write
        logger.warning("Answer artifact registry write skipped: %s", exc)
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
        enqueue_conversation_memory_summary(
            {
                "session_id": str(result["session_id"]),
                "conversation_id": conversation_id,
                "user_turn_id": str(user_turn["turn_id"]),
                "assistant_turn_id": str(assistant_turn["turn_id"]),
                "question": question,
                "safe_question": safe_question,
                "answer": answer,
                "answer_summary": answer_summary,
                "intent": str(result.get("intent", "")),
                "citations": result.get("citations", []),
                "upload_context": result.get("upload_context", {}) or {},
                "actor_context": actor_context or result.get("actor_context") or {},
                "dynamic_memory": {
                    "user_goal": question,
                    "outcome": answer_summary,
                    "failure_reason": _summarize_memory_failure_for_doc(result),
                    "memory_scope": str(dynamic_memory_result.get("memory_scope") or "session"),
                    "key_files": dynamic_identifiers.get("files") or [],
                    "recipients": dynamic_identifiers.get("emails") or [],
                    "task_ids": dynamic_identifiers.get("task_ids") or [],
                },
            }
        )
        written = True
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
        knowledge_chunks_loaded=count_collection() if chroma_ok else 0,
        langsmith_enabled=settings.langsmith_enabled,
    )


@app.post("/auth/exmail/request-code")
def auth_exmail_request_code(payload: dict[str, Any]) -> dict[str, Any]:
    email = str((payload or {}).get("email") or "").strip().lower()
    try:
        code_payload = create_login_code(email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    send_result = send_email_smtp(
        to_email=email,
        subject="Enterprise Agent login verification code",
        body=(
            "Your Enterprise Agent verification code is: "
            f"{code_payload['code']}\n\n"
            "This code expires in 10 minutes. If you did not request it, ignore this email."
        ),
    )
    if not send_result.get("ok"):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "verification_email_send_failed",
                "email_masked": mask_email(email),
                "provider": send_result.get("provider"),
                "reason": send_result.get("error"),
                "failure_observation": send_result.get("failure_observation"),
            },
        )
    return {
        "ok": True,
        "email_masked": code_payload["email_masked"],
        "expires_in_seconds": code_payload["expires_in_seconds"],
        "delivery": {
            "provider": send_result.get("provider"),
            "sent_at": send_result.get("sent_at"),
            "from_email_masked": send_result.get("from_email_masked"),
        },
    }


@app.post("/auth/exmail/verify-code")
def auth_exmail_verify_code(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = verify_login_code(
            str((payload or {}).get("email") or ""),
            str((payload or {}).get("code") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    return {"ok": True, **result}


@app.get("/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    token = request.headers.get("x-auth-session") or request.headers.get("X-Auth-Session") or ""
    session = resolve_session_token(token)
    if not session:
        raise HTTPException(status_code=401, detail={"error": "invalid_or_expired_session"})
    return {"ok": True, "user": session}


@app.post("/auth/logout")
def auth_logout(request: Request) -> dict[str, Any]:
    token = request.headers.get("x-auth-session") or request.headers.get("X-Auth-Session") or ""
    revoked = revoke_session_token(token)
    return {"ok": True, "revoked": revoked}


@app.get("/admin/queue-health")
def admin_queue_health(request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "admin.read", "queue_health")
    queue_status = _refresh_queue_metrics()
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "queue_status": queue_status,
    }


@app.get("/admin/task-stats")
def admin_task_stats(request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "admin.read", "task_stats")
    stats = get_task_stats()
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "task_stats": stats,
    }


@app.get("/admin/mail-provider-health")
def admin_mail_provider_health(request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "admin.read", "mail_provider_health")
    response = CurrentImapSmtpMailProvider().get_health(actor_context=actor.to_dict())
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "provider_health": response.to_dict(),
        "observation": response.to_observation(
            observation_type="mail_provider_health",
            actor_context=actor.to_dict(),
        ),
    }


@app.get("/admin/mail-dlq")
def admin_mail_dlq(
    request: Request,
    replay_status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "admin.read", "mail_dead_letter_queue")
    entries = list_mail_dlq_entries(replay_status=replay_status, limit=max(1, min(limit, 200)))
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "entries": entries,
        "count": len(entries),
    }


@app.post("/admin/mail-dlq/{dlq_id}/replay")
def admin_replay_mail_dlq(dlq_id: str, request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "task.approve", f"mail_dlq:{dlq_id}")
    replay = replay_mail_dlq_entry(dlq_id, actor=actor.user_id or "governance_admin")
    operation = str((replay.get("entry") or {}).get("operation") or "unknown")
    if not replay.get("ok"):
        record_mail_dlq_replay(operation, str(replay.get("status") or "blocked"))
        return {
            "ok": False,
            "actor_context": actor.to_dict(),
            "permission_decision": permission_decision,
            "replay": replay,
            "observation": {
                "observation_type": "mail_dlq_replay",
                "status": replay.get("status") or "blocked",
                "success": False,
                "summary": "DLQ replay was blocked or unavailable.",
                "actor_context": actor.to_dict(),
                "payload": replay,
            },
        }

    task_id = str((replay.get("task") or {}).get("task_id") or "")
    try:
        enqueue_email_send_task(task_id)
        queued = mark_mail_dlq_replay(
            dlq_id,
            status="worker_queued",
            result={"ok": True, "task_id": task_id, "worker_queue": EMAIL_QUEUE},
        )
        record_mail_dlq_replay(operation, "worker_queued")
        return {
            "ok": True,
            "actor_context": actor.to_dict(),
            "permission_decision": permission_decision,
            "replay": {**replay, "entry": queued or replay.get("entry")},
            "observation": {
                "observation_type": "mail_dlq_replay",
                "status": "worker_queued",
                "success": True,
                "summary": "DLQ entry was replayed and queued for the email worker.",
                "actor_context": actor.to_dict(),
                "payload": {"task_id": task_id, "dlq_id": dlq_id, "worker_queue": EMAIL_QUEUE},
            },
        }
    except Exception as exc:
        failed = mark_mail_dlq_replay(
            dlq_id,
            status="enqueue_failed",
            result={"ok": False, "task_id": task_id, "error": str(exc)},
        )
        record_mail_dlq_replay(operation, "enqueue_failed")
        return {
            "ok": False,
            "actor_context": actor.to_dict(),
            "permission_decision": permission_decision,
            "replay": {**replay, "entry": failed or replay.get("entry")},
            "observation": make_failure_observation(
                service="celery",
                operation="mail_dlq_replay_enqueue",
                error=str(exc),
                fallback_strategy="keep_dlq_entry_for_manual_recovery",
                retry_count=0,
                actor_context=actor.to_dict(),
                severity="high",
            ),
        }


@app.get("/admin/mail-harness-summary")
def admin_mail_harness_summary(request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "admin.read", "mail_harness")
    harnesses = [
        {
            "name": "mail_harness_regression",
            "scope": "draft/confirm/DLP/send workflow",
            "docker_command": "docker compose exec -T api python scripts/mail_harness_regression.py",
        },
        {
            "name": "mail_provider_contract_regression",
            "scope": "provider contract and typed observation shape",
            "docker_command": "docker compose exec -T api python scripts/mail_provider_contract_regression.py",
        },
        {
            "name": "mail_m5_reliability_regression",
            "scope": "DLQ, retry exhaustion, uncertain SMTP and safe replay",
            "docker_command": "docker compose exec -T api python scripts/mail_m5_reliability_regression.py",
        },
    ]
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "observation": {
            "observation_type": "mail_harness_catalog",
            "status": "available",
            "success": True,
            "summary": "Mail harness commands are available for Docker regression and governance diagnostics.",
            "actor_context": actor.to_dict(),
            "payload": {"harnesses": harnesses},
        },
        "harnesses": harnesses,
    }


@app.get("/admin/policy-version")
def admin_policy_version(request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "policy.read", "privacy_policy")
    policy = load_active_privacy_policy()
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "policy": asdict(policy),
    }


@app.get("/admin/memory-candidates")
def admin_memory_candidates(
    request: Request,
    session_id: str,
    conversation_id: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    actor = build_actor_context(request=request, session_id=session_id, conversation_id=conversation_id)
    permission_decision = _ensure_permission(actor, "admin.read", "memory_candidates")
    candidates = get_structured_turn_summaries(
        session_id=session_id,
        conversation_id=conversation_id,
        limit=max(1, min(limit, 100)),
    )
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "candidates": candidates,
    }


@app.get("/admin/rag-manifest")
def admin_rag_manifest(request: Request, limit: int = 20) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "admin.read", "rag_manifest")
    manifest = load_manifest()
    if isinstance(manifest.get("runs"), list):
        manifest = {**manifest, "runs": list(manifest.get("runs") or [])[-max(1, min(limit, 100)) :]}
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "manifest": manifest,
        "active_index_contract": build_active_index_contract(),
    }


@app.get("/admin/rag-index-health")
def admin_rag_index_health(request: Request, include_details: bool = False) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "admin.read", "rag_index_health")
    contract = audit_active_index_parity() if include_details else build_active_index_contract()
    return {
        "ok": bool((contract.get("parity") or {}).get("ok", False)),
        "actor_context": actor.to_dict(),
        "permission_decision": permission_decision,
        "active_index_contract": contract,
    }


@app.get("/conversations", response_model=list[ConversationSummary])
def conversations(request: Request, session_id: str) -> list[ConversationSummary]:
    actor = build_actor_context(request=request, session_id=session_id)
    return [ConversationSummary(**item) for item in list_conversations(session_id, actor_context=actor.to_dict())]


@app.post("/conversations", response_model=ConversationSummary)
def create_conversation_api(payload: ConversationCreateRequest, request: Request) -> ConversationSummary:
    actor = build_actor_context(request=request, payload=payload, session_id=payload.session_id)
    conversation = create_conversation(payload.session_id, payload.title, actor_context=actor.to_dict())
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


@app.get("/agent/pending-objects")
def list_agent_pending_objects_api(
    request: Request,
    conversation_id: str,
    session_id: str = "",
) -> dict[str, Any]:
    actor = build_actor_context(request=request, session_id=session_id, conversation_id=conversation_id)
    _ensure_permission(actor, "agent.chat", "pending_objects")
    conversation, _ = _ensure_conversation(session_id or actor.session_id, conversation_id, actor.to_dict())
    items = list_active_pending_objects(
        conversation_id=str(conversation["conversation_id"]),
        actor_context=actor.to_dict(),
    )
    snapshots = [
        _safe_pending_object_snapshot(item, actor_context=actor.to_dict())
        for item in items
    ]
    return {
        "ok": True,
        "conversation_id": str(conversation["conversation_id"]),
        "actor_context": actor.to_dict(),
        "pending_objects": snapshots,
        "count": len(snapshots),
    }


@app.get("/tasks", response_model=list[DlpTaskResponse])
def list_dlp_tasks_api(
    request: Request,
    session_id: str | None = None,
    status: str | None = None,
    risk_level: str | None = None,
) -> list[DlpTaskResponse]:
    actor = build_actor_context(request=request, session_id=session_id or "")
    _ensure_permission(actor, "task.read", "dlp_tasks")
    workspace_task_reader = bool({"admin", "approver"}.intersection({str(role).lower() for role in actor.roles}))
    return [
        _task_response(item)
        for item in list_dlp_tasks(
            session_id=session_id,
            status=status,
            risk_level=risk_level,
            tenant_id=None if actor.is_local_dev else actor.tenant_id,
            # Governance users review tasks across users inside their bound
            # workspace. They never receive an unscoped cross-tenant listing.
            user_id=None if actor.is_local_dev or workspace_task_reader else actor.user_id,
            workspace_id=None if actor.is_local_dev else actor.workspace_id,
        )
    ]


@app.post("/mail/inbound/sync", response_model=InboundMailSyncResponse)
def sync_inbound_mail_api(request: Request, payload: InboundMailSyncRequest | None = None) -> InboundMailSyncResponse:
    actor = build_actor_context(request=request)
    _ensure_inbound_mail_access(request, actor, "inbound mail sync")
    _ensure_permission(actor, "mail.read", "inbound_mail_sync")
    sync_request = payload or InboundMailSyncRequest()
    since = _parse_optional_datetime(sync_request.since, "since")
    until = _parse_optional_datetime(sync_request.until, "until")
    return InboundMailSyncResponse(
        **sync_inbound_mail(
            since=since,
            until=until,
            limit=sync_request.limit,
            actor_context=actor.to_dict(),
        )
    )


@app.post("/mail/inbound/sync/async")
def enqueue_inbound_mail_sync_api(request: Request) -> dict[str, str]:
    actor = build_actor_context(request=request)
    _ensure_inbound_mail_access(request, actor, "inbound mail sync")
    _ensure_permission(actor, "mail.read", "inbound_mail_sync")
    return {"task_id": enqueue_inbound_mail_sync(actor.to_dict()), "queue": MAIL_QUEUE}


@app.post("/mail/inbound/digest")
def generate_daily_mail_digest_api(request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    _ensure_inbound_mail_access(request, actor, "inbound mail digest")
    _ensure_permission(actor, "mail.read", "inbound_mail_digest")
    return generate_daily_mail_digest(actor_context=actor.to_dict())


@app.post("/mail/inbound/digest/async")
def enqueue_daily_mail_digest_api(request: Request) -> dict[str, str]:
    actor = build_actor_context(request=request)
    _ensure_inbound_mail_access(request, actor, "inbound mail digest")
    _ensure_permission(actor, "mail.read", "inbound_mail_digest")
    return {"task_id": enqueue_daily_mail_digest(actor.to_dict()), "queue": MAIL_QUEUE}


@app.get("/mail/inbound/summary", response_model=InboundMailSummaryResponse)
def inbound_mail_summary_api(request: Request, since: str | None = None, until: str | None = None) -> InboundMailSummaryResponse:
    actor = build_actor_context(request=request)
    _ensure_inbound_mail_access(request, actor, "inbound mail summary")
    _ensure_permission(actor, "mail.read", "inbound_mail_summary")
    return InboundMailSummaryResponse(**get_inbound_mail_summary(since, until, actor_context=actor.to_dict()))


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
def enterprise_rag_query_api(payload: EnterpriseRagQueryRequest, request: Request) -> EnterpriseRagQueryResponse:
    actor = build_actor_context(
        request=request,
        payload=payload,
        session_id=payload.session_id,
        conversation_id=payload.conversation_id,
    )
    permission_decision = _ensure_permission(actor, "rag.query", "enterprise_rag")
    rate_limit_decision = _ensure_rate_limit(actor, "enterprise_rag_query")
    queue_status = _refresh_queue_metrics()
    if payload.async_mode:
        correlation_id = f"rag_async_{uuid.uuid4().hex[:12]}"
        task_id = enqueue_enterprise_query(
            {
                **payload.dict(),
                "actor_context": actor.to_dict(),
                "correlation_id": correlation_id,
            }
        )
        return EnterpriseRagQueryResponse(
            actor_context=actor.to_dict(),
            permission_decision=permission_decision,
            rate_limit_decision=rate_limit_decision,
            queue_status=queue_status,
            task_mode="async",
            task_id=task_id,
            correlation_id=correlation_id,
            diagnostic_summary={"diagnostic_class": "async_queued"},
        )
    result = answer_enterprise_question(
        payload.question,
        source_types=payload.source_types,
        top_k=payload.top_k,
        session_id=payload.session_id,
        conversation_id=payload.conversation_id,
        actor_context=actor.to_dict(),
    )
    result.update(
        {
            "actor_context": actor.to_dict(),
            "permission_decision": permission_decision,
            "rate_limit_decision": rate_limit_decision,
            "queue_status": queue_status,
            "task_mode": "sync",
        }
    )
    public_result = build_public_enterprise_query_payload(result, include_debug_details=payload.include_debug_details)
    return EnterpriseRagQueryResponse(**public_result)


@app.get("/enterprise-rag/tasks/{task_id}")
def enterprise_rag_task_status_api(task_id: str, request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "rag.query", "enterprise_rag")
    status = get_enterprise_task_status(task_id)
    owner = dict(status.get("actor_context") or {})
    if not actor.is_local_dev:
        if not owner:
            raise HTTPException(status_code=403, detail="task ownership metadata is unavailable")
        if (
            str(owner.get("tenant_id") or "") != actor.tenant_id
            or str(owner.get("workspace_id") or "") != actor.workspace_id
            or str(owner.get("user_id") or "") != actor.user_id
        ):
            raise HTTPException(status_code=403, detail="task_id does not belong to this actor context")
    return {**status, "permission_decision": permission_decision}


@app.post("/enterprise-rag/ingest", response_model=EnterpriseRagIngestResponse)
def enterprise_rag_ingest_api(payload: EnterpriseRagIngestRequest, request: Request) -> EnterpriseRagIngestResponse:
    actor = build_actor_context(request=request, payload=payload)
    permission_decision = _ensure_permission(actor, "rag.ingest", "enterprise_rag")
    rate_limit_decision = _ensure_rate_limit(actor, "enterprise_rag_ingest")
    queue_status = _refresh_queue_metrics()
    if payload.async_mode:
        task_id = enqueue_enterprise_ingest({**payload.dict(), "actor_context": actor.to_dict()})
        return EnterpriseRagIngestResponse(
            dataset="enterprise_rag_bench",
            mode=payload.mode,
            documents_path=payload.documents_path or "",
            questions_path=payload.questions_path or "",
            reset=payload.reset,
            actor_context=actor.to_dict(),
            permission_decision=permission_decision,
            rate_limit_decision=rate_limit_decision,
            queue_status=queue_status,
            task_mode="async",
            task_id=task_id,
        )
    try:
        result = ingest_enterprise_rag_bench(
            mode=payload.mode,
            documents_path=payload.documents_path,
            questions_path=payload.questions_path,
            limit=payload.limit,
            reset=payload.reset,
            actor_context=actor.to_dict(),
        )
        result.update(
            {
                "actor_context": actor.to_dict(),
                "permission_decision": permission_decision,
                "rate_limit_decision": rate_limit_decision,
                "queue_status": queue_status,
                "task_mode": "sync",
            }
        )
        return EnterpriseRagIngestResponse(**result)
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
    request: Request,
    questions_path: str | None = None,
    limit: int = 20,
    top_k: int = 8,
    async_mode: bool = False,
) -> EnterpriseRagBenchmarkResponse:
    actor = build_actor_context(request=request)
    permission_decision = _ensure_permission(actor, "rag.benchmark", "enterprise_rag")
    rate_limit_decision = _ensure_rate_limit(actor, "enterprise_rag_benchmark")
    queue_status = _refresh_queue_metrics()
    if async_mode:
        task_id = enqueue_enterprise_benchmark(
            {
                "questions_path": questions_path,
                "limit": max(1, min(limit, 100)),
                "top_k": max(1, min(top_k, 30)),
                "actor_context": actor.to_dict(),
            }
        )
        return EnterpriseRagBenchmarkResponse(
            actor_context=actor.to_dict(),
            permission_decision=permission_decision,
            rate_limit_decision=rate_limit_decision,
            queue_status=queue_status,
            task_mode="async",
            task_id=task_id,
        )
    try:
        result = run_benchmark_sample(
            questions_path=questions_path,
            limit=max(1, min(limit, 100)),
            top_k=max(1, min(top_k, 30)),
        )
        result.update(
            {
                "actor_context": actor.to_dict(),
                "permission_decision": permission_decision,
                "rate_limit_decision": rate_limit_decision,
                "queue_status": queue_status,
                "task_mode": "sync",
            }
        )
        return EnterpriseRagBenchmarkResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/internal/communication/threads")
def internal_communication_threads_api(
    request: Request,
    limit: int = 20,
    refresh: bool = True,
) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    access_decision = _ensure_internal_communication_thread_access(request, actor)
    permission_decision = _ensure_permission(actor, "mail.read", "communication_threads")
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "access_decision": access_decision,
        "permission_decision": permission_decision,
        "threads": list_communication_threads(
            actor_context=actor.to_dict(),
            limit=max(1, min(int(limit or 20), 100)),
            refresh=refresh,
        ),
    }


@app.get("/internal/communication/threads/active")
def internal_active_communication_thread_api(request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    access_decision = _ensure_internal_communication_thread_access(request, actor)
    permission_decision = _ensure_permission(actor, "mail.read", "active_communication_thread")
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "access_decision": access_decision,
        "permission_decision": permission_decision,
        "active_thread": get_active_communication_thread(actor_context=actor.to_dict()),
    }


@app.post("/internal/communication/threads/{thread_id}/active")
def internal_set_active_communication_thread_api(thread_id: str, request: Request) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    access_decision = _ensure_internal_communication_thread_access(request, actor)
    permission_decision = _ensure_permission(actor, "mail.read", f"communication_thread:{thread_id}")
    thread = set_active_communication_thread(thread_id, actor_context=actor.to_dict())
    if not thread:
        raise HTTPException(status_code=404, detail="Unknown communication thread")
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "access_decision": access_decision,
        "permission_decision": permission_decision,
        "active_thread": thread,
    }


@app.get("/internal/communication/threads/{thread_id}")
def internal_communication_thread_detail_api(
    thread_id: str,
    request: Request,
    refresh: bool = True,
) -> dict[str, Any]:
    actor = build_actor_context(request=request)
    access_decision = _ensure_internal_communication_thread_access(request, actor)
    permission_decision = _ensure_permission(actor, "mail.read", f"communication_thread:{thread_id}")
    thread = get_communication_thread(thread_id, actor_context=actor.to_dict(), refresh=refresh)
    if not thread:
        raise HTTPException(status_code=404, detail="Unknown communication thread")
    return {
        "ok": True,
        "actor_context": actor.to_dict(),
        "access_decision": access_decision,
        "permission_decision": permission_decision,
        "thread": thread,
    }


@app.get("/mail/inbound/messages", response_model=list[InboundMailMessage])
def inbound_mail_messages_api(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[InboundMailMessage]:
    actor = build_actor_context(request=request)
    _ensure_inbound_mail_access(request, actor, "inbound mail messages")
    _ensure_permission(actor, "mail.read", "inbound_mail_messages")
    bounded_limit = max(1, min(limit, 200))
    return [
        InboundMailMessage(**item)
        for item in list_inbound_mail_messages(
            since=since,
            until=until,
            limit=bounded_limit,
            offset=max(0, offset),
            actor_context=actor.to_dict(),
        )
    ]


@app.post("/mail/inbound/{message_id}/draft-reply", response_model=InboundDraftReplyResponse)
def draft_inbound_mail_reply_api(message_id: str, request: Request) -> InboundDraftReplyResponse:
    actor = build_actor_context(request=request)
    _ensure_inbound_mail_access(request, actor, "inbound mail draft reply")
    _ensure_permission(actor, "mail.read", f"inbound_mail_message:{message_id}")
    try:
        return InboundDraftReplyResponse(**draft_reply_for_message(message_id, actor_context=actor.to_dict()))
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
def approve_dlp_task_api(task_id: str, payload: DlpTaskApprovalRequest, request: Request) -> DlpTaskResponse:
    actor = build_actor_context(request=request, payload=payload)
    _ensure_permission(actor, "task.approve", task_id)
    existing_task = get_dlp_task(task_id)
    if not existing_task:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    if not actor.is_local_dev and (
        str(existing_task.get("tenant_id") or "") != actor.tenant_id
        or str(existing_task.get("workspace_id") or "") != actor.workspace_id
    ):
        raise HTTPException(status_code=403, detail="task_id does not belong to this actor context")
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
        try:
            enqueue_email_send_task(task_id)
        except Exception as exc:
            task = _mark_task_enqueue_failed(
                task,
                service="celery",
                operation="enqueue_email_send_task",
                error=str(exc),
                actor_context=actor.to_dict(),
            )
    return _task_response(task)


@app.post("/tasks/{task_id}/reject", response_model=DlpTaskResponse)
def reject_dlp_task_api(task_id: str, payload: DlpTaskApprovalRequest, request: Request) -> DlpTaskResponse:
    actor = build_actor_context(request=request, payload=payload)
    _ensure_permission(actor, "task.approve", task_id)
    existing_task = get_dlp_task(task_id)
    if not existing_task:
        raise HTTPException(status_code=404, detail="Unknown task_id")
    if not actor.is_local_dev and (
        str(existing_task.get("tenant_id") or "") != actor.tenant_id
        or str(existing_task.get("workspace_id") or "") != actor.workspace_id
    ):
        raise HTTPException(status_code=403, detail="task_id does not belong to this actor context")
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


@app.post("/agent/chat", response_model=UnifiedAgentResponse)
def agent_chat(payload: UnifiedAgentRequest, request: Request) -> UnifiedAgentResponse:
    actor = build_actor_context(
        request=request,
        payload=payload,
        session_id=payload.session_id,
        conversation_id=payload.conversation_id or "",
    )
    permission_decision = _ensure_permission(actor, "agent.chat", "agent_chat")
    rate_limit_decision = _ensure_rate_limit(actor, "agent_chat")
    queue_status = _refresh_queue_metrics()

    conversation, _ = _ensure_conversation(payload.session_id, payload.conversation_id, actor.to_dict())
    conversation_id = str(conversation["conversation_id"])
    actor = replace(actor, conversation_id=conversation_id)
    actor_context = actor.to_dict()
    actor_context = _agent_chat_mark_mail_read_if_authorized(request, actor, actor_context)
    continuation_state_snapshot: dict[str, Any] = {}
    agent_chat_context_snapshot: dict[str, Any] = {}
    agent_chat_context_observations: list[dict[str, Any]] = []

    def finalize(response: UnifiedAgentResponse, *, task_mode: str = "sync") -> UnifiedAgentResponse:
        if agent_chat_context_observations:
            response.tool_observations = _prepend_observations_once(
                list(response.tool_observations or []),
                agent_chat_context_observations,
            )
            response.task_plan = {
                **dict(response.task_plan or {}),
                "tool_observations": response.tool_observations,
                "agent_chat_context": dict(agent_chat_context_snapshot),
            }
        _persist_confirmation_object(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            confirmation_payload=dict(response.confirmation_payload or response.pending_confirmation or {}),
            actor_context=actor_context,
        )
        response.task_plan = {
            **dict(response.task_plan or {}),
            "communication_workspace": _derive_communication_workspace_state(
                {
                    "task_plan": dict(response.task_plan or {}),
                    "tool_observations": list(response.tool_observations or []),
                    "pending_confirmation": dict(response.pending_confirmation or {}),
                    "confirmation_payload": dict(response.confirmation_payload or {}),
                },
                conversation_id,
            ),
        }
        if continuation_state_snapshot:
            response.task_plan = {
                **dict(response.task_plan or {}),
                "continuation_state": dict(continuation_state_snapshot),
            }
        return _attach_landing_context(
            response,
            actor=actor,
            permission_decision=permission_decision,
            rate_limit_decision=rate_limit_decision,
            queue_status=queue_status,
            task_mode=task_mode,
        )

    outbound_message = _build_outbound_message(payload.message, payload.uploaded_text, payload.uploaded_filename)
    display_message = _build_display_message(payload.message, payload.uploaded_filename, payload.source_parse_status)
    upload_context, recalled_upload = _resolve_upload_context(payload, conversation_id, actor_context)
    resolved_agent_chat_context = _resolve_agent_chat_context(
        payload,
        conversation_id=conversation_id,
        actor_context=actor_context,
    )
    agent_chat_context_snapshot = dict(resolved_agent_chat_context.get("context") or {})
    agent_chat_context_observations = [
        dict(item)
        for item in list(resolved_agent_chat_context.get("observations") or [])
        if isinstance(item, dict)
    ]
    if agent_chat_context_snapshot:
        upload_context = {
            **dict(upload_context or {}),
            "agent_chat_context": dict(agent_chat_context_snapshot),
        }
    if recalled_upload and upload_context.get("filename"):
        display_message = _build_display_message(
            payload.message,
            f"{upload_context.get('filename')} (沿用上次上传)",
            str(upload_context.get("parse_status") or "parsed"),
        )
    completed_meeting_task = _latest_completed_meeting_task(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        actor_context=actor_context,
    )
    if completed_meeting_task and _looks_like_meeting_result_followup(payload.message):
        return finalize(_build_meeting_result_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            task=completed_meeting_task,
            actor_context=actor_context,
        ))
    if completed_meeting_task and _looks_like_meeting_invitation_continuation(payload.message):
        recipient = _extract_email_from_message(payload.message)
        meeting_mail_plan = _mail_plan_from_meeting_task(
            task=completed_meeting_task,
            conversation_id=conversation_id,
            request_message=payload.message,
            recipient=recipient,
        )
        patched_mail_plan = patch_pending_mail_plan(
            message=payload.message,
            request_message=payload.message,
            mail_plan=meeting_mail_plan,
        )
        if patched_mail_plan.get("needs_clarification"):
            return finalize(_build_mail_clarification_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(patched_mail_plan.get("mail_plan") or meeting_mail_plan),
                candidates=[],
                actor_context=actor_context,
            ))
        if patched_mail_plan.get("ok"):
            patched_plan = dict(patched_mail_plan.get("mail_plan") or meeting_mail_plan)
            if patched_plan.get("resolved_recipients") and not patched_plan.get("missing_fields"):
                return finalize(_build_mail_confirmation_response(
                    session_id=payload.session_id,
                    conversation_id=conversation_id,
                    message=payload.message,
                    display_message=display_message,
                    mail_plan=patched_plan,
                    actor_context=actor_context,
                ))
            return finalize(_build_mail_patch_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=patched_plan,
                patch_kind=str(patched_mail_plan.get("patch_kind") or "meeting_invitation_continuation"),
                actor_context=actor_context,
            ))
        return finalize(_build_mail_clarification_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            mail_plan=meeting_mail_plan,
            candidates=[],
            actor_context=actor_context,
        ))
    latest_confirmation = _get_latest_pending_confirmation(conversation_id, actor_context)
    pending_object = pending_object_from_confirmation(latest_confirmation)
    latest_source_clarification = _get_latest_pending_source_clarification(conversation_id, actor_context)
    source_pending_object = pending_object_from_source_clarification(
        dict(latest_source_clarification.get("payload") or {})
    )
    latest_field_clarification = _get_latest_pending_mail_field_clarification(conversation_id, actor_context)
    field_pending_object = pending_object_from_mail_clarification(
        dict(latest_field_clarification.get("payload") or {})
    )
    registry_objects = _active_registry_objects(conversation_id, actor_context)
    pending_objects_by_key = {
        (item.object_type, item.object_id): item
        for item in registry_objects
    }
    for item in (pending_object, source_pending_object, field_pending_object):
        if item is not None:
            pending_objects_by_key[(item.object_type, item.object_id)] = item
    pending_objects = list(pending_objects_by_key.values())
    continuation_decision = resolve_continuation(payload.message, pending_objects)
    continuation_state_snapshot.update(
        {
            "decision": continuation_decision.to_dict(),
            "active_objects": [
                {
                    "object_id": item.object_id,
                    "object_type": item.object_type,
                    "status": item.status,
                    "allowed_continuations": list(item.allowed_continuations),
                }
                for item in pending_objects
            ],
        }
    )
    if continuation_decision.mode == "ambiguous":
        return finalize(_build_continuation_clarification_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            decision=continuation_decision,
            pending_objects=pending_objects,
            actor_context=actor_context,
        ))
    if (
        continuation_decision.mode == "continue_existing"
        and continuation_decision.continuation_type == "cancel"
    ):
        cancel_target = next(
            (
                item
                for item in pending_objects
                if item.object_type == continuation_decision.object_type
                and item.object_id == continuation_decision.object_id
            ),
            None,
        )
        if cancel_target is not None:
            registry_object_id = cancel_target.object_id
            if cancel_target.object_type == "source_clarification":
                registry_object_id = str(latest_source_clarification.get("object_id") or registry_object_id)
            elif cancel_target.object_type in {"recipient_clarification", "body_clarification"}:
                registry_object_id = str(latest_field_clarification.get("object_id") or registry_object_id)
            elif cancel_target.object_type in {"mail_confirmation", "domain_confirmation"}:
                registry_object_id = str(latest_confirmation.get("object_id") or registry_object_id)
            consume_pending_object(registry_object_id, actor_context=actor_context, status="cancelled")
            cancel_mail_plan = dict(cancel_target.payload.get("mail_plan") or {})
            draft_id = str(cancel_mail_plan.get("draft_id") or "")
            if draft_id:
                cancel_mail_draft(draft_id, actor_context=actor_context)
                _consume_mail_draft_registry_object(draft_id, actor_context)
            return finalize(_build_pending_object_cancelled_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                pending_object=cancel_target,
                actor_context=actor_context,
            ))
    if (
        continuation_decision.mode == "continue_existing"
        and continuation_decision.continuation_type == "confirm"
        and pending_object is not None
    ):
        if latest_confirmation.get("kind") == "domain":
            pending_domain_confirmation = dict(latest_confirmation.get("payload") or {})
            action = str(pending_domain_confirmation.get("tool_name") or pending_domain_confirmation.get("action_name") or "")
            permission_action = "meeting.write" if action.startswith("meeting_") else "calendar.write"
            _ensure_permission(actor, permission_action, action)
            task = _create_domain_task_from_confirmation(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                request_message=payload.message,
                confirmation_payload=pending_domain_confirmation,
                actor_context=actor_context,
            )
            consume_pending_object(pending_object.object_id, actor_context=actor_context)
            return finalize(_build_task_agent_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                outbound_message=payload.message,
                display_message=display_message,
                task=task,
                routing_reason="Confirmed a pending domain-agent action and queued it for governed async execution.",
                actor_context=actor_context,
            ))
        pending_confirmation = dict(latest_confirmation.get("payload") or {}) if latest_confirmation.get("kind") == "mail" else {}
        pending_mail_plan = dict(pending_confirmation.get("mail_plan") or {})
        if pending_mail_plan:
            _ensure_permission(actor, "mail.send", "pending_mail_confirmation")
            task = _create_dlp_task_from_mail_plan(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                request_message=str(pending_mail_plan.get("request_message") or payload.message),
                mail_plan=pending_mail_plan,
                actor_context=actor_context,
            )
            consume_pending_object(pending_object.object_id, actor_context=actor_context)
            _consume_mail_draft_registry_object(str(pending_mail_plan.get("draft_id") or ""), actor_context)
            mail_task_observation = _build_mail_task_created_observation(
                task=task,
                mail_plan=pending_mail_plan,
                actor_context=actor_context,
            )
            return finalize(_build_task_agent_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                outbound_message=str(pending_mail_plan.get("resolved_body") or payload.message),
                display_message=display_message,
                task=task,
                routing_reason="Confirmed a pending mail plan and created a governed DLP task.",
                intent="action_or_draft",
                current_goal="action_or_draft",
                routing_source="mail_action",
                routing_confidence=0.99,
                candidate_intents=["action_or_draft"],
                mode_used="mail_action",
                extra_observations=[mail_task_observation],
                final_answer_source="mail_task_created_renderer",
                actor_context=actor_context,
            ))
    if (
        continuation_decision.mode == "continue_existing"
        and continuation_decision.continuation_type == "choose_source"
        and source_pending_object is not None
    ):
        if _contains_inbound_mail_candidates(list(source_pending_object.payload.get("candidates") or [])):
            actor_context = _ensure_agent_chat_inbound_mail_access(request, actor, actor_context)
        clarified_mail_action_plan = _resolve_pending_mail_source_clarification(
            user_message=payload.message,
            pending_payload=dict(source_pending_object.payload),
            conversation_id=conversation_id,
        )
        if clarified_mail_action_plan.get("needs_clarification"):
            return finalize(_build_mail_clarification_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=str(dict(source_pending_object.payload).get("request_message") or payload.message),
                display_message=display_message,
                mail_plan=dict(clarified_mail_action_plan.get("mail_plan") or {}),
                candidates=list(clarified_mail_action_plan.get("candidates") or source_pending_object.payload.get("candidates") or []),
                actor_context=actor_context,
            ))
        if clarified_mail_action_plan.get("ok") and clarified_mail_action_plan.get("mode") == "confirmation_required":
            if latest_source_clarification.get("object_id"):
                consume_pending_object(str(latest_source_clarification.get("object_id")), actor_context=actor_context)
            return finalize(_build_mail_confirmation_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=str(dict(source_pending_object.payload).get("request_message") or payload.message),
                display_message=display_message,
                mail_plan=dict(clarified_mail_action_plan.get("mail_plan") or {}),
                actor_context=actor_context,
            ))
        if clarified_mail_action_plan.get("ok") and clarified_mail_action_plan.get("mode") == "draft_only":
            if latest_source_clarification.get("object_id"):
                consume_pending_object(str(latest_source_clarification.get("object_id")), actor_context=actor_context)
            return finalize(_build_mail_draft_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=str(dict(source_pending_object.payload).get("request_message") or payload.message),
                display_message=display_message,
                mail_plan=dict(clarified_mail_action_plan.get("mail_plan") or {}),
                actor_context=actor_context,
            ))
    if (
        continuation_decision.mode == "continue_existing"
        and continuation_decision.continuation_type == "provide_missing_field"
        and field_pending_object is not None
    ):
        clarified_mail_action_plan = _resolve_pending_mail_field_clarification(
            user_message=payload.message,
            pending_payload=dict(field_pending_object.payload),
        )
        if clarified_mail_action_plan.get("needs_clarification"):
            return finalize(_build_mail_clarification_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=str(dict(field_pending_object.payload).get("request_message") or payload.message),
                display_message=display_message,
                mail_plan=dict(clarified_mail_action_plan.get("mail_plan") or {}),
                candidates=list(clarified_mail_action_plan.get("candidates") or field_pending_object.payload.get("candidates") or []),
                actor_context=actor_context,
            ))
        if clarified_mail_action_plan.get("ok") and clarified_mail_action_plan.get("mode") == "confirmation_required":
            if latest_field_clarification.get("object_id"):
                consume_pending_object(str(latest_field_clarification.get("object_id")), actor_context=actor_context)
            return finalize(_build_mail_confirmation_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=str(dict(field_pending_object.payload).get("request_message") or payload.message),
                display_message=display_message,
                mail_plan=dict(clarified_mail_action_plan.get("mail_plan") or {}),
                actor_context=actor_context,
            ))
    if (
        continuation_decision.mode == "continue_existing"
        and continuation_decision.continuation_type == "patch"
    ):
        patch_target = next(
            (
                item
                for item in pending_objects
                if item.object_type == continuation_decision.object_type
                and item.object_id == continuation_decision.object_id
            ),
            None,
        )
        patch_mail_plan = dict((patch_target.payload if patch_target else {}).get("mail_plan") or {})
        if patch_target is not None and patch_target.object_type == "mail_draft" and patch_mail_plan:
            patched_mail_plan = patch_pending_mail_plan(
                message=payload.message,
                request_message=payload.message,
                mail_plan=patch_mail_plan,
                structured_patch=dict(continuation_decision.parameters or {}),
            )
            if patched_mail_plan.get("needs_clarification"):
                return finalize(_build_mail_clarification_response(
                    session_id=payload.session_id,
                    conversation_id=conversation_id,
                    message=payload.message,
                    display_message=display_message,
                    mail_plan=dict(patched_mail_plan.get("mail_plan") or patch_mail_plan),
                    candidates=[],
                    actor_context=actor_context,
                ))
            if patched_mail_plan.get("ok"):
                return finalize(_build_mail_patch_response(
                    session_id=payload.session_id,
                    conversation_id=conversation_id,
                    message=payload.message,
                    display_message=display_message,
                    mail_plan=dict(patched_mail_plan.get("mail_plan") or patch_mail_plan),
                    patch_kind=str(patched_mail_plan.get("patch_kind") or "edit_pending_draft"),
                    actor_context=actor_context,
                ))
    multi_agent_dag_plan = plan_multi_agent_dag_request(message=payload.message, actor_context=actor_context)
    if _agent_chat_requests_inbound_mail_access(payload.message, multi_agent_plan=multi_agent_dag_plan):
        actor_context = _ensure_agent_chat_inbound_mail_access(request, actor, actor_context)
    recoverable_task = get_latest_recoverable_task(payload.session_id, conversation_id)
    if multi_agent_dag_plan is None and _should_apply_recoverable_supplement(recoverable_task, payload, conversation_id):
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
        return finalize(_build_task_agent_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            outbound_message=outbound_message or str(supplemented.get("message_raw", "")),
            display_message=display_message,
            task=supplemented,
            routing_reason="Applied supplemental information to a governed DLP task.",
            actor_context=actor_context,
        ))
    route_decision: dict[str, Any] | None = None
    rule_mail_action_match = looks_like_mail_action_request(payload.message)
    is_mail_status_query = _looks_like_outbound_mail_summary_query(payload.message) or _looks_like_inbound_mail_query(payload.message)
    if multi_agent_dag_plan is None and not rule_mail_action_match and not is_mail_status_query:
        route_decision = route_agent_request(
            message=payload.message,
            safe_message=payload.message,
            upload_context=upload_context,
        )
        if _agent_chat_requests_inbound_mail_access(payload.message, route_decision=route_decision):
            actor_context = _ensure_agent_chat_inbound_mail_access(request, actor, actor_context)
    semantic_mail_action_match = str((route_decision or {}).get("intent") or "") == "mail_action"
    if multi_agent_dag_plan is None and (rule_mail_action_match or semantic_mail_action_match) and not is_mail_status_query:
        mail_action_plan = _build_mail_action_plan(payload, conversation_id, dict(upload_context or {}), actor_context=actor_context)
        if mail_action_plan.get("needs_clarification"):
            return finalize(_build_mail_clarification_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
                candidates=list(mail_action_plan.get("candidates") or []),
                actor_context=actor_context,
            ))
        if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "unsupported":
            return finalize(_build_mail_unsupported_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
                actor_context=actor_context,
            ))
        if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "draft_only":
            return finalize(_build_mail_draft_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
                actor_context=actor_context,
            ))
        if mail_action_plan.get("ok") and mail_action_plan.get("mode") == "confirmation_required":
            return finalize(_build_mail_confirmation_response(
                session_id=payload.session_id,
                conversation_id=conversation_id,
                message=payload.message,
                display_message=display_message,
                mail_plan=dict(mail_action_plan.get("mail_plan") or {}),
                actor_context=actor_context,
            ))
    upload_decision = classify_upload_request(message=payload.message, upload_context=upload_context)
    if upload_decision.route == "outbound" and multi_agent_dag_plan is None:
        _ensure_permission(actor, "mail.send", "outbound_mail")
        return finalize(
            _handle_async_outbound_agent_request(
                payload,
                conversation_id,
                outbound_message,
                display_message,
                upload_context,
                actor_context=actor_context,
            )
        )
    if (
        upload_decision.route in {"none", "analyze"}
        and multi_agent_dag_plan is None
        and not (_looks_like_outbound_mail_summary_query(payload.message) or _looks_like_inbound_mail_query(payload.message))
        and (_looks_like_outbound_action(payload.message) or _looks_like_contextual_outbound_request(payload.message))
    ):
        _ensure_permission(actor, "mail.send", "outbound_mail")
        return finalize(
            _handle_async_outbound_agent_request(
                payload,
                conversation_id,
                outbound_message,
                display_message,
                upload_context,
                actor_context=actor_context,
            )
        )

    compound_response = _handle_compound_agent_request(payload, conversation_id, actor_context=actor_context)
    if compound_response is not None:
        return finalize(compound_response)

    if route_decision is None:
        route_decision = route_agent_request(
            message=payload.message,
            safe_message=payload.message,
            upload_context=upload_context,
        )
        if _agent_chat_requests_inbound_mail_access(payload.message, route_decision=route_decision):
            actor_context = _ensure_agent_chat_inbound_mail_access(request, actor, actor_context)
    if multi_agent_dag_plan is not None:
        route_decision = {
            **route_decision,
            "route_mode": "slow",
            "intent": "multi_agent_dag",
            "required_grounding": "tool",
            "recommended_tool": "dag_executor",
            "router_reason": multi_agent_dag_plan.planner_reason,
        }
    if str(route_decision.get("route_mode") or "slow") == "fast":
        try:
            return finalize(_execute_fast_path(
                payload=payload,
                conversation_id=conversation_id,
                display_message=display_message,
                upload_context=upload_context,
                route_decision=route_decision,
                actor_context=actor_context,
            ))
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
            actor_context=actor_context,
            initial_observations=agent_chat_context_observations,
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
                return finalize(_execute_fast_path(
                    payload=payload,
                    conversation_id=conversation_id,
                    display_message=display_message,
                    upload_context=upload_context,
                    route_decision=fallback_route_decision,
                    actor_context=actor_context,
                ))
            except Exception:
                logger.exception("Safe fast fallback failed after orchestration failure")
        return finalize(_build_rule_clarification_response(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            display_message=display_message,
            clarification_question="The planning stage failed before a grounded answer could be produced. Please narrow the question and try again.",
            routing_reason="The orchestration path failed before a grounded answer could be safely produced.",
        ))

    result["actor_context"] = actor_context
    if agent_chat_context_observations:
        result["tool_observations"] = _prepend_observations_once(
            list(result.get("tool_observations") or []),
            agent_chat_context_observations,
        )
        task_plan = dict(result.get("task_plan") or {})
        task_plan["tool_observations"] = list(result["tool_observations"])
        task_plan["agent_chat_context"] = dict(agent_chat_context_snapshot)
        result["task_plan"] = task_plan
    result["permission_decision"] = permission_decision
    result["rate_limit_decision"] = rate_limit_decision
    result["queue_status"] = queue_status
    _refresh_result_communication_workspace(result, conversation_id)
    _persist_confirmation_object(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        confirmation_payload=dict(result.get("confirmation_payload") or result.get("pending_confirmation") or {}),
        actor_context=actor_context,
    )
    result = attach_trace_evaluation(result, actor_context=actor_context)
    turn_id, memory_written = _write_unified_conversation_memory(result, conversation_id, actor_context=actor_context)
    _refresh_result_communication_workspace(result, conversation_id)
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
        actor_context=actor_context,
        permission_decision=permission_decision,
        rate_limit_decision=rate_limit_decision,
        queue_status=queue_status,
        task_mode="sync",
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
    try:
        refresh_rag_index_metrics(build_active_index_contract())
    except Exception:
        logger.debug("EnterpriseRAG index metrics refresh failed", exc_info=True)
    return Response(content=render_metrics(), media_type=content_type())
