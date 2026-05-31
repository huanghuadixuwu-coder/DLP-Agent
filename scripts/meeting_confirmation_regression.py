from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module
from app.task_store import update_task


def main() -> None:
    enqueued: list[str] = []

    def _fake_enqueue(task_id: str) -> str:
        enqueued.append(task_id)
        return task_id

    main_module.enqueue_meeting_task = _fake_enqueue
    idempotency_key = f"meeting-confirmation-regression-{uuid4().hex[:12]}"
    actor_context = {
        "tenant_id": "tenant-meeting-regression",
        "user_id": "user-meeting-regression",
        "workspace_id": "workspace-meeting-regression",
        "roles": ["admin", "user"],
    }
    confirmation_payload = {
        "tool_name": "meeting_create_tencent_meeting",
        "tool_input": {
            "topic": "project sync",
            "natural_time": "tomorrow afternoon",
            "timezone": "Asia/Shanghai",
            "attendees": ["alice@example.com"],
            "duration_minutes": 30,
            "idempotency_key": idempotency_key,
        },
    }
    first = main_module._create_domain_task_from_confirmation(
        session_id="session-meeting-regression",
        conversation_id="conversation-meeting-regression",
        request_message="确认创建腾讯会议。",
        confirmation_payload=confirmation_payload,
        actor_context=actor_context,
    )
    assert first["task_type"] == "domain_meeting", first
    assert first["status"] == "queued", first
    assert first["domain_action"] == "meeting_create_tencent_meeting", first
    assert first["domain_payload"]["topic"] == "project sync", first
    assert first["domain_payload"]["idempotency_key"] == idempotency_key, first
    assert enqueued == [first["task_id"]], enqueued

    second = main_module._create_domain_task_from_confirmation(
        session_id="session-meeting-regression",
        conversation_id="conversation-meeting-regression",
        request_message="确认创建腾讯会议。",
        confirmation_payload=confirmation_payload,
        actor_context=actor_context,
    )
    assert second["task_id"] == first["task_id"], (first, second)
    assert enqueued == [first["task_id"]], enqueued

    # Keep the regression task from being picked up by a real meeting worker later.
    update_task(
        str(first["task_id"]),
        status="completed",
        domain_result={"ok": True, "test": True, "reason": "regression_task_neutralized"},
        delivery_status="completed",
        final_result="Regression task neutralized without provider execution.",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "task_id": first["task_id"],
                "task_type": first["task_type"],
                "domain_action": first["domain_action"],
                "idempotency_deduped": second["task_id"] == first["task_id"],
                "enqueued": enqueued,
                "neutralized": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
