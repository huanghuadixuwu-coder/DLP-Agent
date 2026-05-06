from __future__ import annotations

import re
from typing import Callable, Iterable

from app.config import get_settings
from app.enterprise_rag.core.types import ENTERPRISE_COLLECTION_VERSION, ENTERPRISE_DOMAIN, EnterpriseChunk, EnterpriseDocument
from app.enterprise_rag.libs.metadata import chroma_safe_metadata
from app.enterprise_rag.libs.text_cleaning import clean_text


SOURCE_CHUNK_SIZES = {
    "gmail": 1100,
    "slack": 900,
    "linear": 900,
    "jira": 900,
    "github": 1000,
    "confluence": 1400,
    "google_drive": 1400,
    "fireflies": 1400,
    "hubspot": 1000,
}

FIRELIES_TURN_RE = re.compile(
    r"(?P<turn>\[\d{1,2}:\d{2}(?::\d{2})?\]\s*[^:\n]{1,80}:\s*.*?)(?=(?:\n\[\d{1,2}:\d{2}(?::\d{2})?\]\s*[^:\n]{1,80}:)|\Z)",
    re.S,
)
FIRELIES_META_RE = re.compile(r"^\[(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?P<speaker>[^:\n]{1,80}):\s*(?P<body>.*)$", re.S)
MAIL_QUOTE_SPLIT_RE = re.compile(r"\n(?=(?:On .+ wrote:|From: .+|> ))", re.I)
SLACK_MESSAGE_RE = re.compile(
    r"(?P<msg>(?:\[[^\]\n]{3,40}\]\s*)?(?:[A-Za-z0-9_. -]{1,80}|<@[^>\n]+>|@[A-Za-z0-9_.-]+):\s*.*?)(?=(?:\n(?:\[[^\]\n]{3,40}\]\s*)?(?:[A-Za-z0-9_. -]{1,80}|<@[^>\n]+>|@[A-Za-z0-9_.-]+):\s)|\Z)",
    re.S,
)
HEADING_RE = re.compile(r"^(#{1,6}\s+.+|[A-Z][A-Za-z0-9 /&()_-]{2,80}\n[-=]{3,}|[A-Z][A-Za-z0-9 /&()_-]{2,80}:)$", re.M)


def chunk_document(document: EnterpriseDocument, *, overlap: int = 120) -> list[EnterpriseChunk]:
    raw_content = str(document.content or "").replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not document.doc_id or not raw_content:
        return []

    source_type = document.source_type or "unknown"
    if source_type == "fireflies":
        chunks = _chunk_fireflies(document, raw_content)
        if chunks:
            return chunks
    if source_type == "gmail":
        chunks = _chunk_gmail(document, raw_content)
        if chunks:
            return chunks
    if source_type == "slack":
        chunks = _chunk_slack(document, raw_content)
        if chunks:
            return chunks
    if source_type in {"confluence", "google_drive"}:
        chunks = _chunk_structured_sections(document, raw_content)
        if chunks:
            return chunks

    return _fixed_window_chunks(document, clean_text(raw_content), overlap=overlap, strategy="fixed_window")


def _chunk_fireflies(document: EnterpriseDocument, raw_content: str) -> list[EnterpriseChunk]:
    turns = []
    for match in FIRELIES_TURN_RE.finditer(raw_content):
        turn_text = clean_text(match.group("turn"), preserve_structure=True)
        parsed = FIRELIES_META_RE.match(turn_text)
        speaker = parsed.group("speaker").strip() if parsed else ""
        timestamp = parsed.group("ts").strip() if parsed else ""
        body = clean_text(parsed.group("body"), preserve_structure=True) if parsed else turn_text
        turns.append({"speaker": speaker, "timestamp": timestamp, "body": body, "raw": turn_text})
    if len(turns) < 2:
        return []

    chunks: list[EnterpriseChunk] = []
    current_turns: list[dict[str, str]] = []
    max_chars = 1350
    for turn in turns:
        current_turns.append(turn)
        joined = "\n".join(item["raw"] for item in current_turns)
        if len(joined) >= max_chars or len(current_turns) >= 4:
            chunks.append(
                _build_structured_chunk(
                    document,
                    len(chunks),
                    joined,
                    strategy="fireflies_turns",
                    extra_metadata={
                        "speaker": current_turns[0].get("speaker", ""),
                        "turn_start": current_turns[0].get("timestamp", ""),
                        "turn_end": current_turns[-1].get("timestamp", ""),
                    },
                )
            )
            current_turns = current_turns[-1:]
    if current_turns:
        joined = "\n".join(item["raw"] for item in current_turns)
        chunks.append(
            _build_structured_chunk(
                document,
                len(chunks),
                joined,
                strategy="fireflies_turns",
                extra_metadata={
                    "speaker": current_turns[0].get("speaker", ""),
                    "turn_start": current_turns[0].get("timestamp", ""),
                    "turn_end": current_turns[-1].get("timestamp", ""),
                },
            )
        )
    return chunks


def _chunk_gmail(document: EnterpriseDocument, raw_content: str) -> list[EnterpriseChunk]:
    blocks = [clean_text(part, preserve_structure=True) for part in MAIL_QUOTE_SPLIT_RE.split(raw_content) if clean_text(part, preserve_structure=True)]
    if len(blocks) < 2:
        return []
    return _structured_groups(
        document,
        blocks,
        max_chars=1250,
        strategy="gmail_thread_blocks",
        metadata_builder=lambda group: {"message_id": str(document.metadata.get("thread_id") or document.doc_id)},
    )


def _chunk_slack(document: EnterpriseDocument, raw_content: str) -> list[EnterpriseChunk]:
    messages = [clean_text(match.group("msg"), preserve_structure=True) for match in SLACK_MESSAGE_RE.finditer(raw_content)]
    if len(messages) < 2:
        return []
    return _structured_groups(
        document,
        messages,
        max_chars=1000,
        strategy="slack_message_blocks",
    )


def _chunk_structured_sections(document: EnterpriseDocument, raw_content: str) -> list[EnterpriseChunk]:
    sections = list(_iter_heading_sections(raw_content))
    if len(sections) < 2:
        return []
    chunks: list[EnterpriseChunk] = []
    for section_path, body in sections:
        cleaned = clean_text(body, preserve_structure=True)
        if not cleaned:
            continue
        if len(cleaned) <= SOURCE_CHUNK_SIZES.get(document.source_type, 1400):
            chunks.append(
                _build_structured_chunk(
                    document,
                    len(chunks),
                    cleaned,
                    strategy="structured_sections",
                    extra_metadata={"section_path": section_path},
                )
            )
            continue
        chunks.extend(
            _fixed_window_chunks(
                document,
                clean_text(cleaned),
                overlap=160,
                strategy="structured_recursive_fallback",
                start_index=len(chunks),
                extra_metadata={"section_path": section_path},
            )
        )
    return chunks


def _structured_groups(
    document: EnterpriseDocument,
    blocks: list[str],
    *,
    max_chars: int,
    strategy: str,
    metadata_builder: Callable[[list[str]], dict[str, str]] | None = None,
) -> list[EnterpriseChunk]:
    chunks: list[EnterpriseChunk] = []
    group: list[str] = []
    for block in blocks:
        tentative = "\n\n".join(group + [block])
        if group and (len(tentative) > max_chars or len(group) >= 4):
            extra_metadata = metadata_builder(group) if metadata_builder else {}
            chunks.append(
                _build_structured_chunk(
                    document,
                    len(chunks),
                    "\n\n".join(group),
                    strategy=strategy,
                    extra_metadata=extra_metadata,
                )
            )
            group = group[-1:]
        group.append(block)
    if group:
        extra_metadata = metadata_builder(group) if metadata_builder else {}
        chunks.append(
            _build_structured_chunk(
                document,
                len(chunks),
                "\n\n".join(group),
                strategy=strategy,
                extra_metadata=extra_metadata,
            )
        )
    return chunks


def _iter_heading_sections(raw_content: str) -> Iterable[tuple[str, str]]:
    content = clean_text(raw_content, preserve_structure=True)
    matches = list(HEADING_RE.finditer(content))
    if not matches:
        paragraphs = [block.strip() for block in content.split("\n\n") if block.strip()]
        for index, paragraph in enumerate(paragraphs):
            yield (f"section_{index + 1}", paragraph)
        return
    for index, match in enumerate(matches):
        heading = clean_text(match.group(0))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        if body:
            yield (heading, body)


def _fixed_window_chunks(
    document: EnterpriseDocument,
    content: str,
    *,
    overlap: int,
    strategy: str,
    start_index: int = 0,
    extra_metadata: dict[str, str] | None = None,
) -> list[EnterpriseChunk]:
    settings = get_settings()
    if not content:
        return []
    chunk_size = SOURCE_CHUNK_SIZES.get(document.source_type, 1100)
    chunks: list[EnterpriseChunk] = []
    start = 0
    index = start_index
    while start < len(content):
        end = min(start + chunk_size, len(content))
        chunk_text = content[start:end].strip()
        if chunk_text:
            chunks.append(
                _build_chunk(
                    document,
                    index,
                    chunk_text,
                    settings=settings,
                    strategy=strategy,
                    extra_metadata=extra_metadata,
                )
            )
        if end >= len(content):
            break
        start = max(end - overlap, start + 1)
        index += 1
    return chunks


def _build_structured_chunk(
    document: EnterpriseDocument,
    index: int,
    content: str,
    *,
    strategy: str,
    extra_metadata: dict[str, str] | None = None,
) -> EnterpriseChunk:
    return _build_chunk(
        document,
        index,
        clean_text(content, preserve_structure=True),
        settings=get_settings(),
        strategy=strategy,
        extra_metadata=extra_metadata,
    )


def _build_chunk(
    document: EnterpriseDocument,
    index: int,
    content: str,
    *,
    settings,
    strategy: str,
    extra_metadata: dict[str, str] | None = None,
) -> EnterpriseChunk:
    chunk_id = f"enterprise:{document.doc_id}:{index}"
    metadata = {
        **document.metadata,
        **dict(extra_metadata or {}),
        "domain": ENTERPRISE_DOMAIN,
        "collection_version": settings.enterprise_collection_version or ENTERPRISE_COLLECTION_VERSION,
        "doc_id": document.doc_id,
        "chunk_id": chunk_id,
        "source_type": document.source_type,
        "title": document.title,
        "chunk_index": index,
        "chunk_strategy": strategy,
    }
    return EnterpriseChunk(
        chunk_id=chunk_id,
        doc_id=document.doc_id,
        source_type=document.source_type,
        title=document.title,
        content=content,
        chunk_index=index,
        metadata=chroma_safe_metadata(metadata),
    )
