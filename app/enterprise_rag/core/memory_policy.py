from __future__ import annotations


def enterprise_memory_policy() -> dict[str, str]:
    return {
        "conversation_memory": "Only conversation continuity and user-visible summaries.",
        "task_memory": "DLP task status, approvals, outbound delivery audit, and recoverable workflow context.",
        "enterprise_knowledge": "EnterpriseRAG-Bench indexed documents and citations; never overwrite with chat turns.",
    }
