from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

from sentence_transformers import CrossEncoder

from app.config import get_settings


@lru_cache(maxsize=1)
def get_enterprise_reranker() -> CrossEncoder:
    settings = get_settings()
    local_dir = settings.enterprise_reranker_local_dir.strip()
    model_name = local_dir if local_dir and Path(local_dir).exists() else settings.enterprise_reranker_model
    return CrossEncoder(model_name, device=settings.embedding_device, trust_remote_code=True)


def rerank_pairs(query: str, passages: Iterable[str]) -> list[float]:
    pairs = [[query, passage] for passage in passages]
    if not pairs:
        return []
    model = get_enterprise_reranker()
    scores = model.predict(pairs)
    return [float(score) for score in scores]
