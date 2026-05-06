from __future__ import annotations

from collections import defaultdict
from typing import Any

from langchain_core.documents import Document

from app.enterprise_rag.core.evidence_pack import build_evidence_pack
from app.enterprise_rag.core.types import ENTERPRISE_DOMAIN, EnterpriseCitation, EvidencePack, RetrievalPlan
from app.enterprise_rag.libs.metadata import normalize_source_type
from app.enterprise_rag.libs.reranker import rerank_pairs
from app.enterprise_rag.libs.scoring import build_rerank_debug_rows, heuristic_candidate_score
from app.enterprise_rag.libs.sparse_index import search_enterprise_sparse
from app.enterprise_rag.libs.text_cleaning import query_focused_snippet
from app.vectorstore import get_enterprise_collection, get_enterprise_vectorstore


def _build_filter(source_types: list[str]) -> dict[str, Any]:
    source_types = [normalize_source_type(item) for item in source_types if item]
    if not source_types:
        return {"domain": {"$eq": ENTERPRISE_DOMAIN}}
    return {
        "$and": [
            {"domain": {"$eq": ENTERPRISE_DOMAIN}},
            {"source_type": {"$in": source_types}},
        ]
    }


def retrieve_evidence(plan: RetrievalPlan) -> EvidencePack:
    if not _has_enterprise_documents():
        return build_evidence_pack(
            plan.query,
            [],
            retrieval_stage_debug={
                "dense_hits": 0,
                "sparse_hits": 0,
                "merged_hits": 0,
                "rerank_hits": 0,
                "rerank_backend": "none",
            },
        )

    dense_docs = _dense_recall(plan)
    sparse_docs = _sparse_recall(plan)
    merged_docs, retrieval_sources = _merge_candidates(plan, dense_docs, sparse_docs)
    reranked_docs, rerank_scores, rerank_meta = _rerank_candidates(plan, merged_docs, retrieval_sources)
    evidence_docs = _select_evidence_docs(reranked_docs, plan.evidence_top_k)

    citations = [
        EnterpriseCitation(
            doc_id=str(doc.metadata.get("doc_id") or ""),
            chunk_id=str(doc.metadata.get("chunk_id") or ""),
            source_type=str(doc.metadata.get("source_type") or ""),
            title=str(doc.metadata.get("title") or ""),
            snippet=query_focused_snippet(doc.page_content, plan.query, limit=520),
            score=float(rerank_scores.get(str(doc.metadata.get("chunk_id") or ""), 0.0)),
            retrieval_source=retrieval_sources.get(str(doc.metadata.get("chunk_id") or ""), ""),
            metadata={
                "thread_id": str(doc.metadata.get("thread_id") or ""),
                "timestamp": str(doc.metadata.get("timestamp") or ""),
                "collection_version": str(doc.metadata.get("collection_version") or ""),
                "chunk_index": int(doc.metadata.get("chunk_index") or 0),
                "chunk_strategy": str(doc.metadata.get("chunk_strategy") or ""),
                "speaker": str(doc.metadata.get("speaker") or ""),
                "turn_start": str(doc.metadata.get("turn_start") or ""),
                "turn_end": str(doc.metadata.get("turn_end") or ""),
                "section_path": str(doc.metadata.get("section_path") or ""),
                "message_id": str(doc.metadata.get("message_id") or ""),
                "bm25_score": float(doc.metadata.get("bm25_score") or 0.0),
                "full_content": doc.page_content,
            },
        )
        for doc in evidence_docs
    ]
    rerank_debug = build_rerank_debug_rows(
        plan.query,
        reranked_docs,
        [rerank_scores.get(str(doc.metadata.get("chunk_id") or ""), 0.0) for doc in reranked_docs],
        retrieval_sources=retrieval_sources,
    )
    return build_evidence_pack(
        plan.query,
        citations,
        retrieval_stage_debug={
            "dense_hits": len(dense_docs),
            "sparse_hits": len(sparse_docs),
            "merged_hits": len(merged_docs),
            "rerank_hits": len(reranked_docs),
            "question_type": plan.question_type,
            "source_types": list(plan.source_types),
            "dense_candidate_doc_ids": len({str(doc.metadata.get("doc_id") or "") for doc in dense_docs}),
            "sparse_candidate_doc_ids": len({str(doc.metadata.get("doc_id") or "") for doc in sparse_docs}),
            **rerank_meta,
        },
        rerank_debug=rerank_debug[: plan.rerank_top_k],
    )


def _dense_recall(plan: RetrievalPlan) -> list[Document]:
    try:
        vectorstore = get_enterprise_vectorstore()
        return vectorstore.similarity_search(
            plan.query,
            k=plan.dense_top_k,
            filter=_build_filter(plan.source_types),
        )
    except Exception:
        return []


def _sparse_recall(plan: RetrievalPlan) -> list[Document]:
    try:
        rows = search_enterprise_sparse(plan.query, source_types=plan.source_types, limit=plan.sparse_top_k)
    except Exception:
        return []
    return [
        Document(
            page_content=str(row.get("content") or ""),
            metadata={
                "chunk_id": str(row.get("chunk_id") or ""),
                "doc_id": str(row.get("doc_id") or ""),
                "source_type": str(row.get("source_type") or ""),
                "title": str(row.get("title") or ""),
                "chunk_index": int(row.get("chunk_index") or 0),
                "chunk_strategy": str(row.get("chunk_strategy") or ""),
                "thread_id": str(row.get("thread_id") or ""),
                "timestamp": str(row.get("timestamp") or ""),
                "collection_version": str(row.get("collection_version") or ""),
                "bm25_score": float(row.get("bm25_score") or 0.0),
            },
        )
        for row in rows
    ]


def _merge_candidates(plan: RetrievalPlan, dense_docs: list[Document], sparse_docs: list[Document]) -> tuple[list[Document], dict[str, str]]:
    merged: dict[str, Document] = {}
    retrieval_sources: dict[str, str] = {}

    def _remember(doc: Document, source: str) -> None:
        chunk_id = str(doc.metadata.get("chunk_id") or "")
        if not chunk_id:
            return
        if chunk_id not in merged:
            merged[chunk_id] = doc
            retrieval_sources[chunk_id] = source
            return
        if retrieval_sources.get(chunk_id) != source:
            retrieval_sources[chunk_id] = "hybrid"

    for doc in dense_docs:
        _remember(doc, "dense")
    for doc in sparse_docs:
        _remember(doc, "sparse")

    score_map = {
        str(doc.metadata.get("chunk_id") or ""): heuristic_candidate_score(
            plan.query,
            content=doc.page_content,
            title=str(doc.metadata.get("title") or ""),
            source_type=str(doc.metadata.get("source_type") or ""),
            retrieval_source=retrieval_sources.get(str(doc.metadata.get("chunk_id") or ""), ""),
        )
        for doc in merged.values()
    }
    ranked = sorted(
        merged.values(),
        key=lambda doc: score_map.get(str(doc.metadata.get("chunk_id") or ""), 0.0),
        reverse=True,
    )
    if ranked:
        best_score = score_map.get(str(ranked[0].metadata.get("chunk_id") or ""), 0.0)
        score_floor = max(0.18, best_score * 0.62)
        filtered = [doc for doc in ranked if score_map.get(str(doc.metadata.get("chunk_id") or ""), 0.0) >= score_floor]
        if filtered:
            ranked = filtered
    diversified = _diversify_candidates(ranked, retrieval_sources, limit=min(60, max(plan.rerank_top_k * 8, 24)))
    return diversified, retrieval_sources


def _rerank_candidates(
    plan: RetrievalPlan,
    docs: list[Document],
    retrieval_sources: dict[str, str],
) -> tuple[list[Document], dict[str, float], dict[str, Any]]:
    if not docs:
        return [], {}, {"rerank_backend": "none", "rerank_error": ""}
    passages = [
        f"title: {doc.metadata.get('title') or ''}\nsource: {doc.metadata.get('source_type') or ''}\ncontent: {doc.page_content}"
        for doc in docs
    ]
    heuristic_scores = {
        str(doc.metadata.get("chunk_id") or ""): heuristic_candidate_score(
            plan.query,
            content=doc.page_content,
            title=str(doc.metadata.get("title") or ""),
            source_type=str(doc.metadata.get("source_type") or ""),
            retrieval_source=retrieval_sources.get(str(doc.metadata.get("chunk_id") or ""), ""),
        )
        for doc in docs
    }
    try:
        scores = rerank_pairs(plan.query, passages)
        paired = []
        for cross_encoder_score, doc in zip(scores, docs):
            chunk_id = str(doc.metadata.get("chunk_id") or "")
            combined_score = float(cross_encoder_score) + (heuristic_scores.get(chunk_id, 0.0) * 0.8)
            paired.append((combined_score, doc))
        paired.sort(key=lambda item: item[0], reverse=True)
        selected = paired[: plan.rerank_top_k]
        return (
            [doc for _, doc in selected],
            {str(doc.metadata.get("chunk_id") or ""): float(score) for score, doc in selected},
            {"rerank_backend": "cross_encoder", "rerank_error": ""},
        )
    except Exception as exc:
        fallback_scores = [heuristic_scores.get(str(doc.metadata.get("chunk_id") or ""), 0.0) for doc in docs]
        paired = list(zip(fallback_scores, docs))
        paired.sort(key=lambda item: item[0], reverse=True)
        selected = paired[: plan.rerank_top_k]
        return (
            [doc for _, doc in selected],
            {str(doc.metadata.get("chunk_id") or ""): float(score) for score, doc in selected},
            {"rerank_backend": "heuristic_fallback", "rerank_error": str(exc)},
        )


def _select_evidence_docs(docs: list[Document], top_k: int) -> list[Document]:
    if not docs:
        return []
    selected: list[Document] = []
    seen_doc_ids: defaultdict[str, int] = defaultdict(int)
    for doc in docs:
        doc_id = str(doc.metadata.get("doc_id") or "")
        if seen_doc_ids[doc_id] >= 4:
            continue
        selected.append(doc)
        seen_doc_ids[doc_id] += 1
        if len(selected) >= top_k:
            break
    return selected


def _diversify_candidates(docs: list[Document], retrieval_sources: dict[str, str], *, limit: int) -> list[Document]:
    preferred = [doc for doc in docs if retrieval_sources.get(str(doc.metadata.get("chunk_id") or ""), "") in {"hybrid", "sparse"}]
    fallback = [doc for doc in docs if doc not in preferred]
    ordered = preferred + fallback
    selected: list[Document] = []
    per_doc_counts: defaultdict[str, int] = defaultdict(int)
    for doc in ordered:
        doc_id = str(doc.metadata.get("doc_id") or "")
        if per_doc_counts[doc_id] >= 6:
            continue
        selected.append(doc)
        per_doc_counts[doc_id] += 1
        if len(selected) >= limit:
            break
    return selected


def _has_enterprise_documents() -> bool:
    collection = get_enterprise_collection()
    result = collection.get(where={"domain": {"$eq": ENTERPRISE_DOMAIN}}, limit=1, include=[])
    return bool(result.get("ids"))
