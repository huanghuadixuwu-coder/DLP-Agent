from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import get_settings


@lru_cache(maxsize=16)
def get_llm(
    model: str | None = None,
    timeout: float | None = None,
    temperature: float = 0.2,
    max_retries: int = 1,
) -> ChatOpenAI:
    """Build the shared LLM client used by the active agent pipeline."""
    settings = get_settings()
    return ChatOpenAI(
        model=model or settings.llm_model_main,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=temperature,
        timeout=settings.llm_timeout_seconds if timeout is None else timeout,
        max_retries=max_retries,
    )
