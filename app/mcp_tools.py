from __future__ import annotations

from app.agent_cli import parse_time_expression
from app.disambiguation_lab import answer_apple_query
from app.email_sender import send_email_163, send_email_smtp
from app.labs_long_doc import allocate_context
from app.privacy_lab import scan_sensitive_message
from app.reminder_store import create_reminder, list_reminders


def tool_create_reminder(text: str) -> dict:
    task_text, remind_at = parse_time_expression(text)
    return create_reminder(task_text, remind_at)


def tool_list_reminders() -> dict:
    return {"reminders": list_reminders()}


def tool_privacy_scan(message: str, context_budget: int = 600) -> dict:
    return scan_sensitive_message(message, context_budget)


def tool_budget_context(question: str, context_budget: int = 10_000) -> dict:
    return allocate_context(question, context_budget)


def tool_disambiguate_entity(query: str) -> dict:
    return answer_apple_query(query)


def tool_send_email_163(to_email: str, subject: str, body: str) -> dict:
    return send_email_163(to_email=to_email, subject=subject, body=body)


def tool_send_email_smtp(to_email: str, subject: str, body: str) -> dict:
    return send_email_smtp(to_email=to_email, subject=subject, body=body)


TOOLS = {
    "create_reminder": tool_create_reminder,
    "list_reminders": tool_list_reminders,
    "privacy_scan": tool_privacy_scan,
    "budget_context": tool_budget_context,
    "disambiguate_entity": tool_disambiguate_entity,
    "send_email_smtp": tool_send_email_smtp,
    "send_email_163": tool_send_email_163,
}
