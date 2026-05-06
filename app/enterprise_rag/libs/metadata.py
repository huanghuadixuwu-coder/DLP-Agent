from __future__ import annotations

import json
import re
from typing import Any


SOURCE_ALIASES = {
    "google drive": "google_drive",
    "gdrive": "google_drive",
    "gmail": "gmail",
    "email": "gmail",
    "slack": "slack",
    "linear": "linear",
    "hubspot": "hubspot",
    "fireflies": "fireflies",
    "github": "github",
    "jira": "jira",
    "confluence": "confluence",
}


def normalize_source_type(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_ ]+", "", (value or "").lower()).strip()
    return SOURCE_ALIASES.get(cleaned, cleaned.replace(" ", "_") or "unknown")


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        try:
            value = value.tolist()
        except Exception:
            pass
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        return [item.strip().strip("'\"") for item in re.split(r"[,;|]", text) if item.strip()]
    return [str(value).strip()]


def chroma_safe_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    safe: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            safe[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            safe[key] = value
        else:
            safe[key] = json.dumps(value, ensure_ascii=False, default=str)
    return safe
