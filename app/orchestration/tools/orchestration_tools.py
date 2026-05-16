from __future__ import annotations

from typing import Any

from app.orchestration.tool_discovery import register_tool
from app.orchestration.types import OrchestrationContext


def _legacy(name: str):
    from app.orchestration import registry

    return getattr(registry, name)


@register_tool(name="tool_catalog_list", description="List available orchestration tools and preconditions.", input_schema={}, read_only=True, returns_observation_type="tool_catalog")
def tool_catalog_list(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_tool_catalog_list")(payload, context, dependency_payloads)


@register_tool(name="conversation_context_fetch", description="Fetch short-term conversation context metadata and retrieved summary context.", input_schema={"query": "optional user query"}, read_only=True, safe_when=["The user asks about earlier turns in the current conversation."], returns_observation_type="conversation_context")
def conversation_context_fetch(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_conversation_context_fetch")(payload, context, dependency_payloads)


@register_tool(name="governance_task_context_fetch", description="Fetch latest recoverable governance task in the conversation.", input_schema={}, when_to_use=["Use when the user asks about a pending DLP task, failed send, approval state, or recovery path."], reads_from=["task_store"], read_only=True, returns_observation_type="task_state")
def governance_task_context_fetch(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_governance_task_context_fetch")(payload, context, dependency_payloads)


@register_tool(name="memory_search", description="Search workspace memory markdown files via hybrid vector + FTS retrieval.", input_schema={"query": "user query", "top_k": "int"}, read_only=True, safe_when=["The user asks for background, project context, or prior notes outside the current turn history."], returns_observation_type="workspace_memory")
def memory_search(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_memory_search")(payload, context, dependency_payloads)


@register_tool(name="outbound_mail_summary", description="Summarize outbound mail sent by this Agent in a time window.", input_schema={"since": "ISO datetime", "until": "ISO datetime"}, when_to_use=["Use when the user asks how many mails were sent, recent sent mail, or outbound delivery history."], reads_from=["task_store"], read_only=True, returns_observation_type="mailbox_status", provider_constraints=["Uses governed task records, not a provider-native Sent folder."], examples=["sent mail count", "show recent outbound mail"])
def outbound_mail_summary(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_outbound_mail_summary")(payload, context, dependency_payloads)


@register_tool(name="inbound_mail_summary", description="Summarize inbound mail in a time window.", input_schema={"since": "ISO datetime", "until": "ISO datetime"}, when_to_use=["Use when the user asks for a mailbox digest, unread count, or important inbound mail summary."], reads_from=["local_inbound_store"], read_only=True, returns_observation_type="mailbox_status", provider_constraints=["Depends on synchronized inbound mailbox records."], examples=["mail digest", "unread mail count"])
def inbound_mail_summary(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_inbound_mail_summary")(payload, context, dependency_payloads)


@register_tool(name="inbound_message_search", description="Search synchronized inbound mail by keywords.", input_schema={"query": "user query", "limit": "int"}, when_to_use=["Use when the user refers to a past inbound mail but the exact message is not yet resolved."], reads_from=["local_inbound_store"], read_only=True, returns_observation_type="mail_search", examples=["find the mail about billing"])
def inbound_message_search(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_inbound_message_search")(payload, context, dependency_payloads)


@register_tool(name="inbound_message_read", description="Read a synchronized inbound mail message.", input_schema={"message_id": "mail message id"}, when_to_use=["Use after a target inbound mail has been identified and full details are needed."], reads_from=["local_inbound_store"], read_only=True, returns_observation_type="mail_message", examples=["read that message"])
def inbound_message_read(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_inbound_message_read")(payload, context, dependency_payloads)


@register_tool(name="inbound_reply_draft", description="Draft a reply for a synchronized inbound mail message.", input_schema={"message_id": "mail message id"}, when_to_use=["Use when the user wants a reply draft to a synchronized inbound message."], reads_from=["local_inbound_store"], writes_to=["draft_answer"], read_only=True, returns_observation_type="draft", provider_constraints=["Draft only; does not send mail directly."], examples=["reply to the latest billing mail"])
def inbound_reply_draft(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_inbound_reply_draft")(payload, context, dependency_payloads)


@register_tool(name="uploaded_content_analyze", description="Analyze uploaded content as a document or a single incoming email for summarization or question answering.", input_schema={"content_kind": "document|email|unknown", "task_type": "summarize|qa|critique|rewrite|extract_action_items", "message": "user request", "uploaded_filename": "filename", "uploaded_content_type": "mime type", "uploaded_text": "parsed uploaded text", "source_parse_status": "parse status", "source_parse_error": "parse error"}, read_only=True, returns_observation_type="uploaded_content")
def uploaded_content_analyze(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_uploaded_content_analyze")(payload, context, dependency_payloads)


@register_tool(name="persona_or_chitchat", description="Handle self-introduction, capability explanation, how-to-use guidance, and lightweight daily chitchat.", input_schema={"message": "user message"}, read_only=True, returns_observation_type="persona_answer")
def persona_or_chitchat(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_persona_or_chitchat")(payload, context, dependency_payloads)


@register_tool(name="unsupported_capability", description="Return a clear unsupported-capability response with next-step guidance.", input_schema={"reason": "user-facing explanation", "capability": "unsupported capability id"}, read_only=True, returns_observation_type="unsupported_capability")
def unsupported_capability(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_unsupported_capability")(payload, context, dependency_payloads)


@register_tool(name="enterprise_search", description="Retrieve enterprise knowledge evidence from EnterpriseRAG-Bench index.", input_schema={"question": "user question", "source_types": "list[str]", "top_k": "int"}, read_only=True, safe_when=["The user asks a grounded enterprise knowledge question that needs evidence retrieval."], returns_observation_type="enterprise_evidence")
def enterprise_search(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_enterprise_search")(payload, context, dependency_payloads)


@register_tool(name="enterprise_evidence_pack", description="Normalize retrieved enterprise evidence into a citation-ready pack.", input_schema={"evidence": "evidence payload"}, read_only=True, returns_observation_type="enterprise_evidence_pack")
def enterprise_evidence_pack(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_enterprise_evidence_pack")(payload, context, dependency_payloads)


@register_tool(name="enterprise_answer", description="Compose an enterprise answer from evidence only.", input_schema={"question": "user question"}, read_only=True, returns_observation_type="enterprise_answer")
def enterprise_answer(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_enterprise_answer")(payload, context, dependency_payloads)


@register_tool(name="enterprise_rag_query", description="Run retrieval + evidence + answer composition for enterprise knowledge questions.", input_schema={"question": "user question", "source_types": "list[str]", "top_k": "int"}, read_only=True, safe_when=["The user asks about enterprise facts, meetings, customers, docs, or internal systems."], returns_observation_type="enterprise_answer")
def enterprise_rag_query(payload: dict[str, Any], context: OrchestrationContext, dependency_payloads: dict[str, Any]) -> dict[str, Any]:
    return _legacy("_enterprise_rag_query")(payload, context, dependency_payloads)
