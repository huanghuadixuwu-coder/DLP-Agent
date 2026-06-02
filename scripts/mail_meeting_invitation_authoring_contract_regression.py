from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main_module


def main() -> None:
    task = {
        "task_id": "task_meeting_invitation_contract",
        "domain_action": "meeting_create_tencent_meeting",
        "status": "completed",
        "domain_result": {
            "ok": True,
            "status": "completed",
            "result": {
                "subject": "Customer follow-up",
                "meeting_id": "mtg-contract-123",
                "meeting_code": "654321",
                "meeting_url": "https://meeting.tencent.com/dm/contract",
                "start_time": "2026-06-03T15:00:00+08:00",
                "end_time": "2026-06-03T15:30:00+08:00",
                "provider": "tencent_meeting_mcp",
            },
        },
    }
    mail_plan = main_module._mail_plan_from_meeting_task(
        task=task,
        conversation_id="conv_meeting_invitation_contract",
        request_message="send meeting invitation to alice@example.com",
        recipient="alice@example.com",
    )

    assert mail_plan["mail_action_type"] == "send_meeting_invitation", mail_plan
    assert mail_plan["resolved_recipients"] == ["alice@example.com"], mail_plan
    assert mail_plan["requires_confirmation"] is True, mail_plan
    assert mail_plan["compose_mode"] == "recipient_ready_summary", mail_plan
    assert mail_plan["resolved_body"] == "", mail_plan
    assert mail_plan["review_content"] == "", mail_plan
    assert list(mail_plan.get("reference_sources") or [])[0]["role"] == "meeting_result", mail_plan
    assert "https://meeting.tencent.com/dm/contract" in list(mail_plan.get("reference_sources") or [])[0]["content"], mail_plan
    assert list(mail_plan.get("source_artifacts") or [])[0]["kind"] == "meeting_result", mail_plan
    assert list(mail_plan.get("provenance_refs") or [])[0]["kind"] == "meeting_result", mail_plan

    print(
        json.dumps(
            {
                "ok": True,
                "compose_mode": mail_plan["compose_mode"],
                "resolved_body": mail_plan["resolved_body"],
                "review_content": mail_plan["review_content"],
                "reference_role": list(mail_plan.get("reference_sources") or [])[0]["role"],
                "source_artifacts": mail_plan.get("source_artifacts"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
