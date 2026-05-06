from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from app.config import get_settings
from app.conversation_memory import build_memory_context, compact_text
from app.session_transcript import load_recent_transcript_entries
from app.user_model_provider import get_user_model_provider
from app.vectorstore import get_workspace_memory_collection, get_workspace_memory_vectorstore, upsert_workspace_memory_documents


def _workspace_root() -> Path:
    root = Path(get_settings().workspace_memory_root)
    root.mkdir(parents=True, exist_ok=True)
    for child in ("bootstrap", "notes", "skills"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def _workspace_db_path() -> Path:
    path = Path(get_settings().workspace_memory_sparse_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect_workspace_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_workspace_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_workspace_memory_index() -> None:
    with _connect_workspace_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace_memory_sections (
                chunk_id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                heading TEXT NOT NULL,
                content TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                file_mtime REAL NOT NULL DEFAULT 0
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS workspace_memory_sections_fts USING fts5(
                chunk_id UNINDEXED,
                file_path,
                heading,
                content,
                tokenize='unicode61'
            );
            """
        )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iter_memory_files() -> list[Path]:
    root = _workspace_root()
    return sorted(path for path in root.rglob("*.md") if path.is_file())


def _split_markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_heading = "General"
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("#"):
            if buffer:
                sections.append((current_heading, "\n".join(buffer).strip()))
                buffer = []
            current_heading = line.lstrip("#").strip() or "General"
            continue
        buffer.append(line)
    if buffer:
        sections.append((current_heading, "\n".join(buffer).strip()))
    return [(heading, section) for heading, section in sections if section]


def sync_workspace_memory_index() -> dict[str, int]:
    init_workspace_memory_index()
    files = _iter_memory_files()
    docs: list[Document] = []
    current_chunk_ids: set[str] = set()
    with _connect_workspace_db() as conn:
        existing_rows = conn.execute("SELECT chunk_id, file_path FROM workspace_memory_sections").fetchall()
        existing_chunk_ids = {str(row["chunk_id"]) for row in existing_rows}
        for file_path in files:
            content = file_path.read_text(encoding="utf-8")
            file_hash = _hash_text(content)
            file_mtime = file_path.stat().st_mtime
            sections = _split_markdown_sections(content)
            rel_path = str(file_path.relative_to(_workspace_root())).replace("\\", "/")
            for index, (heading, section_text) in enumerate(sections):
                chunk_id = f"workspace:{rel_path}:{index}"
                current_chunk_ids.add(chunk_id)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO workspace_memory_sections (
                        chunk_id, file_path, heading, content, file_hash, file_mtime
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (chunk_id, rel_path, heading, section_text, file_hash, file_mtime),
                )
                conn.execute("DELETE FROM workspace_memory_sections_fts WHERE chunk_id = ?", (chunk_id,))
                conn.execute(
                    """
                    INSERT INTO workspace_memory_sections_fts (chunk_id, file_path, heading, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (chunk_id, rel_path, heading, section_text),
                )
                docs.append(
                    Document(
                        page_content=section_text,
                        metadata={
                            "chunk_id": chunk_id,
                            "domain": "workspace_memory",
                            "file_path": rel_path,
                            "title": heading,
                            "source_type": "workspace_memory",
                        },
                    )
                )
        stale_chunk_ids = sorted(existing_chunk_ids - current_chunk_ids)
        for chunk_id in stale_chunk_ids:
            conn.execute("DELETE FROM workspace_memory_sections WHERE chunk_id = ?", (chunk_id,))
            conn.execute("DELETE FROM workspace_memory_sections_fts WHERE chunk_id = ?", (chunk_id,))
        if stale_chunk_ids:
            get_workspace_memory_collection().delete(ids=stale_chunk_ids)
    indexed = upsert_workspace_memory_documents(docs)
    return {"files_seen": len(files), "chunks_indexed": indexed}


def _fts_query(query: str) -> str:
    terms = [term.strip() for term in query.replace("\n", " ").split() if term.strip()]
    sanitized = [term.replace('"', "").replace("'", "") for term in terms if term]
    if not sanitized:
        return ""
    return " OR ".join(f'"{term}"' for term in sanitized[:12])


def search_workspace_memory(question: str, *, top_k: int = 6) -> list[dict[str, Any]]:
    sync_workspace_memory_index()
    sparse_hits: list[dict[str, Any]] = []
    fts_query = _fts_query(question)
    if fts_query:
        with _connect_workspace_db() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.chunk_id,
                    s.file_path,
                    s.heading,
                    s.content,
                    bm25(workspace_memory_sections_fts, 1.0, 3.0, 1.2) AS bm25_score
                FROM workspace_memory_sections_fts
                JOIN workspace_memory_sections s ON s.chunk_id = workspace_memory_sections_fts.chunk_id
                WHERE workspace_memory_sections_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (fts_query, top_k),
            ).fetchall()
        sparse_hits = [dict(row) for row in rows]

    vector_hits = get_workspace_memory_vectorstore().similarity_search(question, k=top_k, filter={"domain": {"$eq": "workspace_memory"}})
    merged: dict[str, dict[str, Any]] = {}
    for rank, row in enumerate(sparse_hits, start=1):
        merged[row["chunk_id"]] = {
            "chunk_id": row["chunk_id"],
            "file_path": row["file_path"],
            "title": row["heading"],
            "snippet": compact_text(str(row["content"]), 280),
            "score": float(1.0 / max(abs(float(row["bm25_score"])) + 1.0, 1.0)),
            "source": "fts",
        }
    for rank, doc in enumerate(vector_hits, start=1):
        chunk_id = str(doc.metadata.get("chunk_id") or "")
        payload = merged.get(chunk_id) or {
            "chunk_id": chunk_id,
            "file_path": str(doc.metadata.get("file_path") or ""),
            "title": str(doc.metadata.get("title") or "General"),
            "snippet": compact_text(doc.page_content, 280),
            "score": 0.0,
            "source": "vector",
        }
        payload["score"] = max(float(payload.get("score") or 0.0), 1.0 - ((rank - 1) * 0.08))
        payload["source"] = "hybrid" if chunk_id in merged else "vector"
        merged[chunk_id] = payload
    return sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]


def build_runtime_context_bundle(*, session_id: str, conversation_id: str, question: str) -> dict[str, Any]:
    legacy_memory = build_memory_context(session_id=session_id, conversation_id=conversation_id, question=question)
    workspace_hits = search_workspace_memory(question, top_k=4)
    transcript_entries = load_recent_transcript_entries(session_id=session_id, conversation_id=conversation_id, limit=8)
    provider = get_user_model_provider()
    profile = provider.get_user_profile(session_id=session_id, conversation_id=conversation_id)

    context_sections: list[str] = []
    context_sources: list[str] = []

    if workspace_hits:
        context_sources.append("workspace_memory_md")
        workspace_block = "\n\n".join(
            f"[{index}] {item['file_path']} :: {item['title']}\n{item['snippet']}"
            for index, item in enumerate(workspace_hits, start=1)
        )
        context_sections.append(f"Workspace memory:\n{workspace_block}")

    if transcript_entries:
        context_sources.append("session_transcript_jsonl")
        transcript_block = "\n".join(
            f"{entry.get('role', 'unknown')}: {compact_text(str(entry.get('content') or ''), 240)}"
            for entry in transcript_entries[-6:]
        )
        context_sections.append(f"Recent transcript:\n{transcript_block}")

    if profile:
        context_sources.append("user_model_provider")
        profile_lines = [profile.summary]
        if profile.preferences:
            profile_lines.append("Preferences: " + "; ".join(profile.preferences[:4]))
        if profile.traits:
            profile_lines.append("Traits: " + "; ".join(profile.traits[:4]))
        context_sections.append("User model:\n" + "\n".join(profile_lines))

    if legacy_memory.get("memory_context"):
        context_sources.append("legacy_summary_memory")
        context_sections.append("Legacy memory:\n" + str(legacy_memory["memory_context"]))

    return {
        "context_text": "\n\n".join(section for section in context_sections if section).strip(),
        "context_sources": context_sources,
        "workspace_memory_hits": len(workspace_hits),
        "transcript_hits": len(transcript_entries),
        "user_model_used": bool(profile),
        "memory_retrieval_hits": len(workspace_hits) + len(transcript_entries) + int(legacy_memory.get("memory_hits", 0)),
        "workspace_memory": workspace_hits,
        "transcript_entries": transcript_entries,
        "legacy_memory": legacy_memory,
    }

