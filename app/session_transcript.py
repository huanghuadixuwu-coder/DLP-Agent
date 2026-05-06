from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from app.config import get_settings


def _transcript_path() -> Path:
    path = Path(get_settings().transcript_export_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_transcript_event(event: dict[str, Any]) -> None:
    path = _transcript_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_recent_transcript_entries(
    *,
    session_id: str,
    conversation_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    path = _transcript_path()
    if not path.exists():
        return []
    entries: deque[dict[str, Any]] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if item.get("session_id") != session_id or item.get("conversation_id") != conversation_id:
                continue
            entries.append(item)
    return list(entries)

