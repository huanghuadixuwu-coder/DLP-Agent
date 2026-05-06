from __future__ import annotations

import re


WHITESPACE_RE = re.compile(r"\s+")
INLINE_WHITESPACE_RE = re.compile(r"[^\S\r\n]+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\?\u3002\uff01\uff1f])\s+")


def clean_text(value: str, *, limit: int | None = None, preserve_structure: bool = False) -> str:
    raw = (value or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    if preserve_structure:
        lines = [INLINE_WHITESPACE_RE.sub(" ", line).strip() for line in raw.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    else:
        text = WHITESPACE_RE.sub(" ", raw).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip()
    return text


def snippet(value: str, limit: int = 480) -> str:
    text = clean_text(value)
    return text[:limit] + ("..." if len(text) > limit else "")


def query_focused_snippet(value: str, query: str, limit: int = 520) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text

    query_terms = [
        term.lower()
        for term in re.findall(r"[A-Za-z0-9_]{3,}", query or "")
        if term.strip()
    ][:24]
    if not query_terms:
        return snippet(text, limit=limit)

    sentences = [item.strip() for item in SENTENCE_SPLIT_RE.split(text) if item.strip()]
    if not sentences:
        return snippet(text, limit=limit)

    best_sentence = max(
        sentences,
        key=lambda sentence: sum(term in sentence.lower() for term in query_terms),
    )
    best_index = text.lower().find(best_sentence.lower())
    if best_index < 0:
        return snippet(text, limit=limit)

    start = max(0, best_index - (limit // 3))
    end = min(len(text), start + limit)
    if end - start < limit and start > 0:
        start = max(0, end - limit)
    focused = text[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{focused}{suffix}"
