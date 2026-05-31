from __future__ import annotations

from typing import Any


def orchestrate_agent_request(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from app.orchestration.service import orchestrate_agent_request as _orchestrate_agent_request

    return _orchestrate_agent_request(*args, **kwargs)

__all__ = ["orchestrate_agent_request"]
