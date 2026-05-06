from __future__ import annotations

from dataclasses import asdict

from app.enterprise_rag.ingestion.question_loader import load_questions


def build_casebook(*, questions_path: str | None = None, limit: int = 50) -> list[dict]:
    return [asdict(question) for question in load_questions(local_path=questions_path, limit=limit)]
