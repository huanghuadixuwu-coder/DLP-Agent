from __future__ import annotations

import re

from app.enterprise_rag.core.types import AgentSubTask, AgentTaskPlan, RetrievalPlan
from app.enterprise_rag.libs.metadata import normalize_source_type


SOURCE_HINTS = {
    "gmail": ("gmail", "email", "邮件", "客户邮件", "来信"),
    "slack": ("slack", "频道", "聊天记录"),
    "linear": ("linear", "issue", "工单"),
    "jira": ("jira", "ticket", "缺陷"),
    "github": ("github", "pull request", "pr", "代码仓库"),
    "google_drive": ("google drive", "drive", "文档", "文件"),
    "confluence": ("confluence", "知识库", "wiki"),
    "hubspot": ("hubspot", "客户", "销售"),
    "fireflies": ("fireflies", "会议", "meeting"),
}

QUESTION_TYPE_HINTS = {
    "conflicting": ("conflicting", "contradict", "冲突", "不一致", "相互矛盾"),
    "constrained": ("default", "limit", "限制", "最大", "最小", "policy", "规定", "recommend"),
    "semantic": ("why", "how", "recommend", "建议", "原因", "处理", "flow", "onboarding"),
    "info_not_found": ("not found", "unknown", "有没有提到", "是否提到"),
}


def infer_source_types(message: str) -> list[str]:
    text = (message or "").lower()
    source_types = []
    for source_type, hints in SOURCE_HINTS.items():
        matched = False
        for hint in hints:
            normalized_hint = str(hint or "").strip().lower()
            if not normalized_hint:
                continue
            if len(normalized_hint) <= 2 and normalized_hint.isascii() and normalized_hint.isalpha():
                if re.search(rf"\b{re.escape(normalized_hint)}\b", text):
                    matched = True
                    break
            elif normalized_hint in text or normalized_hint in message:
                matched = True
                break
        if matched:
            source_types.append(source_type)
    return source_types


def infer_question_type(question: str) -> str:
    text = (question or "").lower()
    for label, hints in QUESTION_TYPE_HINTS.items():
        if any(hint in text or hint in question for hint in hints):
            return label
    if "?" in question or question.strip().startswith(("what", "which", "who", "when", "where", "how")):
        return "basic"
    return "semantic"


def build_retrieval_plan(question: str, *, source_types: list[str] | None = None, top_k: int = 8) -> RetrievalPlan:
    normalized_sources = [normalize_source_type(item) for item in (source_types or infer_source_types(question))]
    question_type = infer_question_type(question)
    rerank_top_k = min(max(top_k, 8), 12)
    evidence_top_k = min(max(4, top_k // 2), 6)
    dense_top_k = max(top_k * 5, 40)
    sparse_top_k = max(top_k * 2 + 4, 20)
    if question_type in {"constrained", "semantic"}:
        dense_top_k = max(dense_top_k, 60)
        sparse_top_k = max(sparse_top_k, 40)
        rerank_top_k = 16
        evidence_top_k = 8
    return RetrievalPlan(
        query=question,
        source_types=normalized_sources,
        question_type=question_type,
        dense_top_k=dense_top_k,
        sparse_top_k=sparse_top_k,
        rerank_top_k=rerank_top_k,
        evidence_top_k=evidence_top_k,
        require_evidence=True,
    )


def plan_compound_tasks(message: str) -> AgentTaskPlan:
    text = (message or "").lower()
    subtasks: list[AgentSubTask] = []
    if re.search(r"(发送|发出|发了|sent).*(邮件|mail|email)", message + text):
        subtasks.append(AgentSubTask(task_id="task_outbound_mail_summary", capability="outbound_mail_summary", input={}))
    if re.search(r"(收到|收了|收件|来信|inbox|unread).*(邮件|mail|email)", message + text):
        subtasks.append(AgentSubTask(task_id="task_inbound_mail_summary", capability="inbound_mail_summary", input={}))
    enterprise_hints = ("项目", "客户", "供应商", "合同", "工单", "文档", "知识库", "jira", "linear", "github", "slack", "confluence", "hubspot")
    if any(hint in message or hint in text for hint in enterprise_hints):
        subtasks.append(
            AgentSubTask(
                task_id="task_enterprise_rag_query",
                capability="enterprise_rag_query",
                input={"question": message, "source_types": infer_source_types(message)},
            )
        )
    if not subtasks:
        subtasks.append(
            AgentSubTask(
                task_id="task_enterprise_rag_query",
                capability="enterprise_rag_query",
                input={"question": message, "source_types": infer_source_types(message)},
            )
        )
    return AgentTaskPlan(original_message=message, subtasks=subtasks)
