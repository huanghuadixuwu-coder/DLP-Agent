from __future__ import annotations

from typing import Any

from app.actor_context import actor_from_mapping


MAIL_READ_AUTHORIZED_KEY = "_mail_read_authorized"
INBOUND_MAIL_TOOL_NAMES = frozenset(
    {
        "inbound_mail_summary",
        "inbound_message_search",
        "inbound_message_read",
        "inbound_reply_draft",
    }
)


def mark_mail_read_authorized(actor_context: dict[str, Any] | None) -> dict[str, Any]:
    marked = dict(actor_context or {})
    marked[MAIL_READ_AUTHORIZED_KEY] = True
    return marked


def is_mail_read_authorized(actor_context: dict[str, Any] | None) -> bool:
    actor = actor_from_mapping(actor_context or {})
    return actor.is_local_dev or bool(dict(actor_context or {}).get(MAIL_READ_AUTHORIZED_KEY))


def is_inbound_mail_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() in INBOUND_MAIL_TOOL_NAMES
