from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_TENANT_ID = "local-dev"
DEFAULT_USER_ID = "local-user"
DEFAULT_WORKSPACE_ID = "default"
LOCAL_DEV_ROLES = ("admin", "user", "viewer")


@dataclass(frozen=True, slots=True)
class ActorContext:
    tenant_id: str = DEFAULT_TENANT_ID
    user_id: str = DEFAULT_USER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    roles: tuple[str, ...] = field(default_factory=lambda: LOCAL_DEV_ROLES)
    session_id: str = ""
    conversation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["roles"] = list(self.roles)
        return data

    @property
    def is_local_dev(self) -> bool:
        return self.tenant_id == DEFAULT_TENANT_ID and self.user_id == DEFAULT_USER_ID


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    action: str
    resource: str = ""
    reason: str = "allowed"
    required_roles: tuple[str, ...] = ()
    actor_roles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_roles"] = list(self.required_roles)
        data["actor_roles"] = list(self.actor_roles)
        return data


ACTION_ROLES: dict[str, tuple[str, ...]] = {
    "agent.chat": ("viewer", "user", "admin"),
    "rag.query": ("viewer", "user", "admin"),
    "rag.ingest": ("ingest_admin", "admin"),
    "rag.benchmark": ("ingest_admin", "admin"),
    "mail.read": ("viewer", "user", "admin"),
    "mail.send": ("mail_sender", "admin"),
    "task.read": ("viewer", "user", "approver", "admin"),
    "task.approve": ("approver", "admin"),
    "policy.read": ("policy_admin", "admin"),
    "policy.write": ("policy_admin", "admin"),
    "admin.read": ("admin",),
}


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def normalize_roles(value: Any, *, tenant_id: str = "", user_id: str = "") -> tuple[str, ...]:
    roles: list[str] = []
    if isinstance(value, str):
        roles.extend(part.strip() for part in value.replace(";", ",").split(","))
    elif isinstance(value, (list, tuple, set)):
        roles.extend(str(part).strip() for part in value)
    roles = [role.lower() for role in roles if role]
    if not roles and (not tenant_id or tenant_id == DEFAULT_TENANT_ID) and (not user_id or user_id == DEFAULT_USER_ID):
        roles = list(LOCAL_DEV_ROLES)
    if "admin" in roles:
        roles = ["admin", *[role for role in roles if role != "admin"]]
    deduped: list[str] = []
    seen: set[str] = set()
    for role in roles:
        if role not in seen:
            deduped.append(role)
            seen.add(role)
    return tuple(deduped or ("viewer",))


def actor_from_mapping(
    values: Any,
    *,
    session_id: str = "",
    conversation_id: str = "",
    fallback: ActorContext | None = None,
) -> ActorContext:
    data = values if isinstance(values, dict) else {}
    fallback = fallback or ActorContext(session_id=session_id, conversation_id=conversation_id)
    tenant_id = _clean(data.get("tenant_id"), fallback.tenant_id)
    user_id = _clean(data.get("user_id"), fallback.user_id)
    workspace_id = _clean(data.get("workspace_id"), fallback.workspace_id)
    roles = normalize_roles(data.get("roles"), tenant_id=tenant_id, user_id=user_id)
    return ActorContext(
        tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=workspace_id,
        roles=roles,
        session_id=_clean(data.get("session_id"), session_id or fallback.session_id),
        conversation_id=_clean(data.get("conversation_id"), conversation_id or fallback.conversation_id),
    )


def build_actor_context(
    *,
    request: Any | None = None,
    payload: Any | None = None,
    session_id: str = "",
    conversation_id: str = "",
) -> ActorContext:
    payload_values = {
        "tenant_id": getattr(payload, "tenant_id", "") if payload is not None else "",
        "user_id": getattr(payload, "user_id", "") if payload is not None else "",
        "workspace_id": getattr(payload, "workspace_id", "") if payload is not None else "",
        "roles": getattr(payload, "roles", []) if payload is not None else [],
        "session_id": getattr(payload, "session_id", "") if payload is not None else "",
        "conversation_id": getattr(payload, "conversation_id", "") if payload is not None else "",
    }
    header_values: dict[str, Any] = {}
    if request is not None:
        headers = getattr(request, "headers", {}) or {}
        header_values = {
            "tenant_id": headers.get("x-tenant-id") or headers.get("X-Tenant-Id") or "",
            "user_id": headers.get("x-user-id") or headers.get("X-User-Id") or "",
            "workspace_id": headers.get("x-workspace-id") or headers.get("X-Workspace-Id") or "",
            "roles": headers.get("x-roles") or headers.get("X-Roles") or "",
        }
    merged = {**payload_values, **{key: value for key, value in header_values.items() if value}}
    return actor_from_mapping(merged, session_id=session_id, conversation_id=conversation_id)


def require_permission(actor: ActorContext | dict[str, Any], action: str, resource: str = "") -> PermissionDecision:
    actor_obj = actor_from_mapping(actor) if isinstance(actor, dict) else actor
    required = ACTION_ROLES.get(action, ("admin",))
    actor_roles = tuple(role.lower() for role in actor_obj.roles)
    if "admin" in actor_roles or any(role in actor_roles for role in required):
        return PermissionDecision(
            allowed=True,
            action=action,
            resource=resource,
            reason="role_allowed",
            required_roles=required,
            actor_roles=actor_roles,
        )
    return PermissionDecision(
        allowed=False,
        action=action,
        resource=resource,
        reason="missing_required_role",
        required_roles=required,
        actor_roles=actor_roles,
    )


def permission_observation(actor: ActorContext, decision: PermissionDecision) -> dict[str, Any]:
    return {
        "observation_type": "permission_denied",
        "ok": False,
        "actor_context": actor.to_dict(),
        "permission_decision": decision.to_dict(),
    }
