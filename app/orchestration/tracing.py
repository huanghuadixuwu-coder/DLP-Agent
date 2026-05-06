from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.orchestration.types import AgentTaskPlan, ToolResult


def render_task_plan(plan: AgentTaskPlan) -> dict[str, Any]:
    return {
        "original_message": plan.original_message,
        "planner_type": plan.planner_type,
        "planner_reason": plan.planner_reason,
        "confidence": plan.confidence,
        "aggregation_strategy": plan.aggregation_strategy,
        "subtasks": [asdict(item) for item in plan.subtasks],
    }


def render_tool_results(results: list[ToolResult]) -> list[dict[str, Any]]:
    return [asdict(item) for item in results]
