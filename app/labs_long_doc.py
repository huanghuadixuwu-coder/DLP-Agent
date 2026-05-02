from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


QueryScope = Literal["global", "local"]


@dataclass(frozen=True)
class LongDocPiece:
    piece_id: str
    layer: str
    title: str
    text: str
    keywords: tuple[str, ...]


def estimate_tokens(text: str) -> int:
    # A rough deterministic token estimate is enough for budget demos.
    return max(1, len(text) // 2)


SAMPLE_PIECES: list[LongDocPiece] = [
    LongDocPiece(
        "book-summary",
        "book_summary",
        "Agent/RAG handbook summary",
        "A long source should be represented by raw chunks, chunk summaries, chapter summaries, and a book-level summary.",
        ("book", "summary", "long", "context", "budget", "rag", "memory", "全书", "总结", "书"),
    ),
    LongDocPiece(
        "chapter-1-summary",
        "chapter_summary",
        "Chapter 1: Chunking",
        "Chunking splits a long book into stable passages with metadata such as chapter, section, topic, and citation id.",
        ("chunk", "metadata", "citation", "split", "切块", "元数据"),
    ),
    LongDocPiece(
        "chapter-2-summary",
        "chapter_summary",
        "Chapter 2: Hierarchical summary",
        "Hierarchical summaries compress chunks into section, chapter, and book summaries so global questions use compressed context.",
        ("hierarchical", "summary", "chapter", "global", "分层", "摘要", "章节"),
    ),
    LongDocPiece(
        "chapter-3-summary",
        "chapter_summary",
        "Chapter 3: Context budget",
        "A context budget allocator reserves room for instructions, user question, memory, global summary, local evidence, and citations.",
        ("budget", "allocator", "evidence", "citation", "预算", "证据", "引用"),
    ),
    LongDocPiece(
        "chunk-1",
        "raw_chunk",
        "Why direct stuffing fails",
        "A 1.5M-token book cannot fit into a 10K-token memory window. Store the source externally and retrieve only evidence needed by the current question.",
        ("1.5m", "10k", "chunk", "vector", "retrieve", "prompt", "上下文"),
    ),
    LongDocPiece(
        "chunk-2",
        "raw_chunk",
        "Global question strategy",
        "For broad questions, chapter summaries and a book summary are more useful than many raw chunks because they preserve global structure.",
        ("broad", "chapter", "book", "summary", "global", "整体", "框架"),
    ),
    LongDocPiece(
        "chunk-3",
        "raw_chunk",
        "Budget priority",
        "When context is limited, low-signal passages are dropped first. Keep direct evidence, summary context, and citation metadata.",
        ("context", "limited", "drop", "evidence", "metadata", "溢出", "压缩"),
    ),
]


def classify_long_doc_question(question: str) -> QueryScope:
    lowered = question.lower()
    global_markers = (
        "书",
        "全书",
        "整体",
        "总结",
        "主题",
        "框架",
        "1.5m",
        "10k",
        "memory",
        "rag",
        "global",
        "overall",
        "book",
    )
    return "global" if any(marker in lowered for marker in global_markers) else "local"


def score_piece(piece: LongDocPiece, question: str) -> int:
    lowered = question.lower()
    return sum(1 for keyword in piece.keywords if keyword.lower() in lowered)


def retrieve_pieces(question: str, scope: QueryScope, preferred_layer: str = "auto") -> list[LongDocPiece]:
    if preferred_layer != "auto":
        candidates = [piece for piece in SAMPLE_PIECES if piece.layer == preferred_layer]
    elif scope == "global":
        candidates = [piece for piece in SAMPLE_PIECES if piece.layer in {"book_summary", "chapter_summary", "raw_chunk"}]
    else:
        candidates = [piece for piece in SAMPLE_PIECES if piece.layer in {"raw_chunk", "chapter_summary", "book_summary"}]

    return sorted(candidates, key=lambda piece: (score_piece(piece, question), piece.layer != "raw_chunk"), reverse=True)


def allocate_context(question: str, budget_tokens: int, preferred_layer: str = "auto") -> dict:
    scope = classify_long_doc_question(question)
    fixed_budget = {
        "system_prompt": 700,
        "question": estimate_tokens(question),
        "answer_margin": 1200,
        "citations": 500,
    }
    available = max(500, budget_tokens - sum(fixed_budget.values()))
    selected: list[LongDocPiece] = []
    used_context_tokens = 0

    for piece in retrieve_pieces(question, scope, preferred_layer):
        piece_tokens = estimate_tokens(piece.text)
        if used_context_tokens + piece_tokens > available:
            continue
        selected.append(piece)
        used_context_tokens += piece_tokens

    layer_counts: dict[str, int] = {}
    for piece in selected:
        layer_counts[piece.layer] = layer_counts.get(piece.layer, 0) + 1

    analysis = {
        "strategy": "hierarchical_retrieval_with_context_budget",
        "question_scope": scope,
        "why_not_full_book": "The source is larger than the context window, so the system stores layers and retrieves evidence dynamically.",
        "recommended_pipeline": [
            "split source into semantic chunks",
            "create chunk, chapter, and book summaries",
            "store raw chunks and summaries with metadata",
            "classify the user question",
            "retrieve the right evidence layer",
            "pack context by budget and answer with citations",
        ],
    }

    return {
        "question_scope": scope,
        "budget_tokens": budget_tokens,
        "used_tokens": sum(fixed_budget.values()) + used_context_tokens,
        "available_context_tokens": available,
        "allocation": fixed_budget | {"retrieved_context": used_context_tokens},
        "retrieved_layers": layer_counts,
        "citations": [
            {"chunk_id": piece.piece_id, "layer": piece.layer, "title": piece.title, "snippet": piece.text[:220]}
            for piece in selected
        ],
        "analysis": analysis,
        "answer": "Use hierarchical retrieval with context budgeting; do not place the entire long source directly into the prompt.",
        "why_not_full_book": analysis["why_not_full_book"],
    }
