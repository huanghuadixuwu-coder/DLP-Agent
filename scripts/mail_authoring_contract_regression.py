from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.outbound_delivery import build_mail_action_plan


SOURCE_TEXT = (
    "Customer renewal thread summary: customer asked for renewal pricing, SLA confirmation, "
    "and a concise next-step email. Sales should propose a follow-up meeting and avoid adding "
    "unrelated internal debug notes."
)


def main() -> None:
    candidates = [
        {
            "candidate_id": "mail-thread:renewal-contract",
            "kind": "mail_thread",
            "label": "mail thread: Customer renewal discussion",
            "content": SOURCE_TEXT,
            "content_type": "application/json",
        }
    ]
    plan = build_mail_action_plan(
        message="Please polish the customer renewal thread into a concise outbound email draft",
        request_message="Please polish the customer renewal thread into a concise outbound email draft",
        candidates=candidates,
        destination_email="",
        referential_request=True,
        explicit_summary=True,
        send_both=False,
        conversation_id="conv_mail_authoring_contract",
    )
    assert plan.get("ok"), plan
    assert plan.get("mode") == "draft_only", plan
    mail_plan = dict(plan.get("mail_plan") or {})
    assert mail_plan.get("mail_action_type") == "polish_body", mail_plan
    assert mail_plan.get("compose_mode") == "recipient_ready_summary", mail_plan
    assert mail_plan.get("resolved_body") == "", mail_plan
    assert mail_plan.get("review_content") == "", mail_plan
    assert list(mail_plan.get("reference_sources") or [])[0].get("content") == SOURCE_TEXT, mail_plan
    assert list(mail_plan.get("reference_sources") or [])[0].get("role") == "mail_thread", mail_plan
    assert {
        "kind": "reference_source",
        "role": "mail_thread",
        "policy": "author_with_renderer",
    } in list(mail_plan.get("body_sources") or []), mail_plan
    assert list(mail_plan.get("source_artifacts") or [])[0].get("kind") == "mail_thread", mail_plan

    print(
        json.dumps(
            {
                "ok": True,
                "mode": plan.get("mode"),
                "action": mail_plan.get("mail_action_type"),
                "compose_mode": mail_plan.get("compose_mode"),
                "resolved_body": mail_plan.get("resolved_body"),
                "review_content": mail_plan.get("review_content"),
                "reference_role": list(mail_plan.get("reference_sources") or [])[0].get("role"),
                "source_artifacts": mail_plan.get("source_artifacts"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
