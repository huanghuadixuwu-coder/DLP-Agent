from __future__ import annotations

from typing import Any

from langchain_core.documents import Document

from app.config import get_settings
from app.enterprise_rag.core.types import ENTERPRISE_DOMAIN
from app.enterprise_rag.ingestion.chunker import chunk_document
from app.enterprise_rag.ingestion.hf_loader import iter_dataset_rows, iter_parquet_filtered
from app.enterprise_rag.ingestion.manifest_store import append_manifest_run
from app.enterprise_rag.ingestion.normalizer import normalize_document
from app.enterprise_rag.ingestion.question_loader import load_questions
from app.enterprise_rag.libs.sparse_index import reset_enterprise_sparse_index, upsert_enterprise_sparse_chunks
from app.vectorstore import get_enterprise_collection, reset_named_collection, upsert_enterprise_documents


def _to_langchain_documents(chunks: list) -> list[Document]:
    return [
        Document(
            page_content=chunk.content,
            metadata=dict(chunk.metadata),
        )
        for chunk in chunks
    ]


def upsert_enterprise_documents_hybrid(documents: list) -> int:
    chunks = []
    for document in documents:
        chunks.extend(chunk_document(document))
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
            }
            for chunk in chunks
        ]
    )
    return len(docs)


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

    indexed_chunks = upsert_enterprise_documents_hybrid(documents)

    run = {
        "dataset": "onyx-dot-app/EnterpriseRAG-Bench",
        "mode": mode,
        "documents_path": documents_path or "huggingface:documents",
        "questions_path": questions_path or "",
        "documents_seen": len(documents),
        "questions_seen": indexed_questions,
        "chunks_indexed": indexed_chunks,
        "expected_doc_ids_seeded": len(expected_doc_ids),
        "sample_source_types": sorted(sample_source_types),
        "enterprise_collection": get_settings().enterprise_chroma_collection,
        "sparse_index": get_settings().enterprise_sparse_db_path,
        "reset": reset,
    }
    append_manifest_run(run)
    return run
