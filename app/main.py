from __future__ import annotations

import json
import logging
import re
import uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from langchain_core.messages import HumanMessage, SystemMessage

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
from app.graph import execute_confirmed_plan, get_llm, preview_plan, run_agent
from app.ingest import ingest_if_needed
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
    record_turn_summary_write,
    record_unified_agent,
    record_unified_evidence_hits,
    record_workflow_approved,
    record_workflow_created,
    record_workflow_email_sent,
    record_workflow_rejected,
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
    DisambiguationQueryRequest,
    DisambiguationQueryResponse,
    ExecuteRequest,
    FrameworkCompareRequest,
    FrameworkCompareResponse,
    HealthResponse,
    IngestResponse,
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
from app.privacy_lab import scan_sensitive_message
from app.raw_vs_langgraph import compare_raw_llm_and_langgraph
from app.session_store import append_turn, delete_plan, load_plan, save_plan
from app.sensitive_workflow import run_sensitive_outbound_workflow
from app.unified_agent import run_unified_agent
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
    try:
        written = ingest_if_needed(force=False)
        if written:
            logger.info("Seeded Chroma with %s documents.", written)
    except Exception as exc:  # pragma: no cover - startup best effort
        logger.warning("Startup ingest skipped: %s", exc)
    yield


app = FastAPI(title="LeetCode RAG Agent", version="0.3.0", lifespan=lifespan)

DEFAULT_DLP_EMAIL = "17388861183@163.com"
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _workflow_response(workflow: dict) -> SensitiveWorkflowResponse:
    return SensitiveWorkflowResponse(**workflow, audit_events=get_audit_events(str(workflow["workflow_id"])))


def _source_preview(text: str, limit: int = 800) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def _looks_like_outbound_action(message: str) -> bool:
    lowered = message.lower()
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
        "reflection_notes": result.get("reflection_notes"),
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

    question = str(result.get("message", ""))
    safe_question = str(result.get("safe_message") or question)
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
    if _looks_like_outbound_action(outbound_message):
        workflow_payload = SensitiveWorkflowCreateRequest(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            message=outbound_message,
            business_context="agent_chat_outbound_request",
            recipient_type="email",
            destination_email=_extract_destination_email(payload.message),
            source_filename=payload.uploaded_filename,
            source_content_type=payload.uploaded_content_type,
        )
        workflow = _create_dlp_workflow_from_payload(workflow_payload)
        workflow_id = str(workflow["workflow_id"])
        status = str(workflow["status"])
        risk_level = str(workflow["risk_level"])
        if status == "pending_approval":
            answer_text = (
                f"检测到敏感外发风险，任务已挂起。workflow_id={workflow_id}，"
                "请在侧边栏 DLP 外发审批 Agent 中审批后再真实发送。"
            )
        elif status == "sent":
            answer_text = str(workflow.get("final_result") or workflow.get("delivery_result") or "外发已完成。")
        elif status == "send_failed":
            answer_text = (
                "DLP 检查已通过，但邮件真实发送失败。"
                f"workflow_id={workflow_id}，错误：{workflow.get('delivery_error', '')}"
            )
        else:
            answer_text = str(workflow.get("final_result") or workflow.get("delivery_result") or "任务已完成。")

        synthetic_result = {
            "session_id": payload.session_id,
            "conversation_id": conversation_id,
            "request_id": workflow_id,
            "message": outbound_message,
            "safe_message": workflow.get("redacted_text", outbound_message),
            "answer": answer_text,
            "intent": "privacy_alert",
            "routing_source": "rule",
            "routing_confidence": 0.99,
            "routing_reason": "Detected summarize-and-send outbound request.",
            "candidate_intents": ["privacy_alert"],
            "mode_used": "workflow",
            "tool_calls": [
                {
                    "tool_name": "send_email_163",
                    "success": status == "sent",
                    "status": workflow.get("delivery_status") or status,
                    "error": workflow.get("delivery_error", ""),
                }
            ],
            "retrieved_evidence": [],
            "needs_clarification": False,
            "clarification_question": None,
            "privacy": {
                "redacted": bool(workflow.get("redactions")),
                "risk_level": risk_level,
                "redactions": workflow.get("redactions", []),
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
            mode="workflow",
            latency_ms=0.0,
            token_in=0,
            token_out=0,
            estimated_cost=0.0,
            retrieval_hits=0,
        )
        return UnifiedAgentResponse(
            session_id=payload.session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            answer=answer_text,
            intent="privacy_alert",
            routing_source="rule",
            routing_confidence=0.99,
            routing_reason="Detected summarize-and-send outbound request.",
            candidate_intents=["privacy_alert"],
            mode_used="workflow",
            tool_calls=synthetic_result["tool_calls"],
            retrieved_evidence=[],
            needs_clarification=False,
            clarification_question=None,
            privacy=synthetic_result["privacy"],
            context_budget={},
            citations=[],
            memory_written=memory_written,
            memory_hits=0,
            merged_memory_hits=0,
            memory_context={},
            answer_collapsed=False,
            reflection_notes=None,
            workflow_id=workflow_id,
            workflow_status=status,
            workflow_risk_level=risk_level,
            delivery_status=str(workflow.get("delivery_status") or status),
            delivery_result=str(workflow.get("delivery_result") or ""),
            delivery_error=str(workflow.get("delivery_error") or ""),
            trace_id=str(uuid.uuid4()),
            latency_ms=0.0,
            token_in=0,
            token_out=0,
            estimated_cost=0.0,
        )

    try:
        result = run_unified_agent(
            session_id=payload.session_id,
            message=outbound_message,
            mode=payload.mode,
            problem_id=payload.problem_id,
            conversation_id=conversation_id,
            show_steps=payload.show_steps,
        )
    except Exception as exc:
        record_failure()
        logger.exception("Unified Agent request failed")
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
        answer_collapsed=answer_collapsed,
        reflection_notes=result.get("reflection_notes"),
        workflow_id=None,
        workflow_status=None,
        workflow_risk_level=None,
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
    return Response(content=render_metrics(), media_type=content_type())
