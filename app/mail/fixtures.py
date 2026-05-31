from __future__ import annotations

from app.mail.domain import MailAccount, MailMessage, MailThread


FIXTURE_TENANT_ID = "tenant-mail-harness"
FIXTURE_USER_ID = "user-mail-harness"
FIXTURE_WORKSPACE_ID = "workspace-mail-harness"


def fixture_actor_context(*, user_id: str = FIXTURE_USER_ID, tenant_id: str = FIXTURE_TENANT_ID) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "workspace_id": FIXTURE_WORKSPACE_ID,
        "roles": ["admin", "mail_sender", "approver", "user", "viewer"],
        "session_id": "session-mail-harness",
        "conversation_id": "conversation-mail-harness",
    }


def build_fixture_account() -> MailAccount:
    return MailAccount(
        account_id="account_mail_harness",
        tenant_id=FIXTURE_TENANT_ID,
        user_id=FIXTURE_USER_ID,
        workspace_id=FIXTURE_WORKSPACE_ID,
        provider="fake",
        email_address="owner@example.com",
        display_name="Harness Owner",
        capabilities={"search": True, "read": True, "threads": True, "labels": True, "sync": True, "send": True},
        auth_ref="fake://mail-harness",
    )


def build_fixture_messages() -> list[MailMessage]:
    return [
        MailMessage(
            message_id="message_customer_followup_1",
            provider_message_id="provider-message-001",
            thread_id="thread_customer_followup",
            provider_thread_id="provider-thread-customer-followup",
            labels=["inbox", "customer"],
            sender="alice@customer.example",
            to=["owner@example.com"],
            subject="Customer onboarding follow-up",
            received_at="2026-05-29T08:30:00+00:00",
            body_text="Could we schedule a follow-up meeting next week to review the onboarding checklist?",
            is_seen=False,
            source_policy={"body_source": "provider_message", "attachment_source": "metadata_only"},
        ),
        MailMessage(
            message_id="message_customer_followup_2",
            provider_message_id="provider-message-002",
            thread_id="thread_customer_followup",
            provider_thread_id="provider-thread-customer-followup",
            labels=["sent", "customer"],
            sender="owner@example.com",
            to=["alice@customer.example"],
            subject="Re: Customer onboarding follow-up",
            sent_at="2026-05-29T09:00:00+00:00",
            body_text="Thanks. I will send proposed times after checking the team calendar.",
            is_seen=True,
            source_policy={"body_source": "provider_message", "attachment_source": "metadata_only"},
        ),
        MailMessage(
            message_id="message_invoice_notice",
            provider_message_id="provider-message-003",
            thread_id="thread_invoice_notice",
            provider_thread_id="provider-thread-invoice-notice",
            labels=["inbox", "finance"],
            sender="billing@example.com",
            to=["owner@example.com"],
            subject="Invoice notice for public test account",
            received_at="2026-05-30T02:15:00+00:00",
            body_text="The public test account invoice is ready for review.",
            is_seen=False,
            source_policy={"body_source": "provider_message", "attachment_source": "metadata_only"},
        ),
    ]


def build_fixture_threads() -> list[MailThread]:
    return [
        MailThread(
            thread_id="thread_customer_followup",
            provider_thread_id="provider-thread-customer-followup",
            subject_normalized="customer onboarding follow-up",
            participants=["alice@customer.example", "owner@example.com"],
            message_ids=["message_customer_followup_1", "message_customer_followup_2"],
            last_message_at="2026-05-29T09:00:00+00:00",
            unread_count=1,
            labels=["customer"],
            open_actions=["schedule_followup"],
        ),
        MailThread(
            thread_id="thread_invoice_notice",
            provider_thread_id="provider-thread-invoice-notice",
            subject_normalized="invoice notice for public test account",
            participants=["billing@example.com", "owner@example.com"],
            message_ids=["message_invoice_notice"],
            last_message_at="2026-05-30T02:15:00+00:00",
            unread_count=1,
            labels=["finance"],
        ),
    ]
