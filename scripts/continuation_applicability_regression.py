from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.continuation_state import PendingObject, resolve_continuation


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    active_draft = PendingObject(
        object_id="mail-draft:old-body",
        object_type="mail_draft",
        status="active",
        allowed_continuations=("patch", "cancel"),
        salience=0.9,
        payload={
            "mail_plan": {
                "draft_id": "old-body",
                "resolved_recipients": ["first@example.com"],
                "resolved_subject": "Old subject",
                "resolved_body": "测试",
            }
        },
    )
    pending_confirmation = PendingObject(
        object_id="mail-confirmation:ready",
        object_type="mail_confirmation",
        status="pending_confirmation",
        allowed_continuations=("confirm", "edit", "cancel"),
        salience=1.0,
    )
    source_clarification = PendingObject(
        object_id="source-clarification:gcp-or-medthink",
        object_type="source_clarification",
        status="needs_clarification",
        allowed_continuations=("choose_source", "cancel"),
        salience=0.95,
    )

    enterprise_question = resolve_continuation(
        "GCP Marketplace onboarding 中，订阅 entitlement 延迟时应如何处理？",
        [active_draft],
    )
    _assert(
        enterprise_question.mode == "new_task",
        f"active mail draft intercepted a fresh EnterpriseRAG question: {enterprise_question}",
    )

    enterprise_followup = resolve_continuation(
        "处理GCP Marketplace订阅延迟的问题",
        [active_draft],
    )
    _assert(
        enterprise_followup.mode == "new_task",
        f"active mail draft intercepted a fresh RAG follow-up: {enterprise_followup}",
    )

    new_outbound = resolve_continuation(
        "Please send the selected information to second@example.com",
        [active_draft],
    )
    _assert(
        new_outbound.mode == "new_task",
        f"new outbound request was swallowed as draft patch: {new_outbound}",
    )

    edit_draft = resolve_continuation(
        "Please make the current draft more concise",
        [active_draft],
        semantic_classifier=lambda *_: None,
    )
    _assert(
        edit_draft.mode == "ambiguous" and edit_draft.source == "state_resolver_classifier_fallback",
        f"classifier outage should only clarify when the message is draft-applicable: {edit_draft}",
    )

    confirm = resolve_continuation("confirm", [pending_confirmation, active_draft])
    _assert(
        confirm.mode == "continue_existing" and confirm.continuation_type == "confirm",
        f"pending confirmation no longer wins over planner: {confirm}",
    )

    source_choice = resolve_continuation("GCP", [source_clarification, active_draft])
    _assert(
        source_choice.mode == "continue_existing" and source_choice.continuation_type == "choose_source",
        f"source clarification no longer accepts a compact source choice: {source_choice}",
    )

    print(
        json.dumps(
            {
                "ok": True,
                "enterprise_question": enterprise_question.to_dict(),
                "enterprise_followup": enterprise_followup.to_dict(),
                "new_outbound": new_outbound.to_dict(),
                "edit_draft": edit_draft.to_dict(),
                "confirm": confirm.to_dict(),
                "source_choice": source_choice.to_dict(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
