from __future__ import annotations

import hashlib
from typing import Any

from langchain_core.documents import Document

from app.actor_context import actor_from_mapping
from app.config import get_settings
from app.enterprise_rag.core.types import ENTERPRISE_DOMAIN
from app.enterprise_rag.ingestion.chunker import chunk_document
from app.enterprise_rag.ingestion.hf_loader import iter_dataset_rows, iter_parquet_filtered
from app.enterprise_rag.ingestion.manifest_store import append_manifest_run
from app.enterprise_rag.ingestion.normalizer import normalize_document
from app.enterprise_rag.ingestion.question_loader import load_questions
from app.enterprise_rag.libs.sparse_index import (
    delete_enterprise_sparse_documents_by_doc_ids,
    reset_enterprise_sparse_index,
    upsert_enterprise_sparse_chunks,
)
from app.vectorstore import (
    delete_enterprise_documents_by_doc_ids,
    get_enterprise_collection,
    reset_named_collection,
    upsert_enterprise_documents,
)


def _to_langchain_documents(chunks: list) -> list[Document]:
    return [
        Document(
            page_content=chunk.content,
            metadata=dict(chunk.metadata),
        )
        for chunk in chunks
    ]


def _document_content_hash(document: Any) -> str:
    digest = hashlib.sha256()
    for value in (
        getattr(document, "doc_id", ""),
        getattr(document, "source_type", ""),
        getattr(document, "title", ""),
        getattr(document, "content", ""),
    ):
        digest.update(str(value or "").encode("utf-8", errors="ignore"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _batch_content_hash(documents: list[Any]) -> str:
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: str(getattr(item, "doc_id", "") or "")):
        digest.update(str(getattr(document, "doc_id", "") or "").encode("utf-8", errors="ignore"))
        digest.update(b":")
        digest.update(_document_content_hash(document).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest() if documents else ""


def upsert_enterprise_documents_hybrid(
    documents: list,
    *,
    replace_existing: bool = True,
    return_details: bool = False,
    actor_context: dict[str, Any] | None = None,
) -> int | dict[str, Any]:
    documents = list(documents or [])
    actor = actor_from_mapping(actor_context or {})
    doc_ids = sorted({str(getattr(document, "doc_id", "") or "").strip() for document in documents if str(getattr(document, "doc_id", "") or "").strip()})
    dense_delete = {"documents_requested": 0, "chunks_deleted": 0}
    sparse_delete = {"documents_requested": 0, "chunks_deleted": 0}
    if replace_existing and doc_ids:
        dense_delete = delete_enterprise_documents_by_doc_ids(doc_ids)
        sparse_delete = delete_enterprise_sparse_documents_by_doc_ids(doc_ids)

    chunks = []
    for document in documents:
        chunks.extend(chunk_document(document))
    for chunk in chunks:
        chunk.metadata.setdefault("tenant_id", actor.tenant_id)
        chunk.metadata.setdefault("workspace_id", actor.workspace_id)
        chunk.metadata.setdefault("user_id", actor.user_id)
    docs = _to_langchain_documents(chunks)
    upsert_enterprise_documents(docs)
    upsert_enterprise_sparse_chunks(
        [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "source_type": chunk.source_type,
                "title": chunk.title,
                "content": chunk.content,
                "chunk_index": chunk.chunk_index,
                "chunk_strategy": str(chunk.metadata.get("chunk_strategy") or ""),
                "business_domain": str(chunk.metadata.get("business_domain") or ""),
                "thread_id": str(chunk.metadata.get("thread_id") or ""),
                "timestamp": str(chunk.metadata.get("timestamp") or ""),
                "collection_version": str(chunk.metadata.get("collection_version") or ""),
                "tenant_id": str(chunk.metadata.get("tenant_id") or ""),
                "workspace_id": str(chunk.metadata.get("workspace_id") or ""),
            }
            for chunk in chunks
        ]
    )
    details = {
        "chunks_indexed": len(docs),
        "documents_seen": len(documents),
        "replace_existing": replace_existing,
        "doc_ids_replaced": doc_ids if replace_existing else [],
        "documents_replaced": len(doc_ids) if replace_existing else 0,
        "dense_chunks_deleted": int(dense_delete.get("chunks_deleted", 0)),
        "sparse_chunks_deleted": int(sparse_delete.get("chunks_deleted", 0)),
        "content_hash": _batch_content_hash(documents),
        "tenant_id": actor.tenant_id,
        "workspace_id": actor.workspace_id,
        "content_hash_sample": {
            str(getattr(document, "doc_id", "") or ""): _document_content_hash(document)
            for document in documents[:20]
            if str(getattr(document, "doc_id", "") or "").strip()
        },
    }
    return details if return_details else len(docs)


def count_enterprise_chunks() -> int:
    collection = get_enterprise_collection()
    result = collection.get(where={"domain": {"$eq": ENTERPRISE_DOMAIN}}, limit=1, include=[])
    if not result.get("ids"):
        return 0
    return len(collection.get(where={"domain": {"$eq": ENTERPRISE_DOMAIN}}, include=[]).get("ids", []))


def ingest_enterprise_rag_bench(
    *,
    mode: str = "sample",
    documents_path: str | None = None,
    questions_path: str | None = None,
    limit: int = 200,
    reset: bool = False,
    actor_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in {"sample", "full"}:
        raise ValueError("mode must be 'sample' or 'full'")
    if not documents_path:
        raise ValueError("documents_path is required for local EnterpriseRAG-Bench ingest in the current setup.")

    if reset:
        settings = get_settings()
        reset_named_collection(settings.enterprise_chroma_collection)
        reset_enterprise_sparse_index()

    indexed_questions = 0
    expected_doc_ids: set[str] = set()
    sample_source_types: set[str] = set()
    if questions_path:
        questions = load_questions(local_path=questions_path, limit=limit)
        indexed_questions = len(questions)
        for question in questions:
            expected_doc_ids.update(question.expected_doc_ids)
            sample_source_types.update(question.source_types)

    documents: list = []
    if mode == "full":
        rows = iter_dataset_rows(split="documents", local_path=documents_path, limit=None)
        documents = [normalize_document(row) for row in rows]
        documents = [document for document in documents if document.doc_id and document.content]
    else:
        expected_rows = iter_parquet_filtered(documents_path, doc_ids=expected_doc_ids or None)
        documents.extend(
            document
            for document in (normalize_document(row) for row in expected_rows)
            if document.doc_id and document.content
        )

        distractor_limit = max(10, min(limit * 2, 80))
        if sample_source_types:
            source_rows = iter_parquet_filtered(
                documents_path,
                source_types=sample_source_types,
                limit=distractor_limit + len(expected_doc_ids),
            )
            seen_doc_ids = {document.doc_id for document in documents}
            for row in source_rows:
                document = normalize_document(row)
                if not document.doc_id or not document.content or document.doc_id in seen_doc_ids:
                    continue
                documents.append(document)
                seen_doc_ids.add(document.doc_id)
                if len(seen_doc_ids) >= len(expected_doc_ids) + distractor_limit:
                    break

    index_details = upsert_enterprise_documents_hybrid(
        documents,
        replace_existing=True,
        return_details=True,
        actor_context=actor_context,
    )
    indexed_chunks = int(index_details["chunks_indexed"])

    run = {
        "dataset": "onyx-dot-app/EnterpriseRAG-Bench",
        "mode": mode,
        "documents_path": documents_path or "huggingface:documents",
        "questions_path": questions_path or "",
        "documents_seen": len(documents),
        "questions_seen": indexed_questions,
        "chunks_indexed": indexed_chunks,
        "doc_ids_replaced": index_details.get("doc_ids_replaced", []),
        "documents_replaced": index_details.get("documents_replaced", 0),
        "replace_existing": index_details.get("replace_existing", True),
        "dense_chunks_deleted": index_details.get("dense_chunks_deleted", 0),
        "sparse_chunks_deleted": index_details.get("sparse_chunks_deleted", 0),
        "content_hash": index_details.get("content_hash", ""),
        "content_hash_sample": index_details.get("content_hash_sample", {}),
        "expected_doc_ids_seeded": len(expected_doc_ids),
        "sample_source_types": sorted(sample_source_types),
        "enterprise_collection": get_settings().enterprise_chroma_collection,
        "sparse_index": get_settings().enterprise_sparse_db_path,
        "reset": reset,
        "actor_context": dict(actor_context or {}),
        "tenant_id": str((actor_context or {}).get("tenant_id") or ""),
        "workspace_id": str((actor_context or {}).get("workspace_id") or ""),
    }
    append_manifest_run(run)
    return run
