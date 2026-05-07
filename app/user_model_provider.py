from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import get_settings


@dataclass(slots=True)
class UserModelSummary:
    summary: str
    preferences: list[str]
    traits: list[str]
    source: str = "null"


class UserModelProvider(Protocol):
    def get_user_profile(self, *, session_id: str, conversation_id: str = "") -> UserModelSummary | None: ...


class NullUserModelProvider:
    def get_user_profile(self, *, session_id: str, conversation_id: str = "") -> UserModelSummary | None:
        return None


class LocalUserModelProvider:
    def get_user_profile(self, *, session_id: str, conversation_id: str = "") -> UserModelSummary | None:
        from app.hermes_dynamic_memory import get_user_memory_context

        context = get_user_memory_context(session_id=session_id, conversation_id=conversation_id, limit=8)
        if not context.get("hits"):
            return None
        facts = list(context.get("facts") or [])
        active = [item for item in facts if item.get("status") == "active"]
        pending = [item for item in facts if item.get("status") == "pending"]
        preferences = [str(item.get("content") or "") for item in active if item.get("category") == "user_preference"]
        if pending:
            preferences.extend(f"Pending candidate: {item.get('content')}" for item in pending[:3])
        return UserModelSummary(
            summary=str(context.get("summary") or "Local enterprise-agent user memory is available."),
            preferences=[item for item in preferences if item][:8],
            traits=[],
            source="local_hermes_memory",
        )


class HonchoUserModelProvider:
    def get_user_profile(self, *, session_id: str, conversation_id: str = "") -> UserModelSummary | None:
        settings = get_settings()
        if not settings.honcho_base_url or not settings.honcho_api_key:
            return None
        return None


def get_user_model_provider() -> UserModelProvider:
    mode = get_settings().user_model_provider_mode.strip().lower()
    if mode == "honcho":
        return HonchoUserModelProvider()
    if mode in {"local", "hermes", "auto"}:
        return LocalUserModelProvider()
    return NullUserModelProvider()
