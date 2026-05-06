from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from contextlib import asynccontextmanager
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from redis import Redis

from app.config import get_settings
from app.conversation_memory import compact_text, infer_title, write_merged_summary, write_turn_summary
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
from app.unified_agent import run_unified_agent
from app.upload_analysis import build_upload_context, classify_upload_request, refers_to_recent_upload
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


def _create_async_dlp_task(payload: DlpTaskCreateRequest) -> dict:
    combined_message = _task_message(payload.message, payload.uploaded_text, payload.uploaded_filename)
    fault_injection = normalize_fault_injection(payload.fault_injection)
    task = create_dlp_task(
        session_id=payload.session_id,
        conversation_id=payload.conversation_id,
        message_raw=combined_message,
        destination_email=payload.destination_email,
        source_filename=payload.uploaded_filename,
        source_content_type=payload.uploaded_content_type,
        requested_action=payload.requested_action,
        lab_run=payload.lab_run,
        scenario_id=payload.scenario_id,
        scenario_name=payload.scenario_name,
        fault_injection=fault_injection,
        expected_outcome=dict(payload.expected_outcome or {}),
    )
    record_task_created()
    if payload.lab_run and payload.scenario_id:
        record_dlp_scenario_replay(payload.scenario_id)
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
    return task


def _looks_like_outbound_action(message: str) -> bool:
    return dlp_looks_like_outbound_action(message)


def _extract_destination_email(message: str) -> str:
    return dlp_extract_destination_email(message)


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
        summary = str(upload_context.get("summary") or "").strip()
        snippets = [
            str(item).strip()
            for item in upload_context.get("key_snippets") or []
            if str(item).strip()
        ]
        if summary or snippets:
            return upload_context
    return {}


def _resolve_upload_context(payload: UnifiedAgentRequest, conversation_id: str) -> tuple[dict[str, Any], bool]:
    current_upload_context = build_upload_context(
        uploaded_filename=payload.uploaded_filename,
        uploaded_content_type=payload.uploaded_content_type,
        uploaded_text=payload.uploaded_text,
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


def _task_answer_text(task: dict[str, Any]) -> str:
    status = str(task.get("status", ""))
    task_id = str(task.get("task_id", ""))
    if status in {"needs_clarification", "input_invalid"}:
        return str(task.get("clarification_question") or "我已保留你的外发请求，但还需要补充信息后才能继续处理。")
    if status == "queued":
        return (
            f"已创建 DLP 外发任务 `{task_id}`，系统正在异步执行风险判断。"
            " 你可以在左侧任务台查看状态；如果命中敏感信息，任务会自动转入待审批。"
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
    synthetic_result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": task_id,
        "message": outbound_message,
        "safe_message": str(task.get("message_redacted") or outbound_message),
        "display_message": display_message,
        "answer": answer_text,
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
        token_in=0,
        token_out=0,
        estimated_cost=0.0,
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
        answer=answer_text,
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
    combined_message = _task_message(payload.message, payload.uploaded_text, payload.uploaded_filename)
    fault_injection = normalize_fault_injection(payload.fault_injection)
    source_parse_status, source_parse_error = _normalize_source_parse(
        payload.uploaded_filename,
        payload.uploaded_text,
        payload.source_parse_status,
        payload.source_parse_error,
    )
    decision = None if payload.lab_run else classify_dlp_entry(
        message=combined_message,
        destination_email=payload.destination_email,
        uploaded_text=payload.uploaded_text,
        uploaded_filename=payload.uploaded_filename,
        source_parse_status=source_parse_status,
        source_parse_error=source_parse_error,
    )
    destination_email = payload.destination_email.strip() or (decision.destination_email if decision else "")
    if payload.lab_run:
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
    updated_task = update_task(
        str(task["task_id"]),
        message_raw=merged_message,
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
) -> UnifiedAgentResponse:
    result = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "request_id": str(uuid.uuid4()),
        "message": message,
        "safe_message": message,
        "answer": answer,
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
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }
    turn_id, memory_written = _write_unified_conversation_memory(result, conversation_id)
    record_request(
        mode=mode_used,
        latency_ms=0.0,
        token_in=0,
        token_out=0,
        estimated_cost=0.0,
        retrieval_hits=0,
    )
    return UnifiedAgentResponse(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        answer=answer,
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
        token_in=0,
        token_out=0,
        estimated_cost=0.0,
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


def _handle_compound_agent_request(payload: UnifiedAgentRequest, conversation_id: str) -> UnifiedAgentResponse | None:
    task_plan = plan_compound_tasks(payload.message)
    if len(task_plan.subtasks) < 2:
        return None
    since, until = _local_day_window()
    answer_sections: list[str] = []
    tool_results: dict[str, Any] = {"subtasks": []}
    tool_calls: list[dict[str, Any]] = []
    for subtask in task_plan.subtasks:
        if subtask.capability == "outbound_mail_summary":
            result = {"since": since, "until": until, **get_sent_mail_stats(since=since, until=until, session_id=payload.session_id)}
            answer_sections.append(_outbound_summary_answer(result))
        elif subtask.capability == "inbound_mail_summary":
            result = get_inbound_mail_summary()
            answer_sections.append(_inbound_summary_answer(result, latest_sync_state()))
        elif subtask.capability == "enterprise_rag_query":
            result = answer_enterprise_question(
                str(subtask.input.get("question") or payload.message),
                source_types=list(subtask.input.get("source_types") or []),
            )
            answer_sections.append(_enterprise_rag_answer_text(result))
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
) -> UnifiedAgentResponse:
    task_payload = DlpTaskCreateRequest(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        message=payload.message,
        destination_email=_extract_destination_email(payload.message),
        uploaded_filename=payload.uploaded_filename,
        uploaded_content_type=payload.uploaded_content_type,
        uploaded_text=payload.uploaded_text,
        source_parse_status=payload.source_parse_status,
        source_parse_error=payload.source_parse_error,
    )
    task = _create_async_dlp_task(task_payload)
    return _build_task_agent_response(
        session_id=payload.session_id,
        conversation_id=conversation_id,
        outbound_message=outbound_message,
        display_message=display_message,
        task=task,
        routing_reason="Detected outbound request and created a governed DLP task.",
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
        "reflection_notes": result.get("reflection_notes"),
        "planner_type": str(result.get("planner_type", "")),
        "task_plan": result.get("task_plan", {}) or {},
        "subtask_results": result.get("subtask_results", []) or [],
        "aggregation_strategy": str(result.get("aggregation_strategy", "")),
        "partial_failures": result.get("partial_failures", []) or [],
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
    try:
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


@app.post("/tasks/dlp-outbound", response_model=DlpTaskResponse)
def create_dlp_task_api(payload: DlpTaskCreateRequest) -> DlpTaskResponse:
    conversation = get_conversation(payload.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Unknown conversation_id")
    if conversation["session_id"] != payload.session_id:
        raise HTTPException(status_code=403, detail="conversation_id does not belong to this session_id")
    task = _create_async_dlp_task(payload)
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
    upload_decision = classify_upload_request(message=payload.message, upload_context=upload_context)
    if upload_decision.route == "outbound":
        return _handle_async_outbound_agent_request(payload, conversation_id, outbound_message, display_message)
    if (
        upload_decision.route == "none"
        and not (_looks_like_outbound_mail_summary_query(payload.message) or _looks_like_inbound_mail_query(payload.message))
        and _looks_like_outbound_action(payload.message)
    ):
        return _handle_async_outbound_agent_request(payload, conversation_id, outbound_message, display_message)

    try:
        result = orchestrate_agent_request(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=payload.message,
            safe_message=payload.message,
            display_message=display_message,
            upload_context=upload_context,
        )
    except Exception:
        logger.exception("Orchestration request failed; falling back to unified agent")
        try:
            result = run_unified_agent(
                session_id=payload.session_id,
                message=payload.message,
                mode=payload.mode,
                problem_id=payload.problem_id,
                conversation_id=conversation_id,
                show_steps=payload.show_steps,
            )
            result["display_message"] = display_message
            result["upload_context"] = upload_context
        except Exception as exc:
            record_failure()
            logger.exception("Unified Agent fallback failed")
            raise HTTPException(status_code=500, detail=f"Unified Agent execution failed: {exc}") from exc

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
        planner_type=str(result.get("planner_type", "")),
        task_plan=result.get("task_plan", {}) or {},
        subtask_results=result.get("subtask_results", []) or [],
        aggregation_strategy=str(result.get("aggregation_strategy", "")),
        partial_failures=result.get("partial_failures", []) or [],
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
