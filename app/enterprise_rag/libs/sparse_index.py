from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or get_settings().enterprise_sparse_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _fts_query(query: str) -> str:
    terms = [term.strip() for term in str(query or "").replace("\n", " ").split() if term.strip()]
    sanitized = [term.replace('"', "").replace("'", "") for term in terms if term]
    if not sanitized:
        return ""
    return " OR ".join(f'"{term}"' for term in sanitized[:16])


def init_enterprise_sparse_index(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS enterprise_chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                chunk_strategy TEXT NOT NULL DEFAULT '',
                business_domain TEXT NOT NULL DEFAULT '',
                thread_id TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL DEFAULT '',
                collection_version TEXT NOT NULL DEFAULT ''
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS enterprise_chunks_fts USING fts5(
                chunk_id UNINDEXED,
                doc_id UNINDEXED,
                source_type,
                title,
                content,
                business_domain,
                tokenize='unicode61'
            );

            CREATE INDEX IF NOT EXISTS idx_enterprise_chunks_doc
                ON enterprise_chunks(doc_id, source_type);
            """
        )


def reset_enterprise_sparse_index(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS enterprise_chunks_fts;
            DROP TABLE IF EXISTS enterprise_chunks;
            """
        )
    init_enterprise_sparse_index(db_path)


def upsert_enterprise_sparse_chunks(rows: list[dict[str, Any]], db_path: str | Path | None = None) -> int:
    if not rows:
        return 0
    init_enterprise_sparse_index(db_path)
    with _connect(db_path) as conn:
        for row in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO enterprise_chunks (
                    chunk_id, doc_id, source_type, title, content, chunk_index,
                    chunk_strategy, business_domain, thread_id, timestamp, collection_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["chunk_id"],
                    row["doc_id"],
                    row["source_type"],
                    row["title"],
                    row["content"],
                    int(row.get("chunk_index") or 0),
                    row.get("chunk_strategy", ""),
                    row.get("business_domain", ""),
                    row.get("thread_id", ""),
                    row.get("timestamp", ""),
                    row.get("collection_version", ""),
                ),
            )
            conn.execute("DELETE FROM enterprise_chunks_fts WHERE chunk_id = ?", (row["chunk_id"],))
            conn.execute(
                """
                INSERT INTO enterprise_chunks_fts (chunk_id, doc_id, source_type, title, content, business_domain)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["chunk_id"],
                    row["doc_id"],
                    row["source_type"],
                    row["title"],
                    row["content"],
                    row.get("business_domain", ""),
                ),
            )
    return len(rows)


def search_enterprise_sparse(
    query: str,
    *,
    source_types: list[str] | None = None,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    init_enterprise_sparse_index(db_path)
    source_types = [item for item in source_types or [] if item]
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    with _connect(db_path) as conn:
        params: list[Any] = [fts_query]
        where_sql = ""
        if source_types:
            placeholders = ",".join("?" for _ in source_types)
            where_sql = f"AND c.source_type IN ({placeholders})"
            params.extend(source_types)
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT
                c.chunk_id,
                c.doc_id,
                c.source_type,
                c.title,
                c.content,
                c.chunk_index,
                c.chunk_strategy,
                c.business_domain,
                c.thread_id,
                c.timestamp,
                c.collection_version,
                bm25(enterprise_chunks_fts, 1.0, 4.0, 1.0, 2.5, 0.8) AS bm25_score
            FROM enterprise_chunks_fts
            JOIN enterprise_chunks c ON c.chunk_id = enterprise_chunks_fts.chunk_id
            WHERE enterprise_chunks_fts MATCH ?
            {where_sql}
            ORDER BY bm25_score
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]
