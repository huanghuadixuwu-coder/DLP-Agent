from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.mcp_tools import TOOLS  # noqa: E402


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("tool")
    args = payload.get("args") or {}
    if name == "list_tools":
        return {"ok": True, "tools": sorted(TOOLS)}
    if name not in TOOLS:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return {"ok": True, "result": TOOLS[name](**args)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle(json.loads(line))
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
