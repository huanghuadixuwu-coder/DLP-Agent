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
    answer_collapsed: bool = False
    reflection_notes: str | None = None
    workflow_id: str | None = None
    workflow_status: str | None = None
    workflow_risk_level: str | None = None
    delivery_status: str | None = None
    delivery_result: str | None = None
    delivery_error: str | None = None
    trace_id: str
    latency_ms: float
    token_in: int = 0
    token_out: int = 0
    estimated_cost: float = 0.0


class ConversationCreateRequest(BaseModel):
    session_id: str = Field(min_length=1)
    title: str | None = None


class ConversationSummary(BaseModel):
    conversation_id: str
    session_id: str
    title: str
    summary: str = ""
    created_at: str
    updated_at: str
    is_merged: bool = False
    source_conversation_ids: list[str] = Field(default_factory=list)


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


class SensitiveWorkflowCreateRequest(BaseModel):
    session_id: str = Field(min_length=1)
    conversation_id: str | None = None
    message: str = Field(min_length=1)
    business_context: str = ""
    recipient_type: str = ""
    context_budget: int = Field(default=600, ge=100, le=5_000)
    destination_email: str = "17388861183@163.com"
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
    destination_email: str = "17388861183@163.com"
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
