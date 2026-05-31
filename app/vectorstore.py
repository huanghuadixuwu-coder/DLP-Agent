from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from app.actor_context import DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_ID
from app.config import get_settings


_EMBEDDING_INIT_LOCK = Lock()


@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.HttpClient:
    settings = get_settings()
    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _resolve_model_name(local_dir: str, model_name: str) -> str:
    candidate = local_dir.strip()
    if candidate and Path(candidate).exists():
        return candidate
    return model_name


@lru_cache(maxsize=8)
def _get_embeddings(model_name: str, device: str) -> HuggingFaceEmbeddings:
    with _EMBEDDING_INIT_LOCK:
        return HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": device},
            encode_kwargs={"normalize_embeddings": True},
        )


def get_embeddings() -> HuggingFaceEmbeddings:
    settings = get_settings()
    model_name = _resolve_model_name(settings.embedding_local_dir, settings.embedding_model)
    return _get_embeddings(model_name, settings.embedding_device)


def get_enterprise_embeddings() -> HuggingFaceEmbeddings:
    settings = get_settings()
    model_name = _resolve_model_name(settings.enterprise_embedding_local_dir, settings.enterprise_embedding_model)
    return _get_embeddings(model_name, settings.embedding_device)


def get_named_vectorstore(collection_name: str, embedding_function: HuggingFaceEmbeddings) -> Chroma:
    return Chroma(
        client=get_chroma_client(),
        collection_name=collection_name,
        embedding_function=embedding_function,
    )


@lru_cache(maxsize=1)
def get_vectorstore() -> Chroma:
    settings = get_settings()
    return get_named_vectorstore(settings.chroma_collection, get_embeddings())


@lru_cache(maxsize=1)
def get_enterprise_vectorstore() -> Chroma:
    settings = get_settings()
    return get_named_vectorstore(settings.enterprise_chroma_collection, get_enterprise_embeddings())


@lru_cache(maxsize=1)
def get_conversation_memory_vectorstore() -> Chroma:
    settings = get_settings()
    get_conversation_memory_collection()
    return get_named_vectorstore(settings.conversation_memory_chroma_collection, get_embeddings())


@lru_cache(maxsize=1)
def get_workspace_memory_vectorstore() -> Chroma:
    settings = get_settings()
    return get_named_vectorstore(settings.workspace_memory_chroma_collection, get_enterprise_embeddings())


def get_collection(collection_name: str, *, metadata: dict[str, Any] | None = None):
    kwargs = {"name": collection_name}
    if metadata:
        kwargs["metadata"] = metadata
    return get_chroma_client().get_or_create_collection(**kwargs)


def get_enterprise_collection():
    settings = get_settings()
    return get_collection(settings.enterprise_chroma_collection)


def _conversation_memory_collection_metadata() -> dict[str, Any]:
    settings = get_settings()
    return {
        "purpose": "conversation_memory",
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.conversation_memory_embedding_dimension,
        "schema_version": settings.conversation_memory_collection_version,
    }


def get_conversation_memory_collection():
    settings = get_settings()
    return get_collection(
        settings.conversation_memory_chroma_collection,
        metadata=_conversation_memory_collection_metadata(),
    )


def get_workspace_memory_collection():
    settings = get_settings()
    return get_collection(settings.workspace_memory_chroma_collection)


def _batched(values: list[str], batch_size: int = 128) -> Iterable[list[str]]:
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


def reset_caches() -> None:
    get_chroma_client.cache_clear()
    _get_embeddings.cache_clear()
    get_vectorstore.cache_clear()
    get_enterprise_vectorstore.cache_clear()
    get_conversation_memory_vectorstore.cache_clear()
    get_workspace_memory_vectorstore.cache_clear()


def count_collection() -> int:
    settings = get_settings()
    return get_collection(settings.chroma_collection).count()


def reset_collection() -> None:
    settings = get_settings()
    reset_named_collection(settings.chroma_collection)


def reset_named_collection(collection_name: str) -> None:
    client = get_chroma_client()
    try:
        client.delete_collection(name=collection_name)
    except Exception:
        pass
    reset_caches()


def _enterprise_doc_delete_filter(
    doc_ids: list[str],
    *,
    tenant_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"doc_id": {"$in": doc_ids}}]
    if tenant_id and tenant_id != DEFAULT_TENANT_ID:
        clauses.append({"tenant_id": {"$eq": tenant_id}})
    if workspace_id and workspace_id != DEFAULT_WORKSPACE_ID:
        clauses.append({"workspace_id": {"$eq": workspace_id}})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _enterprise_single_doc_delete_filter(
    doc_id: str,
    *,
    tenant_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [{"doc_id": {"$eq": doc_id}}]
    if tenant_id and tenant_id != DEFAULT_TENANT_ID:
        clauses.append({"tenant_id": {"$eq": tenant_id}})
    if workspace_id and workspace_id != DEFAULT_WORKSPACE_ID:
        clauses.append({"workspace_id": {"$eq": workspace_id}})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def delete_enterprise_documents_by_doc_ids(
    doc_ids: Iterable[str],
    *,
    tenant_id: str = "",
    workspace_id: str = "",
) -> dict[str, int]:
    """Hard-delete existing enterprise chunks for the provided document ids."""
    normalized = sorted({str(doc_id).strip() for doc_id in doc_ids if str(doc_id or "").strip()})
    if not normalized:
        return {"documents_requested": 0, "chunks_deleted": 0}

    collection = get_enterprise_collection()
    chunk_ids: list[str] = []
    for batch in _batched(normalized):
        try:
            result = collection.get(
                where=_enterprise_doc_delete_filter(batch, tenant_id=tenant_id, workspace_id=workspace_id),
                include=[],
            )
            chunk_ids.extend(str(item) for item in result.get("ids", []) if item)
        except Exception:
            # Older Chroma builds can be picky about $in; fall back to one doc_id at a time.
            for doc_id in batch:
                result = collection.get(
                    where=_enterprise_single_doc_delete_filter(doc_id, tenant_id=tenant_id, workspace_id=workspace_id),
                    include=[],
                )
                chunk_ids.extend(str(item) for item in result.get("ids", []) if item)

    unique_chunk_ids = sorted(set(chunk_ids))
    for batch in _batched(unique_chunk_ids):
        collection.delete(ids=batch)
    return {"documents_requested": len(normalized), "chunks_deleted": len(unique_chunk_ids)}


def _upsert_to_collection(
    *,
    collection,
    embedding_function: HuggingFaceEmbeddings,
    documents: list[Document],
    batch_size: int = 32,
) -> int:
    if not documents:
        return 0
    for start in range(0, len(documents), batch_size):
        batch = documents[start : start + batch_size]
        ids = [str(doc.metadata["chunk_id"]) for doc in batch]
        texts = [doc.page_content for doc in batch]
        metadatas = [dict(doc.metadata) for doc in batch]
        embeddings = embedding_function.embed_documents(texts)
        collection.upsert(ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings)
    return len(documents)


def upsert_documents(documents: Iterable[Document]) -> int:
    docs = list(documents)
    if not docs:
        return 0
    settings = get_settings()
    return _upsert_to_collection(
        collection=get_collection(settings.chroma_collection),
        embedding_function=get_embeddings(),
        documents=docs,
    )


def upsert_enterprise_documents(documents: Iterable[Document]) -> int:
    docs = list(documents)
    if not docs:
        return 0
    return _upsert_to_collection(
        collection=get_enterprise_collection(),
        embedding_function=get_enterprise_embeddings(),
        documents=docs,
    )


def _prepare_conversation_memory_documents(documents: Iterable[Document]) -> list[Document]:
    settings = get_settings()
    prepared: list[Document] = []
    for document in documents:
        metadata = {
            **dict(document.metadata),
            "memory_collection_version": settings.conversation_memory_collection_version,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": settings.conversation_memory_embedding_dimension,
            "memory_content_hash": sha256(document.page_content.encode("utf-8")).hexdigest(),
        }
        prepared.append(Document(page_content=document.page_content, metadata=metadata))
    return prepared


def upsert_conversation_memory_documents(documents: Iterable[Document]) -> int:
    docs = _prepare_conversation_memory_documents(documents)
    if not docs:
        return 0
    return _upsert_to_collection(
        collection=get_conversation_memory_collection(),
        embedding_function=get_embeddings(),
        documents=docs,
    )


def migrate_conversation_memory_documents(source_collection_name: str = "") -> dict[str, Any]:
    """Re-embed runtime memory into the configured versioned memory collection."""
    settings = get_settings()
    source_name = source_collection_name.strip() or settings.chroma_collection
    target_name = settings.conversation_memory_chroma_collection
    if source_name == target_name:
        raise ValueError("Conversation memory migration source and target collection must differ")

    source = get_collection(source_name)
    result = source.get(
        where={"is_runtime_memory": {"$eq": "true"}},
        include=["documents", "metadatas"],
    )
    ids = [str(item) for item in result.get("ids", [])]
    texts = [str(item or "") for item in result.get("documents", [])]
    metadatas = [dict(item or {}) for item in result.get("metadatas", [])]
    target = get_conversation_memory_collection()
    existing = target.get(ids=ids, include=["metadatas"]) if ids else {"ids": [], "metadatas": []}
    existing_metadata = {
        str(chunk_id): dict(metadata or {})
        for chunk_id, metadata in zip(existing.get("ids", []), existing.get("metadatas", []))
    }
    documents: list[Document] = []
    for chunk_id, text, metadata in zip(ids, texts, metadatas):
        if not chunk_id or not text:
            continue
        content_hash = sha256(text.encode("utf-8")).hexdigest()
        current = existing_metadata.get(chunk_id, {})
        if (
            current.get("memory_collection_version") == settings.conversation_memory_collection_version
            and current.get("embedding_model") == settings.embedding_model
            and int(current.get("embedding_dimension") or 0) == settings.conversation_memory_embedding_dimension
            and current.get("memory_content_hash") == content_hash
        ):
            continue
        documents.append(
            Document(
                page_content=text,
                metadata={
                    **metadata,
                    "chunk_id": chunk_id,
                    "migration_source_collection": source_name,
                },
            )
        )
    written = upsert_conversation_memory_documents(documents)
    return {
        "source_collection": source_name,
        "target_collection": target_name,
        "source_runtime_documents": len(ids),
        "documents_reembedded": written,
        "documents_skipped": len(ids) - written,
        "target_count": target.count(),
        "target_metadata": dict(target.metadata or {}),
        "source_preserved": True,
    }


def get_conversation_memory_collection_health() -> dict[str, Any]:
    settings = get_settings()
    collection = get_conversation_memory_collection()
    return {
        "collection": settings.conversation_memory_chroma_collection,
        "count": collection.count(),
        "metadata": dict(collection.metadata or {}),
        "expected_embedding_model": settings.embedding_model,
        "expected_embedding_dimension": settings.conversation_memory_embedding_dimension,
        "expected_schema_version": settings.conversation_memory_collection_version,
    }


def upsert_workspace_memory_documents(documents: Iterable[Document]) -> int:
    docs = list(documents)
    if not docs:
        return 0
    return _upsert_to_collection(
        collection=get_workspace_memory_collection(),
        embedding_function=get_enterprise_embeddings(),
        documents=docs,
    )
