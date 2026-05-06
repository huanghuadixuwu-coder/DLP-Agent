from __future__ import annotations

from langchain_core.documents import Document

from app.enterprise_rag.core.types import EnterpriseCitation
from app.enterprise_rag.libs.text_cleaning import snippet


def citation_from_document(doc: Document, *, score: float = 0.0) -> EnterpriseCitation:
    metadata = doc.metadata or {}
    return EnterpriseCitation(
        doc_id=str(metadata.get("doc_id", "")),
        chunk_id=str(metadata.get("chunk_id", "")),
        source_type=str(metadata.get("source_type", "")),
        title=str(metadata.get("title", "")),
        snippet=snippet(doc.page_content),
        score=score,
    )
