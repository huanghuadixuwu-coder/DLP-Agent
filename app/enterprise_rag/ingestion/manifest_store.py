from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR


MANIFEST_PATH = DATA_DIR / "enterprise_rag_manifest.json"


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def append_manifest_run(run: dict[str, Any], path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = load_manifest(path)
    run = {"created_at": datetime.now(timezone.utc).isoformat(), **run}
    manifest.setdefault("runs", []).append(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
