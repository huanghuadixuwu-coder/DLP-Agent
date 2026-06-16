from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_environment(args: argparse.Namespace) -> None:
    if args.collection:
        os.environ["ENTERPRISE_CHROMA_COLLECTION"] = args.collection
    if args.sparse_db:
        os.environ["ENTERPRISE_SPARSE_DB_PATH"] = str(Path(args.sparse_db).resolve())


def _load_dense(collection_name: str) -> tuple[set[str], set[str], dict[str, dict[str, Any]]]:
    from app.vectorstore import get_chroma_client

    collection = get_chroma_client().get_collection(collection_name)
    rows = collection.get(include=["metadatas"])
    chunk_ids = {str(item) for item in rows.get("ids", []) if item}
    metadata = {
        str(chunk_id): dict(meta or {})
        for chunk_id, meta in zip(rows.get("ids", []), rows.get("metadatas", []))
        if chunk_id
    }
    doc_ids = {str(meta.get("doc_id") or "") for meta in metadata.values() if str(meta.get("doc_id") or "")}
    return chunk_ids, doc_ids, metadata


def _load_sparse(path: Path) -> tuple[set[str], set[str]]:
    with sqlite3.connect(path) as conn:
        chunk_ids = {str(row[0]) for row in conn.execute("SELECT chunk_id FROM enterprise_chunks").fetchall()}
        doc_ids = {str(row[0]) for row in conn.execute("SELECT DISTINCT doc_id FROM enterprise_chunks").fetchall()}
        fts_chunk_ids = {str(row[0]) for row in conn.execute("SELECT chunk_id FROM enterprise_chunks_fts").fetchall()}
    if chunk_ids != fts_chunk_ids:
        missing_in_fts = sorted(chunk_ids - fts_chunk_ids)
        stale_in_fts = sorted(fts_chunk_ids - chunk_ids)
        raise RuntimeError(f"Sparse base/FTS mismatch: missing_in_fts={missing_in_fts[:10]} stale_in_fts={stale_in_fts[:10]}")
    return chunk_ids, doc_ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate EnterpriseRAG dense/sparse shadow-index consistency.")
    parser.add_argument("--collection")
    parser.add_argument("--sparse-db")
    parser.add_argument("--slice-manifest")
    parser.add_argument("--forbidden-prefix", action="append", default=["l3_concurrency_", "l3_iso_"])
    args = parser.parse_args()
    _configure_environment(args)

    from app.enterprise_rag.core.index_contract import build_active_index_contract

    contract = build_active_index_contract()
    collection_name = str(args.collection or contract.get("dense_collection") or "")
    sparse_db = Path(args.sparse_db or str(contract.get("sparse_db_path") or "")).resolve()
    dataset_root = Path(str(contract.get("dataset_root") or ""))
    slice_manifest = Path(args.slice_manifest).resolve() if args.slice_manifest else dataset_root / "slice_manifest.json"
    if not slice_manifest.exists():
        raise RuntimeError(f"Slice manifest not found: {slice_manifest}")
    manifest = json.loads(slice_manifest.read_text(encoding="utf-8"))
    expected_doc_ids = {str(item) for item in manifest.get("expected_doc_ids") or [] if str(item)}
    dense_chunks, dense_docs, dense_metadata = _load_dense(collection_name)
    sparse_chunks, sparse_docs = _load_sparse(sparse_db)
    forbidden_docs = sorted(
        doc_id
        for doc_id in dense_docs | sparse_docs
        if any(doc_id.startswith(prefix) for prefix in args.forbidden_prefix)
    )
    missing_expected_dense = sorted(expected_doc_ids - dense_docs)
    missing_expected_sparse = sorted(expected_doc_ids - sparse_docs)
    missing_in_sparse = sorted(dense_chunks - sparse_chunks)
    missing_in_dense = sorted(sparse_chunks - dense_chunks)
    metadata_missing = sorted(
        chunk_id
        for chunk_id, metadata in dense_metadata.items()
        if not str(metadata.get("doc_id") or "") or not str(metadata.get("source_type") or "")
    )
    ok = not any(
        (
            forbidden_docs,
            missing_expected_dense,
            missing_expected_sparse,
            missing_in_sparse,
            missing_in_dense,
            metadata_missing,
        )
    )
    result = {
        "ok": ok,
        "collection": collection_name,
        "sparse_db": str(sparse_db),
        "slice_manifest": str(slice_manifest),
        "slice_id": manifest.get("slice_id"),
        "dense_chunk_count": len(dense_chunks),
        "sparse_chunk_count": len(sparse_chunks),
        "dense_doc_count": len(dense_docs),
        "sparse_doc_count": len(sparse_docs),
        "expected_doc_count": len(expected_doc_ids),
        "missing_expected_dense": missing_expected_dense,
        "missing_expected_sparse": missing_expected_sparse,
        "missing_in_sparse": missing_in_sparse[:50],
        "missing_in_dense": missing_in_dense[:50],
        "forbidden_docs": forbidden_docs,
        "metadata_missing": metadata_missing[:50],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
