from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AggregationStrategy = Literal["answer_all_parts", "compose_reply_with_context", "status_and_next_action"]
ReactActionType = Literal["call_tool", "read_memory", "direct_answer", "ask_clarification", "request_confirmation", "abort_with_reason"]
ReactTerminationReason = Literal["direct_answer", "needs_confirmation", "needs_clarification", "budget_exhausted", "fatal_tool_failure", "abort_with_reason"]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    mutating: bool = False
    parallelizable: bool = True
    input_schema: dict[str, str] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    when_to_use: list[str] = field(default_factory=list)
    reads_from: list[str] = field(default_factory=list)
    writes_to: list[str] = field(default_factory=list)
    read_only: bool = True
    side_effectful: bool = False
    requires_confirmation: bool = False
    safe_when: list[str] = field(default_factory=list)
    returns_observation_type: str = "generic"
    provider_constraints: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentSubtask:
    task_id: str
    capability: str
    input: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    mutating: bool = False
    parallelizable: bool = True
    user_visible: bool = True
    status: str = "pending"


@dataclass(slots=True)
class AgentTaskPlan:
    original_message: str
    subtasks: list[AgentSubtask]
    aggregation_strategy: AggregationStrategy = "answer_all_parts"
    planner_reason: str = ""
    confidence: float = 0.0
    planner_type: str = "heuristic"


@dataclass(slots=True)
class ToolResult:
    task_id: str
    capability: str
    success: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence: bool = False
    status: str = "completed"


@dataclass(slots=True)
class OrchestrationContext:
    session_id: str
    conversation_id: str
    message: str
    safe_message: str
    display_message: str = ""
    upload_context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReactTraceStep:
    step_index: int
    thought_summary: str
    action_type: ReactActionType
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    memory_kind: str = ""
    observation_summary: str = ""
    status: str = "planned"
    confidence: float = 0.0


@dataclass(slots=True)
class PendingConfirmation:
    action_name: str
    title: str
    message: str
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
