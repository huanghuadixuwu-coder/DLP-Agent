from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.actor_context import ActorContext
from app.orchestration.service import orchestrate_agent_request


def _actor(conversation_id: str) -> dict:
    return ActorContext(
        tenant_id="tenant-dag",
        user_id="user-dag",
        workspace_id="workspace-dag",
        roles=("admin", "user", "viewer"),
        session_id="session-dag",
        conversation_id=conversation_id,
    ).to_dict()


def _call(message: str, conversation_id: str) -> dict:
    return orchestrate_agent_request(
        session_id="session-dag",
        conversation_id=conversation_id,
        message=message,
        safe_message=message,
        display_message=message,
        upload_context={},
        actor_context=_actor(conversation_id),
    )


def main() -> None:
    read_result = _call("Please list my Tencent Meeting meetings.", "conversation-dag-read")
    assert read_result["mode_used"] == "multi_agent_dag", read_result.get("mode_used")
    assert read_result["intent"] == "multi_agent_dag", read_result.get("intent")
    read_tools = {item.get("tool_name") for item in read_result.get("tool_calls", [])}
    assert "meeting_list_user_meetings" in read_tools, read_tools
    assert read_result.get("termination_reason") == "direct_answer", read_result.get("termination_reason")

    write_result = _call("Create a Tencent Meeting. Topic: project sync tomorrow afternoon.", "conversation-dag-write")
    assert write_result["mode_used"] == "multi_agent_dag", write_result.get("mode_used")
    assert write_result.get("termination_reason") == "needs_confirmation", write_result.get("termination_reason")
    pending = dict(write_result.get("pending_confirmation") or {})
    assert pending.get("tool_name") == "meeting_create_tencent_meeting", pending
    tool_input = dict(pending.get("tool_input") or {})
    assert tool_input.get("topic") == "project sync", tool_input
    assert "tomorrow" in str(tool_input.get("natural_time") or ""), tool_input
    assert "afternoon" in str(tool_input.get("natural_time") or ""), tool_input
    assert tool_input.get("duration_minutes") == 30, tool_input
    assert str(tool_input.get("idempotency_key") or "").startswith("dag-"), tool_input
    write_observations = list(write_result.get("tool_observations") or [])
    assert any(item.get("observation_type") == "confirmation_required" for item in write_observations), write_observations
    assert not any(
        item.get("source") == "meeting_create_tencent_meeting" and item.get("status") == "completed"
        for item in write_observations
    ), write_observations

    print(
        json.dumps(
            {
                "ok": True,
                "read_mode": read_result["mode_used"],
                "read_tools": sorted(read_tools),
                "write_mode": write_result["mode_used"],
                "write_pending_tool": pending.get("tool_name"),
                "write_pending_input": tool_input,
                "write_observation_types": sorted({str(item.get("observation_type") or "") for item in write_observations}),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
