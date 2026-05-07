from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

from app.config import DATA_DIR


UPLOAD_BLOB_DIR = DATA_DIR / "upload_blobs"


def _ensure_dir() -> Path:
    UPLOAD_BLOB_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_BLOB_DIR


def save_upload_blob(
    *,
    session_id: str,
    conversation_id: str,
    filename: str,
    content_type: str,
    data_base64: str,
) -> dict[str, Any]:
    raw = base64.b64decode(data_base64.encode("ascii"))
    blob_id = f"blob_{uuid.uuid4().hex[:16]}"
    root = _ensure_dir()
    bin_path = root / f"{blob_id}.bin"
    meta_path = root / f"{blob_id}.json"
    bin_path.write_bytes(raw)
    meta = {
        "blob_id": blob_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "filename": filename,
        "content_type": content_type or "application/octet-stream",
        "size_bytes": len(raw),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def load_upload_blob(blob_id: str) -> dict[str, Any] | None:
    root = _ensure_dir()
    bin_path = root / f"{blob_id}.bin"
    meta_path = root / f"{blob_id}.json"
    if not bin_path.exists() or not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        **meta,
        "content_bytes": bin_path.read_bytes(),
    }

