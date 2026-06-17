from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_true(value: Any, label: str) -> None:
    if not value:
        raise AssertionError(f"{label}: expected truthy value, got {value!r}")


def run_contracts_import() -> dict[str, Any]:
    from app.communication.observations import (
        build_communication_brief_observation,
        build_communication_thread_observation,
        build_meeting_escalation_candidate_observation,
    )
    from app.communication.types import (
        CommunicationBrief,
        CommunicationThreadRef,
        MeetingEscalationCandidate,
    )
    from app.orchestration.types import TypedObservation

    actor_context = {"tenant_id": "tenant-1", "user_id": "user-1"}
    thread_ref = CommunicationThreadRef(
        thread_id="thread_123",
        source="mail",
        subject="Renewal planning",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-17T08:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id="brief_123",
        conversation_id="conversation_123",
        thread_ref=thread_ref,
        employee_goal="Prepare renewal reply",
        customer_context_summary="Customer asked for renewal timing.",
        grounding_refs=[{"kind": "mail_message", "id": "message_123"}],
        must_include=["pricing timeline"],
        must_avoid=["unsupported discounts"],
        open_questions=["Confirm procurement owner"],
        recommended_next_action="draft_reply",
        source_observation_ids=["obs_thread_123"],
        confidence=0.82,
        created_at="2026-06-17T08:01:00+00:00",
        actor_context=actor_context,
    )
    candidate = MeetingEscalationCandidate(
        candidate_id="candidate_123",
        source_brief_id=brief.brief_id,
        topic="Renewal timeline alignment",
        attendees=["customer@example.com", "rep@example.com"],
        time_window="next week",
        reason="Open procurement question blocks draft confidence.",
        confirmation_required=True,
    )

    thread_observation = build_communication_thread_observation(thread_ref)
    brief_observation = build_communication_brief_observation(brief)
    candidate_observation = build_meeting_escalation_candidate_observation(
        candidate,
        actor_context=actor_context,
        confidence=0.7,
    )

    for observation in (thread_observation, brief_observation, candidate_observation):
        if not isinstance(observation, TypedObservation):
            raise AssertionError("builder did not return TypedObservation")
        _assert_equal(observation.status, "completed", "status")
        _assert_equal(observation.source, "communication_copilot", "source")
        _assert_equal(observation.grounding_kind, "tool", "grounding_kind")

    _assert_equal(thread_observation.observation_type, "communication_thread", "thread observation_type")
    _assert_equal(thread_observation.payload, asdict(thread_ref), "thread payload")
    _assert_equal(thread_observation.confidence, 1.0, "thread confidence")
    _assert_equal(thread_observation.actor_context, actor_context, "thread actor_context")

    _assert_equal(brief_observation.observation_type, "communication_brief", "brief observation_type")
    _assert_equal(brief_observation.payload, asdict(brief), "brief payload")
    _assert_equal(brief_observation.payload["thread_ref"]["thread_id"], thread_ref.thread_id, "brief thread_ref")
    _assert_equal(brief_observation.confidence, brief.confidence, "brief confidence")
    _assert_equal(brief_observation.actor_context, actor_context, "brief actor_context")

    _assert_equal(
        candidate_observation.observation_type,
        "meeting_escalation_candidate",
        "candidate observation_type",
    )
    _assert_equal(candidate_observation.payload, asdict(candidate), "candidate payload")
    _assert_equal(candidate_observation.confidence, 0.7, "candidate confidence")
    _assert_equal(candidate_observation.actor_context, actor_context, "candidate actor_context")
    _assert_equal(
        candidate_observation.side_effects,
        [{"kind": "confirmation_required", "allowed": False}],
        "candidate side_effects",
    )

    return {
        "ok": True,
        "case": "contracts_import",
        "observation_types": [
            thread_observation.observation_type,
            brief_observation.observation_type,
            candidate_observation.observation_type,
        ],
    }


def run_brief_assembly() -> dict[str, Any]:
    import app.communication.thread_context as thread_context_module
    from app.communication.brief_service import assemble_communication_brief_observation
    from app.orchestration.types import TypedObservation

    actor_context = {"tenant_id": "tenant-1", "user_id": "employee-1", "workspace_id": "workspace-1"}
    messages = [
        {
            "message_id": "mail_1",
            "thread_id": "thread_renewal",
            "provider_thread_id": "provider_thread_renewal",
            "sender": "customer@example.com",
            "recipients": "rep@example.com",
            "subject": "Renewal planning",
            "received_at": "2026-06-17T08:00:00+00:00",
            "summary": "Customer asked whether renewal pricing can be confirmed this week.",
        },
        {
            "message_id": "mail_2",
            "thread_id": "thread_renewal",
            "provider_thread_id": "provider_thread_renewal",
            "sender": "rep@example.com",
            "recipients": "customer@example.com",
            "subject": "Renewal planning",
            "received_at": "2026-06-17T08:05:00+00:00",
            "summary": "Rep acknowledged the renewal timing question.",
        },
    ]

    original_list_thread_messages = thread_context_module.list_thread_messages
    original_list_recent_inbound_threads = thread_context_module.list_recent_inbound_threads
    thread_context_module.list_thread_messages = lambda thread_id, **_: messages if thread_id == "thread_renewal" else []
    thread_context_module.list_recent_inbound_threads = lambda **_: []
    try:
        grounding_observation = {
            "observation_type": "enterprise_answer_observation",
            "payload": {
                "answer_state": {"confidence": 0.84, "answerable": True},
                "evidence_manifest": [
                    {
                        "doc_id": "doc_pricing",
                        "chunk_id": "chunk_pricing_1",
                        "source_type": "policy",
                        "title": "Renewal pricing policy",
                        "score": 0.91,
                    }
                ],
                "canonical_facts": [
                    {
                        "fact_id": "fact_renewal_notice",
                        "normalized_fact": "Renewal pricing requires current policy grounding.",
                    }
                ],
                "answer": "Hi customer, here is the final email body that must not leak into the brief.",
            },
            "confidence": 0.84,
        }
        memory_context = {
            "memory_hits": 1,
            "turn_memory": [{"chunk_id": "conversation-conv_1-turn_1", "source_turn_ids": "turn_1,turn_2"}],
            "merged_memory": [],
        }

        brief, observation = assemble_communication_brief_observation(
            employee_goal="Prepare renewal reply",
            conversation_id="conversation_123",
            thread_id="thread_renewal",
            grounding_observation=grounding_observation,
            memory_context=memory_context,
            actor_context=actor_context,
            source_observation_ids=["obs_thread_renewal", "obs_grounding_pricing"],
        )
    finally:
        thread_context_module.list_thread_messages = original_list_thread_messages
        thread_context_module.list_recent_inbound_threads = original_list_recent_inbound_threads

    if not isinstance(observation, TypedObservation):
        raise AssertionError("brief assembly did not return TypedObservation")

    payload = observation.payload
    _assert_equal(brief.thread_ref.thread_id, "thread_renewal", "brief thread_ref.thread_id")
    _assert_equal(brief.thread_ref.subject, "Renewal planning", "brief thread_ref.subject")
    _assert_equal(
        brief.thread_ref.participants,
        ["customer@example.com", "rep@example.com"],
        "brief thread_ref.participants",
    )
    _assert_equal(brief.thread_ref.last_message_at, "2026-06-17T08:05:00+00:00", "brief last_message_at")
    _assert_equal(brief.grounding_refs[0]["chunk_id"], "chunk_pricing_1", "brief grounding_refs")
    _assert_equal(brief.source_observation_ids, ["obs_thread_renewal", "obs_grounding_pricing"], "source_observation_ids")
    _assert_equal(brief.actor_context, actor_context, "brief actor_context")
    _assert_true(brief.confidence >= 0.8, "brief confidence")
    _assert_equal(brief.open_questions, [], "explicit grounded open_questions")
    _assert_equal(brief.recommended_next_action, "draft_with_grounding", "recommended_next_action")
    _assert_equal(observation.observation_type, "communication_brief", "observation_type")
    _assert_equal(payload["thread_ref"]["thread_id"], brief.thread_ref.thread_id, "payload thread_ref")
    _assert_equal(payload["grounding_refs"][0]["doc_id"], "doc_pricing", "payload grounding_refs")
    _assert_equal(observation.actor_context, actor_context, "observation actor_context")
    _assert_equal(observation.missing_fields, [], "observation missing_fields")
    if "answer" in payload:
        raise AssertionError("brief payload leaked an answer field")
    if "final email body" in json.dumps(payload, ensure_ascii=False):
        raise AssertionError("brief payload leaked direct final answer wording")

    thread_context_module.list_thread_messages = lambda *_, **__: []
    thread_context_module.list_recent_inbound_threads = lambda **_: []
    try:
        empty_brief, _ = assemble_communication_brief_observation(
            employee_goal="Prepare renewal reply",
            actor_context=actor_context,
            source_observation_ids=[],
        )
    finally:
        thread_context_module.list_thread_messages = original_list_thread_messages
        thread_context_module.list_recent_inbound_threads = original_list_recent_inbound_threads

    _assert_equal(empty_brief.thread_ref.thread_id, "", "empty thread_ref.thread_id")
    _assert_true("communication_thread_required" in empty_brief.open_questions, "empty thread open question")
    _assert_true("grounding_required" in empty_brief.open_questions, "empty grounding open question")
    _assert_equal(empty_brief.recommended_next_action, "request_thread_selection", "empty recommended_next_action")
    _assert_true(empty_brief.confidence <= 0.3, "empty brief confidence")

    return {
        "ok": True,
        "case": "brief_assembly",
        "brief_id": brief.brief_id,
        "thread_id": brief.thread_ref.thread_id,
        "grounding_refs": len(brief.grounding_refs),
        "empty_open_questions": empty_brief.open_questions,
    }


def _seed_thread_store_message(
    *,
    actor_context: dict[str, Any],
    message_id: str,
    uid: str,
    thread_id: str,
    provider_thread_id: str,
    sender: str,
    recipients: str,
    subject: str,
    received_at: str,
    summary: str,
    risk_hint: str = "",
) -> None:
    from app.inbound_mail_store import upsert_inbound_message

    upsert_inbound_message(
        {
            "message_id": message_id,
            "mailbox": "INBOX",
            "uid": uid,
            "thread_id": thread_id,
            "provider_thread_id": provider_thread_id,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "received_at": received_at,
            "snippet": summary,
            "summary": summary,
            "body_text": f"RAW_BODY_SHOULD_NOT_BE_PROJECTED::{message_id}",
            "body_preview": f"Preview for {message_id}",
            "risk_hint": risk_hint,
            "actor_context": actor_context,
        }
    )


def run_thread_store() -> dict[str, Any]:
    from types import SimpleNamespace

    import app.inbound_mail as inbound_mail_module
    import app.main as main_module
    import app.mail.current_provider as current_provider_module
    import app.orchestration.registry as registry_module
    from app.actor_context import DEFAULT_TENANT_ID, DEFAULT_USER_ID, DEFAULT_WORKSPACE_ID, ActorContext, actor_from_mapping
    from app.auth_store import create_login_code, verify_login_code
    from app.communication.thread_store import (
        get_active_communication_thread,
        get_communication_thread,
        list_communication_threads,
        set_active_communication_thread,
        upsert_thread_projection,
    )
    from app.inbound_mail_store import create_notification, get_inbound_message, list_notifications
    from app.mail.current_provider import CurrentImapSmtpMailProvider
    from app.models import InboundMailSyncRequest, UnifiedAgentRequest
    from app.orchestration.types import OrchestrationContext

    suffix = uuid4().hex[:8]
    actor_a_email = f"employee-a-{suffix}@threadstore.example"
    actor_a_login = create_login_code(actor_a_email)
    actor_a_auth = verify_login_code(actor_a_email, str(actor_a_login["code"]))
    actor_a = dict(actor_a_auth["user"])
    actor_a_session_token = str(actor_a_auth["session_token"])
    actor_b = {
        "tenant_id": f"tenant-thread-store-{suffix}",
        "user_id": "employee-b",
        "workspace_id": "workspace-b",
    }
    actor_a_other_workspace = {
        "tenant_id": actor_a["tenant_id"],
        "user_id": actor_a["user_id"],
        "workspace_id": "workspace-b",
    }
    local_workspace_a = {
        "tenant_id": DEFAULT_TENANT_ID,
        "user_id": DEFAULT_USER_ID,
        "workspace_id": f"local-workspace-a-{suffix}",
    }
    local_workspace_b = {
        "tenant_id": DEFAULT_TENANT_ID,
        "user_id": DEFAULT_USER_ID,
        "workspace_id": f"local-workspace-b-{suffix}",
    }
    bulk_actor = {
        "tenant_id": f"tenant-thread-store-bulk-{suffix}",
        "user_id": "employee-bulk",
        "workspace_id": "workspace-bulk",
    }
    alias_actor = {
        "tenant_id": f"tenant-thread-store-alias-{suffix}",
        "user_id": "employee-alias",
        "workspace_id": "workspace-alias",
    }
    thread_a = f"thread-store-a-{suffix}"
    thread_b = f"thread-store-b-{suffix}"
    local_thread_a = f"thread-store-local-a-{suffix}"
    local_thread_b = f"thread-store-local-b-{suffix}"
    alias_thread = f"thread-store-alias-{suffix}"
    alias_provider_thread = f"provider-{alias_thread}"
    shared_provider_message_id = f"shared-provider-msg-{suffix}"
    local_shared_provider_message_id = f"shared-local-provider-msg-{suffix}"

    def assert_http_status(fn: Any, expected_status: int, label: str) -> None:
        try:
            fn()
        except Exception as exc:
            _assert_equal(getattr(exc, "status_code", None), expected_status, label)
        else:
            raise AssertionError(f"{label}: expected HTTP {expected_status}")

    _seed_thread_store_message(
        actor_context=actor_a,
        message_id=shared_provider_message_id,
        uid=f"uid-a-1-{suffix}",
        thread_id=thread_a,
        provider_thread_id=f"provider-{thread_a}",
        sender="customer-a@example.com",
        recipients="employee-a@example.com",
        subject="Actor A renewal",
        received_at="2026-06-17T08:00:00+00:00",
        summary="Actor A first renewal question.",
        risk_hint="",
    )
    _seed_thread_store_message(
        actor_context=actor_a,
        message_id=f"msg-thread-store-a-2-{suffix}",
        uid=f"uid-a-2-{suffix}",
        thread_id=thread_a,
        provider_thread_id=f"provider-{thread_a}",
        sender="employee-a@example.com",
        recipients="customer-a@example.com",
        subject="Re: Actor A renewal",
        received_at="2026-06-17T08:05:00+00:00",
        summary="Actor A latest renewal summary.",
        risk_hint="medium",
    )
    _seed_thread_store_message(
        actor_context=actor_b,
        message_id=shared_provider_message_id,
        uid=f"uid-b-1-{suffix}",
        thread_id=thread_b,
        provider_thread_id=f"provider-{thread_b}",
        sender="customer-b@example.com",
        recipients="employee-b@example.com",
        subject="Actor B onboarding",
        received_at="2026-06-17T08:10:00+00:00",
        summary="Actor B onboarding question.",
        risk_hint="high",
    )

    actor_a_shared = get_inbound_message(shared_provider_message_id, actor_context=actor_a)
    actor_b_shared = get_inbound_message(shared_provider_message_id, actor_context=actor_b)
    _assert_true(actor_a_shared, "actor A shared provider message retained")
    _assert_true(actor_b_shared, "actor B shared provider message retained")
    _assert_equal(actor_a_shared["thread_id"], thread_a, "actor A shared message thread")
    _assert_equal(actor_b_shared["thread_id"], thread_b, "actor B shared message thread")
    _assert_equal(actor_a_shared.get("provider_message_id"), shared_provider_message_id, "actor A provider message id")
    _assert_equal(actor_b_shared.get("provider_message_id"), shared_provider_message_id, "actor B provider message id")
    if actor_a_shared["message_id"] == actor_b_shared["message_id"]:
        raise AssertionError("actor-scoped inbound storage keys collided")

    _seed_thread_store_message(
        actor_context=local_workspace_a,
        message_id=local_shared_provider_message_id,
        uid=f"uid-local-a-1-{suffix}",
        thread_id=local_thread_a,
        provider_thread_id=f"provider-{local_thread_a}",
        sender="local-customer-a@example.com",
        recipients="local-user@example.com",
        subject="Local workspace A renewal",
        received_at="2026-06-17T08:20:00+00:00",
        summary="Local workspace A renewal question.",
        risk_hint="",
    )
    _seed_thread_store_message(
        actor_context=local_workspace_b,
        message_id=local_shared_provider_message_id,
        uid=f"uid-local-b-1-{suffix}",
        thread_id=local_thread_b,
        provider_thread_id=f"provider-{local_thread_b}",
        sender="local-customer-b@example.com",
        recipients="local-user@example.com",
        subject="Local workspace B onboarding",
        received_at="2026-06-17T08:25:00+00:00",
        summary="Local workspace B onboarding question.",
        risk_hint="high",
    )
    local_a_shared = get_inbound_message(local_shared_provider_message_id, actor_context=local_workspace_a)
    local_b_shared = get_inbound_message(local_shared_provider_message_id, actor_context=local_workspace_b)
    _assert_true(local_a_shared, "local workspace A shared provider message retained")
    _assert_true(local_b_shared, "local workspace B shared provider message retained")
    _assert_equal(local_a_shared["thread_id"], local_thread_a, "local workspace A shared message thread")
    _assert_equal(local_b_shared["thread_id"], local_thread_b, "local workspace B shared message thread")
    _assert_equal(
        local_a_shared.get("provider_message_id"),
        local_shared_provider_message_id,
        "local workspace A provider message id",
    )
    _assert_equal(
        local_b_shared.get("provider_message_id"),
        local_shared_provider_message_id,
        "local workspace B provider message id",
    )
    if local_a_shared["message_id"] == local_b_shared["message_id"]:
        raise AssertionError("local-dev workspace storage keys collided")
    _assert_equal(
        get_inbound_message(local_shared_provider_message_id, actor_context=ActorContext().to_dict()),
        None,
        "default local-dev workspace does not read non-default workspace rows",
    )
    local_a_list_ids = {
        str(item.get("message_id") or "")
        for item in inbound_mail_module.list_inbound_mail_messages(actor_context=local_workspace_a, limit=10)
    }
    local_b_list_ids = {
        str(item.get("message_id") or "")
        for item in inbound_mail_module.list_inbound_mail_messages(actor_context=local_workspace_b, limit=10)
    }
    _assert_true(local_a_shared["message_id"] in local_a_list_ids, "local workspace A list includes own message")
    _assert_true(local_b_shared["message_id"] not in local_a_list_ids, "local workspace A list excludes workspace B")
    _assert_true(local_b_shared["message_id"] in local_b_list_ids, "local workspace B list includes own message")
    _assert_true(local_a_shared["message_id"] not in local_b_list_ids, "local workspace B list excludes workspace A")
    local_a_thread_ids = {
        item.get("thread_id") for item in list_communication_threads(actor_context=local_workspace_a, limit=5)
    }
    local_b_thread_ids = {
        item.get("thread_id") for item in list_communication_threads(actor_context=local_workspace_b, limit=5)
    }
    _assert_true(local_thread_a in local_a_thread_ids, "local workspace A thread listed")
    _assert_true(local_thread_b not in local_a_thread_ids, "local workspace A cannot list workspace B thread")
    _assert_true(local_thread_b in local_b_thread_ids, "local workspace B thread listed")
    _assert_true(local_thread_a not in local_b_thread_ids, "local workspace B cannot list workspace A thread")

    bulk_thread_ids: list[str] = []
    for index in range(15):
        bulk_thread_id = f"thread-store-bulk-{index:02d}-{suffix}"
        bulk_thread_ids.append(bulk_thread_id)
        _seed_thread_store_message(
            actor_context=bulk_actor,
            message_id=f"msg-thread-store-bulk-{index:02d}-{suffix}",
            uid=f"uid-bulk-{index:02d}-{suffix}",
            thread_id=bulk_thread_id,
            provider_thread_id=f"provider-{bulk_thread_id}",
            sender=f"bulk-customer-{index:02d}@example.com",
            recipients="employee-bulk@example.com",
            subject=f"Bulk thread {index:02d}",
            received_at=f"2026-06-17T09:{index:02d}:00+00:00",
            summary=f"Bulk thread {index:02d} summary.",
            risk_hint="",
        )
    bulk_threads_limit_12 = list_communication_threads(actor_context=bulk_actor, limit=12)
    bulk_threads_limit_20 = list_communication_threads(actor_context=bulk_actor, limit=20)
    _assert_equal(len(bulk_threads_limit_12), 12, "cold refresh projects more than 10 threads for limit 12")
    _assert_equal(len(bulk_threads_limit_20), 15, "cold refresh projects all seeded threads for limit 20")
    bulk_limit_20_ids = {str(item.get("thread_id") or "") for item in bulk_threads_limit_20}
    _assert_true(set(bulk_thread_ids).issubset(bulk_limit_20_ids), "bulk cold refresh includes every seeded thread")

    _seed_thread_store_message(
        actor_context=alias_actor,
        message_id=f"msg-thread-store-alias-{suffix}",
        uid=f"uid-alias-{suffix}",
        thread_id=alias_thread,
        provider_thread_id=alias_provider_thread,
        sender="alias-customer@example.com",
        recipients="employee-alias@example.com",
        subject="Alias projection",
        received_at="2026-06-17T09:40:00+00:00",
        summary="Alias projection canonical summary.",
        risk_hint="",
    )
    upsert_thread_projection(
        {"thread_id": alias_provider_thread, "subject": "Stale alias projection"},
        actor_context=alias_actor,
    )
    alias_detail = get_communication_thread(alias_provider_thread, actor_context=alias_actor)
    _assert_true(alias_detail, "provider thread alias detail resolves")
    _assert_equal(alias_detail["thread_id"], alias_thread, "provider thread alias resolves to canonical thread")
    alias_projection_ids = [
        str(item.get("thread_id") or "")
        for item in list_communication_threads(actor_context=alias_actor, limit=5, refresh=False)
    ]
    _assert_equal(alias_projection_ids.count(alias_thread), 1, "alias actor has one canonical projection")
    _assert_true(alias_provider_thread not in alias_projection_ids, "alias actor has no provider-id projection")

    actor_a_list = inbound_mail_module.list_inbound_mail_messages(actor_context=actor_a, limit=10)
    actor_b_list = inbound_mail_module.list_inbound_mail_messages(actor_context=actor_b, limit=10)
    actor_a_list_ids = {str(item.get("message_id") or "") for item in actor_a_list}
    actor_b_list_ids = {str(item.get("message_id") or "") for item in actor_b_list}
    _assert_true(actor_a_shared["message_id"] in actor_a_list_ids, "helper actor A list includes own message")
    _assert_true(actor_b_shared["message_id"] not in actor_a_list_ids, "helper actor A list excludes actor B message")
    _assert_true(actor_b_shared["message_id"] in actor_b_list_ids, "helper actor B list includes own message")
    _assert_true(actor_a_shared["message_id"] not in actor_b_list_ids, "helper actor B list excludes actor A message")
    actor_a_summary = inbound_mail_module.get_inbound_mail_summary(
        "2026-06-17T00:00:00+00:00",
        "2026-06-18T00:00:00+00:00",
        actor_context=actor_a,
    )
    actor_b_summary = inbound_mail_module.get_inbound_mail_summary(
        "2026-06-17T00:00:00+00:00",
        "2026-06-18T00:00:00+00:00",
        actor_context=actor_b,
    )
    _assert_equal(actor_a_summary["total"], 2, "helper actor A summary total")
    _assert_equal(actor_b_summary["total"], 1, "helper actor B summary total")
    actor_a_digest = inbound_mail_module.generate_daily_mail_digest(
        "2026-06-17T00:00:00+00:00",
        "2026-06-18T00:00:00+00:00",
        actor_context=actor_a,
    )
    actor_b_digest = inbound_mail_module.generate_daily_mail_digest(
        "2026-06-17T00:00:00+00:00",
        "2026-06-18T00:00:00+00:00",
        actor_context=actor_b,
    )
    _assert_equal(actor_a_digest["summary"]["total"], 2, "digest actor A summary total")
    _assert_equal(actor_b_digest["summary"]["total"], 1, "digest actor B summary total")
    digest_notification_ids = {
        str(actor_a_digest["notification"].get("notification_id") or ""),
        str(actor_b_digest["notification"].get("notification_id") or ""),
    }
    direct_leaky_digest = create_notification(
        "daily_mail_digest",
        "Leaky Actor A renewal",
        "customer-a@example.com: Actor A latest renewal summary.",
        {"summary": actor_a_summary},
    )
    digest_notification_ids.add(str(direct_leaky_digest.get("notification_id") or ""))
    outbox_digest_notifications = [
        item
        for item in list_notifications(event_type="daily_mail_digest", limit=200)
        if str(item.get("notification_id") or "") in digest_notification_ids
    ]
    api_digest_notifications = [
        item.model_dump() if hasattr(item, "model_dump") else item.dict()
        for item in main_module.notification_outbox_api(event_type="daily_mail_digest", limit=200)
        if str(getattr(item, "notification_id", "") or "") in digest_notification_ids
    ]
    _assert_equal(len(outbox_digest_notifications), 3, "store outbox contains new digest notifications")
    _assert_equal(len(api_digest_notifications), 3, "public outbox contains new digest notifications")
    digest_sensitive_markers = [
        shared_provider_message_id,
        str(actor_a_shared["message_id"] or ""),
        str(actor_b_shared["message_id"] or ""),
        thread_a,
        thread_b,
        "customer-a@example.com",
        "employee-a@example.com",
        "customer-b@example.com",
        "employee-b@example.com",
        "Actor A renewal",
        "Re: Actor A renewal",
        "Actor B onboarding",
        "Actor A first renewal question.",
        "Actor A latest renewal summary.",
        "Actor B onboarding question.",
    ]
    for source_name, notifications in [
        ("store", outbox_digest_notifications),
        ("public API", api_digest_notifications),
    ]:
        for notification in notifications:
            payload = dict(notification.get("payload_json") or {})
            _assert_equal(payload.get("details_redacted"), True, f"{source_name} digest payload redacted")
            if "summary" in payload or "recent_messages" in payload or "important_messages" in payload:
                raise AssertionError(f"{source_name} digest notification persisted message summary lists")
            serialized_notification = json.dumps(notification, ensure_ascii=False)
            for marker in digest_sensitive_markers:
                if marker and marker in serialized_notification:
                    raise AssertionError(f"{source_name} digest notification leaked actor mail content: {marker}")

    provider = CurrentImapSmtpMailProvider()
    provider_search_a = provider.search_messages(limit=10, actor_context=actor_a)
    provider_search_b = provider.search_messages(limit=10, actor_context=actor_b)
    _assert_true(provider_search_a.ok, "provider actor A search ok")
    _assert_true(provider_search_b.ok, "provider actor B search ok")
    provider_a_message_ids = {
        str(item.get("message_id") or "") for item in list(provider_search_a.data.get("messages") or [])
    }
    provider_b_message_ids = {
        str(item.get("message_id") or "") for item in list(provider_search_b.data.get("messages") or [])
    }
    _assert_true(actor_a_shared["message_id"] in provider_a_message_ids, "provider actor A search includes own message")
    _assert_true(actor_b_shared["message_id"] not in provider_a_message_ids, "provider actor A search excludes actor B message")
    _assert_true(actor_b_shared["message_id"] in provider_b_message_ids, "provider actor B search includes own message")
    _assert_true(actor_a_shared["message_id"] not in provider_b_message_ids, "provider actor B search excludes actor A message")

    provider_read_a = provider.read_message(shared_provider_message_id, actor_context=actor_a)
    provider_read_b = provider.read_message(shared_provider_message_id, actor_context=actor_b)
    _assert_true(provider_read_a.ok, "provider actor A read shared provider id")
    _assert_true(provider_read_b.ok, "provider actor B read shared provider id")
    provider_read_a_message = dict(provider_read_a.data.get("message") or {})
    provider_read_b_message = dict(provider_read_b.data.get("message") or {})
    _assert_equal(provider_read_a_message.get("thread_id"), thread_a, "provider actor A read thread")
    _assert_equal(provider_read_b_message.get("thread_id"), thread_b, "provider actor B read thread")
    _assert_equal(
        provider_read_a_message.get("provider_message_id"),
        shared_provider_message_id,
        "provider actor A preserved provider id",
    )
    _assert_equal(
        provider_read_b_message.get("provider_message_id"),
        shared_provider_message_id,
        "provider actor B preserved provider id",
    )
    blocked_cross_read = provider.read_message(str(actor_b_shared["message_id"] or ""), actor_context=actor_a)
    _assert_equal(blocked_cross_read.ok, False, "provider actor A cannot read actor B storage id")
    _assert_equal(blocked_cross_read.status, "not_found", "provider cross actor read status")

    context_a = OrchestrationContext(
        session_id=f"session-thread-store-{suffix}",
        conversation_id=f"conversation-thread-store-{suffix}",
        message="Read inbound mail",
        safe_message="Read inbound mail",
        actor_context=actor_a,
    )
    registry_search_a = registry_module._inbound_message_search({"limit": 10}, context_a, {})
    registry_search_a_ids = {
        str(item.get("message_id") or "") for item in list(registry_search_a.get("messages") or [])
    }
    _assert_true(actor_a_shared["message_id"] in registry_search_a_ids, "registry search actor A includes own message")
    _assert_true(actor_b_shared["message_id"] not in registry_search_a_ids, "registry search actor A excludes actor B message")
    registry_summary_a = registry_module._inbound_mail_summary(
        {
            "since": "2026-06-17T00:00:00+00:00",
            "until": "2026-06-18T00:00:00+00:00",
        },
        context_a,
        {},
    )
    _assert_equal(registry_summary_a["total"], 2, "registry actor A summary total")
    registry_read_a = registry_module._inbound_message_read({"message_id": shared_provider_message_id}, context_a, {})
    _assert_equal(dict(registry_read_a.get("message") or {}).get("thread_id"), thread_a, "registry actor A read own provider id")
    registry_cross_read = registry_module._inbound_message_read(
        {"message_id": str(actor_b_shared["message_id"] or "")},
        context_a,
        {},
    )
    _assert_true("error" in registry_cross_read, "registry actor A cannot read actor B storage id")

    original_inbound_get_llm = inbound_mail_module.get_llm
    inbound_mail_module.get_llm = lambda **_: (_ for _ in ()).throw(RuntimeError("forced draft fallback"))
    try:
        helper_draft_a = inbound_mail_module.draft_reply_for_message(shared_provider_message_id, actor_context=actor_a)
        _assert_equal(helper_draft_a["message"]["thread_id"], thread_a, "helper draft actor A thread")
        try:
            inbound_mail_module.draft_reply_for_message(str(actor_b_shared["message_id"] or ""), actor_context=actor_a)
        except KeyError:
            pass
        else:
            raise AssertionError("helper draft actor A read actor B storage id")
        registry_draft_cross = registry_module._inbound_reply_draft(
            {"message_id": str(actor_b_shared["message_id"] or "")},
            context_a,
            {},
        )
        _assert_true("error" in registry_draft_cross, "registry draft actor A cannot read actor B storage id")
    finally:
        inbound_mail_module.get_llm = original_inbound_get_llm

    actor_a_spoof_request = SimpleNamespace(
        headers={
            "x-tenant-id": actor_a["tenant_id"],
            "x-user-id": actor_a["user_id"],
            "x-workspace-id": actor_a["workspace_id"],
        }
    )
    assert_http_status(
        lambda: main_module.inbound_mail_summary_api(
            actor_a_spoof_request,
            since="2026-06-17T00:00:00+00:00",
            until="2026-06-18T00:00:00+00:00",
        ),
        401,
        "spoofed non-local public summary requires auth session",
    )
    assert_http_status(
        lambda: main_module.inbound_mail_messages_api(actor_a_spoof_request, limit=10),
        401,
        "spoofed non-local public messages require auth session",
    )
    assert_http_status(
        lambda: main_module.draft_inbound_mail_reply_api(shared_provider_message_id, actor_a_spoof_request),
        401,
        "spoofed non-local public draft requires auth session",
    )
    assert_http_status(
        lambda: main_module.generate_daily_mail_digest_api(actor_a_spoof_request),
        401,
        "spoofed non-local digest requires auth session",
    )
    assert_http_status(
        lambda: main_module.sync_inbound_mail_api(actor_a_spoof_request, InboundMailSyncRequest(limit=1)),
        401,
        "spoofed non-local sync requires auth session",
    )
    assert_http_status(
        lambda: main_module.enqueue_inbound_mail_sync_api(actor_a_spoof_request),
        401,
        "spoofed non-local async sync requires auth session",
    )
    local_request = SimpleNamespace(headers={})
    local_summary = main_module.inbound_mail_summary_api(
        local_request,
        since="2026-06-17T00:00:00+00:00",
        until="2026-06-18T00:00:00+00:00",
    )
    _assert_true(local_summary.total >= 0, "local-dev public summary remains allowed")
    main_module.inbound_mail_messages_api(local_request, limit=1)

    actor_a_request = SimpleNamespace(headers={"x-auth-session": actor_a_session_token})
    api_summary_a = main_module.inbound_mail_summary_api(
        actor_a_request,
        since="2026-06-17T00:00:00+00:00",
        until="2026-06-18T00:00:00+00:00",
    )
    _assert_equal(api_summary_a.total, 2, "public API actor A summary total")
    api_messages_a = main_module.inbound_mail_messages_api(actor_a_request, limit=10)
    api_message_ids_a = {item.message_id for item in api_messages_a}
    _assert_true(actor_a_shared["message_id"] in api_message_ids_a, "public API actor A messages include own message")
    _assert_true(actor_b_shared["message_id"] not in api_message_ids_a, "public API actor A messages exclude actor B message")
    inbound_mail_module.get_llm = lambda **_: (_ for _ in ()).throw(RuntimeError("forced draft fallback"))
    try:
        api_draft_a = main_module.draft_inbound_mail_reply_api(shared_provider_message_id, actor_a_request)
        _assert_equal(api_draft_a.message.thread_id, thread_a, "public API actor A draft thread")
        try:
            main_module.draft_inbound_mail_reply_api(str(actor_b_shared["message_id"] or ""), actor_a_request)
        except Exception as exc:
            _assert_equal(getattr(exc, "status_code", None), 404, "public API actor A cross draft status")
        else:
            raise AssertionError("public API actor A drafted actor B storage id")
    finally:
        inbound_mail_module.get_llm = original_inbound_get_llm

    from datetime import datetime, timezone

    sync_provider_message_id = f"sync-provider-msg-{suffix}"
    sync_thread_id = f"sync-thread-{suffix}"

    class FakeIMAP:
        def __enter__(self) -> "FakeIMAP":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def login(self, *_args: Any) -> None:
            return None

        def select(self, *_args: Any, **_kwargs: Any) -> tuple[str, list[bytes]]:
            return "OK", [b"1"]

        def uid(self, command: str, *_args: Any) -> tuple[str, list[Any]]:
            if command == "search":
                return "OK", [b"101"]
            if command == "fetch":
                return "OK", [(b"FLAGS () RFC822", b"raw")]
            return "NO", []

    original_sync_settings = inbound_mail_module.get_settings
    original_imap_ssl = inbound_mail_module.imaplib.IMAP4_SSL
    original_parse_fetched = inbound_mail_module._parse_fetched_message
    inbound_mail_module.get_settings = lambda: SimpleNamespace(
        imap_enabled=True,
        imap_mailbox="INBOX",
        imap_username="sync-user",
        imap_password="sync-password",
        imap_host="imap.example.invalid",
        imap_port=993,
    )
    inbound_mail_module.imaplib.IMAP4_SSL = lambda *_args, **_kwargs: FakeIMAP()
    inbound_mail_module._parse_fetched_message = lambda *_args, **_kwargs: {
        "message_id": sync_provider_message_id,
        "mailbox": "INBOX",
        "uid": f"sync-uid-{suffix}",
        "thread_id": sync_thread_id,
        "provider_thread_id": f"provider-{sync_thread_id}",
        "sender": "sync-customer@example.com",
        "recipients": "employee-a@example.com",
        "subject": "Actor sync boundary",
        "received_at": "2026-06-17T10:00:00+00:00",
        "snippet": "Actor sync snippet.",
        "summary": "Actor sync summary.",
        "body_text": "Actor sync body.",
        "body_html_sanitized": "",
        "body_preview": "Actor sync body.",
        "labels": [],
        "attachments": [],
        "headers_json": {},
        "risk_hint": "",
        "raw_size": 128,
        "is_seen": False,
    }
    try:
        sync_result = inbound_mail_module.sync_inbound_mail(
            since=datetime(2026, 6, 17, tzinfo=timezone.utc),
            until=datetime(2026, 6, 18, tzinfo=timezone.utc),
            limit=1,
            actor_context=actor_a,
        )
    finally:
        inbound_mail_module.get_settings = original_sync_settings
        inbound_mail_module.imaplib.IMAP4_SSL = original_imap_ssl
        inbound_mail_module._parse_fetched_message = original_parse_fetched
    _assert_equal(sync_result.get("ok"), True, "actor sync helper ok")
    sync_actor_message = get_inbound_message(sync_provider_message_id, actor_context=actor_a)
    _assert_true(sync_actor_message, "actor sync message stored under actor")
    _assert_equal(sync_actor_message["thread_id"], sync_thread_id, "actor sync message thread")
    _assert_equal(
        get_inbound_message(sync_provider_message_id, actor_context=ActorContext().to_dict()),
        None,
        "actor sync message not stored under default actor",
    )

    original_main_sync = main_module.sync_inbound_mail
    captured_sync_api: dict[str, Any] = {}

    def fake_main_sync(**kwargs: Any) -> dict[str, Any]:
        captured_sync_api.clear()
        captured_sync_api.update(kwargs)
        return {"enabled": True, "ok": True, "synced": 0, "new": 0, "mailbox": "INBOX", "state": {}}

    main_module.sync_inbound_mail = fake_main_sync
    try:
        main_module.sync_inbound_mail_api(local_request, InboundMailSyncRequest(limit=2))
        _assert_equal(
            dict(captured_sync_api.get("actor_context") or {}).get("workspace_id"),
            DEFAULT_WORKSPACE_ID,
            "local-dev sync API keeps default workspace",
        )
        main_module.sync_inbound_mail_api(actor_a_request, InboundMailSyncRequest(limit=2))
        _assert_equal(
            dict(captured_sync_api.get("actor_context") or {}).get("tenant_id"),
            actor_a["tenant_id"],
            "authenticated sync API forwards actor tenant",
        )
    finally:
        main_module.sync_inbound_mail = original_main_sync

    original_enqueue_sync = main_module.enqueue_inbound_mail_sync
    captured_enqueue_sync: dict[str, Any] = {}
    main_module.enqueue_inbound_mail_sync = lambda actor_context=None: (
        captured_enqueue_sync.clear(),
        captured_enqueue_sync.update(actor_context or {}),
        "task-sync-test",
    )[-1]
    try:
        enqueue_sync_response = main_module.enqueue_inbound_mail_sync_api(actor_a_request)
        _assert_equal(enqueue_sync_response["task_id"], "task-sync-test", "async sync API task id")
        _assert_equal(captured_enqueue_sync.get("tenant_id"), actor_a["tenant_id"], "async sync forwards actor tenant")
    finally:
        main_module.enqueue_inbound_mail_sync = original_enqueue_sync

    original_provider_sync = current_provider_module.sync_inbound_mail
    captured_provider_sync: dict[str, Any] = {}
    current_provider_module.sync_inbound_mail = lambda **kwargs: (
        captured_provider_sync.clear(),
        captured_provider_sync.update(kwargs),
        {"enabled": True, "ok": True, "synced": 0, "new": 0, "mailbox": "INBOX", "state": {}},
    )[-1]
    try:
        provider_sync = current_provider_module.CurrentImapSmtpMailProvider(
            allow_external_sync=True,
        ).sync_mailbox(actor_context=actor_a)
        _assert_true(provider_sync.ok, "provider sync wrapper ok")
        _assert_equal(
            dict(captured_provider_sync.get("actor_context") or {}).get("tenant_id"),
            actor_a["tenant_id"],
            "provider sync forwards actor tenant",
        )
    finally:
        current_provider_module.sync_inbound_mail = original_provider_sync

    actor_a_threads = list_communication_threads(actor_context=actor_a, limit=5)
    actor_b_threads = list_communication_threads(actor_context=actor_b, limit=5)
    actor_a_other_workspace_threads = list_communication_threads(actor_context=actor_a_other_workspace, limit=5)

    actor_a_thread_ids = {item.get("thread_id") for item in actor_a_threads}
    actor_b_thread_ids = {item.get("thread_id") for item in actor_b_threads}
    _assert_true(thread_a in actor_a_thread_ids, "actor A thread listed")
    _assert_true(thread_b not in actor_a_thread_ids, "actor A cannot list actor B thread")
    _assert_true(thread_b in actor_b_thread_ids, "actor B thread listed")
    _assert_true(thread_a not in actor_b_thread_ids, "actor B cannot list actor A thread")
    _assert_equal(actor_a_other_workspace_threads, [], "workspace-isolated actor A listing")

    actor_a_detail = get_communication_thread(thread_a, actor_context=actor_a)
    _assert_true(actor_a_detail, "actor A thread detail")
    _assert_equal(actor_a_detail["thread_id"], thread_a, "actor A detail thread_id")
    _assert_equal(actor_a_detail["source_message_ids"][-1], f"msg-thread-store-a-2-{suffix}", "source message ids")
    _assert_equal(actor_a_detail["latest_summary"], "Actor A latest renewal summary.", "latest summary")
    _assert_equal(actor_a_detail["risk_hint"], "medium", "risk hint")
    if "RAW_BODY_SHOULD_NOT_BE_PROJECTED" in json.dumps(actor_a_detail, ensure_ascii=False):
        raise AssertionError("thread projection duplicated raw message body")

    _assert_equal(get_communication_thread(thread_b, actor_context=actor_a), None, "actor A cannot read actor B thread")
    _assert_equal(
        set_active_communication_thread(thread_b, actor_context=actor_a),
        None,
        "actor A cannot activate actor B thread",
    )

    payload = UnifiedAgentRequest(
        session_id=f"session-thread-store-{suffix}",
        conversation_id=f"conversation-thread-store-{suffix}",
        message="Please summarize the recent customer thread.",
    )
    actor_a_candidates = main_module._collect_outbound_candidates(
        payload,
        payload.conversation_id or "",
        {},
        actor_context=actor_a,
    )
    actor_a_mail_candidate_ids = {
        str(item.get("candidate_id") or "") for item in actor_a_candidates if item.get("kind") == "mail_thread"
    }
    _assert_true(f"mail-thread:{thread_a}" in actor_a_mail_candidate_ids, "actor A outbound candidate includes own thread")
    _assert_true(f"mail-thread:{thread_b}" not in actor_a_mail_candidate_ids, "actor A outbound candidates exclude actor B thread")

    main_module._ensure_internal_communication_thread_access(
        SimpleNamespace(headers={}),
        ActorContext(),
    )
    try:
        main_module._ensure_internal_communication_thread_access(
            SimpleNamespace(
                headers={
                    "x-tenant-id": actor_a["tenant_id"],
                    "x-user-id": actor_a["user_id"],
                    "x-workspace-id": actor_a["workspace_id"],
                }
            ),
            actor_from_mapping(actor_a),
        )
    except Exception as exc:
        _assert_equal(getattr(exc, "status_code", None), 401, "non-local internal endpoint requires auth session")
    else:
        raise AssertionError("non-local internal endpoint access without auth session was allowed")

    selected_a = set_active_communication_thread(thread_a, actor_context=actor_a)
    _assert_true(selected_a, "actor A selected active thread")
    _assert_equal(selected_a["thread_id"], thread_a, "selected actor A thread")
    active_a = get_active_communication_thread(actor_context=actor_a)
    _assert_true(active_a, "actor A active thread")
    _assert_equal(active_a["thread_id"], thread_a, "actor A active thread id")
    _assert_equal(get_active_communication_thread(actor_context=actor_b), None, "actor B has no active thread yet")
    _assert_equal(
        get_active_communication_thread(actor_context=actor_a_other_workspace),
        None,
        "active thread is workspace-isolated",
    )

    selected_b = set_active_communication_thread(thread_b, actor_context=actor_b)
    _assert_true(selected_b, "actor B selected active thread")
    _assert_equal(selected_b["thread_id"], thread_b, "selected actor B thread")
    _assert_equal(get_active_communication_thread(actor_context=actor_a)["thread_id"], thread_a, "actor A active remains isolated")
    _assert_equal(get_active_communication_thread(actor_context=actor_b)["thread_id"], thread_b, "actor B active thread id")

    return {
        "ok": True,
        "case": "thread_store",
        "actor_a_threads": sorted(actor_a_thread_ids),
        "actor_b_threads": sorted(actor_b_thread_ids),
        "actor_a_active": active_a["thread_id"],
        "actor_b_active": thread_b,
    }


def run_mail_closeout() -> dict[str, Any]:
    import app.main as main_module
    from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND, MailDraft
    from app.mail.source_resolver import resolve_mail_source_request
    from app.outbound_delivery import build_mail_action_plan

    raw_rag_answer = "RAW_RAG_FINAL_ANSWER_SHOULD_NOT_BECOME_MAIL_BODY"
    brief_payload = {
        "brief_id": "brief_closeout_123",
        "conversation_id": "conversation_closeout_123",
        "thread_ref": {
            "thread_id": "thread_closeout_123",
            "source": "mail",
            "subject": "Renewal planning",
            "participants": ["customer@example.com", "rep@example.com"],
            "last_message_at": "2026-06-17T08:05:00+00:00",
        },
        "employee_goal": "Prepare a renewal reply",
        "customer_context_summary": "Customer asked whether renewal pricing can be confirmed this week.",
        "grounding_refs": [{"kind": "enterprise_fact", "doc_id": "doc_pricing", "chunk_id": "chunk_pricing_1"}],
        "must_include": ["pricing timeline"],
        "must_avoid": ["unsupported discounts"],
        "open_questions": [],
        "recommended_next_action": "draft_with_grounding",
        "source_observation_ids": ["obs_brief_closeout"],
        "confidence": 0.86,
    }
    brief_content = json.dumps(brief_payload, ensure_ascii=False, sort_keys=True)
    candidates = [
        {
            "candidate_id": "assistant-turn:raw-answer",
            "kind": "assistant_last_answer",
            "label": "raw prior answer",
            "content": raw_rag_answer,
            "content_type": "text/plain",
        },
        {
            "candidate_id": "communication-brief:brief_closeout_123",
            "kind": COMMUNICATION_BRIEF_SOURCE_KIND,
            "label": "communication brief: Renewal planning",
            "content": brief_content,
            "content_type": "application/json",
        },
        {
            "candidate_id": "mail-thread:incidental",
            "kind": "mail_thread",
            "label": "mail thread: unrelated invoice",
            "content": json.dumps({"thread_id": "incidental", "subject": "Unrelated invoice"}, ensure_ascii=False),
            "content_type": "application/json",
        },
    ]

    source_resolution = resolve_mail_source_request(
        message="Please email the customer at customer@example.com with the closeout.",
        candidates=candidates,
        legacy_referential_request=True,
        explicit_summary=True,
    )
    _assert_equal(source_resolution["source_mode"], COMMUNICATION_BRIEF_SOURCE_KIND, "source_mode")
    _assert_equal(source_resolution["compose_mode"], "recipient_ready_summary", "compose_mode")
    _assert_equal(
        source_resolution["selected_candidate_ids"],
        ["communication-brief:brief_closeout_123"],
        "selected candidate",
    )
    explicit_thread_resolution = resolve_mail_source_request(
        message="Please use the Procurement Renewal thread for the email to customer@example.com.",
        candidates=[
            {
                "candidate_id": "mail-thread:procurement-renewal",
                "kind": "mail_thread",
                "label": "mail thread: Procurement Renewal",
                "content": json.dumps(
                    {"thread_id": "procurement-renewal", "subject": "Procurement Renewal"},
                    ensure_ascii=False,
                ),
                "content_type": "application/json",
            },
            {
                "candidate_id": "communication-brief:brief_closeout_123",
                "kind": COMMUNICATION_BRIEF_SOURCE_KIND,
                "label": "communication brief: Renewal planning",
                "content": brief_content,
                "content_type": "application/json",
            },
        ],
        legacy_referential_request=True,
        explicit_summary=True,
    )
    _assert_equal(explicit_thread_resolution["source_mode"], "mail_thread", "explicit thread source mode")
    _assert_equal(
        explicit_thread_resolution["selected_candidate_ids"],
        ["mail-thread:procurement-renewal"],
        "explicit thread selected candidate",
    )
    upload_resolution = resolve_mail_source_request(
        message="Please email the uploaded file content to customer@example.com.",
        candidates=[
            {
                "candidate_id": "upload:current",
                "kind": "uploaded_text",
                "label": "current upload",
                "content": "Current uploaded content that must not be overridden by a stale brief.",
                "content_type": "text/plain",
            },
            {
                "candidate_id": "communication-brief:brief_closeout_123",
                "kind": COMMUNICATION_BRIEF_SOURCE_KIND,
                "label": "communication brief: Renewal planning",
                "content": brief_content,
                "content_type": "application/json",
            },
        ],
        legacy_referential_request=True,
        explicit_summary=False,
    )
    _assert_equal(upload_resolution["source_mode"], "uploaded_content", "upload source mode")
    _assert_equal(upload_resolution["selected_candidate_ids"], ["upload:current"], "upload selected candidate")
    _assert_equal(upload_resolution["needs_clarification"], False, "upload clarification")
    prior_answer_resolution = resolve_mail_source_request(
        message="Please forward that information to customer@example.com.",
        candidates=[
            {
                "candidate_id": "assistant-turn:single-prior",
                "kind": "assistant_last_answer",
                "label": "single prior answer",
                "content": "Prior assistant answer that should be rewritten for the recipient.",
                "content_type": "text/plain",
            },
            {
                "candidate_id": "mail-thread:unrelated",
                "kind": "mail_thread",
                "label": "mail thread: unrelated renewal thread",
                "content": json.dumps({"thread_id": "unrelated", "subject": "Unrelated"}, ensure_ascii=False),
                "content_type": "application/json",
            },
        ],
        legacy_referential_request=True,
        explicit_summary=True,
    )
    _assert_equal(prior_answer_resolution["source_mode"], "prior_assistant_answer", "prior answer source mode")
    _assert_equal(
        prior_answer_resolution["selected_candidate_ids"],
        ["assistant-turn:single-prior"],
        "prior answer selected candidate",
    )
    _assert_equal(prior_answer_resolution["needs_clarification"], False, "prior answer clarification")
    anchored_prior_answer_resolution = resolve_mail_source_request(
        message="Please forward the MedThink EU failover answer to customer@example.com.",
        candidates=[
            {
                "candidate_id": "assistant-turn:medthink-failover",
                "kind": "assistant_last_answer",
                "label": "assistant answer: MedThink EU failover",
                "content": "MedThink EU failover uses EU hot standby, with US as a short-term fallback.",
                "content_type": "text/plain",
            },
            {
                "candidate_id": "communication-brief:brief_closeout_123",
                "kind": COMMUNICATION_BRIEF_SOURCE_KIND,
                "label": "communication brief: Renewal planning",
                "content": brief_content,
                "content_type": "application/json",
            },
        ],
        legacy_referential_request=True,
        explicit_summary=True,
    )
    _assert_equal(anchored_prior_answer_resolution["source_mode"], "prior_assistant_answer", "anchored prior answer source mode")
    _assert_equal(
        anchored_prior_answer_resolution["selected_candidate_ids"],
        ["assistant-turn:medthink-failover"],
        "anchored prior answer selected candidate",
    )
    _assert_equal(anchored_prior_answer_resolution["needs_clarification"], False, "anchored prior answer clarification")

    plan_result = build_mail_action_plan(
        message="Please email the customer at customer@example.com with the closeout.",
        request_message="Please email the customer at customer@example.com with the closeout.",
        candidates=candidates,
        destination_email="customer@example.com",
        referential_request=True,
        explicit_summary=True,
        send_both=False,
        conversation_id="conversation_closeout_123",
        source_resolution=source_resolution,
    )
    normalized_result = main_module._normalize_communication_brief_mail_plan_result(plan_result)
    mail_plan = dict(normalized_result.get("mail_plan") or {})

    _assert_equal(normalized_result.get("mode"), "confirmation_required", "mode")
    _assert_equal(mail_plan.get("target_object"), COMMUNICATION_BRIEF_SOURCE_KIND, "target_object")
    _assert_equal(mail_plan.get("compose_mode"), "recipient_ready_summary", "normalized compose_mode")
    _assert_equal(mail_plan.get("resolved_recipients"), ["customer@example.com"], "recipient")
    _assert_true(str(mail_plan.get("resolved_subject") or "").strip(), "subject")
    _assert_equal(mail_plan.get("resolved_body"), "", "body before renderer")
    _assert_true(mail_plan.get("requires_confirmation"), "confirmation required")
    _assert_equal(mail_plan.get("status"), "pending_confirmation", "plan status")
    _assert_equal(dict(mail_plan.get("source_resolution") or {}).get("source_mode"), COMMUNICATION_BRIEF_SOURCE_KIND, "plan source mode")
    _assert_true(
        {
            "kind": "reference_source",
            "role": COMMUNICATION_BRIEF_SOURCE_KIND,
            "policy": "recipient_ready_summary",
        }
        in list(mail_plan.get("body_sources") or []),
        "communication brief body source",
    )
    _assert_equal(list(mail_plan.get("reference_sources") or [])[0].get("role"), COMMUNICATION_BRIEF_SOURCE_KIND, "reference role")
    _assert_equal(list(mail_plan.get("source_artifacts") or [])[0].get("kind"), COMMUNICATION_BRIEF_SOURCE_KIND, "source artifact kind")
    _assert_equal(list(mail_plan.get("provenance_refs") or [])[0].get("kind"), COMMUNICATION_BRIEF_SOURCE_KIND, "provenance kind")
    if raw_rag_answer in str(mail_plan.get("resolved_body") or ""):
        raise AssertionError("raw RAG answer leaked into resolved body before renderer")
    if raw_rag_answer in json.dumps(mail_plan.get("reference_sources") or [], ensure_ascii=False):
        raise AssertionError("raw RAG answer leaked into communication brief reference sources")

    captured: dict[str, Any] = {}
    original_renderer = main_module.render_mail_authoring

    def _fake_renderer(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        rendered_body = "Customer-ready renewal reply grounded in the communication brief."
        return {
            "user_message": "Draft prepared.",
            "body_for_sending": rendered_body,
            "clarification_question": "",
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
            "used_fallback": False,
        }

    main_module.render_mail_authoring = _fake_renderer
    try:
        rendered_plan, render_result = main_module._render_mail_plan_with_llm(
            message="Please email the customer at customer@example.com with the closeout.",
            mail_plan=mail_plan,
            render_mode="confirmation",
            observations=[
                {
                    "observation_type": "communication_brief",
                    "payload": brief_payload,
                    "summary": "Communication brief captured.",
                }
            ],
            candidates=candidates,
        )
    finally:
        main_module.render_mail_authoring = original_renderer

    captured_plan = dict(captured.get("mail_plan") or {})
    _assert_equal(captured.get("render_mode"), "confirmation", "render mode")
    _assert_equal(list(captured_plan.get("reference_sources") or [])[0].get("role"), COMMUNICATION_BRIEF_SOURCE_KIND, "renderer reference role")
    if raw_rag_answer in str(captured_plan.get("resolved_body") or ""):
        raise AssertionError("raw RAG answer leaked into renderer body input")
    if raw_rag_answer in json.dumps(captured.get("candidates") or [], ensure_ascii=False):
        raise AssertionError("raw RAG answer leaked into renderer candidate previews")
    _assert_true(str(rendered_plan.get("resolved_body") or "").strip(), "rendered body")
    _assert_equal(rendered_plan.get("authoring_status"), "completed", "authoring status")
    _assert_equal(render_result.get("body_for_sending"), rendered_plan.get("resolved_body"), "rendered body lifecycle")

    draft = MailDraft(
        conversation_id="conversation_closeout_123",
        to=list(rendered_plan.get("resolved_recipients") or []),
        subject=str(rendered_plan.get("resolved_subject") or ""),
        body_text=str(rendered_plan.get("resolved_body") or ""),
        source_refs=list(rendered_plan.get("source_refs") or []),
        body_sources=list(rendered_plan.get("body_sources") or []),
        source_policy=dict(rendered_plan.get("source_policy") or {}),
        requires_confirmation=True,
        requires_dlp=True,
    )
    _assert_equal(draft.status, "draft_ready", "draft status")
    _assert_true(draft.requires_confirmation, "draft confirmation boundary")
    _assert_true(draft.requires_dlp, "draft DLP boundary")
    _assert_equal(draft.missing_fields, [], "draft missing fields")

    return {
        "ok": True,
        "case": "mail_closeout",
        "source_mode": source_resolution["source_mode"],
        "compose_mode": mail_plan.get("compose_mode"),
        "draft_status": draft.status,
        "requires_confirmation": draft.requires_confirmation,
        "requires_dlp": draft.requires_dlp,
    }


def run_subordinate_inputs() -> dict[str, Any]:
    import app.main as main_module
    from app.enterprise_rag.core.service import build_enterprise_answer_observation
    from app.orchestration.dag_executor import execute_dag_plan
    from app.orchestration.domain_agents import build_domain_agent_catalog_dict
    from app.orchestration.types import OrchestrationContext

    rag_observation = build_enterprise_answer_observation(
        {
            "correlation_id": "rag_subordinate_contract",
            "confidence": 0.86,
            "supporting_doc_ids": ["doc-1"],
            "context_sources": ["enterprise_rag"],
            "answer_debug": {"answerable": True, "answer_intent": "enterprise_question"},
            "retrieval_stage_debug": {"budget_profile": "sample"},
            "canonical_facts": [
                {
                    "fact_id": "fact-1",
                    "fact_type": "policy",
                    "normalized_fact": "Grounding fact for downstream communication.",
                    "priority": "high",
                    "score": 0.9,
                    "source_fact_ids": ["source-fact-1"],
                }
            ],
            "citations": [
                {
                    "doc_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "source_type": "policy",
                    "title": "Policy",
                    "snippet": "Grounding evidence.",
                    "score": 0.91,
                }
            ],
        }
    )
    _assert_equal(rag_observation.get("communication_role"), "grounding_provider", "rag communication_role")
    _assert_equal(rag_observation.get("communication_input_kind"), "grounding_bundle", "rag communication_input_kind")
    _assert_equal(rag_observation.get("communication_owner"), "mail_or_brief_closeout", "rag closeout owner")
    _assert_equal(
        dict(rag_observation.get("diagnostic_summary") or {}).get("communication_role"),
        "grounding_provider",
        "rag diagnostic role",
    )

    task = {
        "task_id": "task_meeting_subordinate_contract",
        "status": "completed",
        "task_type": "domain_meeting",
        "domain_action": "meeting_create_tencent_meeting",
        "domain_result": {
            "ok": True,
            "communication_role": "escalation_provider",
            "communication_input_kind": "meeting_result",
            "communication_closeout_owner": "mail_agent",
            "result": {
                "meeting_id": "meeting-subordinate-123",
                "meeting_url": "https://meeting.tencent.com/subordinate",
                "communication_role": "escalation_provider",
                "communication_input_kind": "meeting_result",
            },
            "post_confirm_results": [],
        },
    }
    mail_plan = main_module._mail_plan_from_meeting_task(
        task=task,
        conversation_id="conversation_subordinate_contract",
        request_message="send invitation to alice@example.com",
        recipient="alice@example.com",
    )
    _assert_equal(mail_plan.get("communication_role"), "escalation_provider", "meeting plan role")
    _assert_equal(mail_plan.get("communication_input_kind"), "meeting_result", "meeting plan input kind")
    _assert_equal(mail_plan.get("communication_closeout_owner"), "mail_agent", "meeting plan closeout owner")
    _assert_equal(mail_plan.get("mail_action_type"), "send_meeting_invitation", "mail action type")
    _assert_true(mail_plan.get("requires_confirmation"), "mail confirmation")
    _assert_true(dict(mail_plan.get("body_constraints") or {}).get("send_requires_dlp"), "mail DLP")
    _assert_equal(list(mail_plan.get("reference_sources") or [])[0].get("communication_role"), "escalation_provider", "reference role")

    catalog = build_domain_agent_catalog_dict()
    _assert_equal(catalog["enterprise_rag"].get("communication_role"), "grounding_provider", "catalog rag role")
    _assert_equal(catalog["enterprise_rag"].get("communication_input_kind"), "grounding_bundle", "catalog rag kind")
    _assert_equal(catalog["meeting"].get("communication_role"), "escalation_provider", "catalog meeting role")
    _assert_equal(catalog["meeting"].get("communication_input_kind"), "meeting_escalation_candidate", "catalog meeting kind")

    context = OrchestrationContext(
        session_id="session-subordinate",
        conversation_id="conversation-subordinate",
        message="create meeting",
        safe_message="create meeting",
        actor_context={"tenant_id": "tenant-subordinate", "user_id": "user-subordinate"},
    )
    dag_result = execute_dag_plan(
        {
            "subtasks": [
                {
                    "task_id": "create_meeting",
                    "agent": "meeting",
                    "action": "meeting_create_tencent_meeting",
                    "parameters": {"topic": "subordinate", "idempotency_key": "subordinate-key"},
                }
            ]
        },
        context,
        allow_side_effects=False,
    )
    payload = dict(list(dag_result.get("observations") or [])[0].get("payload") or {})
    _assert_equal(payload.get("communication_role"), "escalation_provider", "dag meeting role")
    _assert_equal(payload.get("communication_input_kind"), "meeting_escalation_candidate", "dag meeting kind")
    _assert_equal(payload.get("confirmation_required"), True, "dag confirmation")

    return {
        "ok": True,
        "case": "subordinate_inputs",
        "rag_role": rag_observation.get("communication_role"),
        "meeting_role": mail_plan.get("communication_role"),
        "closeout_owner": mail_plan.get("communication_closeout_owner"),
    }


def run_workspace_flow() -> dict[str, Any]:
    import app.main as main_module
    from app.communication.observations import build_communication_brief_observation
    from app.communication.types import CommunicationBrief, CommunicationThreadRef

    actor_context = {"tenant_id": "tenant-1", "user_id": "employee-1", "workspace_id": "workspace-1"}
    thread_ref = CommunicationThreadRef(
        thread_id="thread_workspace_123",
        source="mail",
        subject="Renewal workspace flow",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-17T08:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id="brief_workspace_123",
        conversation_id="conversation_workspace_123",
        thread_ref=thread_ref,
        employee_goal="Prepare renewal reply",
        customer_context_summary="Customer asked for renewal timing.",
        grounding_refs=[{"kind": "enterprise_fact", "doc_id": "doc_pricing", "chunk_id": "chunk_pricing_1"}],
        must_include=["pricing timeline"],
        must_avoid=["unsupported discounts"],
        open_questions=[],
        recommended_next_action="draft_with_grounding",
        source_observation_ids=["obs_thread_workspace_123"],
        confidence=0.86,
        created_at="2026-06-17T08:01:00+00:00",
        actor_context=actor_context,
    )
    brief_observation = asdict(build_communication_brief_observation(brief))
    result = {
        "answer": "Workspace context assembled.",
        "intent": "communication_workspace",
        "routing_source": "representative_agent_chat",
        "routing_confidence": 0.99,
        "routing_reason": "Communication thread context is the primary work object.",
        "candidate_intents": ["communication_workspace"],
        "tool_calls": [
            {
                "tool_name": "communication_brief",
                "success": True,
                "status": "completed",
                "result": {"brief_id": brief.brief_id},
            }
        ],
        "tool_observations": [brief_observation],
        "task_plan": {
            "mail_plan": {
                "target_object": "communication_brief",
                "status": "draft_with_grounding",
                "thread_ref": asdict(thread_ref),
            }
        },
        "pending_confirmation": {},
        "confirmation_payload": {},
        "memory_hits": 0,
        "merged_memory_hits": 0,
        "workspace_memory_hits": 0,
        "transcript_hits": 0,
        "node_latencies_ms": {"total": 0.0},
    }

    workspace_state = main_module._derive_communication_workspace_state(result, brief.conversation_id)
    result["task_plan"]["communication_workspace"] = workspace_state
    debug_payload = main_module._build_debug_snapshot(result, brief.conversation_id)

    _assert_equal(debug_payload.get("workspace_kind"), "communication_thread_context", "workspace_kind")
    _assert_equal(debug_payload.get("primary_work_object", {}).get("kind"), "communication_brief", "primary kind")
    _assert_equal(debug_payload.get("primary_work_object", {}).get("id"), brief.brief_id, "primary id")
    _assert_equal(
        debug_payload.get("communication_context", {}).get("thread_ref", {}).get("thread_id"),
        thread_ref.thread_id,
        "thread context",
    )
    _assert_equal(
        debug_payload.get("communication_context", {}).get("backend_roles", {}).get("mail"),
        "closeout_owner",
        "mail backend role",
    )
    _assert_equal(
        debug_payload.get("communication_context", {}).get("backend_roles", {}).get("enterprise_rag"),
        "grounding_provider",
        "rag backend role",
    )
    _assert_equal(
        debug_payload.get("communication_context", {}).get("backend_roles", {}).get("meeting"),
        "escalation_provider",
        "meeting backend role",
    )
    _assert_equal(
        debug_payload.get("communication_context", {}).get("governance_surface"),
        "separate_governance_console",
        "governance separation",
    )
    _assert_equal(
        debug_payload.get("communication_context", {}).get("high_risk_approval_controls_exposed"),
        False,
        "workspace approval controls",
    )
    _assert_equal(
        workspace_state.get("communication_context", {}).get("authority_owner"),
        "backend_observations",
        "authority owner",
    )
    if "approved_email_template" in json.dumps(debug_payload, ensure_ascii=False):
        raise AssertionError("workspace state introduced a fixed business template")

    return {
        "ok": True,
        "case": "workspace_flow",
        "workspace_kind": debug_payload.get("workspace_kind"),
        "primary_work_object": debug_payload.get("primary_work_object", {}).get("kind"),
        "governance_surface": debug_payload.get("communication_context", {}).get("governance_surface"),
    }


def run_runtime_brief_closeout() -> dict[str, Any]:
    import app.communication.thread_context as thread_context_module
    import app.main as main_module
    from app.conversation_store import create_conversation, get_turns
    from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND
    from app.mail.source_resolver import resolve_mail_source_request
    from app.models import UnifiedAgentRequest
    from app.orchestration.observations import make_typed_observation

    suffix = uuid4().hex[:8]
    session_id = f"session-runtime-brief-{suffix}"
    conversation_id = f"conversation-runtime-brief-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-runtime-{suffix}",
        "user_id": f"user-runtime-{suffix}",
        "workspace_id": "workspace-runtime",
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)

    original_persist_answer_artifact = main_module._persist_answer_artifact_object
    original_write_dynamic_turn_memory = main_module.write_dynamic_turn_memory
    original_enqueue_summary = main_module.enqueue_conversation_memory_summary
    original_list_thread_messages = thread_context_module.list_thread_messages
    original_list_recent_inbound_threads = thread_context_module.list_recent_inbound_threads
    main_module._persist_answer_artifact_object = lambda **_: {}
    main_module.write_dynamic_turn_memory = lambda **_: {"memory_scope": "session", "identifiers": {}}
    main_module.enqueue_conversation_memory_summary = lambda *_args, **_kwargs: None
    thread_context_module.list_thread_messages = lambda *_, **__: []
    thread_context_module.list_recent_inbound_threads = lambda **_: []
    try:
        answer = "MedThink EU failover should use EU hot standby first, with US fallback only during declared regional outage."
        citation = {
            "doc_id": "doc-runtime-dr",
            "chunk_id": "chunk-runtime-dr-1",
            "source_type": "policy",
            "title": "MedThink disaster recovery policy",
            "snippet": "EU hot standby is primary for EU failover.",
            "score": 0.94,
        }
        enterprise_observation = make_typed_observation(
            observation_type="enterprise_answer_observation",
            source="enterprise_rag_query",
            grounding_kind="retrieval",
            summary="MedThink EU failover uses EU hot standby first.",
            payload={
                "communication_role": "grounding_provider",
                "communication_input_kind": "grounding_bundle",
                "answer_state": {"answerable": True, "missing_evidence": False, "confidence": 0.91},
                "canonical_facts": [
                    {
                        "fact_id": "fact-runtime-dr",
                        "fact_type": "policy",
                        "normalized_fact": answer,
                        "priority": "high",
                        "score": 0.94,
                        "source_fact_ids": ["source-runtime-dr"],
                    }
                ],
                "evidence_manifest": [
                    {
                        "doc_id": citation["doc_id"],
                        "chunk_id": citation["chunk_id"],
                        "source_type": citation["source_type"],
                        "title": citation["title"],
                        "score": citation["score"],
                    }
                ],
                "selected_evidence": [citation],
            },
            citations=[citation],
            confidence=0.91,
            actor_context=actor_context,
        )
        result = {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "request_id": f"request-runtime-brief-{suffix}",
            "message": "How should MedThink handle EU failover?",
            "safe_message": "How should MedThink handle EU failover?",
            "display_message": "How should MedThink handle EU failover?",
            "answer": answer,
            "intent": "enterprise_rag_query",
            "routing_source": "fast_router",
            "routing_confidence": 0.91,
            "routing_reason": "Enterprise knowledge question answered with citations.",
            "candidate_intents": ["enterprise_fact"],
            "mode_used": "fast_path",
            "tool_calls": [{"tool_name": "enterprise_rag_query", "success": True, "status": "completed", "result": {}}],
            "retrieved_evidence": [citation],
            "needs_clarification": False,
            "clarification_question": None,
            "privacy": {},
            "context_budget": {},
            "citations": [citation],
            "memory_context": {},
            "memory_hits": 0,
            "merged_memory_hits": 0,
            "context_sources": ["enterprise_rag"],
            "workspace_memory_hits": 0,
            "transcript_hits": 0,
            "user_model_used": False,
            "reflection_notes": None,
            "upload_context": {},
            "route_mode": "fast",
            "router_intent": "enterprise_fact",
            "router_reason": "Enterprise knowledge question answered with citations.",
            "required_grounding": "tool",
            "fast_path_used": True,
            "degraded_from": "none",
            "planner_type": "fast_router",
            "task_plan": {},
            "subtask_results": [],
            "aggregation_strategy": "fast_path",
            "partial_failures": [],
            "react_trace": [],
            "loop_step_count": 0,
            "termination_reason": "direct_answer",
            "pending_confirmation": {},
            "confirmation_payload": {},
            "final_answer_source": "fast_enterprise_rag_renderer",
            "memory_reads": [],
            "tool_observations": [enterprise_observation],
            "actor_context": actor_context,
            "node_latencies_ms": {"total": 1.0},
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
        }
        pre_write_workspace = main_module._refresh_result_communication_workspace(result, conversation_id)
        _assert_equal(
            pre_write_workspace.get("primary_work_object", {}).get("kind"),
            "conversation_context",
            "pre-write workspace primary kind",
        )
        turn_id, memory_written = main_module._write_unified_conversation_memory(
            result,
            conversation_id,
            actor_context=actor_context,
        )
        response_workspace = main_module._refresh_result_communication_workspace(result, conversation_id)
    finally:
        main_module._persist_answer_artifact_object = original_persist_answer_artifact
        main_module.write_dynamic_turn_memory = original_write_dynamic_turn_memory
        main_module.enqueue_conversation_memory_summary = original_enqueue_summary
        thread_context_module.list_thread_messages = original_list_thread_messages
        thread_context_module.list_recent_inbound_threads = original_list_recent_inbound_threads

    brief_observations = [
        item
        for item in list(result.get("tool_observations") or [])
        if str(item.get("observation_type") or "") == COMMUNICATION_BRIEF_SOURCE_KIND
    ]
    _assert_true(turn_id, "runtime turn_id")
    _assert_true(memory_written, "runtime memory_written")
    _assert_equal(len(brief_observations), 1, "runtime brief observation count")
    _assert_true(answer in json.dumps(brief_observations[0].get("payload") or {}, ensure_ascii=False), "runtime brief facts")
    _assert_equal(
        response_workspace.get("primary_work_object", {}).get("kind"),
        COMMUNICATION_BRIEF_SOURCE_KIND,
        "response workspace primary kind",
    )
    _assert_equal(
        dict(result.get("task_plan") or {}).get("communication_workspace", {}).get("primary_work_object", {}).get("kind"),
        COMMUNICATION_BRIEF_SOURCE_KIND,
        "response task_plan workspace primary kind",
    )

    assistant_turn = next(
        turn for turn in reversed(get_turns(conversation_id)) if str(turn.get("role") or "") == "assistant"
    )
    debug_payload = dict(assistant_turn.get("debug_payload") or {})
    _assert_equal(debug_payload.get("primary_work_object", {}).get("kind"), COMMUNICATION_BRIEF_SOURCE_KIND, "persisted primary kind")
    _assert_true(
        any(
            str(item.get("observation_type") or "") == COMMUNICATION_BRIEF_SOURCE_KIND
            for item in list(debug_payload.get("tool_observations") or [])
        ),
        "persisted debug communication brief",
    )

    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message="Please email the customer at customer@example.com with the closeout.",
    )
    candidates = main_module._collect_outbound_candidates(payload, conversation_id, {}, actor_context=None)
    brief_candidates = [item for item in candidates if item.get("kind") == COMMUNICATION_BRIEF_SOURCE_KIND]
    _assert_equal(len(brief_candidates), 1, "collected runtime brief candidate")

    source_resolution = resolve_mail_source_request(
        message="Please email the customer at customer@example.com with the closeout.",
        candidates=[
            {
                "candidate_id": "mail-thread:incidental",
                "kind": "mail_thread",
                "label": "mail thread: unrelated invoice",
                "content": json.dumps({"thread_id": "incidental", "subject": "Unrelated invoice"}, ensure_ascii=False),
                "content_type": "application/json",
            },
            brief_candidates[0],
        ],
        legacy_referential_request=True,
        explicit_summary=True,
    )
    _assert_equal(source_resolution["source_mode"], COMMUNICATION_BRIEF_SOURCE_KIND, "brief beats incidental thread")
    _assert_equal(source_resolution["selected_candidate_ids"], [brief_candidates[0]["candidate_id"]], "runtime brief selected")

    return {
        "ok": True,
        "case": "runtime_brief_closeout",
        "turn_id": turn_id,
        "primary_work_object": debug_payload.get("primary_work_object", {}).get("kind"),
        "response_primary_work_object": response_workspace.get("primary_work_object", {}).get("kind"),
        "candidate_count": len(candidates),
        "source_mode": source_resolution["source_mode"],
    }


def run_retirement() -> dict[str, Any]:
    retired_tokens = [
        "legacy_orchestration",
        "legacy_aggregator",
        "enable_legacy_orchestration_fallback",
        "ENABLE_LEGACY_ORCHESTRATION_FALLBACK",
    ]
    active_paths = [REPO_ROOT / "app", REPO_ROOT / "README.md"]
    matches: list[str] = []
    for root in active_paths:
        paths = [root] if root.is_file() else [path for path in root.rglob("*.py") if path.is_file()]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for token in retired_tokens:
                if token in text:
                    matches.append(f"{path.relative_to(REPO_ROOT)}:{token}")
    if matches:
        raise AssertionError(f"retired legacy orchestration carriers remain: {matches}")

    import app.orchestration.service as service_module
    from app.orchestration.final_renderer import fallback_final_answer

    original_run_react = service_module.run_react_agent_request
    original_render_final = service_module.render_final_answer

    def _failing_react(**_: Any) -> dict[str, Any]:
        raise RuntimeError("forced react failure for retirement regression")

    def _fake_renderer(**kwargs: Any) -> dict[str, Any]:
        observations = list(kwargs.get("observations") or [])
        _assert_true(observations, "recovery observations")
        _assert_equal(observations[0].get("observation_type"), "dependency_failure", "recovery observation type")
        _assert_equal(observations[0].get("source"), "react_controller", "recovery observation source")
        _assert_equal(
            dict(observations[0].get("payload") or {}).get("fallback_strategy"),
            "typed_recovery_final_renderer",
            "fallback strategy",
        )
        summary = str(observations[0].get("summary") or "")
        if "react_controller" in summary or "orchestrate_agent_request" in summary:
            raise AssertionError("recovery observation summary leaked internal controller names")
        answer = fallback_final_answer(
            question=str(kwargs.get("question") or ""),
            current_goal=str(kwargs.get("current_goal") or ""),
            observations=observations,
            working_memory=list(kwargs.get("working_memory") or []),
            conservative=bool(kwargs.get("conservative")),
        )
        if "react_controller" in answer or "orchestrate_agent_request" in answer:
            raise AssertionError("renderer fallback answer leaked internal controller names")
        return {
            "answer": answer,
            "token_in": 1,
            "token_out": 2,
            "estimated_cost": 0.0,
            "verifier_verdict": {"passed": True},
            "verifier_rewrite_applied": False,
        }

    service_module.run_react_agent_request = _failing_react
    service_module.render_final_answer = _fake_renderer
    try:
        result = service_module.orchestrate_agent_request(
            session_id="session-retirement",
            conversation_id="conversation-retirement",
            message="Please summarize the customer thread.",
            safe_message="Please summarize the customer thread.",
            display_message="Please summarize the customer thread.",
            router_intent="status_or_mail",
            router_reason="retirement regression",
            actor_context={"tenant_id": "tenant-1", "user_id": "user-1"},
        )
    finally:
        service_module.run_react_agent_request = original_run_react
        service_module.render_final_answer = original_render_final

    _assert_equal(result.get("mode_used"), "react_recovery", "mode_used")
    _assert_equal(result.get("final_answer_source"), "orchestration_recovery_renderer", "final_answer_source")
    _assert_equal(result.get("termination_reason"), "controller_recovery", "termination_reason")
    _assert_true(result.get("tool_observations"), "tool_observations")
    _assert_true(result.get("partial_failures"), "partial_failures")
    _assert_true(result.get("node_latencies_ms", {}).get("total") is not None, "node_latencies_ms.total")
    _assert_true(result.get("answer"), "answer")
    first_summary = str(list(result.get("tool_observations") or [])[0].get("summary") or "")
    if "react_controller" in first_summary or "orchestrate_agent_request" in first_summary:
        raise AssertionError("returned recovery observation summary leaked internal controller names")
    if "react_controller" in str(result.get("answer") or "") or "orchestrate_agent_request" in str(result.get("answer") or ""):
        raise AssertionError("returned recovery answer leaked internal controller names")
    if result.get("mode_used") in {"legacy_orchestration"}:
        raise AssertionError("recovery returned retired legacy mode")
    if result.get("final_answer_source") in {"legacy_aggregator"}:
        raise AssertionError("recovery returned retired legacy final answer source")

    return {
        "ok": True,
        "case": "retirement",
        "scanned_roots": [str(path.relative_to(REPO_ROOT)) for path in active_paths],
        "mode_used": result.get("mode_used"),
        "final_answer_source": result.get("final_answer_source"),
        "observations": len(result.get("tool_observations") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Communication Copilot regression checks")
    parser.add_argument("--case", required=True, dest="case_name")
    args = parser.parse_args()

    cases = {
        "workspace_flow": run_workspace_flow,
        "contracts_import": run_contracts_import,
        "brief_assembly": run_brief_assembly,
        "thread_store": run_thread_store,
        "mail_closeout": run_mail_closeout,
        "subordinate_inputs": run_subordinate_inputs,
        "retirement": run_retirement,
        "runtime_brief_closeout": run_runtime_brief_closeout,
    }
    if args.case_name not in cases:
        print(f"unsupported case: {args.case_name}", file=sys.stderr)
        return 2

    try:
        result = cases[args.case_name]()
    except Exception as exc:
        print(json.dumps({"ok": False, "case": args.case_name, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
