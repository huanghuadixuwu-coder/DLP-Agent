from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class ContentCandidate:
    source_type: str
    content: str
    provenance_span: tuple[int, int]
    confidence: float
    allowed_uses: tuple[str, ...]
    parser_source: str = "deterministic_current_message"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provenance_span"] = list(self.provenance_span)
        payload["allowed_uses"] = list(self.allowed_uses)
        return payload


QUOTE_PAIRS = (("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'"), ("`", "`"))
INLINE_LABEL_PATTERN = re.compile(
    r"(?:文段|正文|内容|文本|message|text|content)\s*(?:是|为|如下|如下内容)?\s*(?:[:：=])?\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
TRAILING_CONTEXT_PATTERN = re.compile(
    r"(?:的?内容)?\s*(?:发送到|发给|寄给|to)\s*[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}.*$",
    re.IGNORECASE | re.DOTALL,
)


def parse_message_content_candidates(message: str) -> list[dict[str, Any]]:
    """Extract explicit current-turn body text as structured candidates.

    The parser only identifies source boundaries. It does not decide whether an
    email should be sent, whether a draft is safe, or how the final user-visible
    clarification should be worded.
    """

    text = str(message or "")
    candidates: list[ContentCandidate] = []
    seen: set[str] = set()

    for content, span, confidence in _iter_quoted_segments(text):
        _append_candidate(candidates, seen, content, span, confidence)

    if not candidates:
        label_match = INLINE_LABEL_PATTERN.search(text)
        if label_match:
            raw = str(label_match.group(1) or "")
            start = label_match.start(1)
            content = _strip_trailing_context(raw)
            if content:
                end = start + len(content)
                _append_candidate(candidates, seen, content, (start, end), 0.86)

    return [item.to_dict() for item in candidates]


def _iter_quoted_segments(text: str) -> list[tuple[str, tuple[int, int], float]]:
    segments: list[tuple[str, tuple[int, int], float]] = []
    for opener, closer in QUOTE_PAIRS:
        start = 0
        while start < len(text):
            left = text.find(opener, start)
            if left < 0:
                break
            right = text.find(closer, left + len(opener))
            if right < 0:
                break
            if opener == closer and right == left:
                right = text.find(closer, left + 1)
                if right < 0:
                    break
            content_start = left + len(opener)
            content = text[content_start:right]
            segments.append((content, (content_start, right), 0.94))
            start = right + len(closer)
    return segments


def _append_candidate(
    candidates: list[ContentCandidate],
    seen: set[str],
    content: str,
    span: tuple[int, int],
    confidence: float,
) -> None:
    cleaned = _clean_inline_content(content)
    if not _is_valid_inline_content(cleaned):
        return
    normalized = cleaned.casefold()
    if normalized in seen:
        return
    seen.add(normalized)
    candidates.append(
        ContentCandidate(
            source_type="user_inline_text",
            content=cleaned,
            provenance_span=span,
            confidence=confidence,
            allowed_uses=("mail_body", "draft_body", "dlp_scan"),
        )
    )


def _strip_trailing_context(value: str) -> str:
    text = str(value or "").strip()
    text = TRAILING_CONTEXT_PATTERN.sub("", text).strip()
    return _clean_inline_content(text)


def _clean_inline_content(value: str) -> str:
    return str(value or "").strip().strip("“”‘’\"'`").strip()


def _is_valid_inline_content(value: str) -> bool:
    text = _clean_inline_content(value)
    if not text:
        return False
    if EMAIL_PATTERN.fullmatch(text):
        return False
    if len(text) > 4000:
        return False
    return True
