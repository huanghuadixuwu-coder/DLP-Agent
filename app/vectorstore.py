from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Iterable

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

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
def get_workspace_memory_vectorstore() -> Chroma:
    settings = get_settings()
    return get_named_vectorstore(settings.workspace_memory_chroma_collection, get_enterprise_embeddings())


def get_collection(collection_name: str):
    return get_chroma_client().get_or_create_collection(name=collection_name)


def get_enterprise_collection():
    settings = get_settings()
    return get_collection(settings.enterprise_chroma_collection)


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


def delete_enterprise_documents_by_doc_ids(doc_ids: Iterable[str]) -> dict[str, int]:
    """Hard-delete existing enterprise chunks for the provided document ids."""
    normalized = sorted({str(doc_id).strip() for doc_id in doc_ids if str(doc_id or "").strip()})
    if not normalized:
        return {"documents_requested": 0, "chunks_deleted": 0}

    collection = get_enterprise_collection()
    chunk_ids: list[str] = []
    for batch in _batched(normalized):
        try:
            result = collection.get(where={"doc_id": {"$in": batch}}, include=[])
            chunk_ids.extend(str(item) for item in result.get("ids", []) if item)
        except Exception:
            # Older Chroma builds can be picky about $in; fall back to one doc_id at a time.
            for doc_id in batch:
                result = collection.get(where={"doc_id": {"$eq": doc_id}}, include=[])
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


def upsert_workspace_memory_documents(documents: Iterable[Document]) -> int:
    docs = list(documents)
    if not docs:
        return 0
    return _upsert_to_collection(
        collection=get_workspace_memory_collection(),
        embedding_function=get_enterprise_embeddings(),
        documents=docs,
    )
