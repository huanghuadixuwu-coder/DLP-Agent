from __future__ import annotations

from app.config import get_settings
from app.unified_corpus import UNIFIED_DOMAINS, build_unified_lab_documents
from app.vectorstore import count_collection, get_chroma_client, reset_collection, upsert_documents


def _has_unified_lab_documents() -> bool:
    settings = get_settings()
    collection = get_chroma_client().get_or_create_collection(name=settings.chroma_collection)
    for domain in UNIFIED_DOMAINS:
        result = collection.get(where={"domain": {"$eq": domain}}, limit=1)
        if not result.get("ids"):
            return False
    return True


def ingest_if_needed(force: bool = False) -> int:
    if force:
        reset_collection()
    current_count = count_collection()
    if current_count > 0 and not force and _has_unified_lab_documents():
        return 0
    documents = build_unified_lab_documents()
    return upsert_documents(documents)
