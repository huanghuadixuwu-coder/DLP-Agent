from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mail.fake_provider import FakeMailProvider
from app.mail.fixtures import FIXTURE_TENANT_ID, FIXTURE_WORKSPACE_ID, fixture_actor_context
from app.mail.harness import MailHarness


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _draft_id(observation: dict) -> str:
    value = str(observation.get("draft_ref") or "")
    _assert(bool(value), f"missing draft_ref: {observation}")
    return value


def _build_ready_draft(harness: MailHarness, actor: dict, *, suffix: str) -> str:
    observation = harness.build_send_draft(
        actor_context=actor,
        conversation_id=str(actor["conversation_id"]),
        to=[f"{suffix}@example.com"],
        subject=f"Harness {suffix}",
        body_text=f"Public harness body for {suffix}.",
    )
    _assert(observation["status"] == "draft_ready", f"draft should be ready: {observation}")
    return _draft_id(observation)


def main() -> None:
    cases: list[str] = []
    actor = fixture_actor_context()
    provider = FakeMailProvider()
    harness = MailHarness(provider)

    search = harness.search_messages(query="onboarding", actor_context=actor)
    _assert(search["status"] == "completed", f"search failed: {search}")
    _assert("message_customer_followup_1" in search["message_refs"], f"search mismatch: {search}")
    cases.append("read.search_recent_mail")

    message = harness.read_message("message_customer_followup_1", actor_context=actor)
    _assert(message["status"] == "completed", f"message read failed: {message}")
    cases.append("read.single_message")

    thread = harness.read_thread("thread_customer_followup", actor_context=actor)
    _assert(thread["status"] == "completed", f"thread read failed: {thread}")
    _assert(len(thread["payload"]["messages"]) == 2, f"thread message count mismatch: {thread}")
    cases.append("read.thread")

    incomplete = harness.build_send_draft(
        actor_context=actor,
        conversation_id=str(actor["conversation_id"]),
        to=[],
        subject="",
        body_text="",
    )
    _assert(incomplete["status"] == "needs_clarification", f"incomplete draft should require clarification: {incomplete}")
    _assert(set(incomplete["missing_fields"]) == {"to", "subject", "body"}, f"missing fields mismatch: {incomplete}")
    cases.append("draft.structured_missing_fields")

    low_draft = _build_ready_draft(harness, actor, suffix="low")
    unconfirmed = harness.submit_dlp_result(low_draft, actor_context=actor, risk="low", confirmed=False)
    _assert(unconfirmed["status"] == "blocked", f"unconfirmed draft must not send: {unconfirmed}")
    _assert(provider.sent_count == 0, f"unconfirmed draft reached provider: {provider.sent_messages}")
    cases.append("governance.unconfirmed_never_sends")

    pending = harness.request_send_confirmation(low_draft, actor_context=actor)
    _assert(pending["status"] == "pending_confirmation", f"confirmation boundary failed: {pending}")
    low = harness.submit_dlp_result(low_draft, actor_context=actor, risk="low", confirmed=True)
    _assert(low["status"] == "sent", f"low risk send failed: {low}")
    cases.append("governance.low_risk_send")

    medium_draft = _build_ready_draft(harness, actor, suffix="medium")
    harness.request_send_confirmation(medium_draft, actor_context=actor)
    medium = harness.submit_dlp_result(medium_draft, actor_context=actor, risk="medium", confirmed=True)
    _assert(medium["status"] == "sender_review_required", f"medium route mismatch: {medium}")
    medium_sent = harness.sender_review(medium_draft, actor_context=actor, action="confirm")
    _assert(medium_sent["status"] == "sent", f"medium sender second confirm failed: {medium_sent}")
    cases.append("governance.medium_sender_review")

    high_draft = _build_ready_draft(harness, actor, suffix="high")
    harness.request_send_confirmation(high_draft, actor_context=actor)
    high = harness.submit_dlp_result(high_draft, actor_context=actor, risk="high", confirmed=True)
    _assert(high["status"] == "governance_review_required", f"high route mismatch: {high}")
    governance_actor = {
        **actor,
        "tenant_id": FIXTURE_TENANT_ID,
        "workspace_id": FIXTURE_WORKSPACE_ID,
        "user_id": "governance-approver",
        "roles": ["approver"],
    }
    high_sent = harness.governance_review(high_draft, actor_context=governance_actor, action="approve")
    _assert(high_sent["status"] == "sent", f"high governance approval failed: {high_sent}")
    cases.append("governance.high_exception_approval")

    critical_draft = _build_ready_draft(harness, actor, suffix="critical")
    harness.request_send_confirmation(critical_draft, actor_context=actor)
    critical = harness.submit_dlp_result(critical_draft, actor_context=actor, risk="critical", confirmed=True)
    _assert(critical["status"] == "blocked", f"critical route mismatch: {critical}")
    critical_release = harness.governance_review(critical_draft, actor_context=governance_actor, action="approve")
    _assert(critical_release["status"] == "blocked", f"critical risk must not use normal release: {critical_release}")
    cases.append("governance.critical_default_block")

    _assert(provider.sent_count == 3, f"unexpected provider sends: {provider.sent_messages}")
    print(
        json.dumps(
            {
                **harness.report(ok=True, cases_run=cases),
                "provider_sent_count": provider.sent_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
