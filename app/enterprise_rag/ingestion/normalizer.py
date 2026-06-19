from __future__ import annotations

import json
from typing import Any

from app.enterprise_rag.core.types import EnterpriseDocument, EnterpriseQuestion
from app.enterprise_rag.libs.metadata import normalize_list, normalize_source_type
from app.enterprise_rag.libs.source_parsers import infer_business_domain, infer_thread_id
from app.enterprise_rag.libs.text_cleaning import clean_text


def normalize_document(row: dict[str, Any]) -> EnterpriseDocument:
    doc_id = str(row.get("doc_id") or row.get("id") or "").strip()
    source_type = normalize_source_type(str(row.get("source_type") or row.get("source") or "unknown"))
    title = clean_text(str(row.get("title") or row.get("subject") or "(untitled)"), limit=500)
    content = clean_text(str(row.get("content") or row.get("text") or row.get("content_preview") or ""), preserve_structure=True)
    metadata = {
        "thread_id": str(row.get("thread_id") or infer_thread_id(content, title)),
        "parent_doc_id": str(row.get("parent_doc_id") or ""),
        "timestamp": str(row.get("timestamp") or row.get("created_at") or row.get("updated_at") or ""),
        "business_domain": str(row.get("business_domain") or infer_business_domain(source_type, title, content)),
    }
    return EnterpriseDocument(
        doc_id=doc_id,
        source_type=source_type,
        title=title,
        content=content,
        metadata=metadata,
    )


def normalize_question(row: dict[str, Any]) -> EnterpriseQuestion:
    answer_facts = row.get("answer_facts")
    if hasattr(answer_facts, "tolist") and not isinstance(answer_facts, str):
        try:
            answer_facts = answer_facts.tolist()
        except Exception:
            pass
    if isinstance(answer_facts, str):
        try:
            answer_facts = json.loads(answer_facts)
        except Exception:
            answer_facts = normalize_list(answer_facts)
    else:
        answer_facts = normalize_list(answer_facts)
    return EnterpriseQuestion(
        question_id=str(row.get("question_id") or row.get("id") or "").strip(),
        question_type=str(row.get("question_type") or "").strip(),
        source_types=[normalize_source_type(item) for item in normalize_list(row.get("source_types"))],
        question=clean_text(str(row.get("question") or "")),
        expected_doc_ids=normalize_list(row.get("expected_doc_ids")),
        gold_answer=clean_text(str(row.get("gold_answer") or "")),
        answer_facts=[clean_text(str(item)) for item in answer_facts],
    )
