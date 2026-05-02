from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from app.config import get_settings


@lru_cache(maxsize=1)
def get_chroma_client() -> chromadb.HttpClient:
    settings = get_settings()
    return chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    settings = get_settings()
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_local_dir or settings.embedding_model,
        model_kwargs={"device": settings.embedding_device},
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache(maxsize=1)
def get_vectorstore() -> Chroma:
    settings = get_settings()
    return Chroma(
        client=get_chroma_client(),
        collection_name=settings.chroma_collection,
        embedding_function=get_embeddings(),
    )


def reset_caches() -> None:
    get_chroma_client.cache_clear()
    get_embeddings.cache_clear()
    get_vectorstore.cache_clear()


def count_collection() -> int:
    settings = get_settings()
    collection = get_chroma_client().get_or_create_collection(name=settings.chroma_collection)
    return collection.count()


def reset_collection() -> None:
    settings = get_settings()
    client = get_chroma_client()
    try:
        client.delete_collection(name=settings.chroma_collection)
    except Exception:
        pass
    reset_caches()


def upsert_documents(documents: Iterable[Document]) -> int:
    docs = list(documents)
    if not docs:
        return 0

    vectorstore = get_vectorstore()
    batch_size = 32
    for start in range(0, len(docs), batch_size):
        batch = docs[start : start + batch_size]
        ids = [str(doc.metadata["chunk_id"]) for doc in batch]
        vectorstore.add_documents(documents=batch, ids=ids)
    return len(docs)
