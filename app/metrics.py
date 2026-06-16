from __future__ import annotations

from dataclasses import dataclass, field

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


REQUESTS_TOTAL = Counter("agent_requests_total", "Total requests handled by the agent.")
FAILURES_TOTAL = Counter("agent_failures_total", "Total failed requests.")
MODE_RUNS_TOTAL = Counter("agent_mode_runs_total", "Total mode executions.", ["mode"])
TOKENS_INPUT_TOTAL = Counter("agent_tokens_input_total", "Total input tokens.")
TOKENS_OUTPUT_TOTAL = Counter("agent_tokens_output_total", "Total output tokens.")
ESTIMATED_COST_TOTAL = Counter("agent_estimated_cost_total", "Estimated model cost in CNY.")
RETRIEVAL_HITS_TOTAL = Counter("agent_retrieval_hits_total", "Total retrieved document hits.")
MEMORY_HITS_TOTAL = Counter("agent_memory_hits_total", "Total conversation memory hits.")
CONVERSATION_MEMORY_USED_TOTAL = Counter("agent_conversation_memory_used_total", "Total requests that used conversation memory.")
CONVERSATION_SUMMARY_WRITES_TOTAL = Counter("agent_conversation_summary_writes_total", "Total conversation summaries written to Chroma.")
LAB_REQUESTS_TOTAL = Counter("agent_lab_requests_total", "Total lab workflow requests.", ["lab"])
PRIVACY_REDACTIONS_TOTAL = Counter("agent_privacy_redactions_total", "Total redacted privacy fields.", ["pii_type"])
PRIVACY_ALERTS_TOTAL = Counter("agent_privacy_alerts_total", "Total privacy alerts by risk level.", ["risk_level"])
CONTEXT_TRUNCATED_SEGMENTS_TOTAL = Counter("agent_context_truncated_segments_total", "Total context segments dropped by budget packers.", ["lab"])
CONTEXT_BUDGET_USED_TOKENS = Gauge("agent_context_budget_used_tokens", "Last context budget used tokens.", ["lab"])
CONTEXT_BUDGET_LIMIT_TOKENS = Gauge("agent_context_budget_limit_tokens", "Last context budget limit tokens.", ["lab"])
UNIFIED_REQUESTS_TOTAL = Counter("agent_unified_requests_total", "Total unified Agent requests.")
INTENT_RUNS_TOTAL = Counter("agent_intent_runs_total", "Total unified Agent intent runs.", ["intent"])
TOOL_CALLS_TOTAL = Counter("agent_tool_calls_total", "Total unified Agent tool calls.", ["tool"])
TOOL_FAILURES_TOTAL = Counter("agent_tool_failures_total", "Total unified Agent tool failures.", ["tool"])
CLARIFICATION_REQUESTS_TOTAL = Counter("agent_clarification_requests_total", "Total clarification requests.")
PRIVACY_GUARDRAILS_TOTAL = Counter("agent_privacy_guardrails_total", "Total privacy guardrail activations.")
AGENT_CONTEXT_BUDGET_USED_TOKENS = Gauge("agent_context_budget_used_tokens_by_intent", "Last unified Agent context budget used tokens.", ["intent"])
ROUTER_RUNS_TOTAL = Counter("agent_router_runs_total", "Total hybrid router runs.", ["source"])
ROUTER_FALLBACKS_TOTAL = Counter("agent_router_fallbacks_total", "Total hybrid router fallbacks.")
ROUTER_LLM_CONFIDENCE = Gauge("agent_router_llm_confidence", "Last LLM router confidence.")
UNIFIED_EVIDENCE_HITS_TOTAL = Counter("agent_unified_evidence_hits_total", "Total unified RAG evidence hits.", ["domain"])
CONVERSATIONS_TOTAL = Counter("agent_conversations_total", "Total conversations created.")
CONVERSATION_TURNS_TOTAL = Counter("agent_conversation_turns_total", "Total conversation turns written.", ["role"])
CONVERSATION_MERGES_TOTAL = Counter("agent_conversation_merges_total", "Total conversation merges.")
TURN_SUMMARY_WRITES_TOTAL = Counter("agent_turn_summary_writes_total", "Total turn summaries written to Chroma.")
MERGED_SUMMARY_WRITES_TOTAL = Counter("agent_merged_summary_writes_total", "Total merged summaries written to Chroma.")
MEMORY_RETRIEVAL_HITS_TOTAL = Counter("agent_memory_retrieval_hits_total", "Total memory retrieval hits.", ["source_type"])
ANSWER_COLLAPSES_TOTAL = Counter("agent_answer_collapses_total", "Total long answers collapsed in the UI.")
TASKS_CREATED_TOTAL = Counter("agent_tasks_created_total", "Total asynchronous DLP tasks created.")
TASK_RETRIES_TOTAL = Counter("agent_task_retries_total", "Total asynchronous task retries.", ["task_type"])
MAIL_DLQ_CREATED_TOTAL = Counter(
    "agent_mail_dlq_created_total",
    "Total mail dead-letter queue entries created.",
    ["operation", "safe_replay_allowed"],
)
MAIL_DLQ_REPLAY_TOTAL = Counter(
    "agent_mail_dlq_replay_total",
    "Total mail dead-letter queue replay attempts.",
    ["operation", "result"],
)
TASKS_INFLIGHT = Gauge("agent_tasks_inflight", "Current asynchronous DLP tasks in non-terminal states.")
TASK_STATUS_TOTAL = Gauge("agent_task_status_total", "Current DLP tasks grouped by status.", ["status"])
TASK_LATENCY_MS = Gauge("agent_task_latency_ms", "Average terminal task latency in milliseconds.")
QUEUE_BACKLOG = Gauge("agent_queue_backlog", "Current queue backlog size.", ["queue"])
RATE_LIMITED_TOTAL = Counter("agent_rate_limited_total", "Total requests rejected by rate limiting.", ["resource"])
LLM_ERRORS_TOTAL = Counter("agent_llm_errors_total", "Total LLM or renderer call errors.", ["source"])
RENDERER_FALLBACK_TOTAL = Counter("agent_renderer_fallback_total", "Total final renderer fallbacks.", ["source"])
RETRIEVAL_EXPANSION_TOTAL = Counter("agent_retrieval_expansion_total", "Total retrieval expansions.", ["domain", "reason"])
DEPENDENCY_FAILURES_TOTAL = Counter(
    "agent_dependency_failures_total",
    "Total dependency failures that produced degraded observations.",
    ["service", "operation", "fallback_strategy"],
)
MULTI_AGENT_PLAN_FAILURES_TOTAL = Counter(
    "agent_multi_agent_plan_failures_total",
    "Total multi-agent DAG plan validation or execution failures.",
    ["reason"],
)
SIDE_EFFECT_BLOCKED_TOTAL = Counter(
    "agent_side_effect_blocked_total",
    "Total side-effectful domain-agent steps blocked before confirmation.",
    ["agent", "action"],
)
DEPENDENCY_BLOCKED_TOTAL = Counter(
    "agent_dependency_blocked_total",
    "Total DAG steps blocked because dependencies failed or were missing.",
    ["agent", "action"],
)
APPROVAL_PENDING_TOTAL = Gauge("agent_approval_pending_total", "Current DLP tasks waiting for human approval.")
EMAIL_SEND_TOTAL = Gauge("agent_email_send_total", "Current number of successfully sent DLP emails.")
EMAIL_SEND_FAILURES_TOTAL = Gauge("agent_email_send_failures_total", "Current number of failed DLP email sends.")
SCENARIO_REPLAYS_TOTAL = Counter(
    "agent_dlp_scenario_replays_total",
    "Total DLP scenario replay tasks created.",
    ["scenario_id"],
)
FAULT_INJECTIONS_TOTAL = Counter(
    "agent_dlp_fault_injections_total",
    "Total DLP fault injections triggered.",
    ["fault_type"],
)
DEGRADATION_MODE_TOTAL = Counter(
    "agent_dlp_degradation_mode_total",
    "Total DLP degradation mode activations.",
    ["mode"],
)
REQUEST_LATENCY_MS = Histogram(
    "agent_request_latency_ms",
    "Total request latency in milliseconds.",
    buckets=(100, 300, 500, 800, 1200, 2000, 5000, 8000, 12000, 15000, 20000, 30000),
)
NODE_LATENCY_MS = Histogram(
    "agent_node_latency_ms",
    "Per-node latency in milliseconds.",
    ["node"],
    buckets=(10, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000),
)
RAG_STAGE_LATENCY_MS = Histogram(
    "agent_rag_stage_latency_ms",
    "EnterpriseRAG stage latency in milliseconds.",
    ["stage"],
    buckets=(10, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 30000),
)
RAG_INDEX_DENSE_CHUNKS = Gauge("agent_rag_index_dense_chunks", "Chunks in the active EnterpriseRAG dense collection.")
RAG_INDEX_SPARSE_CHUNKS = Gauge("agent_rag_index_sparse_chunks", "Chunks in the active EnterpriseRAG sparse index.")
RAG_INDEX_PARITY_OK = Gauge("agent_rag_index_parity_ok", "1 if the active EnterpriseRAG dense/sparse index contract is aligned.")
RAG_LLM_TOKENS_INPUT_TOTAL = Counter("agent_rag_llm_tokens_input_total", "EnterpriseRAG LLM input tokens.")
RAG_LLM_TOKENS_OUTPUT_TOTAL = Counter("agent_rag_llm_tokens_output_total", "EnterpriseRAG LLM output tokens.")

LAST_REQUEST_LATENCY_MS = Gauge("agent_last_request_latency_ms", "Latency of the last completed request in milliseconds.")
LAST_REQUEST_TOKENS_INPUT = Gauge("agent_last_request_tokens_input", "Input tokens used by the last completed request.")
LAST_REQUEST_TOKENS_OUTPUT = Gauge("agent_last_request_tokens_output", "Output tokens used by the last completed request.")
LAST_REQUEST_ESTIMATED_COST = Gauge("agent_last_request_estimated_cost", "Estimated cost of the last completed request in CNY.")
LAST_RETRIEVAL_HITS = Gauge("agent_last_retrieval_hits", "Retrieval hits for the last completed request.")
LAST_REQUEST_SUCCESS = Gauge("agent_last_request_success", "1 if the last request succeeded, otherwise 0.")
LAST_MEMORY_HITS = Gauge("agent_last_memory_hits", "Number of conversation memory hits for the last completed request.")
LAST_CONVERSATION_MEMORY_USED = Gauge("agent_last_conversation_memory_used", "1 if the last request used conversation memory.")
LAST_CONVERSATION_SUMMARY_WRITTEN = Gauge("agent_last_conversation_summary_written", "1 if the last request wrote a conversation summary.")


@dataclass
class UsageTracker:
    token_in: int = 0
    token_out: int = 0
    estimated_cost: float = 0.0
    node_latencies_ms: dict[str, float] = field(default_factory=dict)


def record_request(
    mode: str,
    latency_ms: float,
    token_in: int,
    token_out: int,
    estimated_cost: float,
    retrieval_hits: int,
    node_latencies_ms: dict[str, float] | None = None,
    memory_hits: int = 0,
    used_conversation_memory: bool = False,
    conversation_summary_written: bool = False,
) -> None:
    REQUESTS_TOTAL.inc()
    MODE_RUNS_TOTAL.labels(mode=mode).inc()
    TOKENS_INPUT_TOTAL.inc(token_in)
    TOKENS_OUTPUT_TOTAL.inc(token_out)
    ESTIMATED_COST_TOTAL.inc(estimated_cost)
    RETRIEVAL_HITS_TOTAL.inc(retrieval_hits)
    REQUEST_LATENCY_MS.observe(latency_ms)
    MEMORY_HITS_TOTAL.inc(memory_hits)

    LAST_REQUEST_LATENCY_MS.set(latency_ms)
    LAST_REQUEST_TOKENS_INPUT.set(token_in)
    LAST_REQUEST_TOKENS_OUTPUT.set(token_out)
    LAST_REQUEST_ESTIMATED_COST.set(estimated_cost)
    LAST_RETRIEVAL_HITS.set(retrieval_hits)
    LAST_REQUEST_SUCCESS.set(1)
    LAST_MEMORY_HITS.set(memory_hits)
    LAST_CONVERSATION_MEMORY_USED.set(1 if used_conversation_memory else 0)
    LAST_CONVERSATION_SUMMARY_WRITTEN.set(1 if conversation_summary_written else 0)

    if used_conversation_memory:
        CONVERSATION_MEMORY_USED_TOTAL.inc()
    if conversation_summary_written:
        CONVERSATION_SUMMARY_WRITES_TOTAL.inc()

    for node, value in (node_latencies_ms or {}).items():
        if node == "total":
            continue
        NODE_LATENCY_MS.labels(node=node).observe(value)


def record_failure() -> None:
    FAILURES_TOTAL.inc()
    LAST_REQUEST_SUCCESS.set(0)


def record_lab_request(lab: str) -> None:
    LAB_REQUESTS_TOTAL.labels(lab=lab).inc()


def record_context_budget(lab: str, used_tokens: int, limit_tokens: int, truncated_segments: int = 0) -> None:
    CONTEXT_BUDGET_USED_TOKENS.labels(lab=lab).set(used_tokens)
    CONTEXT_BUDGET_LIMIT_TOKENS.labels(lab=lab).set(limit_tokens)
    if truncated_segments:
        CONTEXT_TRUNCATED_SEGMENTS_TOTAL.labels(lab=lab).inc(truncated_segments)


def record_privacy_scan(risk_level: str, redactions: list[dict[str, int | str]]) -> None:
    PRIVACY_ALERTS_TOTAL.labels(risk_level=risk_level).inc()
    for item in redactions:
        PRIVACY_REDACTIONS_TOTAL.labels(pii_type=str(item["pii_type"])).inc(int(item["count"]))


def record_unified_agent(
    intent: str,
    tool_calls: list[dict],
    needs_clarification: bool,
    privacy_guardrail: bool,
    context_budget_used: int | None = None,
) -> None:
    UNIFIED_REQUESTS_TOTAL.inc()
    INTENT_RUNS_TOTAL.labels(intent=intent).inc()
    if needs_clarification:
        CLARIFICATION_REQUESTS_TOTAL.inc()
    if privacy_guardrail:
        PRIVACY_GUARDRAILS_TOTAL.inc()
    if context_budget_used is not None:
        AGENT_CONTEXT_BUDGET_USED_TOKENS.labels(intent=intent).set(context_budget_used)

    for call in tool_calls:
        tool_name = str(call.get("tool_name", "unknown"))
        TOOL_CALLS_TOTAL.labels(tool=tool_name).inc()
        if not call.get("success", False):
            TOOL_FAILURES_TOTAL.labels(tool=tool_name).inc()


def record_router(source: str, confidence: float) -> None:
    ROUTER_RUNS_TOTAL.labels(source=source).inc()
    if source == "fallback":
        ROUTER_FALLBACKS_TOTAL.inc()
    if source == "llm":
        ROUTER_LLM_CONFIDENCE.set(confidence)


def record_unified_evidence_hits(evidence: list[dict]) -> None:
    for item in evidence:
        domain = str(item.get("domain", "unknown"))
        if domain:
            UNIFIED_EVIDENCE_HITS_TOTAL.labels(domain=domain).inc()


def record_conversation_created() -> None:
    CONVERSATIONS_TOTAL.inc()


def record_conversation_turns(user_written: bool = True, assistant_written: bool = True) -> None:
    if user_written:
        CONVERSATION_TURNS_TOTAL.labels(role="user").inc()
    if assistant_written:
        CONVERSATION_TURNS_TOTAL.labels(role="assistant").inc()


def record_conversation_merge() -> None:
    CONVERSATION_MERGES_TOTAL.inc()


def record_turn_summary_write() -> None:
    TURN_SUMMARY_WRITES_TOTAL.inc()


def record_merged_summary_write() -> None:
    MERGED_SUMMARY_WRITES_TOTAL.inc()


def record_memory_retrieval_hits(turn_hits: int, merged_hits: int) -> None:
    if turn_hits:
        MEMORY_RETRIEVAL_HITS_TOTAL.labels(source_type="turn_summary").inc(turn_hits)
    if merged_hits:
        MEMORY_RETRIEVAL_HITS_TOTAL.labels(source_type="merged_summary").inc(merged_hits)


def record_answer_collapse() -> None:
    ANSWER_COLLAPSES_TOTAL.inc()


def record_task_created() -> None:
    TASKS_CREATED_TOTAL.inc()


def record_task_retry(task_type: str) -> None:
    TASK_RETRIES_TOTAL.labels(task_type=task_type).inc()


def record_mail_dlq_created(operation: str, safe_replay_allowed: bool) -> None:
    MAIL_DLQ_CREATED_TOTAL.labels(
        operation=operation or "unknown",
        safe_replay_allowed=str(bool(safe_replay_allowed)).lower(),
    ).inc()


def record_mail_dlq_replay(operation: str, result: str) -> None:
    MAIL_DLQ_REPLAY_TOTAL.labels(operation=operation or "unknown", result=result or "unknown").inc()


def record_dlp_scenario_replay(scenario_id: str) -> None:
    if scenario_id:
        SCENARIO_REPLAYS_TOTAL.labels(scenario_id=scenario_id).inc()


def record_fault_injection(fault_type: str) -> None:
    if fault_type:
        FAULT_INJECTIONS_TOTAL.labels(fault_type=fault_type).inc()


def record_task_degradation(mode: str) -> None:
    if mode:
        DEGRADATION_MODE_TOTAL.labels(mode=mode).inc()


def record_rate_limited(resource: str) -> None:
    RATE_LIMITED_TOTAL.labels(resource=resource or "unknown").inc()


def record_llm_error(source: str = "unknown") -> None:
    LLM_ERRORS_TOTAL.labels(source=source or "unknown").inc()


def record_renderer_fallback(source: str = "unknown") -> None:
    RENDERER_FALLBACK_TOTAL.labels(source=source or "unknown").inc()


def record_retrieval_expansion(domain: str, reason: str) -> None:
    RETRIEVAL_EXPANSION_TOTAL.labels(domain=domain or "unknown", reason=reason or "unknown").inc()


def record_dependency_failure(service: str, operation: str, fallback_strategy: str) -> None:
    DEPENDENCY_FAILURES_TOTAL.labels(
        service=service or "unknown",
        operation=operation or "unknown",
        fallback_strategy=fallback_strategy or "unknown",
    ).inc()


def record_multi_agent_plan_failure(reason: str) -> None:
    MULTI_AGENT_PLAN_FAILURES_TOTAL.labels(reason=reason or "unknown").inc()


def record_side_effect_blocked(agent: str, action: str) -> None:
    SIDE_EFFECT_BLOCKED_TOTAL.labels(agent=agent or "unknown", action=action or "unknown").inc()


def record_dependency_blocked(agent: str, action: str) -> None:
    DEPENDENCY_BLOCKED_TOTAL.labels(agent=agent or "unknown", action=action or "unknown").inc()


def record_rag_stage_latencies(stage_latencies_ms: dict[str, float]) -> None:
    for stage, value in stage_latencies_ms.items():
        RAG_STAGE_LATENCY_MS.labels(stage=stage or "unknown").observe(float(value or 0.0))


def refresh_rag_index_metrics(contract: dict) -> None:
    RAG_INDEX_DENSE_CHUNKS.set(int((contract.get("dense") or {}).get("chunk_count") or 0))
    RAG_INDEX_SPARSE_CHUNKS.set(int((contract.get("sparse") or {}).get("chunk_count") or 0))
    RAG_INDEX_PARITY_OK.set(1 if (contract.get("parity") or {}).get("ok") else 0)


def record_rag_llm_usage(usage: dict[str, int]) -> None:
    RAG_LLM_TOKENS_INPUT_TOTAL.inc(int(usage.get("input_tokens") or 0))
    RAG_LLM_TOKENS_OUTPUT_TOTAL.inc(int(usage.get("output_tokens") or 0))


def record_queue_backlog(backlog: dict[str, int]) -> None:
    for queue_name, count in backlog.items():
        QUEUE_BACKLOG.labels(queue=str(queue_name)).set(int(count))


def refresh_task_metrics(
    *,
    total: int,
    inflight: int,
    by_status: dict[str, int],
    backlog: dict[str, int],
    average_latency_ms: float,
) -> None:
    TASKS_INFLIGHT.set(inflight)
    TASK_LATENCY_MS.set(average_latency_ms)
    APPROVAL_PENDING_TOTAL.set(by_status.get("pending_approval", 0))
    EMAIL_SEND_TOTAL.set(by_status.get("sent", 0))
    EMAIL_SEND_FAILURES_TOTAL.set(by_status.get("send_failed", 0))
    for status, count in by_status.items():
        TASK_STATUS_TOTAL.labels(status=status).set(count)
    for queue_name, count in backlog.items():
        QUEUE_BACKLOG.labels(queue=queue_name).set(count)


def render_metrics() -> bytes:
    return generate_latest()


def content_type() -> str:
    return CONTENT_TYPE_LATEST
