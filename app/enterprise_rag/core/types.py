from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ENTERPRISE_DOMAIN = "enterprise_knowledge"
ENTERPRISE_COLLECTION_VERSION = "enterprise_rag_bench_v2"

SourceType = Literal[
    "slack",
    "gmail",
    "google_drive",
    "confluence",
    "linear",
    "hubspot",
    "fireflies",
    "github",
    "jira",
    "unknown",
]


@dataclass(slots=True)
class EnterpriseDocument:
    doc_id: str
    source_type: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EnterpriseChunk:
    chunk_id: str
    doc_id: str
    source_type: str
    title: str
    content: str
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EnterpriseQuestion:
    question_id: str
    question_type: str
    source_types: list[str]
    question: str
    expected_doc_ids: list[str]
    gold_answer: str = ""
    answer_facts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RetrievalPlan:
    query: str
    source_types: list[str] = field(default_factory=list)
    question_type: str = "basic"
    budget_profile: str = "medium"
    dense_top_k: int = 40
    sparse_top_k: int = 20
    rerank_candidate_top_k: int = 18
    rerank_top_k: int = 8
    evidence_top_k: int = 5
    require_evidence: bool = True
    expansion_enabled: bool = True
    tenant_id: str = ""
    workspace_id: str = ""


@dataclass(slots=True)
class EnterpriseCitation:
    doc_id: str
    chunk_id: str
    source_type: str
    title: str
    snippet: str
    score: float = 0.0
    retrieval_source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SupportingFact:
    doc_id: str
    chunk_id: str
    sentence_text: str
    score: float = 0.0
    source_type: str = ""
    title: str = ""
    speaker: str = ""
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CanonicalFact:
    fact_id: str
    fact_type: str
    normalized_fact: str
    priority: str = "core"
    source_fact_ids: list[str] = field(default_factory=list)
    score: float = 0.0
    source_type: str = ""
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AnswerPlan:
    question_type: str
    direct_answer: str
    key_points: list[str] = field(default_factory=list)
    optional_context: str = ""
    excluded_content: list[str] = field(default_factory=list)
    style_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidencePack:
    query: str
    citations: list[EnterpriseCitation]
    supporting_doc_ids: list[str]
    missing_evidence: bool
    confidence: float
    supporting_facts: list[str] = field(default_factory=list)
    supporting_fact_details: list[SupportingFact] = field(default_factory=list)
    canonical_facts: list[CanonicalFact] = field(default_factory=list)
    excluded_facts: list[CanonicalFact] = field(default_factory=list)
    retrieval_stage_debug: dict[str, Any] = field(default_factory=dict)
    rerank_debug: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class EnterpriseRagAnswer:
    answer: str
    citations: list[EnterpriseCitation]
    supporting_doc_ids: list[str]
    missing_evidence: bool
    confidence: float
    supporting_facts: list[str] = field(default_factory=list)
    supporting_fact_details: list[SupportingFact] = field(default_factory=list)
    canonical_facts: list[CanonicalFact] = field(default_factory=list)
    retrieval_stage_debug: dict[str, Any] = field(default_factory=dict)
    rerank_debug: list[dict[str, Any]] = field(default_factory=list)
    evidence_fact_hits: list[str] = field(default_factory=list)
    answer_debug: dict[str, Any] = field(default_factory=dict)
    context_sources: list[str] = field(default_factory=list)
    workspace_memory_hits: int = 0
    transcript_hits: int = 0
    user_model_used: bool = False


@dataclass(slots=True)
class AgentSubTask:
    task_id: str
    capability: str
    input: dict[str, Any]
    dependencies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentTaskPlan:
    original_message: str
    subtasks: list[AgentSubTask]
    aggregation_strategy: str = "answer_all_parts"
