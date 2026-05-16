from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.mcp_tools import build_mcp_tool_manifest  # noqa: E402
from app.orchestration.tool_discovery import dispatch_tool_call  # noqa: E402


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("tool")
    args = payload.get("args") or {}
    if name == "list_tools":
        return {"ok": True, "tools": build_mcp_tool_manifest()}
    return dispatch_tool_call(str(name or ""), args if isinstance(args, dict) else {}, allow_side_effects=False)


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle(json.loads(line))
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
