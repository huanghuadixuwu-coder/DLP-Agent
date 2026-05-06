from __future__ import annotations

from app.enterprise_rag.core.types import EnterpriseQuestion
from app.enterprise_rag.ingestion.hf_loader import iter_dataset_rows
from app.enterprise_rag.ingestion.normalizer import normalize_question


def load_questions(*, local_path: str | None = None, limit: int = 50) -> list[EnterpriseQuestion]:
    rows = iter_dataset_rows(split="questions", local_path=local_path, limit=limit)
    return [normalize_question(row) for row in rows]
