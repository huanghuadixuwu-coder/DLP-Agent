from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.conversation_memory import compact_text
from app.enterprise_rag.core.answer_composer import compose_enterprise_answer
from app.enterprise_rag.core.query_planner import build_retrieval_plan, infer_source_types
from app.enterprise_rag.core.retrieval_orchestrator import retrieve_evidence
from app.enterprise_rag.core.service import answer_enterprise_question
from app.enterprise_rag.core.types import EvidencePack, SupportingFact
from app.enterprise_rag.libs.metadata import normalize_source_type
from app.graph import get_llm
from app.hermes_memory import build_runtime_context_bundle, search_workspace_memory
from app.inbound_mail import draft_reply_for_message, get_inbound_mail_summary, latest_sync_state, list_inbound_mail_messages
from app.inbound_mail_store import get_inbound_message
from app.orchestration.types import OrchestrationContext, ToolDefinition
from app.task_store import get_latest_recoverable_task, get_sent_mail_stats


ToolCallable = Callable[[dict[str, Any], OrchestrationContext, dict[str, Any]], dict[str, Any]]
VALID_ENTERPRISE_SOURCE_TYPES = {
    "gmail",
    "slack",
    "linear",
    "jira",
    "github",
    "google_drive",
    "confluence",
    "hubspot",
    "fireflies",
}


def _tool_catalog_list(_: dict[str, Any], __: OrchestrationContext, ___: dict[str, Any]) -> dict[str, Any]:
    return {"tools": [asdict(item) for item in build_tool_registry().values()]}


def _conversation_context_fetch(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    return {"conversation_id": payload.get("conversation_id", ""), "notes": "Conversation memory is written by the chat pipeline."}


def _governance_task_context_fetch(_: dict[str, Any], context: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    task = get_latest_recoverable_task(context.session_id, context.conversation_id)
    return {"task": task or {}}


def _memory_search(payload: dict[str, Any], context: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or context.message)
    top_k = int(payload.get("top_k") or 6)
    return {"hits": search_workspace_memory(query, top_k=top_k)}


def _outbound_mail_summary(payload: dict[str, Any], context: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    return get_sent_mail_stats(
        since=payload.get("since"),
        until=payload.get("until"),
        session_id=context.session_id,
    )


def _inbound_mail_summary(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    result = get_inbound_mail_summary(since=payload.get("since"), until=payload.get("until"))
    result["sync_state"] = latest_sync_state()
    return result


def _inbound_message_search(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip().lower()
    limit = int(payload.get("limit") or 8)
    messages = list_inbound_mail_messages(limit=max(limit, 20))
    if query:
        terms = [token for token in query.split() if token]
        filtered = []
        for item in messages:
            haystack = " ".join(
                [
                    str(item.get("subject") or ""),
                    str(item.get("sender") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("snippet") or ""),
                ]
            ).lower()
            if any(term in haystack for term in terms):
                filtered.append(item)
        messages = filtered or messages
    return {"messages": messages[:limit]}


def _inbound_message_read(payload: dict[str, Any], _: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        for item in dependency_payloads.values():
            messages = list(item.get("messages") or [])
            if messages:
                message_id = str(messages[0].get("message_id") or "")
                break
    if not message_id:
        return {"error": "No message_id available for inbound_message_read."}
    message = get_inbound_message(message_id)
    if not message:
        return {"error": f"Unknown message_id: {message_id}"}
    return {"message": message}


def _inbound_reply_draft(payload: dict[str, Any], _: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        for item in dependency_payloads.values():
            direct = item.get("message")
            if isinstance(direct, dict) and direct.get("message_id"):
                message_id = str(direct["message_id"])
                break
            messages = list(item.get("messages") or [])
            if messages:
                message_id = str(messages[0].get("message_id") or "")
                break
    if not message_id:
        return {"error": "No synchronized inbound mail message was found for drafting a reply."}
    return draft_reply_for_message(message_id)


def _enterprise_search(payload: dict[str, Any], context: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    question = str(payload.get("question") or context.message).strip()
    top_k = int(payload.get("top_k") or 8)
    source_types = _sanitize_enterprise_source_types(payload.get("source_types"), question)
    plan = build_retrieval_plan(question, source_types=source_types, top_k=top_k)
    evidence = retrieve_evidence(plan)
    return {"retrieval_plan": asdict(plan), "evidence": _evidence_to_dict(evidence)}


def _enterprise_evidence_pack(payload: dict[str, Any], _: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    evidence_payload = None
    for item in dependency_payloads.values():
        evidence_payload = item.get("evidence")
        if evidence_payload:
            break
    if not evidence_payload:
        evidence_payload = payload.get("evidence")
    if not isinstance(evidence_payload, dict):
        return {"evidence_pack": {"citations": [], "supporting_doc_ids": [], "missing_evidence": True, "confidence": 0.0, "supporting_facts": []}}
    return {"evidence_pack": evidence_payload}


def _enterprise_answer(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    question = str(payload.get("question") or context.message).strip()
    evidence_payload = None
    for item in dependency_payloads.values():
        evidence_payload = item.get("evidence_pack") or item.get("evidence")
        if evidence_payload:
            break
    if not isinstance(evidence_payload, dict):
        return {"answer": "当前企业知识库中没有检索到足够证据，无法可靠回答这个问题。", "citations": [], "supporting_doc_ids": [], "missing_evidence": True, "confidence": 0.0}
    evidence = _dict_to_evidence_pack(question, evidence_payload)
    context_bundle = build_runtime_context_bundle(
        session_id=context.session_id,
        conversation_id=context.conversation_id,
        question=question,
    )
    answer = compose_enterprise_answer(
        question,
        evidence,
        context_text=str(context_bundle.get("context_text") or ""),
        context_sources=list(context_bundle.get("context_sources") or []),
        workspace_memory_hits=int(context_bundle.get("workspace_memory_hits", 0)),
        transcript_hits=int(context_bundle.get("transcript_hits", 0)),
        user_model_used=bool(context_bundle.get("user_model_used", False)),
    )
    return {
        "answer": answer.answer,
        "citations": [asdict(item) for item in answer.citations],
        "supporting_doc_ids": answer.supporting_doc_ids,
        "missing_evidence": answer.missing_evidence,
        "confidence": answer.confidence,
        "supporting_facts": answer.supporting_facts,
        "supporting_fact_details": [asdict(item) for item in answer.supporting_fact_details],
        "retrieval_stage_debug": answer.retrieval_stage_debug,
        "rerank_debug": answer.rerank_debug,
        "evidence_fact_hits": answer.evidence_fact_hits,
        "answer_debug": answer.answer_debug,
        "context_sources": answer.context_sources,
        "workspace_memory_hits": answer.workspace_memory_hits,
        "transcript_hits": answer.transcript_hits,
        "user_model_used": answer.user_model_used,
        "memory_context": context_bundle,
    }


def _enterprise_rag_query(payload: dict[str, Any], context: OrchestrationContext, _: dict[str, Any]) -> dict[str, Any]:
    question = str(payload.get("question") or context.message)
    return answer_enterprise_question(
        question,
        source_types=_sanitize_enterprise_source_types(payload.get("source_types"), question),
        top_k=int(payload.get("top_k") or 8),
        session_id=context.session_id,
        conversation_id=context.conversation_id,
    )


UPLOAD_ANALYZE_PROMPT = """You analyze uploaded content for a secure enterprise mail agent.

Rules:
- Use only the uploaded content provided.
- Do not assume this content should be sent externally unless the user explicitly asks.
- For document summarize: provide a concise summary of what the document is about.
- For document qa: answer the user's question only from the uploaded content.
- For email summarize: summarize the incoming email's topic, key asks, and likely next actions.
- For email qa: answer only from the uploaded email content.
- If the uploaded content is insufficient, say so clearly.
"""


PERSONA_PROMPT = """You are the secure enterprise mail agent for this product.

Goals:
- Answer naturally in Chinese.
- Support lightweight daily chat, greetings, self-introduction, feature introduction, and how-to-use questions.
- Base every capability claim on the current product only.
- Do not claim unsupported abilities such as automatic replying, full mailbox client behavior, or treating uploaded emails as formal inbox records.

Current real capabilities:
- Safe outbound email governance with DLP, approval, and real SMTP sending.
- Inbound mailbox daily digest and inbound mail summaries.
- Enterprise knowledge Q&A backed by EnterpriseRAG-Bench evidence retrieval.
- Uploaded content analysis for document/email summarization and Q&A.
- Task governance status tracking, approval, rejection, and supplement flows.
"""


def _uploaded_content_analyze(payload: dict[str, Any], context: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    upload_context = dict(context.upload_context or {})
    content_kind = str(payload.get("content_kind") or upload_context.get("kind") or "document")
    task_type = str(payload.get("task_type") or "summarize")
    parse_status = str(payload.get("source_parse_status") or upload_context.get("parse_status") or "not_provided")
    parse_error = str(payload.get("source_parse_error") or upload_context.get("parse_error") or "")
    filename = str(payload.get("uploaded_filename") or upload_context.get("filename") or "")
    content_type = str(payload.get("uploaded_content_type") or upload_context.get("content_type") or "")
    uploaded_text = str(payload.get("uploaded_text") or upload_context.get("uploaded_text") or "")

    if parse_error or parse_status in {"parse_failed", "invalid", "empty"}:
        answer = "当前无法解析上传文件，请重新上传可解析的文本文件，或直接粘贴正文后再让我继续分析。"
        return {
            "answer": answer,
            "upload_context": {
                "kind": content_kind,
                "filename": filename,
                "content_type": content_type,
                "parse_status": parse_status,
                "content_available": False,
                "summary": answer,
                "key_snippets": [],
            },
        }

    analysis_source = uploaded_text.strip()
    if not analysis_source:
        recalled_summary = str(upload_context.get("summary") or "").strip()
        snippets = [str(item).strip() for item in upload_context.get("key_snippets") or [] if str(item).strip()]
        analysis_source = "\n".join([part for part in [recalled_summary, *snippets] if part]).strip()
    if not analysis_source:
        answer = "当前没有可用的上传内容可供分析。请重新上传文件，或直接粘贴正文。"
        return {
            "answer": answer,
            "upload_context": {
                "kind": content_kind,
                "filename": filename,
                "content_type": content_type,
                "parse_status": parse_status,
                "content_available": False,
                "summary": answer,
                "key_snippets": [],
            },
        }

    kind_label = "incoming email" if content_kind == "email" else "document"
    task_label = "summarize" if task_type == "summarize" else "answer the question"
    user_message = str(payload.get("message") or context.message)
    answer = ""
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=UPLOAD_ANALYZE_PROMPT),
                HumanMessage(
                    content=(
                        f"Content kind: {kind_label}\n"
                        f"Task: {task_label}\n"
                        f"Filename: {filename or '(unknown)'}\n"
                        f"User request: {user_message}\n\n"
                        f"Uploaded content:\n{analysis_source}"
                    )
                ),
            ]
        )
        answer = str(getattr(response, "content", response)).strip()
    except Exception:
        if task_type == "qa":
            answer = "我已读取上传内容，但当前无法稳定完成问答。你可以改成先让我总结这份内容，或重新提一个更聚焦的问题。"
        elif content_kind == "email":
            answer = "我已按上传邮件内容进行概览。当前看起来这是一封需要阅读和提炼重点的来信，建议先确认主题、核心诉求和待办。"
        else:
            answer = compact_text(analysis_source, 600)

    summary = compact_text(answer, 400)
    raw_snippets = [segment.strip() for segment in re.split(r"\n{2,}", analysis_source) if segment.strip()]
    key_snippets = [compact_text(item, 220) for item in raw_snippets[:3]]
    return {
        "answer": answer,
        "upload_context": {
            "kind": content_kind,
            "filename": filename,
            "content_type": content_type,
            "parse_status": parse_status,
            "content_available": True,
            "summary": summary,
            "key_snippets": key_snippets,
        },
    }


def _unsupported_capability(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    reason = str(payload.get("reason") or "当前暂不支持这个请求。")
    capability = str(payload.get("capability") or "unsupported_capability")
    return {"answer": reason, "unsupported_capability": capability}


def _persona_or_chitchat(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    message = str(payload.get("message") or "").strip()
    lowered = message.lower()
    if lowered in {"你好", "hi", "hello", "hey"}:
        return {"answer": "你好，我是安全外发与邮件协作 Agent。你可以让我总结上传文档、查看收件早报、回答企业知识问题，或把外发内容交给我走 DLP 审批和真实发信。"}
    try:
        response = get_llm().invoke(
            [
                SystemMessage(content=PERSONA_PROMPT),
                HumanMessage(content=message),
            ]
        )
        answer = str(getattr(response, "content", response)).strip()
    except Exception:
        answer = "我是安全外发与邮件协作 Agent，主要帮助你做上传内容分析、企业知识问答、收件早报查看，以及带 DLP 审批的安全外发。"
    return {"answer": answer}


def build_tool_registry() -> dict[str, ToolDefinition]:
    return {
        "tool_catalog_list": ToolDefinition("tool_catalog_list", "List available orchestration tools and preconditions.", input_schema={}),
        "conversation_context_fetch": ToolDefinition("conversation_context_fetch", "Fetch short-term conversation context metadata.", input_schema={}),
        "governance_task_context_fetch": ToolDefinition("governance_task_context_fetch", "Fetch latest recoverable governance task in the conversation.", input_schema={}),
        "memory_search": ToolDefinition("memory_search", "Search workspace memory markdown files via hybrid vector + FTS retrieval.", input_schema={"query": "user query", "top_k": "int"}),
        "outbound_mail_summary": ToolDefinition("outbound_mail_summary", "Summarize outbound mail sent by this Agent in a time window.", input_schema={"since": "ISO datetime", "until": "ISO datetime"}),
        "inbound_mail_summary": ToolDefinition("inbound_mail_summary", "Summarize inbound mail in a time window.", input_schema={"since": "ISO datetime", "until": "ISO datetime"}),
        "inbound_message_search": ToolDefinition("inbound_message_search", "Search synchronized inbound mail by keywords.", input_schema={"query": "user query", "limit": "int"}),
        "inbound_message_read": ToolDefinition("inbound_message_read", "Read a synchronized inbound mail message.", input_schema={"message_id": "mail message id"}),
        "inbound_reply_draft": ToolDefinition("inbound_reply_draft", "Draft a reply for a synchronized inbound mail message.", input_schema={"message_id": "mail message id"}),
        "uploaded_content_analyze": ToolDefinition(
            "uploaded_content_analyze",
            "Analyze uploaded content as a document or a single incoming email for summarization or question answering.",
            input_schema={
                "content_kind": "document|email|unknown",
                "task_type": "summarize|qa",
                "message": "user request",
                "uploaded_filename": "filename",
                "uploaded_content_type": "mime type",
                "uploaded_text": "parsed uploaded text",
                "source_parse_status": "parse status",
                "source_parse_error": "parse error",
            },
        ),
        "persona_or_chitchat": ToolDefinition(
            "persona_or_chitchat",
            "Handle self-introduction, capability explanation, how-to-use guidance, and lightweight daily chitchat.",
            input_schema={"message": "user message"},
        ),
        "unsupported_capability": ToolDefinition("unsupported_capability", "Return a clear unsupported-capability response with next-step guidance.", input_schema={"reason": "user-facing explanation", "capability": "unsupported capability id"}),
        "enterprise_search": ToolDefinition("enterprise_search", "Retrieve enterprise knowledge evidence from EnterpriseRAG-Bench index.", input_schema={"question": "user question", "source_types": "list[str]", "top_k": "int"}),
        "enterprise_evidence_pack": ToolDefinition("enterprise_evidence_pack", "Normalize retrieved enterprise evidence into a citation-ready pack.", input_schema={"evidence": "evidence payload"}),
        "enterprise_answer": ToolDefinition("enterprise_answer", "Compose an enterprise answer from evidence only.", input_schema={"question": "user question"}),
        "enterprise_rag_query": ToolDefinition("enterprise_rag_query", "Run retrieval + evidence + answer composition for enterprise knowledge questions.", input_schema={"question": "user question", "source_types": "list[str]", "top_k": "int"}),
    }


def build_tool_executor_map() -> dict[str, ToolCallable]:
    return {
        "tool_catalog_list": _tool_catalog_list,
        "conversation_context_fetch": _conversation_context_fetch,
        "governance_task_context_fetch": _governance_task_context_fetch,
        "memory_search": _memory_search,
        "outbound_mail_summary": _outbound_mail_summary,
        "inbound_mail_summary": _inbound_mail_summary,
        "inbound_message_search": _inbound_message_search,
        "inbound_message_read": _inbound_message_read,
        "inbound_reply_draft": _inbound_reply_draft,
        "uploaded_content_analyze": _uploaded_content_analyze,
        "persona_or_chitchat": _persona_or_chitchat,
        "unsupported_capability": _unsupported_capability,
        "enterprise_search": _enterprise_search,
        "enterprise_evidence_pack": _enterprise_evidence_pack,
        "enterprise_answer": _enterprise_answer,
        "enterprise_rag_query": _enterprise_rag_query,
    }


def _sanitize_enterprise_source_types(raw_source_types: Any, question: str) -> list[str]:
    normalized = [
        normalize_source_type(str(source_type))
        for source_type in list(raw_source_types or [])
        if str(source_type).strip()
    ]
    normalized = [source_type for source_type in normalized if source_type in VALID_ENTERPRISE_SOURCE_TYPES]
    return normalized or infer_source_types(question)


def _evidence_to_dict(evidence: EvidencePack) -> dict[str, Any]:
    return {
        "query": evidence.query,
        "citations": [asdict(item) for item in evidence.citations],
        "supporting_doc_ids": list(evidence.supporting_doc_ids),
        "missing_evidence": bool(evidence.missing_evidence),
        "confidence": float(evidence.confidence),
        "supporting_facts": list(evidence.supporting_facts),
        "supporting_fact_details": [asdict(item) for item in evidence.supporting_fact_details],
        "retrieval_stage_debug": dict(evidence.retrieval_stage_debug),
        "rerank_debug": list(evidence.rerank_debug),
    }


def _dict_to_evidence_pack(query: str, payload: dict[str, Any]) -> EvidencePack:
    citations = list(payload.get("citations") or [])
    from app.enterprise_rag.core.types import EnterpriseCitation

    return EvidencePack(
        query=query,
        citations=[
            EnterpriseCitation(
                doc_id=str(item.get("doc_id") or ""),
                chunk_id=str(item.get("chunk_id") or ""),
                source_type=str(item.get("source_type") or ""),
                title=str(item.get("title") or ""),
                snippet=str(item.get("snippet") or ""),
                score=float(item.get("score") or 0.0),
                retrieval_source=str(item.get("retrieval_source") or ""),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in citations
        ],
        supporting_doc_ids=[str(item) for item in payload.get("supporting_doc_ids") or []],
        missing_evidence=bool(payload.get("missing_evidence", False)),
        confidence=float(payload.get("confidence") or 0.0),
        supporting_facts=[str(item) for item in payload.get("supporting_facts") or []],
        supporting_fact_details=[
            SupportingFact(
                doc_id=str(item.get("doc_id") or ""),
                chunk_id=str(item.get("chunk_id") or ""),
                sentence_text=str(item.get("sentence_text") or ""),
                score=float(item.get("score") or 0.0),
                source_type=str(item.get("source_type") or ""),
                title=str(item.get("title") or ""),
                speaker=str(item.get("speaker") or ""),
                timestamp=str(item.get("timestamp") or ""),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload.get("supporting_fact_details") or []
        ],
        retrieval_stage_debug=dict(payload.get("retrieval_stage_debug") or {}),
        rerank_debug=list(payload.get("rerank_debug") or []),
    )
