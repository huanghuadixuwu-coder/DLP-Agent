from __future__ import annotations

from collections import Counter
from typing import Any

from langchain_core.documents import Document

from app.vectorstore import get_vectorstore


UNIFIED_DOMAINS = ("long_doc", "privacy", "disambiguation", "framework")


def _doc(
    chunk_id: str,
    domain: str,
    intent: str,
    source_type: str,
    title: str,
    text: str,
    *,
    entity_type: str = "",
    sensitivity: str = "",
) -> Document:
    return Document(
        page_content=text,
        metadata={
            "chunk_id": chunk_id,
            "domain": domain,
            "intent": intent,
            "source_type": source_type,
            "title": title,
            "entity_type": entity_type,
            "sensitivity": sensitivity,
            "is_runtime_memory": False,
        },
    )


def build_unified_lab_documents() -> list[Document]:
    """Build static evidence docs used by the unified Agent tools."""
    return [
        _doc(
            "long-doc-book-summary",
            "long_doc",
            "long_document_budget",
            "book_summary",
            "长文档全局摘要策略",
            "1.5M token 级别的书不能直接塞进 10K memory 或 prompt。工程上应把原文放在外部知识库，建立原文 chunk、chunk 摘要、章节摘要、全书摘要多层索引，再按问题动态检索。",
        ),
        _doc(
            "long-doc-hierarchical-ingestion",
            "long_doc",
            "long_document_budget",
            "ingestion_strategy",
            "分层入库流程",
            "长文档 ingestion 应包含语义切块、章节结构保留、chunk 摘要、章节摘要、全局摘要、引用 ID 和 metadata。全局问题优先使用摘要层，局部问题优先使用原文证据。",
        ),
        _doc(
            "long-doc-context-budget",
            "long_doc",
            "long_document_budget",
            "budget_strategy",
            "10K 上下文预算分配",
            "上下文预算通常拆给系统提示词、用户问题、短期记忆、全局摘要、局部证据、引用和答案余量。超预算时优先保留直接证据和高层摘要，丢弃低相关闲聊或重复材料。",
        ),
        _doc(
            "long-doc-local-evidence",
            "long_doc",
            "long_document_budget",
            "raw_chunk",
            "局部证据检索",
            "如果问题问某个章节、概念或句子的细节，应检索原文 chunk 并附带章节 metadata，而不是只依赖全书摘要。RAG 的关键是把当前问题最需要的证据放进上下文。",
        ),
        _doc(
            "privacy-pii-redaction",
            "privacy",
            "privacy_alert",
            "privacy_policy",
            "PII 检测与脱敏",
            "敏感消息预警不能只靠 prompt。手机号、邮箱、身份证、银行卡、密码、token、api_key 等字段应先在本地规则层识别并脱敏，再把最小必要上下文送入模型。",
            sensitivity="high",
        ),
        _doc(
            "privacy-context-minimization",
            "privacy",
            "privacy_alert",
            "context_policy",
            "最小必要上下文",
            "处理隐私预警时，Agent 应只保留风险判断所需的证据片段、脱敏文本、风险标签和审计 metadata。上下文超预算时优先保留风险证据，舍弃闲聊和重复内容。",
            sensitivity="medium",
        ),
        _doc(
            "privacy-storage-boundary",
            "privacy",
            "privacy_alert",
            "storage_policy",
            "敏感数据存储边界",
            "向量库不应写入原始敏感文本。可写入的是脱敏摘要、风险类别、命中规则、处理时间和审计 ID。真实生产系统还需要权限、加密、留存周期和删除机制。",
            sensitivity="high",
        ),
        _doc(
            "privacy-alert-workflow",
            "privacy",
            "privacy_alert",
            "workflow_policy",
            "隐私预警 Workflow",
            "典型流程是输入消息、PII 检测、脱敏、风险分类、上下文打包、预警解释生成、审计记录。合规类任务更适合确定性 workflow，而不是完全自由的 Agent。",
            sensitivity="medium",
        ),
        _doc(
            "apple-inc-overview",
            "disambiguation",
            "entity_disambiguation",
            "entity_doc",
            "Apple Inc.",
            "Apple Inc. 是消费电子与软件服务公司，相关实体包括 iPhone、Mac、iPad、iOS、App Store、芯片、公司业务和股票表现。",
            entity_type="company",
        ),
        _doc(
            "apple-iphone-product",
            "disambiguation",
            "entity_disambiguation",
            "entity_doc",
            "iPhone 产品线",
            "当用户问 Apple 手机、iPhone、iOS、拍照、芯片或生态时，应限定到 company/product 实体并检索 Apple Inc. 与 iPhone 相关资料。",
            entity_type="company",
        ),
        _doc(
            "apple-fruit-overview",
            "disambiguation",
            "entity_disambiguation",
            "entity_doc",
            "苹果水果",
            "苹果是一种水果，相关问题通常涉及营养、含糖量、热量、膳食纤维、种植、品种和食用建议。",
            entity_type="fruit",
        ),
        _doc(
            "apple-fruit-nutrition",
            "disambiguation",
            "entity_disambiguation",
            "entity_doc",
            "苹果营养",
            "当用户问 apple 含糖量、营养、热量、膳食纤维、减脂或种植时，应限定到 fruit 实体，而不是召回 Apple 公司资料。",
            entity_type="fruit",
        ),
        _doc(
            "rag-disambiguation-policy",
            "disambiguation",
            "entity_disambiguation",
            "retrieval_policy",
            "RAG 歧义消解策略",
            "RAG 不能只靠向量相似度解决 apple 这类歧义词。应先做 query 理解和实体判断，再用 metadata filter 检索；如果上下文不足，应提出澄清问题。",
        ),
        _doc(
            "framework-raw-sdk",
            "framework",
            "framework_opinion",
            "comparison",
            "原生 SDK 的适用场景",
            "简单单轮大模型调用、轻量脚本或高度定制系统可以直接使用原生 SDK。优点是轻、自由、依赖少；缺点是状态流转、工具调用、观测和错误处理要自己搭。",
        ),
        _doc(
            "framework-langgraph",
            "framework",
            "framework_opinion",
            "comparison",
            "LangGraph 的适用场景",
            "当应用包含 RAG、工具调用、记忆、分支、人工确认、Reflection、监控和状态流转时，LangGraph 能把节点、边、状态和失败路径显式化，降低工程维护成本。",
        ),
        _doc(
            "framework-balanced-view",
            "framework",
            "framework_opinion",
            "position",
            "不站队的框架判断",
            "LangChain/LangGraph 不是所有场景都必须用，但也不是没用了。关键是按复杂度选工具：简单调用用原生 SDK，复杂 Agent workflow 用框架更容易组织和观测。",
        ),
    ]


def _merge_filters(filters: list[dict[str, Any]]) -> dict[str, Any] | None:
    clean = [item for item in filters if item]
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    return {"$and": clean}


def retrieve_unified_evidence(
    query: str,
    *,
    domain: str,
    top_k: int = 4,
    entity_type: str | None = None,
    source_type: str | None = None,
) -> list[Document]:
    filters: list[dict[str, Any]] = [{"domain": {"$eq": domain}}]
    if entity_type:
        filters.append({"entity_type": {"$eq": entity_type}})
    if source_type:
        filters.append({"source_type": {"$eq": source_type}})
    return get_vectorstore().similarity_search(query, k=top_k, filter=_merge_filters(filters))


def documents_to_evidence(documents: list[Document]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for doc in documents:
        evidence.append(
            {
                "chunk_id": str(doc.metadata.get("chunk_id", "")),
                "domain": str(doc.metadata.get("domain", "")),
                "intent": str(doc.metadata.get("intent", "")),
                "source_type": str(doc.metadata.get("source_type", "")),
                "title": str(doc.metadata.get("title", "")),
                "entity_type": str(doc.metadata.get("entity_type", "")),
                "sensitivity": str(doc.metadata.get("sensitivity", "")),
                "snippet": doc.page_content[:320],
            }
        )
    return evidence


def evidence_source_counts(evidence: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(item.get("source_type", "unknown")) for item in evidence))
