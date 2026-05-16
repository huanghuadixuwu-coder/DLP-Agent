from __future__ import annotations

from typing import Any

from app.orchestration.tool_discovery import dispatch_tool_call


def call_mcp_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call the local MCP-style tool registry with structured inputs and outputs."""
    dispatched = dispatch_tool_call(tool_name, dict(args or {}), allow_side_effects=True)
    if not dispatched.get("ok"):
        return {"ok": False, "error": str(dispatched.get("error") or "MCP tool dispatch failed")}
    return {"ok": True, "result": dispatched.get("result") or {}}
