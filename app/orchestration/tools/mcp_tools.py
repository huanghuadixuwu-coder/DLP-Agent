from __future__ import annotations

from typing import Any

from app.agent_cli import parse_time_expression
from app.disambiguation_lab import answer_apple_query
from app.email_sender import send_email_163 as _send_email_163
from app.email_sender import send_email_smtp as _send_email_smtp
from app.labs_long_doc import allocate_context
from app.orchestration.tool_discovery import register_tool
from app.orchestration.types import OrchestrationContext
from app.privacy_lab import scan_sensitive_message
from app.reminder_store import create_reminder, list_reminders


@register_tool(name="create_reminder", description="Create a local reminder from natural language.", input_schema={"text": "natural language reminder text"}, read_only=False, mutating=True, side_effectful=True, requires_confirmation=True, writes_to=["reminder_store"], returns_observation_type="reminder", expose_mcp=True)
def create_reminder_tool(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    task_text, remind_at = parse_time_expression(str(payload.get("text") or ""))
    return create_reminder(task_text, remind_at)


@register_tool(name="list_reminders", description="List local reminders.", input_schema={}, read_only=True, reads_from=["reminder_store"], returns_observation_type="reminder_list", expose_mcp=True)
def list_reminders_tool(_: dict[str, Any], __: OrchestrationContext, ___: dict[str, Any]) -> dict[str, Any]:
    return {"reminders": list_reminders()}


@register_tool(name="privacy_scan", description="Redact PII and classify sensitive-message risk.", input_schema={"message": "text to scan", "context_budget": "int"}, read_only=True, returns_observation_type="privacy_scan", expose_mcp=True)
def privacy_scan_tool(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    return scan_sensitive_message(str(payload.get("message") or ""), int(payload.get("context_budget") or 600))


@register_tool(name="budget_context", description="Allocate a context budget for long-document question answering.", input_schema={"question": "user question", "context_budget": "int"}, read_only=True, returns_observation_type="context_budget", expose_mcp=True)
def budget_context_tool(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    return allocate_context(str(payload.get("question") or ""), int(payload.get("context_budget") or 10_000))


@register_tool(name="disambiguate_entity", description="Resolve ambiguous entity references such as Apple company vs fruit.", input_schema={"query": "ambiguous user query"}, read_only=True, returns_observation_type="entity_disambiguation", expose_mcp=True)
def disambiguate_entity_tool(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    return answer_apple_query(str(payload.get("query") or ""))


@register_tool(name="send_email_163", description="Send a text email through the configured 163 SMTP provider.", input_schema={"to_email": "recipient email", "subject": "email subject", "body": "email body", "attachments": "list[dict]"}, read_only=False, mutating=True, side_effectful=True, requires_confirmation=True, writes_to=["smtp_provider"], returns_observation_type="email_send", provider_constraints=["Requires configured SMTP credentials and explicit governance approval."], expose_mcp=True)
def send_email_163_tool(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    return _send_email_163(
        to_email=str(payload.get("to_email") or ""),
        subject=str(payload.get("subject") or ""),
        body=str(payload.get("body") or ""),
        attachments=list(payload.get("attachments") or []),
    )


@register_tool(name="send_email_smtp", description="Send a text email through the configured SMTP provider.", input_schema={"to_email": "recipient email", "subject": "email subject", "body": "email body", "attachments": "list[dict]"}, read_only=False, mutating=True, side_effectful=True, requires_confirmation=True, writes_to=["smtp_provider"], returns_observation_type="email_send", provider_constraints=["Requires configured SMTP credentials and explicit governance approval."], expose_mcp=True)
def send_email_smtp_tool(payload: dict[str, Any], _: OrchestrationContext, __: dict[str, Any]) -> dict[str, Any]:
    return _send_email_smtp(
        to_email=str(payload.get("to_email") or ""),
        subject=str(payload.get("subject") or ""),
        body=str(payload.get("body") or ""),
        attachments=list(payload.get("attachments") or []),
    )
