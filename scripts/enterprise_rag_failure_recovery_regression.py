from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document

from app.enterprise_rag.core import retrieval_orchestrator, service
from app.enterprise_rag.core.query_planner import build_retrieval_plan


def main() -> None:
    original_retrieve = service.retrieve_evidence
    original_rerank = retrieval_orchestrator.rerank_pairs
    try:
        service.retrieve_evidence = lambda _plan: (_ for _ in ()).throw(TimeoutError("injected retrieval timeout"))
        degraded = service.answer_enterprise_question("What did MedThink specify?", compose_answer=False)
        failures = list((degraded.get("enterprise_answer_observation") or {}).get("dependency_failures") or [])
        assert failures, degraded
        retrieval_failure = dict(failures[0].get("payload") or {})
        assert retrieval_failure.get("service") == "enterprise_rag", retrieval_failure
        assert retrieval_failure.get("operation") == "retrieve_evidence", retrieval_failure
        assert retrieval_failure.get("retryable") is True, retrieval_failure
        assert retrieval_failure.get("fallback_strategy") == "return_recovery_observation", retrieval_failure

        retrieval_orchestrator.rerank_pairs = lambda _query, _passages: (_ for _ in ()).throw(
            TimeoutError("injected reranker timeout")
        )
        plan = build_retrieval_plan("What did MedThink specify?", top_k=4)
        doc = Document(
            page_content="MedThink requires EU primary -> EU warm standby -> US emergency failover.",
            metadata={"chunk_id": "chunk-1", "doc_id": "doc-1", "title": "MedThink failover", "source_type": "gmail"},
        )
        _, _, rerank_meta = retrieval_orchestrator._rerank_candidates(plan, [doc], {"chunk-1": "hybrid"})
        rerank_failure = dict((rerank_meta.get("failure_observation") or {}).get("payload") or {})
        assert rerank_meta.get("rerank_backend") == "heuristic_fallback", rerank_meta
        assert rerank_failure.get("service") == "reranker", rerank_failure
        assert rerank_failure.get("operation") == "rerank_pairs", rerank_failure
        assert rerank_failure.get("retryable") is True, rerank_failure
        assert rerank_failure.get("fallback_strategy") == "heuristic_candidate_score", rerank_failure
    finally:
        service.retrieve_evidence = original_retrieve
        retrieval_orchestrator.rerank_pairs = original_rerank

    print(json.dumps({"ok": True, "cases": ["retrieval_timeout_observation", "reranker_timeout_heuristic_fallback"]}))


if __name__ == "__main__":
    main()
