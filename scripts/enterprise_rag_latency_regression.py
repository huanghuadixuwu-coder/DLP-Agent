from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.enterprise_rag.core.service import answer_enterprise_question
from app.enterprise_rag.core.query_planner import build_retrieval_plan
from app.orchestration.fast_router import route_agent_request


QUESTIONS = {
    "gcp_onboarding": (
        "In the meeting about onboarding a SaaS product to Google Cloud Marketplace, "
        "what did the GCP team recommend for handling delays where a new subscription "
        "entitlement is not immediately available during the customer onboarding flow?"
    ),
    "medthink_failover": (
        "What failover sequence and recovery targets did MedThink specify for handling "
        "an EU region outage, including any limits on how long traffic can shift to the US?"
    ),
}


def _json_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def main() -> None:
    chinese_failover_plan = build_retrieval_plan("MedThink 的 EU 区域故障转移顺序、RPO/RTO 和切换到美国的时限是什么？")
    assert chinese_failover_plan.question_type in {"constrained", "semantic"}, chinese_failover_plan
    assert chinese_failover_plan.budget_profile in {"large", "medium"}, chinese_failover_plan
    chinese_failover_route = route_agent_request(
        message="MedThink 的 EU 区域故障转移顺序、RPO/RTO 和切换到美国的时限是什么？",
        safe_message="MedThink 的 EU 区域故障转移顺序、RPO/RTO 和切换到美国的时限是什么？",
        upload_context={},
    )
    assert chinese_failover_route["routing_source"] == "fast_router_heuristic", chinese_failover_route
    assert chinese_failover_route["intent"] == "enterprise_fact", chinese_failover_route
    rows = []
    for case_id, question in QUESTIONS.items():
        result = answer_enterprise_question(question, top_k=8)
        observation_only_result = answer_enterprise_question(question, top_k=8, compose_answer=False)
        observation = dict(result.get("enterprise_answer_observation") or {})
        observation_only = dict(observation_only_result.get("enterprise_answer_observation") or {})
        retrieval_debug = dict(result.get("retrieval_stage_debug") or {})
        retrieval_summary = dict(observation.get("retrieval_summary") or {})
        observation_only_retrieval = dict(observation_only_result.get("retrieval_stage_debug") or {})
        assert result.get("stage_latencies_ms"), result
        assert retrieval_debug.get("stage_latencies_ms"), retrieval_debug
        assert observation, result
        assert observation_only_result.get("answer_composition_skipped") is True, observation_only_result
        assert not observation_only_result.get("answer"), observation_only_result
        assert observation_only, observation_only_result
        assert "rerank_debug" not in observation, observation
        assert "retrieval_stage_debug" not in observation, observation
        assert "memory_context" not in observation, observation
        full_size = _json_size(result)
        observation_size = _json_size(observation)
        assert observation_size < full_size, (case_id, full_size, observation_size)
        if case_id == "medthink_failover":
            canonical_facts = list(observation_only.get("canonical_facts") or [])
            canonical_text = "\n".join(str(item.get("normalized_fact") or "") for item in canonical_facts)
            assert "EU primary -> EU warm standby -> US emergency failover" in canonical_text, canonical_facts
            assert "max 4 hours" in canonical_text, canonical_facts
            assert "Target RPO 15 minutes" in canonical_text, canonical_facts
            assert "RTO target 30 minutes" in canonical_text, canonical_facts
            assert not any("?" in str(item.get("normalized_fact") or "") for item in canonical_facts), canonical_facts
            assert not any("FinServX" in str(item.get("title") or "") for item in canonical_facts), canonical_facts
            assert not any("@" in str(item.get("normalized_fact") or "") for item in canonical_facts), canonical_facts
            assert not any("please indicate" in str(item.get("normalized_fact") or "").lower() for item in canonical_facts), canonical_facts
        rows.append(
            {
                "case_id": case_id,
                "answer_preview": str(result.get("answer") or "")[:260],
                "supporting_doc_ids": list(result.get("supporting_doc_ids") or []),
                "pipeline_stage_latencies_ms": dict(result.get("stage_latencies_ms") or {}),
                "retrieval_stage_latencies_ms": dict(retrieval_debug.get("stage_latencies_ms") or {}),
                "retrieval_total_ms": float(retrieval_debug.get("retrieval_total_ms") or 0.0),
                "budget_profile": str(retrieval_summary.get("budget_profile") or ""),
                "expansion_triggered": bool(retrieval_summary.get("expansion_triggered", False)),
                "expansion_reason": str(retrieval_summary.get("expansion_reason") or ""),
                "rerank_candidates_submitted": int(retrieval_summary.get("rerank_candidates_submitted", 0)),
                "rerank_candidate_limit": int(retrieval_summary.get("rerank_candidate_limit", 0)),
                "rerank_passage_chars": int(retrieval_summary.get("rerank_passage_chars", 0)),
                "first_pass_counts": dict(retrieval_summary.get("first_pass_counts") or {}),
                "second_pass_counts": dict(retrieval_summary.get("second_pass_counts") or {}),
                "full_result_bytes": full_size,
                "renderer_observation_bytes": observation_size,
                "renderer_payload_reduction_ratio": round(1.0 - (observation_size / max(full_size, 1)), 4),
                "observation_only_stage_latencies_ms": dict(observation_only_result.get("stage_latencies_ms") or {}),
                "observation_only_retrieval_stage_latencies_ms": dict(observation_only_retrieval.get("stage_latencies_ms") or {}),
            }
        )
    print(json.dumps({"ok": True, "cases": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
