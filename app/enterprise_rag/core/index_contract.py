from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, get_settings
from app.enterprise_rag.ingestion.manifest_store import load_manifest
from app.vectorstore import get_chroma_client


DEFAULT_SPARSE_DB_PATH = DATA_DIR / "enterprise_sparse.db"
FORBIDDEN_DOC_PREFIXES = ("l3_concurrency_", "l3_iso_")


def _latest_manifest_run() -> dict[str, Any]:
    runs = list(load_manifest().get("runs") or [])
    return dict(runs[-1] or {}) if runs else {}


def _read_sparse_stats(path: Path, *, include_ids: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "chunk_count": 0,
        "fts_chunk_count": 0,
        "doc_count": 0,
    }
    if not path.exists():
        return result
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            result["chunk_count"] = int(conn.execute("SELECT COUNT(*) FROM enterprise_chunks").fetchone()[0])
            result["fts_chunk_count"] = int(conn.execute("SELECT COUNT(*) FROM enterprise_chunks_fts").fetchone()[0])
            result["doc_count"] = int(conn.execute("SELECT COUNT(DISTINCT doc_id) FROM enterprise_chunks").fetchone()[0])
            if include_ids:
                result["chunk_ids"] = sorted(str(row[0]) for row in conn.execute("SELECT chunk_id FROM enterprise_chunks"))
                result["fts_chunk_ids"] = sorted(str(row[0]) for row in conn.execute("SELECT chunk_id FROM enterprise_chunks_fts"))
                result["doc_ids"] = sorted(str(row[0]) for row in conn.execute("SELECT DISTINCT doc_id FROM enterprise_chunks"))
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _read_dense_stats(collection_name: str, *, include_ids: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"collection": collection_name, "chunk_count": 0, "doc_count": None, "doc_count_computed": False}
    try:
        collection = get_chroma_client().get_collection(collection_name)
        result["chunk_count"] = int(collection.count())
        if include_ids:
            rows = collection.get(include=["metadatas"])
            result["chunk_ids"] = sorted(str(item) for item in rows.get("ids", []) if item)
            result["doc_ids"] = sorted(
                {
                    str(metadata.get("doc_id") or "")
                    for metadata in rows.get("metadatas", [])
                    if str((metadata or {}).get("doc_id") or "")
                }
            )
            result["doc_count"] = len(result["doc_ids"])
            result["doc_count_computed"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def build_active_index_contract(*, include_details: bool = False) -> dict[str, Any]:
    settings = get_settings()
    latest_run = _latest_manifest_run()
    active_sparse_path = Path(settings.enterprise_sparse_db_path).resolve()
    default_sparse_path = DEFAULT_SPARSE_DB_PATH.resolve()
    documents_path = str(latest_run.get("documents_path") or "")
    inferred_dataset_root = str(Path(documents_path).parent) if documents_path else ""
    dataset_root = str(settings.enterprise_canonical_dataset_root or inferred_dataset_root)
    dense = _read_dense_stats(settings.enterprise_chroma_collection, include_ids=include_details)
    sparse = _read_sparse_stats(active_sparse_path, include_ids=include_details)
    default_is_active = active_sparse_path == default_sparse_path
    stale_default = _read_sparse_stats(default_sparse_path, include_ids=include_details) if not default_is_active else {}
    manifest_chunks = int(latest_run.get("chunks_indexed") or 0)
    issues: list[str] = []
    if dense.get("error"):
        issues.append("dense_unavailable")
    if sparse.get("error") or not sparse.get("exists"):
        issues.append("active_sparse_unavailable")
    if int(sparse.get("chunk_count") or 0) != int(sparse.get("fts_chunk_count") or 0):
        issues.append("sparse_fts_count_mismatch")
    if int(dense.get("chunk_count") or 0) != int(sparse.get("chunk_count") or 0):
        issues.append("dense_sparse_count_mismatch")
    if manifest_chunks and manifest_chunks != int(sparse.get("chunk_count") or 0):
        issues.append("manifest_sparse_count_mismatch")
    if latest_run and str(latest_run.get("enterprise_collection") or "") != settings.enterprise_chroma_collection:
        issues.append("manifest_collection_mismatch")
    if latest_run and str(Path(str(latest_run.get("sparse_index") or "")).resolve()) != str(active_sparse_path):
        issues.append("manifest_sparse_path_mismatch")
    if include_details:
        dense_chunks = set(dense.get("chunk_ids") or [])
        sparse_chunks = set(sparse.get("chunk_ids") or [])
        fts_chunks = set(sparse.get("fts_chunk_ids") or [])
        dense_docs = set(dense.get("doc_ids") or [])
        sparse_docs = set(sparse.get("doc_ids") or [])
        if dense_chunks != sparse_chunks:
            issues.append("dense_sparse_chunk_ids_mismatch")
        if sparse_chunks != fts_chunks:
            issues.append("sparse_fts_chunk_ids_mismatch")
        if dense_docs != sparse_docs:
            issues.append("dense_sparse_doc_ids_mismatch")
    issues = sorted(set(issues))
    compact_latest = {
        key: latest_run.get(key)
        for key in (
            "created_at",
            "dataset",
            "mode",
            "documents_path",
            "questions_path",
            "documents_seen",
            "questions_seen",
            "chunks_indexed",
            "content_hash",
            "enterprise_collection",
            "sparse_index",
            "reset",
        )
        if key in latest_run
    }
    non_authoritative_indexes = []
    if stale_default:
        non_authoritative_indexes.append(
            {
                "kind": "sparse_db",
                "path": str(default_sparse_path),
                "reason": "default_path_is_not_the_configured_runtime_index",
                "stats": stale_default,
            }
        )
    return {
        "authority": "configured_runtime_settings",
        "canonical_baseline": Path(dataset_root).name if dataset_root else "",
        "dataset_root": dataset_root,
        "dense_collection": settings.enterprise_chroma_collection,
        "sparse_db_path": str(active_sparse_path),
        "collection_version": settings.enterprise_collection_version,
        "default_sparse_db_path": str(default_sparse_path),
        "default_sparse_db_authoritative": default_is_active,
        "latest_manifest_run": compact_latest,
        "dense": dense,
        "sparse": sparse,
        "parity": {
            "ok": not issues,
            "issues": issues,
            "manifest_chunks_indexed": manifest_chunks,
        },
        "non_authoritative_indexes": non_authoritative_indexes,
    }


def audit_active_index_parity() -> dict[str, Any]:
    contract = build_active_index_contract(include_details=True)
    dense = dict(contract.get("dense") or {})
    sparse = dict(contract.get("sparse") or {})
    dense_chunks = set(dense.pop("chunk_ids", []) or [])
    sparse_chunks = set(sparse.pop("chunk_ids", []) or [])
    fts_chunks = set(sparse.pop("fts_chunk_ids", []) or [])
    dense_docs = set(dense.pop("doc_ids", []) or [])
    sparse_docs = set(sparse.pop("doc_ids", []) or [])
    forbidden_docs = sorted(
        doc_id
        for doc_id in dense_docs | sparse_docs
        if any(doc_id.startswith(prefix) for prefix in FORBIDDEN_DOC_PREFIXES)
    )
    issues = list((contract.get("parity") or {}).get("issues") or [])
    if forbidden_docs:
        issues.append("forbidden_test_documents_present")
    return {
        **contract,
        "dense": dense,
        "sparse": sparse,
        "parity": {
            **dict(contract.get("parity") or {}),
            "ok": not issues,
            "issues": sorted(set(issues)),
            "missing_in_sparse": sorted(dense_chunks - sparse_chunks)[:50],
            "missing_in_dense": sorted(sparse_chunks - dense_chunks)[:50],
            "missing_in_fts": sorted(sparse_chunks - fts_chunks)[:50],
            "stale_in_fts": sorted(fts_chunks - sparse_chunks)[:50],
            "forbidden_docs": forbidden_docs[:50],
            "dense_doc_count": len(dense_docs),
            "sparse_doc_count": len(sparse_docs),
        },
    }
