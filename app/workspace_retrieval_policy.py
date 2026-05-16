from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


@dataclass(slots=True)
class WorkspaceRetrievalPlan:
    strategy: str
    top_k: int
    filters: dict[str, Any] = field(default_factory=dict)
    enable_fts: bool = True
    enable_vector: bool = False
    sync_mode: str = "incremental"
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DOC_ROLE_MARKERS: dict[str, tuple[str, ...]] = {
    "project_rules": (
        "readme",
        "todolist",
        "requirements",
        "\u603b\u4f53\u8981\u6c42",
    ),
    "prompt_policy": (
        "prompt",
        "policy",
        "config",
        "\u7b56\u7565",
        "\u63d0\u793a\u8bcd",
    ),
    "memory_note": (
        "memory",
        "notes",
        "bootstrap",
        "skills",
        "\u8bb0\u5fc6",
        "\u6458\u8981",
    ),
    "architecture": (
        "architecture",
        "rag",
        "agent",
        "mcp",
        "\u67b6\u6784",
        "\u8bbe\u8ba1",
    ),
}

EXACT_QUERY_MARKERS = (
    "readme",
    "todolist",
    "\u603b\u4f53\u8981\u6c42",
    ".md",
    "memory/",
)

SEMANTIC_QUERY_MARKERS = (
    "why",
    "how",
    "architecture",
    "design",
    "tradeoff",
    "compare",
    "rag",
    "agent",
    "memory",
    "\u4e3a\u4ec0\u4e48",
    "\u600e\u4e48",
    "\u5982\u4f55",
    "\u67b6\u6784",
    "\u8bbe\u8ba1",
)

FTS_QUERY_MARKERS = (
    "prompt",
    "policy",
    "config",
    "requirement",
    "todo",
    "note",
    "\u7b56\u7565",
    "\u89c4\u5b9a",
    "\u8981\u6c42",
)


def infer_workspace_doc_role(file_path: str, title: str = "") -> str:
    haystack = f"{file_path} {title}".lower()
    for role, markers in DOC_ROLE_MARKERS.items():
        if any(marker in haystack for marker in markers):
            return role
    return "general"


def build_workspace_retrieval_plan(
    question: str,
    *,
    top_k: int = 6,
    filters: dict[str, Any] | None = None,
) -> WorkspaceRetrievalPlan:
    text = (question or "").strip()
    lowered = text.lower()
    requested_top_k = max(1, int(top_k or 6))
    reason_codes: list[str] = []
    plan_filters = dict(filters or {})

    if any(marker in lowered for marker in EXACT_QUERY_MARKERS):
        reason_codes.append("exact_workspace_artifact")
        return WorkspaceRetrievalPlan(
            strategy="exact_path",
            top_k=min(requested_top_k, 4),
            filters=plan_filters,
            enable_fts=True,
            enable_vector=False,
            reason_codes=reason_codes,
        )

    if any(marker in lowered for marker in FTS_QUERY_MARKERS):
        reason_codes.append("metadata_or_keyword_query")
        return WorkspaceRetrievalPlan(
            strategy="fts_first",
            top_k=min(max(requested_top_k, 4), 8),
            filters=plan_filters,
            enable_fts=True,
            enable_vector=False,
            reason_codes=reason_codes,
        )

    if any(marker in lowered or marker in text for marker in SEMANTIC_QUERY_MARKERS):
        reason_codes.append("semantic_workspace_query")
        return WorkspaceRetrievalPlan(
            strategy="hybrid",
            top_k=min(max(requested_top_k, 6), 10),
            filters=plan_filters,
            enable_fts=True,
            enable_vector=True,
            reason_codes=reason_codes,
        )

    if len(re.findall(r"\w+", lowered)) <= 4:
        reason_codes.append("short_keyword_query")
        return WorkspaceRetrievalPlan(
            strategy="fts_first",
            top_k=min(max(requested_top_k, 4), 6),
            filters=plan_filters,
            enable_fts=True,
            enable_vector=False,
            reason_codes=reason_codes,
        )

    reason_codes.append("bounded_default_hybrid")
    return WorkspaceRetrievalPlan(
        strategy="hybrid",
        top_k=min(max(requested_top_k, 4), 6),
        filters=plan_filters,
        enable_fts=True,
        enable_vector=True,
        reason_codes=reason_codes,
    )
