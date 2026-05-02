from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
STORE_PATH = ROOT_DIR / "data" / "reminders.json"


def _load() -> list[dict[str, Any]]:
    if not STORE_PATH.exists():
        return []
    return json.loads(STORE_PATH.read_text(encoding="utf-8"))


def _save(items: list[dict[str, Any]]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def create_reminder(text: str, remind_at: str) -> dict[str, Any]:
    items = _load()
    reminder = {
        "id": str(uuid.uuid4())[:8],
        "text": text,
        "remind_at": remind_at,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "done": False,
    }
    items.append(reminder)
    _save(items)
    return reminder


def list_reminders(include_done: bool = False) -> list[dict[str, Any]]:
    items = _load()
    if include_done:
        return items
    return [item for item in items if not item.get("done")]


def delete_reminder(reminder_id: str) -> bool:
    items = _load()
    remaining = [item for item in items if item["id"] != reminder_id]
    _save(remaining)
    return len(remaining) != len(items)


def due_reminders(now_iso: str) -> list[dict[str, Any]]:
    now = datetime.fromisoformat(now_iso)
    due: list[dict[str, Any]] = []
    items = _load()
    for item in items:
        if item.get("done"):
            continue
        try:
            if datetime.fromisoformat(item["remind_at"]) <= now:
                item["done"] = True
                due.append(item)
        except ValueError:
            continue
    _save(items)
    return due
