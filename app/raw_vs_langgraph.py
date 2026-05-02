from __future__ import annotations


def compare_raw_llm_and_langgraph(task: str) -> dict:
    return {
        "task": task,
        "comparison_dimensions": [
            {
                "dimension": "simple_call",
                "raw_llm": "Best for one-shot calls with minimal orchestration.",
                "langgraph": "Usually unnecessary for very small scripts.",
            },
            {
                "dimension": "stateful_workflow",
                "raw_llm": "State, branching, retries, and trace fields must be custom-built.",
                "langgraph": "State, nodes, edges, and routing are explicit.",
            },
            {
                "dimension": "tool_use",
                "raw_llm": "Tool schemas and execution loops are maintained manually.",
                "langgraph": "Tools and graph nodes can be organized as reusable workflow pieces.",
            },
            {
                "dimension": "observability",
                "raw_llm": "Callbacks, spans, and evaluation hooks need custom wiring.",
                "langgraph": "Node-level tracing and workflow debugging are easier to structure.",
            },
            {
                "dimension": "production_control",
                "raw_llm": "More flexible but more maintenance-heavy.",
                "langgraph": "More opinionated structure, useful when workflows grow.",
            },
        ],
        "balanced_position": "Choose raw SDK for simple calls; choose LangGraph when retrieval, tools, memory, branching, and observability matter.",
    }
