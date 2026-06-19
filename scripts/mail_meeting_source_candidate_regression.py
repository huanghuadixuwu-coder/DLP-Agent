from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.mail.source_resolver as source_resolver
from app.outbound_delivery import build_mail_action_plan


MEETING_CONTENT = json.dumps(
    {
        "task_id": "task_meeting_source_contract",
        "subject": "客户复盘会议",
        "meeting_id": "123456789",
        "meeting_code": "987654",
        "meeting_url": "https://meeting.tencent.com/dm/example",
        "start_time": "2026-06-03T15:00:00+08:00",
        "end_time": "2026-06-03T15:30:00+08:00",
    },
    ensure_ascii=False,
)


def main() -> None:
    candidates = [
        {
            "candidate_id": "meeting-task:task_meeting_source_contract",
            "kind": "meeting_result",
            "label": "客户复盘会议",
            "content": MEETING_CONTENT,
            "content_type": "application/json",
        },
        {
            "candidate_id": "assistant-turn:gcp",
            "kind": "assistant_last_answer",
            "label": "GCP entitlement 延迟回答",
            "content": "GCP Marketplace entitlement 延迟应按 pending 状态处理。",
        },
    ]

    original_get_llm = source_resolver.get_llm
    source_resolver.get_llm = lambda **_: (_ for _ in ()).throw(RuntimeError("forced resolver outage"))
    try:
        resolved = source_resolver.resolve_mail_source_request(
            message="请把客户复盘会议链接发送给 1136732521@qq.com",
            candidates=candidates,
            prior_answer_compatibility_request=True,
        )
    finally:
        source_resolver.get_llm = original_get_llm

    assert not resolved.get("needs_clarification"), resolved
    assert resolved.get("source_mode") == "meeting_result", resolved
    assert resolved.get("selected_candidate_ids") == ["meeting-task:task_meeting_source_contract"], resolved
    assert resolved.get("classifier_source") in {"deterministic_reference_anchor_match", "safe_fallback_anchor_match"}, resolved

    plan = build_mail_action_plan(
        message="请把客户复盘会议链接发送给 1136732521@qq.com",
        request_message="请把客户复盘会议链接发送给 1136732521@qq.com",
        candidates=candidates,
        destination_email="1136732521@qq.com",
        referential_request=True,
        explicit_summary=False,
        send_both=False,
        conversation_id="conv_meeting_source_contract",
        source_resolution=resolved,
    )
    assert plan.get("ok"), plan
    mail_plan = dict(plan.get("mail_plan") or {})
    assert plan.get("mode") == "confirmation_required", plan
    assert mail_plan.get("target_object") == "meeting_result", mail_plan
    assert mail_plan.get("compose_mode") == "recipient_ready_summary", mail_plan
    assert mail_plan.get("review_content") == "", mail_plan
    assert {
        "kind": "reference_source",
        "role": "meeting_result",
        "policy": "recipient_ready_summary",
    } in list(mail_plan.get("body_sources") or []), mail_plan
    assert list(mail_plan.get("reference_sources") or [])[0].get("role") == "meeting_result", mail_plan
    assert list(mail_plan.get("source_artifacts") or [])[0].get("kind") == "meeting_result", mail_plan

    print(
        json.dumps(
            {
                "ok": True,
                "source_mode": resolved.get("source_mode"),
                "classifier_source": resolved.get("classifier_source"),
                "target_object": mail_plan.get("target_object"),
                "compose_mode": mail_plan.get("compose_mode"),
                "review_content": mail_plan.get("review_content"),
                "source_artifacts": mail_plan.get("source_artifacts"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
