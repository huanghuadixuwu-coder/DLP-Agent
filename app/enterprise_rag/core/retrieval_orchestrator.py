from __future__ import annotations

from collections import defaultdict
from time import perf_counter
from typing import Any

from langchain_core.documents import Document

from app.config import get_settings
from app.enterprise_rag.core.evidence_pack import build_evidence_pack
from app.enterprise_rag.core.query_planner import expand_retrieval_plan
from app.actor_context import DEFAULT_TENANT_ID, DEFAULT_WORKSPACE_ID
from app.enterprise_rag.core.types import ENTERPRISE_DOMAIN, EnterpriseCitation, EvidencePack, RetrievalPlan
from app.metrics import record_retrieval_expansion
from app.enterprise_rag.libs.metadata import normalize_source_type
from app.enterprise_rag.libs.reranker import rerank_pairs
from app.enterprise_rag.libs.scoring import (
    build_rerank_debug_rows,
    extract_query_entity_anchors,
    heuristic_candidate_score,
    text_matches_entity_anchors,
)
from app.enterprise_rag.libs.sparse_index import fetch_enterprise_sparse_neighbor_chunks, search_enterprise_sparse
from app.enterprise_rag.libs.text_cleaning import query_focused_snippet
from app.resilience import make_failure_observation
from app.vectorstore import get_enterprise_collection, get_enterprise_vectorstore


def _build_filter(plan: RetrievalPlan) -> dict[str, Any]:
    source_types = [normalize_source_type(item) for item in plan.source_types if item]
    clauses: list[dict[str, Any]] = [{"domain": {"$eq": ENTERPRISE_DOMAIN}}]
    if source_types:
        clauses.append({"source_type": {"$in": source_types}})
    if plan.tenant_id and plan.tenant_id != DEFAULT_TENANT_ID:
        clauses.append({"tenant_id": {"$eq": plan.tenant_id}})
    if plan.workspace_id and plan.workspace_id != DEFAULT_WORKSPACE_ID:
        clauses.append({"workspace_id": {"$eq": plan.workspace_id}})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def retrieve_evidence(plan: RetrievalPlan) -> EvidencePack:
    retrieval_started = perf_counter()
    evidence = _retrieve_evidence_once(plan, expansion_triggered=False, expansion_reason="none", first_pass_counts={})
    should_expand, expansion_reason = _should_expand_retrieval(plan, evidence)
    if not should_expand:
        evidence.retrieval_stage_debug["retrieval_total_ms"] = round((perf_counter() - retrieval_started) * 1000.0, 2)
        return evidence
    first_pass_counts = {
        "dense_hits": int(evidence.retrieval_stage_debug.get("dense_hits", 0)),
        "sparse_hits": int(evidence.retrieval_stage_debug.get("sparse_hits", 0)),
        "merged_hits": int(evidence.retrieval_stage_debug.get("merged_hits", 0)),
        "rerank_hits": int(evidence.retrieval_stage_debug.get("rerank_hits", 0)),
        "citations": len(evidence.citations),
    }
    expanded = _retrieve_evidence_once(
        expand_retrieval_plan(plan),
        expansion_triggered=True,
        expansion_reason=expansion_reason,
        first_pass_counts=first_pass_counts,
    )
    expanded.retrieval_stage_debug["retrieval_total_ms"] = round((perf_counter() - retrieval_started) * 1000.0, 2)
    expanded.retrieval_stage_debug["first_pass_stage_latencies_ms"] = dict(
        evidence.retrieval_stage_debug.get("stage_latencies_ms") or {}
    )
    record_retrieval_expansion("enterprise_rag", expansion_reason)
    return expanded


def _retrieve_evidence_once(
    plan: RetrievalPlan,
    *,
    expansion_triggered: bool,
    expansion_reason: str,
    first_pass_counts: dict[str, int],
) -> EvidencePack:
    pass_started = perf_counter()
    stage_latencies_ms: dict[str, float] = {}
    document_check_started = perf_counter()
    has_enterprise_documents = _has_enterprise_documents()
    stage_latencies_ms["document_check"] = round((perf_counter() - document_check_started) * 1000.0, 2)
    if not has_enterprise_documents:
        evidence = build_evidence_pack(
            plan.query,
            [],
            retrieval_stage_debug={
                "dense_hits": 0,
                "sparse_hits": 0,
                "merged_hits": 0,
                "rerank_hits": 0,
                "rerank_backend": "none",
                "no_enterprise_documents": True,
                "budget_profile": plan.budget_profile,
                "expansion_triggered": expansion_triggered,
                "expansion_reason": expansion_reason,
                "first_pass_counts": first_pass_counts,
                "second_pass_counts": {},
            },
        )
        stage_latencies_ms["total"] = round((perf_counter() - pass_started) * 1000.0, 2)
        evidence.retrieval_stage_debug["stage_latencies_ms"] = stage_latencies_ms
        return evidence

    dense_started = perf_counter()
    dense_docs, dense_meta = _dense_recall(plan)
    stage_latencies_ms["dense"] = round((perf_counter() - dense_started) * 1000.0, 2)
    sparse_started = perf_counter()
    sparse_docs, sparse_meta = _sparse_recall(plan)
    stage_latencies_ms["sparse"] = round((perf_counter() - sparse_started) * 1000.0, 2)
    neighbor_started = perf_counter()
    neighbor_docs, neighbor_meta = _neighbor_recall(plan, [*dense_docs, *sparse_docs])
    stage_latencies_ms["neighbor"] = round((perf_counter() - neighbor_started) * 1000.0, 2)
    merge_started = perf_counter()
    merged_docs, retrieval_sources = _merge_candidates(plan, dense_docs, sparse_docs, neighbor_docs)
    stage_latencies_ms["merge"] = round((perf_counter() - merge_started) * 1000.0, 2)
    rerank_started = perf_counter()
    reranked_docs, rerank_scores, rerank_meta = _rerank_candidates(plan, merged_docs, retrieval_sources)
    stage_latencies_ms["rerank"] = round((perf_counter() - rerank_started) * 1000.0, 2)
    evidence_select_started = perf_counter()
    evidence_docs = _select_evidence_docs(reranked_docs, plan.evidence_top_k)
    stage_latencies_ms["evidence_select"] = round((perf_counter() - evidence_select_started) * 1000.0, 2)

    citation_started = perf_counter()
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
                "tenant_id": str(doc.metadata.get("tenant_id") or ""),
                "workspace_id": str(doc.metadata.get("workspace_id") or ""),
                "bm25_score": float(doc.metadata.get("bm25_score") or 0.0),
                "full_content": doc.page_content,
            },
        )
        for doc in evidence_docs
    ]
    stage_latencies_ms["citation_build"] = round((perf_counter() - citation_started) * 1000.0, 2)
    debug_started = perf_counter()
    rerank_debug = build_rerank_debug_rows(
        plan.query,
        reranked_docs,
        [rerank_scores.get(str(doc.metadata.get("chunk_id") or ""), 0.0) for doc in reranked_docs],
        retrieval_sources=retrieval_sources,
    )
    stage_latencies_ms["rerank_debug"] = round((perf_counter() - debug_started) * 1000.0, 2)
    evidence_pack_started = perf_counter()
    evidence = build_evidence_pack(
        plan.query,
        citations,
        retrieval_stage_debug={
            "dense_hits": len(dense_docs),
            "sparse_hits": len(sparse_docs),
            "neighbor_hits": len(neighbor_docs),
            "merged_hits": len(merged_docs),
            "rerank_hits": len(reranked_docs),
            "rerank_candidate_limit": plan.rerank_candidate_top_k,
            "question_type": plan.question_type,
            "budget_profile": plan.budget_profile,
            "source_types": list(plan.source_types),
            "tenant_id": plan.tenant_id,
            "workspace_id": plan.workspace_id,
            "dense_candidate_doc_ids": len({str(doc.metadata.get("doc_id") or "") for doc in dense_docs}),
            "sparse_candidate_doc_ids": len({str(doc.metadata.get("doc_id") or "") for doc in sparse_docs}),
            "expansion_triggered": expansion_triggered,
            "expansion_reason": expansion_reason,
            "first_pass_counts": first_pass_counts,
            "second_pass_counts": {
                "dense_hits": len(dense_docs),
                "sparse_hits": len(sparse_docs),
                "merged_hits": len(merged_docs),
                "rerank_hits": len(reranked_docs),
                "citations": len(citations),
            }
            if expansion_triggered
            else {},
            **rerank_meta,
            **dense_meta,
            **sparse_meta,
            **neighbor_meta,
        },
        rerank_debug=rerank_debug[: plan.rerank_top_k],
    )
    stage_latencies_ms["evidence_pack"] = round((perf_counter() - evidence_pack_started) * 1000.0, 2)
    stage_latencies_ms["total"] = round((perf_counter() - pass_started) * 1000.0, 2)
    evidence.retrieval_stage_debug["stage_latencies_ms"] = stage_latencies_ms
    return evidence


def _should_expand_retrieval(plan: RetrievalPlan, evidence: EvidencePack) -> tuple[bool, str]:
    if not plan.expansion_enabled or plan.budget_profile == "expanded":
        return False, "none"
    debug = evidence.retrieval_stage_debug
    if bool(debug.get("no_enterprise_documents")):
        return False, "none"
    dense_hits = int(debug.get("dense_hits", 0))
    sparse_hits = int(debug.get("sparse_hits", 0))
    merged_hits = int(debug.get("merged_hits", 0))
    rerank_hits = int(debug.get("rerank_hits", 0))
    if dense_hits == 0 and sparse_hits == 0:
        return True, "both_dense_and_sparse_empty"
    single_side_evidence_low = merged_hits < plan.rerank_top_k or len(evidence.citations) < min(3, plan.evidence_top_k)
    if dense_hits == 0 and plan.question_type in {"semantic", "constrained", "conflicting"} and single_side_evidence_low:
        return True, "dense_empty_for_high_recall_question"
    if sparse_hits == 0 and plan.question_type in {"constrained", "conflicting"} and single_side_evidence_low:
        return True, "sparse_empty_for_constrained_question"
    minimum_citations = min(3, plan.evidence_top_k)
    if merged_hits < max(4, min(plan.rerank_top_k, 6)) and len(evidence.citations) < minimum_citations:
        return True, "merged_candidates_below_rerank_budget"
    if rerank_hits < min(plan.rerank_top_k, 4) and len(evidence.citations) < minimum_citations:
        return True, "rerank_hits_too_low"
    if len(evidence.citations) < minimum_citations:
        return True, "selected_evidence_too_low"
    if evidence.missing_evidence and evidence.confidence < 0.35:
        return True, "answerability_confidence_low"
    return False, "none"


def _dense_recall(plan: RetrievalPlan) -> tuple[list[Document], dict[str, Any]]:
    try:
        vectorstore = get_enterprise_vectorstore()
        docs = vectorstore.similarity_search(
            plan.query,
            k=plan.dense_top_k,
            filter=_build_filter(plan),
        )
        return docs, {"dense_error": "", "dense_failure_observation": {}}
    except Exception as exc:
        return [], {
            "dense_error": str(exc),
            "dense_failure_observation": make_failure_observation(
                service="chroma",
                operation="enterprise_dense_recall",
                error=str(exc),
                fallback_strategy="continue_with_sparse_recall",
                retry_count=0,
            ),
        }


def _sparse_recall(plan: RetrievalPlan) -> tuple[list[Document], dict[str, Any]]:
    try:
        rows = search_enterprise_sparse(
            plan.query,
            source_types=plan.source_types,
            tenant_id="" if plan.tenant_id == DEFAULT_TENANT_ID else plan.tenant_id,
            workspace_id="" if plan.workspace_id == DEFAULT_WORKSPACE_ID else plan.workspace_id,
            limit=plan.sparse_top_k,
        )
    except Exception as exc:
        return [], {
            "sparse_error": str(exc),
            "sparse_failure_observation": make_failure_observation(
                service="sqlite_fts",
                operation="enterprise_sparse_recall",
                error=str(exc),
                fallback_strategy="continue_with_dense_recall",
                retry_count=0,
            ),
        }
    docs = [
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
                "tenant_id": str(row.get("tenant_id") or ""),
                "workspace_id": str(row.get("workspace_id") or ""),
                "bm25_score": float(row.get("bm25_score") or 0.0),
            },
        )
        for row in rows
    ]
    return docs, {"sparse_error": "", "sparse_failure_observation": {}}


def _neighbor_recall(plan: RetrievalPlan, docs: list[Document]) -> tuple[list[Document], dict[str, Any]]:
    anchors = extract_query_entity_anchors(plan.query)
    if not anchors:
        return [], {"query_entity_anchors": [], "neighbor_seed_doc_ids": []}
    seeds: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for doc in docs:
        doc_id = str(doc.metadata.get("doc_id") or "")
        if not doc_id or doc_id in seen_doc_ids:
            continue
        if not text_matches_entity_anchors(f"{doc.metadata.get('title') or ''}\n{doc.page_content}", anchors):
            continue
        seen_doc_ids.add(doc_id)
        seeds.append({"doc_id": doc_id, "chunk_index": int(doc.metadata.get("chunk_index") or 0)})
        if len(seeds) >= 4:
            break
    if not seeds:
        return [], {"query_entity_anchors": anchors, "neighbor_seed_doc_ids": []}
    try:
        rows = fetch_enterprise_sparse_neighbor_chunks(
            seeds,
            radius=2,
            tenant_id="" if plan.tenant_id == DEFAULT_TENANT_ID else plan.tenant_id,
            workspace_id="" if plan.workspace_id == DEFAULT_WORKSPACE_ID else plan.workspace_id,
            limit=max(plan.rerank_candidate_top_k * 2, 16),
        )
    except Exception as exc:
        return [], {
            "query_entity_anchors": anchors,
            "neighbor_seed_doc_ids": sorted(seen_doc_ids),
            "neighbor_error": str(exc),
        }
    neighbor_docs = [
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
                "tenant_id": str(row.get("tenant_id") or ""),
                "workspace_id": str(row.get("workspace_id") or ""),
            },
        )
        for row in rows
    ]
    return neighbor_docs, {
        "query_entity_anchors": anchors,
        "neighbor_seed_doc_ids": sorted(seen_doc_ids),
        "neighbor_error": "",
    }


def _merge_candidates(
    plan: RetrievalPlan,
    dense_docs: list[Document],
    sparse_docs: list[Document],
    neighbor_docs: list[Document],
) -> tuple[list[Document], dict[str, str]]:
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
    for doc in neighbor_docs:
        _remember(doc, "neighbor")

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
    diversified = _diversify_candidates(ranked, retrieval_sources, limit=plan.rerank_candidate_top_k)
    return diversified, retrieval_sources


def _rerank_candidates(
    plan: RetrievalPlan,
    docs: list[Document],
    retrieval_sources: dict[str, str],
) -> tuple[list[Document], dict[str, float], dict[str, Any]]:
    if not docs:
        return [], {}, {"rerank_backend": "none", "rerank_error": ""}
    docs = docs[: plan.rerank_candidate_top_k]
    passages = [
        (
            f"title: {doc.metadata.get('title') or ''}\n"
            f"source: {doc.metadata.get('source_type') or ''}\n"
            f"content: {query_focused_snippet(doc.page_content, plan.query, limit=900)}"
        )
        for doc in docs
    ]
    rerank_passage_chars = sum(len(passage) for passage in passages)
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
        rerank_started = perf_counter()
        scores = rerank_pairs(plan.query, passages)
        rerank_elapsed_ms = round((perf_counter() - rerank_started) * 1000.0, 2)
        paired = []
        for cross_encoder_score, doc in zip(scores, docs):
            chunk_id = str(doc.metadata.get("chunk_id") or "")
            combined_score = float(cross_encoder_score) + (heuristic_scores.get(chunk_id, 0.0) * 0.8)
            paired.append((combined_score, doc))
        paired.sort(key=lambda item: item[0], reverse=True)
        selected = paired[: plan.rerank_top_k]
        metadata: dict[str, Any] = {
            "rerank_backend": "cross_encoder",
            "rerank_error": "",
            "rerank_candidates_submitted": len(docs),
            "rerank_candidate_limit": plan.rerank_candidate_top_k,
            "rerank_passage_chars": rerank_passage_chars,
            "rerank_elapsed_ms": rerank_elapsed_ms,
        }
        soft_budget_ms = max(float(get_settings().enterprise_reranker_soft_budget_seconds), 0.0) * 1000.0
        if soft_budget_ms and rerank_elapsed_ms > soft_budget_ms:
            metadata["failure_observation"] = make_failure_observation(
                service="reranker",
                operation="rerank_pairs_soft_budget",
                error=f"soft_budget_exceeded:{rerank_elapsed_ms:.2f}ms>{soft_budget_ms:.2f}ms",
                fallback_strategy="keep_cross_encoder_result_and_surface_latency_warning",
                retry_count=0,
                severity="low",
            )
        return (
            [doc for _, doc in selected],
            {str(doc.metadata.get("chunk_id") or ""): float(score) for score, doc in selected},
            metadata,
        )
    except Exception as exc:
        failure_observation = make_failure_observation(
            service="reranker",
            operation="rerank_pairs",
            error=str(exc),
            fallback_strategy="heuristic_candidate_score",
            retry_count=0,
        )
        fallback_scores = [heuristic_scores.get(str(doc.metadata.get("chunk_id") or ""), 0.0) for doc in docs]
        paired = list(zip(fallback_scores, docs))
        paired.sort(key=lambda item: item[0], reverse=True)
        selected = paired[: plan.rerank_top_k]
        return (
            [doc for _, doc in selected],
            {str(doc.metadata.get("chunk_id") or ""): float(score) for score, doc in selected},
            {
                "rerank_backend": "heuristic_fallback",
                "rerank_error": str(exc),
                "failure_observation": failure_observation,
                "rerank_candidates_submitted": len(docs),
                "rerank_candidate_limit": plan.rerank_candidate_top_k,
                "rerank_passage_chars": rerank_passage_chars,
            },
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
