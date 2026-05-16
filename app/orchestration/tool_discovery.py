from __future__ import annotations

import importlib
import pkgutil
from dataclasses import asdict
from typing import Any, Callable

from app.orchestration.types import OrchestrationContext, ToolDefinition


ToolCallable = Callable[[dict[str, Any], OrchestrationContext, dict[str, Any]], dict[str, Any]]

_TOOL_REGISTRY: dict[str, ToolDefinition] = {}
_TOOL_EXECUTORS: dict[str, ToolCallable] = {}
_TOOL_EXPOSE_MCP: dict[str, bool] = {}
_TOOL_DIAGNOSTICS: list[dict[str, Any]] = []
_DISCOVERED_MODULES: set[str] = set()
_DISCOVERY_RAN = False


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, str] | None = None,
    mutating: bool = False,
    parallelizable: bool = True,
    preconditions: list[str] | None = None,
    when_to_use: list[str] | None = None,
    reads_from: list[str] | None = None,
    writes_to: list[str] | None = None,
    read_only: bool = True,
    side_effectful: bool = False,
    requires_confirmation: bool = False,
    safe_when: list[str] | None = None,
    returns_observation_type: str = "generic",
    provider_constraints: list[str] | None = None,
    examples: list[str] | None = None,
    expose_mcp: bool = False,
) -> Callable[[ToolCallable], ToolCallable]:
    def decorator(handler: ToolCallable) -> ToolCallable:
        tool_name = name.strip()
        if not tool_name:
            raise ValueError("registered tool name cannot be empty")
        if tool_name in _TOOL_REGISTRY and _TOOL_EXECUTORS.get(tool_name) is not handler:
            _TOOL_DIAGNOSTICS.append(
                {
                    "level": "warning",
                    "event": "duplicate_tool_registration",
                    "tool": tool_name,
                    "existing_module": getattr(_TOOL_EXECUTORS.get(tool_name), "__module__", ""),
                    "new_module": getattr(handler, "__module__", ""),
                }
            )
            return handler
        definition = ToolDefinition(
            name=tool_name,
            description=description,
            mutating=mutating,
            parallelizable=parallelizable,
            input_schema=dict(input_schema or {}),
            preconditions=list(preconditions or []),
            when_to_use=list(when_to_use or []),
            reads_from=list(reads_from or []),
            writes_to=list(writes_to or []),
            read_only=read_only,
            side_effectful=side_effectful,
            requires_confirmation=requires_confirmation,
            safe_when=list(safe_when or []),
            returns_observation_type=returns_observation_type,
            provider_constraints=list(provider_constraints or []),
            examples=list(examples or []),
        )
        _TOOL_REGISTRY[tool_name] = definition
        _TOOL_EXECUTORS[tool_name] = handler
        _TOOL_EXPOSE_MCP[tool_name] = bool(expose_mcp)
        _TOOL_DIAGNOSTICS.append(
            {
                "level": "info",
                "event": "tool_registered",
                "tool": tool_name,
                "module": getattr(handler, "__module__", ""),
                "side_effectful": side_effectful,
                "requires_confirmation": requires_confirmation,
                "expose_mcp": expose_mcp,
            }
        )
        return handler

    return decorator


def discover_tools(package_name: str = "app.orchestration.tools") -> dict[str, Any]:
    global _DISCOVERY_RAN
    try:
        package = importlib.import_module(package_name)
    except Exception as exc:
        _TOOL_DIAGNOSTICS.append({"level": "error", "event": "tool_package_import_failed", "package": package_name, "error": str(exc)})
        _DISCOVERY_RAN = True
        return {"registered": len(_TOOL_REGISTRY), "diagnostics": list(_TOOL_DIAGNOSTICS)}

    package_paths = list(getattr(package, "__path__", []))
    for module_info in pkgutil.iter_modules(package_paths, prefix=f"{package_name}."):
        module_name = module_info.name
        if module_name in _DISCOVERED_MODULES:
            continue
        try:
            importlib.import_module(module_name)
            _DISCOVERED_MODULES.add(module_name)
            _TOOL_DIAGNOSTICS.append({"level": "info", "event": "tool_module_imported", "module": module_name})
        except Exception as exc:
            _TOOL_DIAGNOSTICS.append({"level": "error", "event": "tool_module_import_failed", "module": module_name, "error": str(exc)})
    _DISCOVERY_RAN = True
    return {"registered": len(_TOOL_REGISTRY), "diagnostics": list(_TOOL_DIAGNOSTICS)}


def get_dynamic_tool_registry() -> dict[str, ToolDefinition]:
    if not _DISCOVERY_RAN:
        discover_tools()
    return dict(_TOOL_REGISTRY)


def get_dynamic_tool_executor_map() -> dict[str, ToolCallable]:
    if not _DISCOVERY_RAN:
        discover_tools()
    return dict(_TOOL_EXECUTORS)


def get_tool_discovery_diagnostics() -> list[dict[str, Any]]:
    if not _DISCOVERY_RAN:
        discover_tools()
    return list(_TOOL_DIAGNOSTICS)


def get_mcp_tool_manifest() -> dict[str, ToolDefinition]:
    registry = get_dynamic_tool_registry()
    return {name: definition for name, definition in registry.items() if _TOOL_EXPOSE_MCP.get(name)}


def dispatch_tool_call(
    action: str,
    parameters: dict[str, Any] | None,
    context: OrchestrationContext | None = None,
    dependency_payloads: dict[str, Any] | None = None,
    *,
    allow_side_effects: bool = False,
) -> dict[str, Any]:
    tool_name = str(action or "").strip()
    registry = get_dynamic_tool_registry()
    executors = get_dynamic_tool_executor_map()
    definition = registry.get(tool_name)
    if not definition:
        return {"ok": False, "action": tool_name, "result": {}, "error": f"unknown action: {tool_name}", "observation_type": "error", "side_effectful": False, "requires_confirmation": False}
    if parameters is not None and not isinstance(parameters, dict):
        return {"ok": False, "action": tool_name, "result": {}, "error": "parameters must be an object", "observation_type": definition.returns_observation_type, "side_effectful": definition.side_effectful, "requires_confirmation": definition.requires_confirmation}
    if (definition.side_effectful or definition.requires_confirmation) and not allow_side_effects:
        return {"ok": False, "action": tool_name, "result": {}, "error": "confirmation_required", "observation_type": definition.returns_observation_type, "side_effectful": definition.side_effectful, "requires_confirmation": definition.requires_confirmation}
    handler = executors.get(tool_name)
    if not handler:
        return {"ok": False, "action": tool_name, "result": {}, "error": f"no executor found for action: {tool_name}", "observation_type": definition.returns_observation_type, "side_effectful": definition.side_effectful, "requires_confirmation": definition.requires_confirmation}
    safe_context = context or OrchestrationContext(session_id="", conversation_id="", message="", safe_message="")
    try:
        result = handler(dict(parameters or {}), safe_context, dict(dependency_payloads or {})) or {}
        error = str(result.get("error") or "")
        return {"ok": not bool(error), "action": tool_name, "result": result, "error": error, "observation_type": definition.returns_observation_type, "side_effectful": definition.side_effectful, "requires_confirmation": definition.requires_confirmation}
    except Exception as exc:
        return {"ok": False, "action": tool_name, "result": {}, "error": str(exc), "observation_type": definition.returns_observation_type, "side_effectful": definition.side_effectful, "requires_confirmation": definition.requires_confirmation}


def manifest_as_dicts(registry: dict[str, ToolDefinition] | None = None) -> list[dict[str, Any]]:
    return [asdict(item) for item in (registry or get_dynamic_tool_registry()).values()]
