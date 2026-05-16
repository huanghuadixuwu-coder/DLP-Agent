from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from app.orchestration.tool_discovery import dispatch_tool_call, get_mcp_tool_manifest


def _make_tool_callable(tool_name: str) -> Callable[..., dict[str, Any]]:
    def _call(**kwargs: Any) -> dict[str, Any]:
        dispatched = dispatch_tool_call(tool_name, kwargs, allow_side_effects=True)
        if dispatched.get("ok"):
            return dict(dispatched.get("result") or {})
        return {"ok": False, "error": str(dispatched.get("error") or "tool dispatch failed")}

    return _call


def build_mcp_tool_map() -> dict[str, Callable[..., dict[str, Any]]]:
    return {name: _make_tool_callable(name) for name in get_mcp_tool_manifest()}


def build_mcp_tool_manifest() -> list[dict[str, Any]]:
    return [asdict(item) for item in get_mcp_tool_manifest().values()]


TOOLS = build_mcp_tool_map()
