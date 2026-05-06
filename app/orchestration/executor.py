from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.orchestration.registry import build_tool_executor_map
from app.orchestration.types import AgentTaskPlan, OrchestrationContext, ToolResult


def execute_task_plan(plan: AgentTaskPlan, context: OrchestrationContext) -> list[ToolResult]:
    executors = build_tool_executor_map()
    pending = {item.task_id: item for item in plan.subtasks}
    results: dict[str, ToolResult] = {}

    while pending:
        ready = [
            item
            for item in pending.values()
            if all(dep in results for dep in item.dependencies)
        ]
        if not ready:
            for item in pending.values():
                results[item.task_id] = ToolResult(
                    task_id=item.task_id,
                    capability=item.capability,
                    success=False,
                    error="Unresolved subtask dependencies.",
                    status="failed",
                )
            break
        parallel_batch = [item for item in ready if item.parallelizable and not item.mutating]
        serial_batch = [item for item in ready if item not in parallel_batch]
        if parallel_batch:
            with ThreadPoolExecutor(max_workers=min(4, len(parallel_batch))) as pool:
                futures = {
                    pool.submit(_execute_one, item, context, results, executors): item.task_id
                    for item in parallel_batch
                }
                for future in futures:
                    result = future.result()
                    results[result.task_id] = result
                    pending.pop(result.task_id, None)
        for item in serial_batch:
            result = _execute_one(item, context, results, executors)
            results[result.task_id] = result
            pending.pop(result.task_id, None)
    return [results[item.task_id] for item in plan.subtasks if item.task_id in results]


def _execute_one(subtask, context: OrchestrationContext, prior_results: dict[str, ToolResult], executors: dict[str, Any]) -> ToolResult:
    handler = executors.get(subtask.capability)
    if not handler:
        return ToolResult(
            task_id=subtask.task_id,
            capability=subtask.capability,
            success=False,
            error=f"Unsupported capability: {subtask.capability}",
            status="failed",
        )
    dependency_payloads = {
        dep_id: prior_results[dep_id].payload
        for dep_id in subtask.dependencies
        if dep_id in prior_results
    }
    try:
        payload = handler(subtask.input, context, dependency_payloads) or {}
        error = str(payload.get("error") or "")
        success = not error
        return ToolResult(
            task_id=subtask.task_id,
            capability=subtask.capability,
            success=success,
            payload=payload,
            error=error,
            citations=list(payload.get("citations") or []),
            missing_evidence=bool(payload.get("missing_evidence", False)),
            status="completed" if success else "failed",
        )
    except Exception as exc:
        return ToolResult(
            task_id=subtask.task_id,
            capability=subtask.capability,
            success=False,
            error=str(exc),
            status="failed",
        )
