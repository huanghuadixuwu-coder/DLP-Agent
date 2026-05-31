from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module

app = main_module.app


def _post(client: TestClient, message: str, session_id: str) -> dict:
    response = client.post(
        "/agent/chat",
        json={
            "session_id": session_id,
            "message": message,
            "tenant_id": "tenant-dag",
            "user_id": "user-dag",
            "workspace_id": "workspace-dag",
            "roles": ["admin", "user", "viewer"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def main() -> None:
    main_module._write_unified_conversation_memory = lambda *args, **kwargs: ("turn-test", False)
    with TestClient(app) as client:
        read_result = _post(client, "Please list my Tencent Meeting meetings.", "session-dag-endpoint-read")
        assert read_result["mode_used"] == "multi_agent_dag", read_result.get("mode_used")
        assert read_result["intent"] == "multi_agent_dag", read_result.get("intent")
        assert any(call.get("tool_name") == "meeting_list_user_meetings" for call in read_result.get("tool_calls", [])), read_result.get("tool_calls")

        write_result = _post(client, "Create a Tencent Meeting. Topic: project sync tomorrow afternoon.", "session-dag-endpoint-write")
        assert write_result["mode_used"] == "multi_agent_dag", write_result.get("mode_used")
        assert write_result["termination_reason"] == "needs_confirmation", write_result.get("termination_reason")
        assert dict(write_result.get("pending_confirmation") or {}).get("tool_name") == "meeting_create_tencent_meeting", write_result.get("pending_confirmation")

    print(
        json.dumps(
            {
                "ok": True,
                "read_mode": read_result["mode_used"],
                "write_mode": write_result["mode_used"],
                "write_pending_tool": dict(write_result.get("pending_confirmation") or {}).get("tool_name"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
