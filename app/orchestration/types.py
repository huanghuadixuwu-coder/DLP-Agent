from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AggregationStrategy = Literal["answer_all_parts", "compose_reply_with_context", "status_and_next_action"]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    mutating: bool = False
    parallelizable: bool = True
    input_schema: dict[str, str] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)


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
