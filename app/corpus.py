from __future__ import annotations

import json
from pathlib import Path

from langchain_core.documents import Document

from app.config import DATA_DIR
from app.models import ProblemRecord, ProblemSummary


def _problem_dir() -> Path:
    return DATA_DIR / "problems"


def _background_dir() -> Path:
    return DATA_DIR / "algorithm_background"


def load_problem_records() -> list[ProblemRecord]:
    records: list[ProblemRecord] = []
    for path in sorted(_problem_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        records.append(ProblemRecord(**data))
    return records


def list_problem_summaries() -> list[ProblemSummary]:
    return [
        ProblemSummary(
            problem_id=record.problem_id,
            title=record.title,
            difficulty=record.difficulty,
            topic=record.topic,
            tags=record.tags,
        )
        for record in load_problem_records()
    ]


def problem_lookup() -> dict[str, ProblemRecord]:
    return {record.problem_id: record for record in load_problem_records()}


def _code_chunks(problem: ProblemRecord) -> list[tuple[str, str]]:
    code = problem.solution_code.strip().splitlines()
    chunks: list[tuple[str, str]] = []
    if not code:
        return chunks

    current: list[str] = []
    chunk_index = 0
    for line in code:
        stripped = line.strip()
        if stripped.startswith(("def ", "class ")) and current:
            chunks.append((f"{problem.problem_id}-code-{chunk_index}", "\n".join(current).strip()))
            chunk_index += 1
            current = []
        current.append(line)
        if stripped.startswith(("for ", "while ", "if ")) and len(current) >= 4:
            chunks.append((f"{problem.problem_id}-code-{chunk_index}", "\n".join(current).strip()))
            chunk_index += 1
            current = []

    if current:
        chunks.append((f"{problem.problem_id}-code-{chunk_index}", "\n".join(current).strip()))
    return chunks


def build_documents() -> list[Document]:
    documents: list[Document] = []

    for record in load_problem_records():
        base_meta = {
            "problem_id": record.problem_id,
            "problem_title": record.title,
            "topic": record.topic,
            "difficulty": record.difficulty,
            "language": "python",
            "tags": ",".join(record.tags),
        }

        sections = {
            "background": record.statement,
            "examples": "\n".join(record.examples),
            "constraints": "\n".join(record.constraints),
        }
        for section_name, section_text in sections.items():
            documents.append(
                Document(
                    page_content=section_text,
                    metadata={
                        **base_meta,
                        "source_type": "problem_statement",
                        "chunk_level": "problem",
                        "chunk_id": f"{record.problem_id}-statement-{section_name}",
                        "section": section_name,
                    },
                )
            )

        note_map = {
            "idea": record.editor_note.get("idea", ""),
            "data_structure_reason": record.editor_note.get("data_structure_reason", ""),
            "time_complexity": record.editor_note.get("time_complexity", ""),
            "space_complexity": record.editor_note.get("space_complexity", ""),
            "edge_cases": record.editor_note.get("edge_cases", ""),
            "pitfalls": record.editor_note.get("pitfalls", ""),
        }
        for key, value in note_map.items():
            documents.append(
                Document(
                    page_content=value,
                    metadata={
                        **base_meta,
                        "source_type": "editor_note",
                        "chunk_level": "explanation",
                        "chunk_id": f"{record.problem_id}-note-{key}",
                        "section": key,
                    },
                )
            )

        for chunk_id, code_text in _code_chunks(record):
            documents.append(
                Document(
                    page_content=code_text,
                    metadata={
                        **base_meta,
                        "source_type": "code_solution",
                        "chunk_level": "code_block",
                        "chunk_id": chunk_id,
                    },
                )
            )

    for path in sorted(_background_dir().glob("*.md")):
        title = path.stem
        content = path.read_text(encoding="utf-8").strip()
        parts = [part.strip() for part in content.split("\n\n") if part.strip()]
        for index, part in enumerate(parts):
            documents.append(
                Document(
                    page_content=part,
                    metadata={
                        "problem_id": "shared",
                        "problem_title": title,
                        "topic": title,
                        "difficulty": "shared",
                        "language": "text",
                        "tags": title,
                        "source_type": "algo_background",
                        "chunk_level": "explanation",
                        "chunk_id": f"{title}-background-{index}",
                    },
                )
            )

    return documents

