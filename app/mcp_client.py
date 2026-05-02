from __future__ import annotations

from typing import Any

from app.mcp_tools import TOOLS


def call_mcp_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call the local MCP-style tool registry with structured inputs and outputs."""
    tool = TOOLS.get(tool_name)
    if not tool:
        return {"ok": False, "error": f"unknown MCP tool: {tool_name}"}
    try:
        return {"ok": True, "result": tool(**args)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
