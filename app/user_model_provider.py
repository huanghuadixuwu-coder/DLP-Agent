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
    return NullUserModelProvider()

