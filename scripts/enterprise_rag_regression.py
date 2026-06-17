from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_QUERY = (
    "In the meeting about onboarding a SaaS product to Google Cloud Marketplace, "
    "what did the GCP team recommend for handling delays where a new subscription "
    "entitlement is not immediately available during the customer onboarding flow?"
)


def _request(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EnterpriseRAG Docker-first regression against the local API.")
    parser.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--documents-path", default="/app/documents.parquet")
    parser.add_argument("--questions-path", default="/app/questions.parquet")
    parser.add_argument("--mode", choices=["sample", "full"], default="sample")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    args = parser.parse_args()

    base_url = args.api_base_url.rstrip("/")
    ingest_payload = {
        "mode": args.mode,
        "documents_path": args.documents_path,
        "questions_path": args.questions_path,
        "limit": args.limit,
        "reset": args.reset,
    }
    query_payload = {
        "question": args.query,
        "top_k": args.top_k,
    }
    benchmark_params = urllib.parse.urlencode(
        {
            "questions_path": args.questions_path,
            "limit": args.limit,
            "top_k": args.top_k,
        }
    )

    try:
        ingest = _request("POST", f"{base_url}/enterprise-rag/ingest", ingest_payload)
        query = _request("POST", f"{base_url}/enterprise-rag/query", query_payload)
        observation = dict(query.get("enterprise_answer_observation") or {})
        if observation:
            if observation.get("communication_role") != "grounding_provider":
                raise AssertionError(f"enterprise_answer_observation communication_role: {observation!r}")
            if observation.get("communication_input_kind") != "grounding_bundle":
                raise AssertionError(f"enterprise_answer_observation communication_input_kind: {observation!r}")
        benchmark = _request("GET", f"{base_url}/enterprise-rag/benchmark?{benchmark_params}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(json.dumps({"ok": False, "status": exc.code, "detail": detail}, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    cases = list(benchmark.get("cases") or [])
    bad_cases = sorted(
        (
            {
                "question_id": case.get("question_id"),
                "doc_recall": case.get("doc_recall", 0.0),
                "evidence_fact_coverage": case.get("evidence_fact_coverage", 0.0),
                "answer_fact_coverage": case.get("answer_fact_coverage", 0.0),
                "missing_evidence": case.get("missing_evidence", False),
                "expected_doc_ids": case.get("expected_doc_ids", []),
                "actual_doc_ids": case.get("actual_doc_ids", []),
                "answer": case.get("answer", ""),
            }
            for case in cases
        ),
        key=lambda item: (item["doc_recall"], item["evidence_fact_coverage"], item["answer_fact_coverage"]),
    )[:5]

    output = {
        "ok": True,
        "ingest": ingest,
        "query_summary": {
            "answer": query.get("answer", ""),
            "supporting_doc_ids": query.get("supporting_doc_ids", []),
            "supporting_facts": query.get("supporting_facts", []),
            "retrieval_stage_debug": query.get("retrieval_stage_debug", {}),
            "rerank_debug_top3": list(query.get("rerank_debug") or [])[:3],
        },
        "benchmark_summary": {
            "average_doc_recall": benchmark.get("average_doc_recall", 0.0),
            "average_evidence_fact_coverage": benchmark.get("average_evidence_fact_coverage", 0.0),
            "average_answer_fact_coverage": benchmark.get("average_answer_fact_coverage", 0.0),
            "bad_cases": bad_cases,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
