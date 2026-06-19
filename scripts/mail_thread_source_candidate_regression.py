from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.mail.source_resolver as source_resolver
from app.inbound_mail_store import list_recent_inbound_threads, upsert_inbound_message
from app.main import _collect_outbound_candidates
from app.models import UnifiedAgentRequest
from app.outbound_delivery import build_mail_action_plan


def _seed_thread() -> None:
    upsert_inbound_message(
        {
            "message_id": "reg_mail_thread_customer_1",
            "mailbox": "INBOX",
            "uid": "reg-101",
            "thread_id": "thread_customer_renewal_regression",
            "provider_thread_id": "provider_customer_renewal_regression",
            "sender": "customer@example.com",
            "recipients": "sales@example.com",
            "subject": "Customer renewal discussion",
            "received_at": "2026-06-19T23:58:00+00:00",
            "snippet": "Customer asks for renewal pricing and next steps.",
            "summary": "Customer renewal thread: the customer asked for renewal pricing, SLA confirmation, and next steps.",
            "body_text": "Customer renewal details with pricing request and requested follow-up.",
            "body_preview": "Customer renewal details with pricing request and requested follow-up.",
        }
    )
    upsert_inbound_message(
        {
            "message_id": "reg_mail_thread_customer_2",
            "mailbox": "INBOX",
            "uid": "reg-102",
            "thread_id": "thread_customer_renewal_regression",
            "provider_thread_id": "provider_customer_renewal_regression",
            "sender": "sales@example.com",
            "recipients": "customer@example.com",
            "subject": "Re: Customer renewal discussion",
            "received_at": "2026-06-19T23:59:00+00:00",
            "snippet": "We will send a concise renewal summary and propose a meeting.",
            "summary": "Sales replied that they will send a concise renewal summary and propose a meeting.",
            "body_text": "Sales response about renewal summary and meeting proposal.",
            "body_preview": "Sales response about renewal summary and meeting proposal.",
        }
    )
    upsert_inbound_message(
        {
            "message_id": "reg_mail_thread_distractor",
            "mailbox": "INBOX",
            "uid": "reg-103",
            "thread_id": "thread_invoice_distractor_regression",
            "provider_thread_id": "provider_invoice_distractor_regression",
            "sender": "finance@example.com",
            "recipients": "sales@example.com",
            "subject": "Invoice archive notice",
            "received_at": "2026-06-02T08:00:00+00:00",
            "snippet": "Invoice archive is ready.",
            "summary": "Finance sent an invoice archive notice.",
            "body_text": "Invoice archive notice.",
            "body_preview": "Invoice archive notice.",
        }
    )


def main() -> None:
    _seed_thread()
    threads = list_recent_inbound_threads(limit=3, messages_per_thread=5)
    assert any(item.get("thread_id") == "thread_customer_renewal_regression" for item in threads), threads

    payload = UnifiedAgentRequest(
        session_id="sess_mail_thread_source_contract",
        conversation_id="conv_mail_thread_source_contract",
        message="Please send the customer renewal mail thread summary to 1136732521@qq.com",
    )
    candidates = _collect_outbound_candidates(
        payload,
        "conv_mail_thread_source_contract",
        {},
        actor_context={"tenant_id": "local-dev", "user_id": "local-user", "workspace_id": "default"},
    )
    mail_thread_candidates = [item for item in candidates if item.get("kind") == "mail_thread"]
    assert mail_thread_candidates, candidates

    original_get_llm = source_resolver.get_llm
    source_resolver.get_llm = lambda **_: (_ for _ in ()).throw(RuntimeError("forced resolver outage"))
    try:
        resolved = source_resolver.resolve_mail_source_request(
            message="Please send the customer renewal mail thread summary to 1136732521@qq.com",
            candidates=candidates,
            prior_answer_compatibility_request=True,
        )
    finally:
        source_resolver.get_llm = original_get_llm

    assert not resolved.get("needs_clarification"), resolved
    assert resolved.get("source_mode") == "mail_thread", resolved
    assert resolved.get("selected_candidate_ids") == ["mail-thread:thread_customer_renewal_regression"], resolved
    assert resolved.get("classifier_source") in {"deterministic_reference_anchor_match", "safe_fallback_anchor_match"}, resolved

    plan = build_mail_action_plan(
        message="Please send the customer renewal mail thread summary to 1136732521@qq.com",
        request_message="Please send the customer renewal mail thread summary to 1136732521@qq.com",
        candidates=candidates,
        destination_email="1136732521@qq.com",
        referential_request=True,
        explicit_summary=True,
        send_both=False,
        conversation_id="conv_mail_thread_source_contract",
        source_resolution=resolved,
    )
    assert plan.get("ok"), plan
    mail_plan = dict(plan.get("mail_plan") or {})
    assert plan.get("mode") == "confirmation_required", plan
    assert mail_plan.get("target_object") == "mail_thread", mail_plan
    assert mail_plan.get("compose_mode") == "recipient_ready_summary", mail_plan
    assert mail_plan.get("review_content") == "", mail_plan
    assert {
        "kind": "reference_source",
        "role": "mail_thread",
        "policy": "recipient_ready_summary",
    } in list(mail_plan.get("body_sources") or []), mail_plan
    assert list(mail_plan.get("reference_sources") or [])[0].get("role") == "mail_thread", mail_plan
    assert list(mail_plan.get("source_artifacts") or [])[0].get("kind") == "mail_thread", mail_plan

    print(
        json.dumps(
            {
                "ok": True,
                "thread_candidates": len(mail_thread_candidates),
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
