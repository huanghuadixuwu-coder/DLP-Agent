from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Mode = Literal["auto", "react", "plan_execute", "reflection"]


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    problem_id: str
    question: str = Field(min_length=1)
    mode: Mode = "auto"


class PlanRequest(ChatRequest):
    pass


class ExecuteRequest(BaseModel):
    session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    approved_plan: bool = True


class Citation(BaseModel):
    source_type: str
    title: str
    snippet: str
    chunk_id: str


class RetrievalPreview(BaseModel):
    retrieval_hits: int
    snippets: list[Citation]


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[Citation]
    trace_id: str
    latency_ms: float
    token_in: int
    token_out: int
    estimated_cost: float
    mode_used: Mode
    query_type: str
    draft_answer: str | None = None
    reflection_notes: str | None = None
    final_answer: str | None = None
    plan_steps: str | None = None
    retrieval_preview: RetrievalPreview | None = None
    requires_confirmation: bool = False
    memory_hits: int = 0
    used_conversation_memory: bool = False
    conversation_summary_written: bool = False


class PlanResponse(BaseModel):
    session_id: str
    trace_id: str
    mode_suggested: Mode
    query_type: str
    retrieval_preview: RetrievalPreview
    plan_steps: str
    requires_confirmation: bool = True
    memory_hits: int = 0
    used_conversation_memory: bool = False


class ProblemSummary(BaseModel):
    problem_id: str
    title: str
    difficulty: str
    topic: str
    tags: list[str]


class IngestResponse(BaseModel):
    documents_written: int
    collection_name: str


class HealthResponse(BaseModel):
    status: str
    chroma_ok: bool
    problems_loaded: int
    langsmith_enabled: bool


class ProblemRecord(BaseModel):
    problem_id: str
    title: str
    difficulty: str
    topic: str
    tags: list[str]
    statement: str
    examples: list[str]
    constraints: list[str]
    solution_code: str
    editor_note: dict[str, str]


class RunMetrics(BaseModel):
    token_in: int = 0
    token_out: int = 0
    estimated_cost: float = 0.0
    retrieval_hits: int = 0
    node_latencies_ms: dict[str, float] = Field(default_factory=dict)
    raw_usage: dict[str, Any] = Field(default_factory=dict)


class LongDocQueryRequest(BaseModel):
    question: str = Field(min_length=1)
    context_budget: int = Field(default=10_000, ge=1_000, le=50_000)
    retrieval_layer_preference: str = "auto"


class LongDocQueryResponse(BaseModel):
    answer: str
    question_scope: str
    budget_tokens: int
    used_tokens: int
    available_context_tokens: int
    allocation: dict[str, int]
    retrieved_layers: dict[str, int]
    citations: list[dict[str, str]]
    why_not_full_book: str


class PrivacyScanRequest(BaseModel):
    message: str = Field(min_length=1)
    context_budget: int = Field(default=600, ge=100, le=5_000)


class PrivacyScanResponse(BaseModel):
    redacted_text: str
    risk_level: str
    risk_reasons: list[str]
    redactions: list[dict[str, int | str]]
    context_pack: dict[str, int | str]
    storage_policy: str
    alert: bool
    policy_id: str = "static"
    policy_version: str = "static"
    policy_source: str = "static"
    policy_loaded_at: str = ""
    policy_load_error: str = ""


class DisambiguationQueryRequest(BaseModel):
    query: str = Field(min_length=1)


class DisambiguationQueryResponse(BaseModel):
    entity_type: str | None
    needs_clarification: bool
    clarification_question: str | None
    filter_used: dict[str, str] | None
    retrieved_docs: list[dict[str, str]]
    answer: str
    reason: str


class FrameworkCompareRequest(BaseModel):
    task: str = Field(default="Build a RAG agent with retrieval, tools, memory, and monitoring.")


class FrameworkCompareResponse(BaseModel):
    task: str
    comparison_dimensions: list[dict[str, str]]
    balanced_position: str


class UnifiedAgentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    mode: Mode = "auto"
    problem_id: str | None = None
    conversation_id: str | None = None
    show_steps: bool = True
    uploaded_filename: str = ""
    uploaded_content_type: str = ""
    uploaded_text: str = ""
    uploaded_file_base64: str = ""
    source_parse_status: str = "not_provided"
    source_parse_error: str = ""
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    roles: list[str] = Field(default_factory=list)


class UnifiedAgentResponse(BaseModel):
    session_id: str
    conversation_id: str
    turn_id: str | None = None
    answer: str
    intent: str
    routing_source: str = "rule"
    routing_confidence: float = 0.0
    routing_reason: str = ""
    candidate_intents: list[str] = Field(default_factory=list)
    mode_used: str
    tool_calls: list[dict[str, Any]]
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None
    privacy: dict[str, Any] = Field(default_factory=dict)
    context_budget: dict[str, Any] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    memory_written: bool = False
    memory_hits: int = 0
    merged_memory_hits: int = 0
    memory_context: dict[str, Any] = Field(default_factory=dict)
    context_sources: list[str] = Field(default_factory=list)
    workspace_memory_hits: int = 0
    transcript_hits: int = 0
    user_model_used: bool = False
    answer_collapsed: bool = False
    reflection_notes: str | None = None
    upload_context: dict[str, Any] = Field(default_factory=dict)
    route_mode: str = "rule"
    router_intent: str = ""
    router_reason: str = ""
    required_grounding: str = "none"
    fast_path_used: bool = False
    degraded_from: str = "none"
    planner_type: str = ""
    task_plan: dict[str, Any] = Field(default_factory=dict)
    subtask_results: list[dict[str, Any]] = Field(default_factory=list)
    aggregation_strategy: str = ""
    partial_failures: list[dict[str, Any]] = Field(default_factory=list)
    react_trace: list[dict[str, Any]] = Field(default_factory=list)
    loop_step_count: int = 0
    termination_reason: str = ""
    pending_confirmation: dict[str, Any] = Field(default_factory=dict)
    confirmation_payload: dict[str, Any] = Field(default_factory=dict)
    final_answer_source: str = ""
    memory_reads: list[dict[str, Any]] = Field(default_factory=list)
    tool_observations: list[dict[str, Any]] = Field(default_factory=list)
    workflow_id: str | None = None
    workflow_status: str | None = None
    workflow_risk_level: str | None = None
    task_id: str | None = None
    task_status: str | None = None
    task_risk_level: str | None = None
    delivery_status: str | None = None
    delivery_result: str | None = None
    delivery_error: str | None = None
    trace_id: str
    latency_ms: float
    token_in: int = 0
    token_out: int = 0
    estimated_cost: float = 0.0
    actor_context: dict[str, Any] = Field(default_factory=dict)
    permission_decision: dict[str, Any] = Field(default_factory=dict)
    rate_limit_decision: dict[str, Any] = Field(default_factory=dict)
    queue_status: dict[str, Any] = Field(default_factory=dict)
    task_mode: str = "sync"


class InboundMailSyncRequest(BaseModel):
    since: str | None = None
    until: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class InboundMailMessage(BaseModel):
    message_id: str
    mailbox: str = "INBOX"
    uid: str = ""
    sender: str = ""
    recipients: str = ""
    subject: str = ""
    received_at: str = ""
    snippet: str = ""
    summary: str = ""
    risk_hint: str = ""
    raw_size: int = 0
    is_seen: bool = False
    created_at: str = ""
    updated_at: str = ""


class InboundMailSummaryResponse(BaseModel):
    since: str
    until: str
    total: int = 0
    unread: int = 0
    important_count: int = 0
    important_messages: list[InboundMailMessage] = Field(default_factory=list)
    recent_messages: list[InboundMailMessage] = Field(default_factory=list)
    sync_state: dict[str, Any] = Field(default_factory=dict)


class OutboundMailSummaryResponse(BaseModel):
    since: str
    until: str
    total_sent: int = 0
    recent_sent: list[dict[str, Any]] = Field(default_factory=list)


class EnterpriseRagQueryRequest(BaseModel):
    question: str = Field(min_length=1)
    source_types: list[str] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=30)
    session_id: str = ""
    conversation_id: str = ""
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    roles: list[str] = Field(default_factory=list)


class EnterpriseRagQueryResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    supporting_doc_ids: list[str] = Field(default_factory=list)
    missing_evidence: bool = False
    confidence: float = 0.0
    supporting_facts: list[str] = Field(default_factory=list)
    supporting_fact_details: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_plan: dict[str, Any] = Field(default_factory=dict)
    retrieval_stage_debug: dict[str, Any] = Field(default_factory=dict)
    rerank_debug: list[dict[str, Any]] = Field(default_factory=list)
    evidence_fact_hits: list[str] = Field(default_factory=list)
    answer_debug: dict[str, Any] = Field(default_factory=dict)
    context_sources: list[str] = Field(default_factory=list)
    workspace_memory_hits: int = 0
    transcript_hits: int = 0
    user_model_used: bool = False
    memory_context: dict[str, Any] = Field(default_factory=dict)
    actor_context: dict[str, Any] = Field(default_factory=dict)
    permission_decision: dict[str, Any] = Field(default_factory=dict)
    rate_limit_decision: dict[str, Any] = Field(default_factory=dict)
    queue_status: dict[str, Any] = Field(default_factory=dict)
    task_mode: str = "sync"


class EnterpriseRagIngestRequest(BaseModel):
    mode: Literal["sample", "full"] = "sample"
    documents_path: str | None = None
    questions_path: str | None = None
    limit: int = Field(default=200, ge=1, le=100_000)
    reset: bool = False
    async_mode: bool = False
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    roles: list[str] = Field(default_factory=list)


class EnterpriseRagIngestResponse(BaseModel):
    dataset: str
    mode: str
    documents_path: str
    questions_path: str = ""
    documents_seen: int = 0
    questions_seen: int = 0
    chunks_indexed: int = 0
    enterprise_collection: str = ""
    sparse_index: str = ""
    reset: bool = False
    actor_context: dict[str, Any] = Field(default_factory=dict)
    permission_decision: dict[str, Any] = Field(default_factory=dict)
    rate_limit_decision: dict[str, Any] = Field(default_factory=dict)
    queue_status: dict[str, Any] = Field(default_factory=dict)
    task_mode: str = "sync"
    task_id: str = ""


class EnterpriseRagBenchmarkResponse(BaseModel):
    cases: list[dict[str, Any]] = Field(default_factory=list)
    average_doc_recall: float = 0.0
    average_answer_fact_coverage: float = 0.0
    average_evidence_fact_coverage: float = 0.0
    actor_context: dict[str, Any] = Field(default_factory=dict)
    permission_decision: dict[str, Any] = Field(default_factory=dict)
    rate_limit_decision: dict[str, Any] = Field(default_factory=dict)
    queue_status: dict[str, Any] = Field(default_factory=dict)
    task_mode: str = "sync"
    task_id: str = ""


class InboundMailSyncResponse(BaseModel):
    enabled: bool
    ok: bool = False
    synced: int = 0
    new: int = 0
    mailbox: str = "INBOX"
    error: str = ""
    state: dict[str, Any] = Field(default_factory=dict)


class InboundDraftReplyResponse(BaseModel):
    message: InboundMailMessage
    draft_reply: str
    requires_dlp_before_send: bool = True


class NotificationOutboxItem(BaseModel):
    notification_id: str
    event_type: str
    title: str
    body: str = ""
    payload_json: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    created_at: str
    delivered_at: str = ""


class ConversationCreateRequest(BaseModel):
    session_id: str = Field(min_length=1)
    title: str | None = None
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    roles: list[str] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    conversation_id: str
    session_id: str
    title: str
    summary: str = ""
    created_at: str
    updated_at: str
    is_merged: bool = False
    source_conversation_ids: list[str] = Field(default_factory=list)
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""


class ConversationTurn(BaseModel):
    turn_id: str
    conversation_id: str
    session_id: str
    role: str
    content: str
    redacted_content: str = ""
    answer_summary: str = ""
    intent: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    debug_payload: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""


class SensitiveWorkflowCreateRequest(BaseModel):
    session_id: str = Field(min_length=1)
    conversation_id: str | None = None
    message: str = Field(min_length=1)
    business_context: str = ""
    recipient_type: str = ""
    context_budget: int = Field(default=600, ge=100, le=5_000)
    destination_email: str = ""
    source_filename: str = ""
    source_content_type: str = ""
    requested_action: str = "summarize_and_send"


class SensitiveWorkflowApprovalRequest(BaseModel):
    actor: str = Field(default="local_reviewer", min_length=1)
    reason: str = ""


class SensitiveWorkflowResponse(BaseModel):
    workflow_id: str
    session_id: str
    conversation_id: str = ""
    workflow_type: str = "dlp_outbound_approval"
    status: str
    message: str
    redacted_text: str
    business_context: str = ""
    recipient_type: str = ""
    destination_email: str = ""
    source_filename: str = ""
    source_content_type: str = ""
    source_text_preview: str = ""
    requested_action: str = "summarize_and_send"
    risk_level: str
    risk_reasons: list[str] = Field(default_factory=list)
    redactions: list[dict[str, Any]] = Field(default_factory=list)
    proposed_action: str = ""
    final_result: str = ""
    draft_summary: str = ""
    simulated_delivery_result: str = ""
    delivery_status: str = "not_sent"
    delivery_result: str = ""
    delivery_error: str = ""
    sent_at: str = ""
    smtp_provider: str = ""
    approval_required: bool = False
    approved_by: str = ""
    rejected_by: str = ""
    rejection_reason: str = ""
    created_at: str
    updated_at: str
    audit_events: list[dict[str, Any]] = Field(default_factory=list)


class DlpTaskCreateRequest(BaseModel):
    session_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    message: str = ""
    request_message: str = ""
    review_content: str = ""
    resolved_outbound_content: str = ""
    resolved_source_kind: str = ""
    delivery_subject: str = ""
    delivery_body: str = ""
    delivery_plan_kind: str = ""
    attachment_strategy: str = "none"
    attachment_content: str = ""
    attachment_filename: str = ""
    attachment_content_type: str = ""
    attachment_blob_id: str = ""
    destination_email: str = ""
    uploaded_filename: str = ""
    uploaded_content_type: str = ""
    uploaded_text: str = ""
    uploaded_file_base64: str = ""
    source_parse_status: str = "not_provided"
    source_parse_error: str = ""
    requested_action: str = "summarize_and_send"
    lab_run: bool = False
    scenario_id: str = ""
    scenario_name: str = ""
    fault_injection: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    roles: list[str] = Field(default_factory=list)


class DlpTaskApprovalRequest(BaseModel):
    actor: str = Field(default="local_reviewer", min_length=1)
    reason: str = ""
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    roles: list[str] = Field(default_factory=list)


class DlpTaskSupplementRequest(BaseModel):
    session_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    message: str = ""
    destination_email: str = ""
    uploaded_filename: str = ""
    uploaded_content_type: str = ""
    uploaded_text: str = ""
    uploaded_file_base64: str = ""
    source_parse_status: str = "not_provided"
    source_parse_error: str = ""


class DlpTaskEvent(BaseModel):
    event_id: str | None = None
    task_id: str
    event_type: str
    actor: str = ""
    details_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class DlpTaskApproval(BaseModel):
    approval_id: str
    task_id: str
    action: str
    actor: str = ""
    reason: str = ""
    created_at: str


class DlpTaskResponse(BaseModel):
    task_id: str
    task_type: str = "dlp_outbound"
    tenant_id: str = ""
    user_id: str = ""
    workspace_id: str = ""
    session_id: str
    conversation_id: str = ""
    priority: int = 0
    status: str
    domain_action: str = ""
    domain_payload: dict[str, Any] = Field(default_factory=dict)
    domain_result: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = ""
    approval_required: bool = False
    destination_email: str = ""
    requested_action: str = "summarize_and_send"
    message_raw: str
    request_message: str = ""
    delivery_subject: str = ""
    delivery_body: str = ""
    delivery_plan_kind: str = ""
    resolved_source_kind: str = ""
    attachment_strategy: str = "none"
    attachment_filename: str = ""
    attachment_content_type: str = ""
    attachment_blob_id: str = ""
    message_redacted: str = ""
    source_filename: str = ""
    source_content_type: str = ""
    source_parse_status: str = "not_provided"
    source_parse_error: str = ""
    draft_summary: str = ""
    risk_reasons: list[str] = Field(default_factory=list)
    redactions: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)
    delivery_status: str = "not_sent"
    delivery_result: str = ""
    delivery_error: str = ""
    smtp_provider: str = ""
    sent_at: str = ""
    final_result: str = ""
    degradation_mode: str = ""
    fallback_reason: str = ""
    manual_handover_required: bool = False
    next_recommended_action: str = ""
    last_error_category: str = ""
    entry_issue_type: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str = ""
    retrieval_status: str = ""
    summary_status: str = ""
    lab_run: bool = False
    scenario_id: str = ""
    scenario_name: str = ""
    fault_injection: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    status_path: list[str] = Field(default_factory=list)
    scenario_evaluation: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    audit_events: list[DlpTaskEvent] = Field(default_factory=list)
    approvals: list[DlpTaskApproval] = Field(default_factory=list)


class DlpScenarioDefinition(BaseModel):
    scenario_id: str
    name: str
    category: str
    description: str
    message: str
    uploaded_filename: str = ""
    uploaded_content_type: str = ""
    uploaded_text: str = ""
    destination_email: str = ""
    requested_action: str = "summarize_and_send"
    fault_injection: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)


class DlpScenarioReplayRequest(BaseModel):
    session_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    destination_email: str = ""
    fault_injection: dict[str, Any] | None = None


class ConversationMergeRequest(BaseModel):
    session_id: str = Field(min_length=1)
    conversation_ids: list[str] = Field(min_length=2)
    merge_name: str | None = None


class ConversationMergeResponse(BaseModel):
    merged_conversation_id: str
    summary: str
    topics: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    source_conversations: list[str]
    source_turn_ids: list[str]
    written_to_vectorstore: bool
