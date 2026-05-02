from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityDoc:
    doc_id: str
    entity_type: str
    title: str
    text: str
    keywords: tuple[str, ...]


APPLE_CORPUS = [
    EntityDoc(
        "apple-inc-overview",
        "company",
        "Apple Inc.",
        "Apple Inc. 是消费电子与软件服务公司，相关实体包括 iPhone、Mac、iPad、iOS、App Store、芯片、公司业务和股票表现。",
        ("iphone", "mac", "ipad", "公司", "股票", "手机", "ios", "市值", "芯片", "hardware"),
    ),
    EntityDoc(
        "iphone-product",
        "company",
        "iPhone",
        "iPhone 是 Apple 的智能手机产品线，相关问题通常涉及 iOS、App Store、拍照、芯片和生态体验。",
        ("iphone", "手机", "ios", "camera", "app store", "生态"),
    ),
    EntityDoc(
        "apple-fruit-overview",
        "fruit",
        "苹果水果",
        "苹果是一种水果，相关问题通常涉及营养、含糖量、热量、膳食纤维、种植、品种和食用建议。",
        ("水果", "营养", "含糖", "膳食纤维", "种植", "fruit", "nutrition", "吃"),
    ),
    EntityDoc(
        "apple-nutrition",
        "fruit",
        "苹果营养",
        "苹果含有膳食纤维、水分和天然糖分。营养类问题应检索水果实体，而不是 Apple 公司实体。",
        ("营养", "含糖量", "热量", "fiber", "sugar", "nutrition", "果糖"),
    ),
]


COMPANY_HINTS = ("iphone", "mac", "ipad", "ios", "手机", "公司", "股票", "市值", "app store", "芯片", "生态")
FRUIT_HINTS = ("水果", "营养", "含糖", "含糖量", "热量", "种植", "吃", "果糖", "fiber", "nutrition")


def detect_entity_type(query: str) -> tuple[str | None, bool, str]:
    lowered = query.lower()
    company_score = sum(1 for hint in COMPANY_HINTS if hint in lowered)
    fruit_score = sum(1 for hint in FRUIT_HINTS if hint in lowered)

    if company_score > fruit_score:
        return "company", False, "Query contains company, phone, product, or stock hints."
    if fruit_score > company_score:
        return "fruit", False, "Query contains fruit, nutrition, eating, or planting hints."
    return None, True, "The word apple is ambiguous and the query lacks enough entity hints."


def search_entity_docs(query: str, entity_type: str | None) -> list[EntityDoc]:
    candidates = [doc for doc in APPLE_CORPUS if entity_type is None or doc.entity_type == entity_type]
    lowered = query.lower()
    return sorted(
        candidates,
        key=lambda doc: sum(1 for keyword in doc.keywords if keyword.lower() in lowered),
        reverse=True,
    )[:3]


def answer_apple_query(query: str) -> dict:
    entity_type, needs_clarification, reason = detect_entity_type(query)
    if needs_clarification:
        return {
            "entity_type": None,
            "needs_clarification": True,
            "clarification_question": "你说的 apple 是指 Apple 公司/手机，还是苹果这种水果？",
            "filter_used": None,
            "retrieved_docs": [],
            "analysis": "RAG should clarify the entity before retrieval when the query is underspecified.",
            "answer": "当前问题存在实体歧义，应该先澄清实体再检索。",
            "reason": reason,
        }

    docs = search_entity_docs(query, entity_type)
    return {
        "entity_type": entity_type,
        "needs_clarification": False,
        "clarification_question": None,
        "filter_used": {"entity_type": entity_type},
        "retrieved_docs": [
            {"doc_id": doc.doc_id, "entity_type": doc.entity_type, "title": doc.title, "snippet": doc.text}
            for doc in docs
        ],
        "analysis": f"Use metadata filter entity_type={entity_type} before retrieval.",
        "answer": f"Query routed to {entity_type} documents.",
        "reason": reason,
    }
