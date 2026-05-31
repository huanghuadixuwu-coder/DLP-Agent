from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.orchestration.registry import build_tool_registry


@dataclass(slots=True)
class DomainAgentDefinition:
    agent_name: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    read_only: bool = True
    side_effectful: bool = False
    requires_confirmation: bool = False
    observation_types: list[str] = field(default_factory=list)
    provider_constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_DOMAIN_AGENTS: dict[str, DomainAgentDefinition] = {}


def register_domain_agent(definition: DomainAgentDefinition) -> DomainAgentDefinition:
    name = definition.agent_name.strip()
    if not name:
        raise ValueError("domain agent name cannot be empty")
    _DOMAIN_AGENTS[name] = definition
    return definition


def _default_agent_for_tool(tool_name: str) -> str:
    if tool_name.startswith(("inbound_", "outbound_", "send_email", "mail_")):
        return "mail"
    if tool_name.startswith(("privacy_", "governance_", "enqueue_dlp")):
        return "dlp"
    if tool_name.startswith(("enterprise_",)):
        return "enterprise_rag"
    if tool_name.startswith(("memory_", "conversation_context")):
        return "memory"
    if tool_name.startswith(("calendar_",)):
        return "calendar"
    if tool_name.startswith(("meeting_",)):
        return "meeting"
    return "supervisor"


def _agent_description(agent_name: str) -> str:
    return {
        "supervisor": "Central orchestration and lightweight utility capabilities.",
        "mail": "Inbound/outbound mail reading, drafting, governance context, and delivery tools.",
        "calendar": "Calendar availability and event lifecycle capabilities.",
        "meeting": "Tencent Meeting lifecycle and meeting-link capabilities.",
        "dlp": "Privacy scanning, DLP governance, and compliance checks.",
        "enterprise_rag": "Grounded enterprise knowledge retrieval and answer evidence.",
        "memory": "Conversation, workspace, and user-memory context substrate.",
    }.get(agent_name, f"{agent_name} domain capabilities.")


def build_domain_agent_catalog() -> dict[str, DomainAgentDefinition]:
    registry = build_tool_registry()
    grouped: dict[str, list[str]] = {}
    for tool_name in registry:
        grouped.setdefault(_default_agent_for_tool(tool_name), []).append(tool_name)

    catalog: dict[str, DomainAgentDefinition] = {}
    for agent_name, capabilities in grouped.items():
        tools = [registry[name] for name in capabilities if name in registry]
        catalog[agent_name] = DomainAgentDefinition(
            agent_name=agent_name,
            description=_agent_description(agent_name),
            capabilities=sorted(capabilities),
            permissions=sorted({permission for permission in _permissions_for_agent(agent_name)}),
            read_only=all(tool.read_only and not tool.side_effectful for tool in tools),
            side_effectful=any(tool.side_effectful or tool.mutating for tool in tools),
            requires_confirmation=any(tool.requires_confirmation for tool in tools),
            observation_types=sorted({tool.returns_observation_type for tool in tools if tool.returns_observation_type}),
            provider_constraints=sorted(
                {
                    constraint
                    for tool in tools
                    for constraint in list(tool.provider_constraints or [])
                    if constraint
                }
            ),
        )

    for name, definition in _DOMAIN_AGENTS.items():
        catalog[name] = definition
    return catalog


def build_domain_agent_catalog_dict() -> dict[str, dict[str, Any]]:
    return {name: definition.to_dict() for name, definition in build_domain_agent_catalog().items()}


def resolve_domain_agent_for_action(action: str) -> str:
    catalog = build_domain_agent_catalog()
    for agent_name, definition in catalog.items():
        if action in definition.capabilities:
            return agent_name
    return _default_agent_for_tool(action)


def action_belongs_to_agent(action: str, agent_name: str) -> bool:
    if not agent_name:
        return True
    definition = build_domain_agent_catalog().get(agent_name)
    return bool(definition and action in definition.capabilities)


def _permissions_for_agent(agent_name: str) -> tuple[str, ...]:
    return {
        "mail": ("mail.read", "mail.send"),
        "calendar": ("calendar.read", "calendar.write"),
        "meeting": ("meeting.read", "meeting.write"),
        "dlp": ("task.read", "task.approve"),
        "enterprise_rag": ("rag.query",),
        "memory": ("agent.chat",),
        "supervisor": ("agent.chat",),
    }.get(agent_name, ("agent.chat",))
