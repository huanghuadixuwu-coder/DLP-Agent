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
    expected_brief_payload = {
        **asdict(brief),
        "brief_persistence_source": "assembled",
        "brief_version": 1,
        "thread_id": thread_ref.thread_id,
        "refresh_reason": "",
    }
    _assert_equal(brief_observation.payload, expected_brief_payload, "brief payload")
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
    _assert_equal(payload["brief_persistence_source"], "assembled", "payload brief_persistence_source")
    _assert_equal(payload["brief_version"], 1, "payload brief_version")
    _assert_equal(payload["thread_id"], brief.thread_ref.thread_id, "payload thread_id")
    _assert_equal(payload["refresh_reason"], "", "payload refresh_reason")
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


def run_brief_store() -> dict[str, Any]:
    from app.communication.brief_store import (
        get_communication_brief,
        get_latest_brief_for_thread,
        refresh_brief_for_thread,
        upsert_communication_brief,
    )
    from app.communication.brief_service import assemble_communication_brief
    from app.communication.thread_store import set_active_communication_thread

    suffix = uuid4().hex[:8]
    actor_a = {
        "tenant_id": f"tenant-brief-store-{suffix}",
        "user_id": "employee-a",
        "workspace_id": "workspace-a",
    }
    actor_b = {
        "tenant_id": f"tenant-brief-store-{suffix}",
        "user_id": "employee-b",
        "workspace_id": "workspace-a",
    }
    thread_id = f"thread-brief-store-{suffix}"

    _seed_thread_store_message(
        actor_context=actor_a,
        message_id=f"msg-brief-store-1-{suffix}",
        uid=f"uid-brief-store-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Brief store renewal",
        received_at="2026-06-17T08:00:00+00:00",
        summary="Customer asked whether renewal pricing can be confirmed this week.",
    )
    active_thread = set_active_communication_thread(thread_id, actor_context=actor_a)
    _assert_true(active_thread, "active thread for actor A")

    grounding_refs = [
        {
            "doc_id": "doc-renewal-policy",
            "chunk_id": "chunk-renewal-policy-1",
            "source_type": "policy",
            "title": "Renewal pricing policy",
            "score": 0.93,
        }
    ]
    brief = assemble_communication_brief(
        employee_goal="Prepare renewal reply",
        conversation_id=f"conversation-brief-store-{suffix}",
        thread_id=thread_id,
        grounding_refs=grounding_refs,
        actor_context=actor_a,
        source_observation_ids=["obs-grounding-renewal"],
    )
    stored = upsert_communication_brief(
        brief,
        actor_context=actor_a,
        refresh_reason="initial_test_persist",
    )
    _assert_equal(stored["brief"]["brief_id"], brief.brief_id, "stored brief id")
    _assert_equal(stored["thread_id"], thread_id, "stored thread id")
    _assert_equal(stored["version"], 1, "initial version")
    if "RAW_BODY_SHOULD_NOT_BE_PROJECTED" in json.dumps(stored, ensure_ascii=False):
        raise AssertionError("brief store leaked raw message body")

    _seed_thread_store_message(
        actor_context=actor_a,
        message_id=f"msg-brief-store-2-{suffix}",
        uid=f"uid-brief-store-2-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Re: Brief store renewal",
        received_at="2026-06-17T08:10:00+00:00",
        summary="Customer added that procurement needs the answer before Friday.",
    )
    refreshed = refresh_brief_for_thread(
        thread_id,
        actor_context=actor_a,
        employee_goal="Prepare renewal reply with procurement timing",
        grounding_refs=grounding_refs,
        source_observation_ids=["obs-grounding-renewal"],
        refresh_reason="new_message",
    )
    latest = get_latest_brief_for_thread(thread_id, actor_context=actor_a)
    actor_b_latest = get_latest_brief_for_thread(thread_id, actor_context=actor_b)
    actor_b_direct = get_communication_brief(brief.brief_id, actor_context=actor_b)

    _assert_equal(refreshed["brief_id"], brief.brief_id, "refreshed stable brief id")
    _assert_equal(latest["brief"]["brief_id"], brief.brief_id, "latest stable brief id")
    _assert_true(latest["version"] >= 2, "latest version incremented")
    _assert_equal(latest["refresh_reason"], "new_message", "latest refresh reason")
    _assert_equal(latest["brief"]["thread_ref"]["last_message_at"], "2026-06-17T08:10:00+00:00", "latest thread timestamp")
    _assert_true("procurement" in latest["brief"]["thread_ref"]["latest_summary"].lower(), "latest summary refreshed")
    _assert_equal(actor_b_latest, None, "different actor cannot read latest brief")
    _assert_equal(actor_b_direct, None, "different actor cannot read direct brief")

    cleared = refresh_brief_for_thread(
        thread_id,
        actor_context=actor_a,
        employee_goal="Prepare renewal reply without current grounding",
        grounding_refs=[],
        source_observation_ids=[],
        refresh_reason="clear_grounding",
    )
    _assert_equal(cleared["brief"]["brief_id"], brief.brief_id, "cleared stable brief id")
    _assert_equal(cleared["brief"]["grounding_refs"], [], "explicit empty grounding refs clear stale refs")
    _assert_equal(cleared["brief"]["source_observation_ids"], [], "explicit empty source ids clear stale ids")
    _assert_equal(cleared["grounding_refs"], [], "stored empty grounding refs")
    _assert_equal(cleared["source_observation_ids"], [], "stored empty source ids")
    _assert_true("grounding_required" in cleared["brief"]["open_questions"], "cleared grounding requires fresh grounding")

    return {
        "ok": True,
        "case": "brief_store",
        "brief_id": cleared["brief"]["brief_id"],
        "thread_id": thread_id,
        "version": cleared["version"],
        "refresh_reason": cleared["refresh_reason"],
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
    from app.inbound_mail_store import (
        create_notification,
        get_inbound_message,
        get_sync_state,
        list_notifications,
        update_sync_state,
    )
    from app.mail.access import mark_mail_read_authorized
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

    unauthorized_context_a = OrchestrationContext(
        session_id=f"session-thread-store-{suffix}",
        conversation_id=f"conversation-thread-store-{suffix}",
        message="Read inbound mail",
        safe_message="Read inbound mail",
        actor_context=actor_a,
    )
    unauthorized_registry_summary = registry_module._inbound_mail_summary({}, unauthorized_context_a, {})
    _assert_equal(
        unauthorized_registry_summary.get("status"),
        "permission_denied",
        "unauthorized orchestration inbound summary denied",
    )
    context_a = OrchestrationContext(
        session_id=f"session-thread-store-{suffix}",
        conversation_id=f"conversation-thread-store-{suffix}",
        message="Read inbound mail",
        safe_message="Read inbound mail",
        actor_context=mark_mail_read_authorized(actor_a),
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

    spoof_chat_payload = UnifiedAgentRequest(
        session_id=f"session-chat-spoof-{suffix}",
        message="mail digest",
        tenant_id=actor_a["tenant_id"],
        user_id=actor_a["user_id"],
        workspace_id=actor_a["workspace_id"],
    )
    original_main_render_final_answer = main_module.render_final_answer
    original_main_route_agent_request = main_module.route_agent_request
    main_module.render_final_answer = lambda **_kwargs: {
        "answer": "stubbed mail answer",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }
    def inbound_fast_route(**_kwargs: Any) -> dict[str, Any]:
        return {
            "intent": "mail_status",
            "route_mode": "fast",
            "recommended_tool": "inbound_mail_summary",
            "required_grounding": "tool",
            "routing_source": "stubbed_regression_router",
            "router_reason": "stubbed inbound mail regression route",
            "confidence": 0.99,
        }

    def implicit_inbound_fast_route(**_kwargs: Any) -> dict[str, Any]:
        return {
            "intent": "mail_status",
            "route_mode": "fast",
            "recommended_tool": "",
            "required_grounding": "tool",
            "routing_source": "stubbed_regression_router",
            "router_reason": "stubbed implicit inbound mail regression route",
            "confidence": 0.99,
        }

    main_module.route_agent_request = inbound_fast_route
    try:
        assert_http_status(
            lambda: main_module.agent_chat(spoof_chat_payload, SimpleNamespace(headers={})),
            401,
            "spoofed non-local agent chat inbound fast path requires auth session",
        )
        main_module.route_agent_request = implicit_inbound_fast_route
        assert_http_status(
            lambda: main_module.agent_chat(
                UnifiedAgentRequest(
                    session_id=f"session-chat-spoof-implicit-{suffix}",
                    message="status check",
                    tenant_id=actor_a["tenant_id"],
                    user_id=actor_a["user_id"],
                    workspace_id=actor_a["workspace_id"],
                ),
                SimpleNamespace(headers={}),
            ),
            401,
            "spoofed non-local agent chat implicit inbound fast path requires auth session",
        )
        main_module.route_agent_request = inbound_fast_route
        authenticated_chat = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=f"session-chat-auth-{suffix}",
                message="mail digest",
            ),
            actor_a_request,
        )
        _assert_true(
            any(str(call.get("tool_name") or "") == "inbound_mail_summary" for call in authenticated_chat.tool_calls),
            "authenticated non-local agent chat reads inbound summary",
        )
        local_chat = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=f"session-chat-local-{suffix}",
                message="mail digest",
            ),
            local_request,
        )
        _assert_true(local_chat.tool_calls, "local-dev agent chat inbound mail remains allowed")
    finally:
        main_module.render_final_answer = original_main_render_final_answer
        main_module.route_agent_request = original_main_route_agent_request

    sync_state_a_uid = f"sync-state-a-{suffix}"
    sync_state_b_uid = f"sync-state-b-{suffix}"
    sync_state_local_uid = f"sync-state-local-{suffix}"
    update_sync_state("INBOX", last_seen_uid=sync_state_a_uid, actor_context=actor_a)
    update_sync_state("INBOX", last_seen_uid=sync_state_b_uid, last_error="actor b sync error", actor_context=actor_b)
    update_sync_state("INBOX", last_seen_uid=sync_state_local_uid, actor_context=ActorContext().to_dict())
    _assert_equal(
        get_sync_state("INBOX", actor_context=actor_a).get("last_seen_uid"),
        sync_state_a_uid,
        "actor A sync state retained",
    )
    _assert_equal(
        get_sync_state("INBOX", actor_context=actor_b).get("last_seen_uid"),
        sync_state_b_uid,
        "actor B sync state retained",
    )
    _assert_equal(
        inbound_mail_module.latest_sync_state(actor_context=actor_a).get("last_seen_uid"),
        sync_state_a_uid,
        "actor A latest sync state is actor-scoped",
    )
    _assert_equal(
        inbound_mail_module.latest_sync_state(actor_context=actor_b).get("last_error"),
        "actor b sync error",
        "actor B latest sync state error is actor-scoped",
    )
    _assert_equal(
        inbound_mail_module.latest_sync_state().get("last_seen_uid"),
        sync_state_local_uid,
        "local-dev default sync state remains compatible",
    )
    provider_disabled_sync_b = CurrentImapSmtpMailProvider(allow_external_sync=False).sync_mailbox(actor_context=actor_b)
    _assert_equal(
        dict(provider_disabled_sync_b.data.get("local_sync_state") or {}).get("last_seen_uid"),
        sync_state_b_uid,
        "provider disabled sync response uses actor-scoped sync state",
    )
    provider_health_a = CurrentImapSmtpMailProvider().get_health(actor_context=actor_a)
    _assert_equal(
        dict(provider_health_a.data.get("sync_state") or {}).get("last_seen_uid"),
        sync_state_a_uid,
        "provider health response uses actor-scoped sync state",
    )

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
    actor_a_unauthorized_candidates = main_module._collect_outbound_candidates(
        payload,
        payload.conversation_id or "",
        {},
        actor_context=actor_a,
    )
    _assert_equal(
        [item for item in actor_a_unauthorized_candidates if item.get("kind") == "mail_thread"],
        [],
        "unmarked non-local outbound candidates do not include inbound mail threads",
    )
    actor_a_candidates = main_module._collect_outbound_candidates(
        payload,
        payload.conversation_id or "",
        {},
        actor_context=mark_mail_read_authorized(actor_a),
    )
    actor_a_mail_candidate_ids = {
        str(item.get("candidate_id") or "") for item in actor_a_candidates if item.get("kind") == "mail_thread"
    }
    _assert_true(f"mail-thread:{thread_a}" in actor_a_mail_candidate_ids, "actor A outbound candidate includes own thread")
    _assert_true(f"mail-thread:{thread_b}" not in actor_a_mail_candidate_ids, "actor A outbound candidates exclude actor B thread")

    original_mail_renderer = main_module.render_mail_authoring
    original_source_resolver = main_module.resolve_mail_source_request
    main_module.render_mail_authoring = lambda **_kwargs: {
        "clarification_question": "Please provide the mail body or source.",
        "user_message": "Please provide the mail body or source.",
        "token_in": 0,
        "token_out": 0,
        "estimated_cost": 0.0,
    }
    main_module.resolve_mail_source_request = lambda **_kwargs: {
        "referential_request": True,
        "selected_candidate_ids": [],
        "source_mode": "none",
        "compose_mode": "recipient_ready_summary",
        "needs_clarification": True,
        "confidence": 1.0,
        "reason": "stubbed regression source resolution",
        "classifier_source": "stubbed_regression",
    }
    try:
        spoof_mail_action_response = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=f"session-mail-action-spoof-{suffix}",
                message="Send the recent customer thread to reviewer@example.com",
                tenant_id=actor_a["tenant_id"],
                user_id=actor_a["user_id"],
                workspace_id=actor_a["workspace_id"],
            ),
            SimpleNamespace(headers={}),
        )
        spoof_mail_action_json = json.dumps(spoof_mail_action_response.model_dump(), ensure_ascii=False, default=str)
        _assert_true(
            f"mail-thread:{thread_a}" not in spoof_mail_action_json,
            "spoofed mail-action chat does not leak inbound candidate id",
        )
        _assert_true(
            "Actor A latest renewal summary" not in spoof_mail_action_json,
            "spoofed mail-action chat does not leak inbound candidate summary",
        )
        authenticated_mail_action_response = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=f"session-mail-action-auth-{suffix}",
                message="Send the recent customer thread to reviewer@example.com",
            ),
            actor_a_request,
        )
        authenticated_mail_action_json = json.dumps(
            authenticated_mail_action_response.model_dump(),
            ensure_ascii=False,
            default=str,
        )
        _assert_true(
            f"mail-thread:{thread_a}" in authenticated_mail_action_json,
            "authenticated mail-action chat can use inbound candidate",
        )
    finally:
        main_module.render_mail_authoring = original_mail_renderer
        main_module.resolve_mail_source_request = original_source_resolver

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
    import app.main as main_module
    from app.communication.brief_store import get_latest_brief_for_thread
    from app.communication.thread_store import set_active_communication_thread
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
    other_actor_context = {
        "tenant_id": actor_context["tenant_id"],
        "user_id": f"other-user-runtime-{suffix}",
        "workspace_id": actor_context["workspace_id"],
    }
    thread_id = f"thread-runtime-brief-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-runtime-brief-1-{suffix}",
        uid=f"uid-runtime-brief-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Runtime brief closeout",
        received_at="2026-06-17T09:00:00+00:00",
        summary="Customer asked how MedThink should handle EU failover.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "runtime active thread")
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)

    original_persist_answer_artifact = main_module._persist_answer_artifact_object
    original_write_dynamic_turn_memory = main_module.write_dynamic_turn_memory
    original_enqueue_summary = main_module.enqueue_conversation_memory_summary
    main_module._persist_answer_artifact_object = lambda **_: {}
    main_module.write_dynamic_turn_memory = lambda **_: {"memory_scope": "session", "identifiers": {}}
    main_module.enqueue_conversation_memory_summary = lambda *_args, **_kwargs: None
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
        dict(brief_observations[0].get("payload") or {}).get("thread_ref", {}).get("thread_id"),
        thread_id,
        "runtime brief thread id",
    )
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

    latest_persisted_brief = get_latest_brief_for_thread(thread_id, actor_context=actor_context)
    other_actor_brief = get_latest_brief_for_thread(thread_id, actor_context=other_actor_context)
    _assert_true(latest_persisted_brief, "runtime persisted latest brief")
    _assert_equal(latest_persisted_brief["thread_id"], thread_id, "runtime persisted thread id")
    _assert_equal(latest_persisted_brief["refresh_reason"], "runtime_closeout", "runtime persisted refresh reason")
    _assert_equal(latest_persisted_brief["brief"]["brief_id"], brief_observations[0]["payload"]["brief_id"], "runtime persisted brief id")
    _assert_equal(other_actor_brief, None, "runtime persisted brief actor isolation")

    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message="Please email the customer at customer@example.com with the closeout.",
    )
    candidates = main_module._collect_outbound_candidates(payload, conversation_id, {}, actor_context=actor_context)
    brief_candidates = [item for item in candidates if item.get("kind") == COMMUNICATION_BRIEF_SOURCE_KIND]
    _assert_equal(len(brief_candidates), 1, "collected runtime brief candidate")
    _assert_equal(brief_candidates[0].get("source_turn_id"), "", "runtime brief candidate came from store")
    _assert_equal(
        brief_candidates[0].get("candidate_id"),
        f"communication-brief:{latest_persisted_brief['brief']['brief_id']}",
        "store candidate id",
    )
    candidate_content = str(brief_candidates[0].get("content") or "")
    _assert_true(latest_persisted_brief["brief"]["brief_id"] in candidate_content, "store candidate brief id")
    _assert_true("communication_briefs" in candidate_content, "store candidate persistence source")

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
        "persisted_brief_id": latest_persisted_brief["brief"]["brief_id"],
        "source_mode": source_resolution["source_mode"],
    }


def run_grounded_reply_from_thread() -> dict[str, Any]:
    import app.main as main_module
    from app.communication.brief_service import assemble_communication_brief_observation
    from app.communication.brief_store import get_latest_brief_for_thread
    from app.communication.thread_store import set_active_communication_thread
    from app.conversation_store import append_exchange, create_conversation
    from app.enterprise_rag.core.service import build_enterprise_answer_observation
    from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND
    from app.models import UnifiedAgentRequest

    suffix = uuid4().hex[:8]
    session_id = f"session-grounded-reply-{suffix}"
    conversation_id = f"conversation-grounded-reply-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-grounded-reply-{suffix}",
        "user_id": f"user-grounded-reply-{suffix}",
        "workspace_id": "workspace-grounded-reply",
        "roles": ["admin", "mail_sender"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    thread_id = f"thread-grounded-reply-{suffix}"
    customer_email = "customer@example.com"
    raw_answer = "RAW_RAG_FINAL_ANSWER_SHOULD_NOT_BECOME_EXTERNAL_BODY"
    old_summary = "Hello, this is an old conversation summary that must not become an external body source."
    grounded_fact = "MedThink Enterprise EU failover uses EU hot standby before any short-term US fallback."
    citation = {
        "citation_id": "cite-eu-failover-1",
        "doc_id": "doc-eu-failover",
        "chunk_id": "chunk-eu-failover-1",
        "source_type": "policy",
        "title": "Enterprise EU failover policy",
        "snippet": "EU hot standby is the first failover target.",
        "score": 0.96,
    }

    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-grounded-reply-1-{suffix}",
        uid=f"uid-grounded-reply-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender=customer_email,
        recipients="rep@example.com",
        subject="Enterprise EU failover question",
        received_at="2026-06-19T09:00:00+00:00",
        summary="Customer asks whether the Enterprise plan includes EU failover safeguards.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "grounded reply active thread")
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)
    append_exchange(
        session_id=session_id,
        conversation_id=conversation_id,
        question="old greeting",
        answer=old_summary,
        answer_summary=old_summary,
        intent="smalltalk",
        actor_context=actor_context,
    )

    rag_observation = build_enterprise_answer_observation(
        {
            "correlation_id": f"rag-grounded-reply-{suffix}",
            "confidence": 0.92,
            "supporting_doc_ids": [citation["doc_id"]],
            "context_sources": ["enterprise_rag"],
            "answer_debug": {
                "answerable": True,
                "answer_intent": "enterprise_question",
                "fallback_reason": "none",
                "final_answer_source": "regression_fixture",
            },
            "retrieval_stage_debug": {"budget_profile": "sample"},
            "canonical_facts": [
                {
                    "fact_id": "fact-eu-failover",
                    "fact_type": "policy",
                    "normalized_fact": grounded_fact,
                    "priority": "high",
                    "score": 0.96,
                    "source_fact_ids": ["source-eu-failover"],
                }
            ],
            "citations": [citation],
        }
    )
    brief, brief_observation = assemble_communication_brief_observation(
        employee_goal="Answer the active customer's EU failover question and draft a concise reply.",
        conversation_id=conversation_id,
        grounding_observation=rag_observation,
        actor_context=actor_context,
        source_observation_ids=[str(rag_observation.get("correlation_id") or "")],
        persist_snapshot=True,
        refresh_reason="grounded_reply_from_thread",
    )
    _assert_equal(brief.thread_ref.thread_id, thread_id, "grounded brief active thread")
    _assert_true(brief.grounding_refs, "grounded brief refs")
    _assert_equal(brief.grounding_refs[0].get("citation_id"), citation["citation_id"], "grounded brief citation id")
    _assert_equal(brief.grounding_refs[0].get("doc_id"), citation["doc_id"], "grounded brief doc id")
    _assert_true(grounded_fact in brief.must_include, "grounded fact in must_include")
    _assert_equal(brief.recommended_next_action, "draft_with_grounding", "grounded brief next action")
    _assert_equal(brief_observation.observation_type, COMMUNICATION_BRIEF_SOURCE_KIND, "grounded brief observation")
    stored_brief = get_latest_brief_for_thread(thread_id, actor_context=actor_context)
    _assert_true(stored_brief, "grounded stored brief")
    _assert_equal(stored_brief["brief"]["brief_id"], brief.brief_id, "grounded stored brief id")

    main_module._persist_answer_artifact_object(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=f"raw-answer-{suffix}",
        answer_summary=raw_answer,
        intent="enterprise_rag_query",
        citations=[citation],
        actor_context=actor_context,
    )

    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message=f"Please email {customer_email} a concise response from the active thread and latest brief.",
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
        roles=actor_context["roles"],
    )
    candidates = main_module._collect_outbound_candidates(payload, conversation_id, {}, actor_context=actor_context)
    brief_candidates = [item for item in candidates if item.get("kind") == COMMUNICATION_BRIEF_SOURCE_KIND]
    assistant_candidates = [item for item in candidates if item.get("kind") == "assistant_last_answer"]
    _assert_equal(len(brief_candidates), 1, "grounded reply brief candidate count")
    _assert_true(assistant_candidates, "explicit assistant source candidates remain available with active brief")

    plan_result = main_module._build_mail_action_plan(payload, conversation_id, {}, actor_context=actor_context)
    mail_plan = dict(plan_result.get("mail_plan") or {})
    source_resolution = dict(mail_plan.get("source_resolution") or {})
    _assert_equal(source_resolution.get("source_mode"), COMMUNICATION_BRIEF_SOURCE_KIND, "grounded reply source mode")
    _assert_equal(source_resolution.get("selected_candidate_ids"), [brief_candidates[0]["candidate_id"]], "grounded reply selected brief")
    _assert_equal(mail_plan.get("compose_mode"), "recipient_ready_summary", "grounded reply compose mode")
    _assert_equal(mail_plan.get("resolved_body"), "", "grounded reply body before renderer")
    if raw_answer in json.dumps(mail_plan, ensure_ascii=False):
        raise AssertionError("raw answer artifact leaked into communication brief mail plan")
    if old_summary in json.dumps(mail_plan, ensure_ascii=False):
        raise AssertionError("old assistant summary leaked into communication brief mail plan")

    captured: dict[str, Any] = {}
    original_renderer = main_module.render_mail_authoring

    def _fake_renderer(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        selected = list(kwargs.get("candidates") or [])
        _assert_equal(len(selected), 1, "renderer selected candidate count")
        _assert_equal(selected[0].get("kind"), COMMUNICATION_BRIEF_SOURCE_KIND, "renderer selected candidate kind")
        content = str(selected[0].get("content") or "")
        _assert_true(citation["citation_id"] in content, "renderer brief citation id")
        _assert_true(grounded_fact in content, "renderer grounded fact")
        body = (
            "Thanks for asking about Enterprise EU failover. "
            "The current grounded policy says MedThink uses EU hot standby first, "
            "with US fallback only as a short-term fallback. "
            f"Reference: {citation['citation_id']}."
        )
        return {
            "user_message": "Draft prepared from the active communication brief.",
            "body_for_sending": body,
            "clarification_question": "",
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
            "used_fallback": False,
        }

    main_module.render_mail_authoring = _fake_renderer
    try:
        rendered_plan, _ = main_module._render_mail_plan_with_llm(
            message=str(payload.message or ""),
            mail_plan=mail_plan,
            render_mode="confirmation",
            observations=[asdict(brief_observation)],
            candidates=candidates,
        )
    finally:
        main_module.render_mail_authoring = original_renderer

    resolved_body = str(rendered_plan.get("resolved_body") or "")
    _assert_true(resolved_body.strip(), "grounded reply resolved body")
    _assert_equal(rendered_plan.get("authoring_status"), "completed", "grounded reply authoring")
    _assert_equal(resolved_body.count("EU hot standby"), 1, "grounded reply no duplicate fact body")
    _assert_true(citation["citation_id"] in resolved_body, "grounded reply citation backed body")
    _assert_equal(dict(rendered_plan.get("selected_candidate") or {}).get("kind"), COMMUNICATION_BRIEF_SOURCE_KIND, "grounded reply selected candidate")
    _assert_true(
        {
            "kind": "reference_source",
            "role": COMMUNICATION_BRIEF_SOURCE_KIND,
            "policy": "recipient_ready_summary",
        }
        in list(rendered_plan.get("body_sources") or []),
        "grounded reply body source",
    )
    if raw_answer in resolved_body or old_summary in resolved_body:
        raise AssertionError("non-brief raw assistant content leaked into grounded reply body")
    if thread_id not in str(brief_candidates[0].get("content") or ""):
        raise AssertionError("communication brief candidate is not scoped to the active thread")
    captured_candidates = json.dumps(captured.get("candidates") or [], ensure_ascii=False)
    if raw_answer in captured_candidates or old_summary in captured_candidates:
        raise AssertionError("non-brief raw assistant content leaked into renderer candidates")

    return {
        "ok": True,
        "case": "grounded_reply_from_thread",
        "thread_id": thread_id,
        "brief_id": brief.brief_id,
        "source_mode": source_resolution.get("source_mode"),
        "candidate_count": len(candidates),
        "resolved_body_chars": len(resolved_body),
    }


def run_explicit_prior_answer_with_active_brief() -> dict[str, Any]:
    import app.main as main_module
    from app.communication.brief_store import upsert_communication_brief
    from app.communication.thread_store import set_active_communication_thread
    from app.communication.types import CommunicationBrief, CommunicationThreadRef
    from app.conversation_store import create_conversation
    from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND
    from app.models import UnifiedAgentRequest

    suffix = uuid4().hex[:8]
    session_id = f"session-explicit-prior-{suffix}"
    conversation_id = f"conversation-explicit-prior-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-explicit-prior-{suffix}",
        "user_id": f"user-explicit-prior-{suffix}",
        "workspace_id": "workspace-explicit-prior",
        "roles": ["admin", "mail_sender"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    thread_id = f"thread-explicit-prior-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-explicit-prior-1-{suffix}",
        uid=f"uid-explicit-prior-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="active-customer@example.com",
        recipients="rep@example.com",
        subject="Active renewal thread",
        received_at="2026-06-19T10:00:00+00:00",
        summary="Active thread has a renewal brief, but the user may reference a separate prior answer.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "explicit prior active thread")
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Active renewal thread",
        participants=["active-customer@example.com", "rep@example.com"],
        last_message_at="2026-06-19T10:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id=f"brief-explicit-prior-{suffix}",
        conversation_id=conversation_id,
        thread_ref=thread_ref,
        employee_goal="Prepare renewal closeout from the active thread.",
        customer_context_summary="Active thread should remain default closeout context only.",
        grounding_refs=[{"citation_id": "active-brief-cite", "doc_id": "active-brief-doc"}],
        must_include=["active thread renewal fact"],
        recommended_next_action="draft_with_grounding",
        confidence=0.88,
        actor_context=actor_context,
    )
    _assert_true(upsert_communication_brief(brief, actor_context=actor_context, refresh_reason="explicit_prior_regression"), "stored active brief")
    prior_answer = "Global rollout answer says the neutral migration window is Wednesday at 14:00 UTC."
    main_module._persist_answer_artifact_object(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=f"global-answer-{suffix}",
        answer_summary=prior_answer,
        intent="enterprise_rag_query",
        citations=[{"doc_id": "global-rollout-doc", "title": "Global rollout answer"}],
        actor_context=actor_context,
    )
    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message="Please email customer@example.com the Global rollout answer from the prior assistant answer.",
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
        roles=actor_context["roles"],
    )
    candidates = main_module._collect_outbound_candidates(payload, conversation_id, {}, actor_context=actor_context)
    brief_candidates = [item for item in candidates if item.get("kind") == COMMUNICATION_BRIEF_SOURCE_KIND]
    assistant_candidates = [item for item in candidates if item.get("kind") == "assistant_last_answer"]
    _assert_equal(len(brief_candidates), 1, "explicit prior active brief candidate")
    _assert_equal(len(assistant_candidates), 1, "explicit prior assistant candidate retained")
    plan_result = main_module._build_mail_action_plan(payload, conversation_id, {}, actor_context=actor_context)
    mail_plan = dict(plan_result.get("mail_plan") or {})
    source_resolution = dict(mail_plan.get("source_resolution") or {})
    _assert_equal(source_resolution.get("source_mode"), "prior_assistant_answer", "explicit prior source mode")
    _assert_equal(source_resolution.get("selected_candidate_ids"), [assistant_candidates[0]["candidate_id"]], "explicit prior selected source")
    _assert_equal(dict(mail_plan.get("selected_candidate") or {}).get("kind"), "assistant_last_answer", "explicit prior selected candidate")
    if brief.brief_id in json.dumps(mail_plan.get("reference_sources") or [], ensure_ascii=False):
        raise AssertionError("active brief silently overrode explicit prior assistant source")

    return {
        "ok": True,
        "case": "explicit_prior_answer_with_active_brief",
        "source_mode": source_resolution.get("source_mode"),
        "selected_candidate": source_resolution.get("selected_candidate_ids"),
        "candidate_count": len(candidates),
    }


def run_default_closeout_prefers_brief_with_generic_overlap() -> dict[str, Any]:
    import app.main as main_module
    from app.communication.brief_store import upsert_communication_brief
    from app.communication.thread_store import set_active_communication_thread
    from app.communication.types import CommunicationBrief, CommunicationThreadRef
    from app.conversation_store import create_conversation
    from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND
    from app.models import UnifiedAgentRequest

    suffix = uuid4().hex[:8]
    session_id = f"session-default-brief-{suffix}"
    conversation_id = f"conversation-default-brief-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-default-brief-{suffix}",
        "user_id": f"user-default-brief-{suffix}",
        "workspace_id": "workspace-default-brief",
        "roles": ["admin", "mail_sender"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    thread_id = f"thread-default-brief-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-default-brief-1-{suffix}",
        uid=f"uid-default-brief-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Customer closeout plan",
        received_at="2026-06-19T12:00:00+00:00",
        summary="Customer needs a closeout email from the active thread.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "default closeout active thread")
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Customer closeout plan",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-19T12:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id=f"brief-default-closeout-{suffix}",
        conversation_id=conversation_id,
        thread_ref=thread_ref,
        employee_goal="Prepare the active customer closeout email.",
        customer_context_summary="Active thread closeout should be the default source.",
        grounding_refs=[{"citation_id": "default-brief-cite", "doc_id": "default-brief-doc"}],
        must_include=["active brief closeout fact"],
        recommended_next_action="draft_with_grounding",
        confidence=0.9,
        actor_context=actor_context,
    )
    _assert_true(upsert_communication_brief(brief, actor_context=actor_context, refresh_reason="default_closeout_regression"), "stored default brief")
    generic_old_answer = "Old assistant customer closeout plan email content that shares generic words with this request."
    main_module._persist_answer_artifact_object(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=f"generic-overlap-answer-{suffix}",
        answer_summary=generic_old_answer,
        intent="enterprise_rag_query",
        citations=[{"doc_id": "generic-overlap-doc", "title": "Customer closeout plan"}],
        actor_context=actor_context,
    )
    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message="Please email the customer with the closeout plan.",
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
        roles=actor_context["roles"],
    )
    candidates = main_module._collect_outbound_candidates(payload, conversation_id, {}, actor_context=actor_context)
    _assert_equal(len([item for item in candidates if item.get("kind") == COMMUNICATION_BRIEF_SOURCE_KIND]), 1, "default closeout brief candidate")
    _assert_true(any(item.get("kind") == "assistant_last_answer" for item in candidates), "default closeout raw answer candidate retained")
    plan_result = main_module._build_mail_action_plan(payload, conversation_id, {}, actor_context=actor_context)
    mail_plan = dict(plan_result.get("mail_plan") or {})
    source_resolution = dict(mail_plan.get("source_resolution") or {})
    _assert_equal(source_resolution.get("source_mode"), COMMUNICATION_BRIEF_SOURCE_KIND, "default closeout source mode")
    _assert_equal(dict(mail_plan.get("selected_candidate") or {}).get("kind"), COMMUNICATION_BRIEF_SOURCE_KIND, "default closeout selected brief")
    if generic_old_answer in json.dumps(mail_plan.get("reference_sources") or [], ensure_ascii=False):
        raise AssertionError("generic token overlap selected raw prior assistant answer over active brief")

    return {
        "ok": True,
        "case": "default_closeout_prefers_brief_with_generic_overlap",
        "source_mode": source_resolution.get("source_mode"),
        "target_object": mail_plan.get("target_object"),
        "candidate_count": len(candidates),
    }


def run_prior_answer_without_topic_overlap() -> dict[str, Any]:
    import app.main as main_module
    from app.communication.brief_store import upsert_communication_brief
    from app.communication.thread_store import set_active_communication_thread
    from app.communication.types import CommunicationBrief, CommunicationThreadRef
    from app.conversation_store import create_conversation
    from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND
    from app.mail.source_resolver import resolve_mail_source_request
    from app.models import UnifiedAgentRequest

    suffix = uuid4().hex[:8]
    session_id = f"session-prior-no-overlap-{suffix}"
    conversation_id = f"conversation-prior-no-overlap-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-prior-no-overlap-{suffix}",
        "user_id": f"user-prior-no-overlap-{suffix}",
        "workspace_id": "workspace-prior-no-overlap",
        "roles": ["admin", "mail_sender"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    thread_id = f"thread-prior-no-overlap-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-prior-no-overlap-1-{suffix}",
        uid=f"uid-prior-no-overlap-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Active brief unrelated to prior answer",
        received_at="2026-06-19T13:00:00+00:00",
        summary="Active thread has a brief, but the user explicitly asks for a prior answer.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "prior no-overlap active thread")
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Active brief unrelated to prior answer",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-19T13:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id=f"brief-prior-no-overlap-{suffix}",
        conversation_id=conversation_id,
        thread_ref=thread_ref,
        employee_goal="Prepare the active customer closeout.",
        customer_context_summary="This active brief must not override explicit prior-answer intent.",
        grounding_refs=[{"citation_id": "prior-no-overlap-brief-cite", "doc_id": "prior-no-overlap-brief-doc"}],
        must_include=["active thread fact"],
        recommended_next_action="draft_with_grounding",
        confidence=0.9,
        actor_context=actor_context,
    )
    _assert_true(upsert_communication_brief(brief, actor_context=actor_context, refresh_reason="prior_no_overlap_regression"), "stored prior no-overlap brief")
    prior_answer = "Zebra lantern protocol uses amber checkpoints before lunar archival."
    main_module._persist_answer_artifact_object(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=f"zebra-lantern-{suffix}",
        answer_summary=prior_answer,
        intent="enterprise_rag_query",
        citations=[{"doc_id": "zebra-lantern-doc", "title": "Zebra lantern protocol"}],
        actor_context=actor_context,
    )
    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message="Please email customer@example.com the prior answer.",
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
        roles=actor_context["roles"],
    )
    candidates = main_module._collect_outbound_candidates(payload, conversation_id, {}, actor_context=actor_context)
    brief_candidates = [item for item in candidates if item.get("kind") == COMMUNICATION_BRIEF_SOURCE_KIND]
    assistant_candidates = [item for item in candidates if item.get("kind") == "assistant_last_answer"]
    _assert_equal(len(brief_candidates), 1, "prior no-overlap active brief candidate")
    _assert_equal(len(assistant_candidates), 1, "prior no-overlap assistant candidate")
    plan_result = main_module._build_mail_action_plan(payload, conversation_id, {}, actor_context=actor_context)
    mail_plan = dict(plan_result.get("mail_plan") or {})
    source_resolution = dict(mail_plan.get("source_resolution") or {})
    _assert_equal(source_resolution.get("source_mode"), "prior_assistant_answer", "prior no-overlap source mode")
    _assert_equal(source_resolution.get("selected_candidate_ids"), [assistant_candidates[0]["candidate_id"]], "prior no-overlap selected source")
    _assert_equal(dict(mail_plan.get("selected_candidate") or {}).get("kind"), "assistant_last_answer", "prior no-overlap selected candidate")
    if brief.brief_id in json.dumps(mail_plan.get("reference_sources") or [], ensure_ascii=False):
        raise AssertionError("active brief overrode explicit prior-answer request without topic overlap")

    ambiguous_resolution = resolve_mail_source_request(
        message="Please email customer@example.com the prior answer.",
        candidates=[
            *brief_candidates,
            assistant_candidates[0],
            {
                "candidate_id": f"assistant-turn:second-prior-{suffix}",
                "kind": "assistant_last_answer",
                "label": "second unrelated source",
                "content": "Blue orchard schedule uses quiet morning windows.",
                "content_type": "text/plain",
            },
        ],
        legacy_referential_request=True,
        explicit_summary=False,
    )
    _assert_equal(ambiguous_resolution.get("source_mode"), "none", "ambiguous prior source mode")
    _assert_equal(ambiguous_resolution.get("needs_clarification"), True, "ambiguous prior clarification")

    return {
        "ok": True,
        "case": "prior_answer_without_topic_overlap",
        "source_mode": source_resolution.get("source_mode"),
        "ambiguous_status": ambiguous_resolution.get("status"),
        "candidate_count": len(candidates),
    }


def run_polish_rewrite_from_active_brief() -> dict[str, Any]:
    import app.main as main_module
    from app.communication.brief_store import upsert_communication_brief
    from app.communication.thread_store import set_active_communication_thread
    from app.communication.types import CommunicationBrief, CommunicationThreadRef
    from app.conversation_store import create_conversation
    from app.mail.domain import COMMUNICATION_BRIEF_SOURCE_KIND
    from app.models import UnifiedAgentRequest

    suffix = uuid4().hex[:8]
    session_id = f"session-polish-brief-{suffix}"
    conversation_id = f"conversation-polish-brief-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-polish-brief-{suffix}",
        "user_id": f"user-polish-brief-{suffix}",
        "workspace_id": "workspace-polish-brief",
        "roles": ["admin", "mail_sender"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    thread_id = f"thread-polish-brief-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-polish-brief-1-{suffix}",
        uid=f"uid-polish-brief-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Polish active brief",
        received_at="2026-06-19T11:00:00+00:00",
        summary="Customer needs a polished closeout from the active brief.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "polish active thread")
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Polish active brief",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-19T11:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id=f"brief-polish-{suffix}",
        conversation_id=conversation_id,
        thread_ref=thread_ref,
        employee_goal="Polish the active brief into recipient-ready wording.",
        customer_context_summary="Customer wants a concise enterprise support closeout.",
        grounding_refs=[{"citation_id": "polish-brief-cite", "doc_id": "polish-brief-doc"}],
        must_include=["support will monitor the rollout"],
        recommended_next_action="draft_with_grounding",
        confidence=0.9,
        actor_context=actor_context,
    )
    _assert_true(upsert_communication_brief(brief, actor_context=actor_context, refresh_reason="polish_brief_regression"), "stored polish brief")
    raw_answer = "RAW_ASSISTANT_ANSWER_SHOULD_NOT_BE_POLISHED_WHEN_BRIEF_SELECTED"
    main_module._persist_answer_artifact_object(
        session_id=session_id,
        conversation_id=conversation_id,
        turn_id=f"raw-polish-answer-{suffix}",
        answer_summary=raw_answer,
        intent="enterprise_rag_query",
        citations=[{"doc_id": "raw-polish-doc", "title": "Raw polish answer"}],
        actor_context=actor_context,
    )
    payload = UnifiedAgentRequest(
        session_id=session_id,
        conversation_id=conversation_id,
        message="Please polish the latest active brief.",
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
        roles=actor_context["roles"],
    )
    candidates = main_module._collect_outbound_candidates(payload, conversation_id, {}, actor_context=actor_context)
    _assert_true(any(item.get("kind") == "assistant_last_answer" for item in candidates), "polish raw assistant candidate retained")
    plan_result = main_module._build_mail_action_plan(payload, conversation_id, {}, actor_context=actor_context)
    mail_plan = dict(plan_result.get("mail_plan") or {})
    source_resolution = dict(mail_plan.get("source_resolution") or {})
    _assert_equal(plan_result.get("mode"), "draft_only", "polish brief mode")
    _assert_equal(source_resolution.get("source_mode"), COMMUNICATION_BRIEF_SOURCE_KIND, "polish brief source mode")
    _assert_equal(dict(mail_plan.get("selected_candidate") or {}).get("kind"), COMMUNICATION_BRIEF_SOURCE_KIND, "polish selected brief")
    _assert_equal(list(mail_plan.get("reference_sources") or [])[0].get("role"), COMMUNICATION_BRIEF_SOURCE_KIND, "polish reference role")
    if raw_answer in json.dumps(mail_plan.get("reference_sources") or [], ensure_ascii=False):
        raise AssertionError("polish/rewrite selected raw assistant answer instead of active brief")

    return {
        "ok": True,
        "case": "polish_rewrite_from_active_brief",
        "source_mode": source_resolution.get("source_mode"),
        "target_object": mail_plan.get("target_object"),
        "candidate_count": len(candidates),
    }


def run_contextual_chat() -> dict[str, Any]:
    from types import SimpleNamespace

    import app.main as main_module
    from app.communication.brief_store import upsert_communication_brief
    from app.communication.thread_store import set_active_communication_thread
    from app.communication.types import CommunicationBrief, CommunicationThreadRef
    from app.conversation_store import create_conversation
    from app.models import UnifiedAgentRequest

    suffix = uuid4().hex[:8]
    session_id = f"session-contextual-chat-{suffix}"
    conversation_id = f"conversation-contextual-chat-{suffix}"
    actor_context = {
        "tenant_id": f"tenant-contextual-{suffix}",
        "user_id": f"user-contextual-{suffix}",
        "workspace_id": "workspace-contextual",
        "roles": ["admin"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    thread_id = f"thread-contextual-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-contextual-1-{suffix}",
        uid=f"uid-contextual-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Enterprise renewal follow-up",
        received_at="2026-06-17T10:00:00+00:00",
        summary="Customer asks whether Enterprise renewal includes EU failover and wants a concise reply.",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "contextual active thread")
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Enterprise renewal follow-up",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-17T10:00:00+00:00",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id=f"brief-contextual-{suffix}",
        conversation_id=conversation_id,
        thread_ref=thread_ref,
        employee_goal="Answer the customer's EU failover question and draft a concise reply.",
        customer_context_summary="Customer needs Enterprise renewal EU failover details.",
        grounding_refs=[{"doc_id": "policy-eu-failover", "title": "EU failover policy"}],
        must_include=["EU hot standby"],
        must_avoid=["unsupported commitments"],
        recommended_next_action="draft_reply",
        source_observation_ids=["obs-contextual-grounding"],
        confidence=0.88,
        actor_context=actor_context,
    )
    stored_brief = upsert_communication_brief(
        brief,
        actor_context=actor_context,
        refresh_reason="contextual_chat_regression",
    )
    _assert_true(stored_brief, "contextual stored brief")
    create_conversation(session_id, conversation_id=conversation_id, actor_context=actor_context)
    pending_confirmation_payload = {
        "confirmation_id": f"pending-confirmation-{suffix}",
        "mail_plan": {
            "draft_id": f"draft-pending-{suffix}",
            "mail_action_type": "send_reply",
            "status": "pending_confirmation",
            "request_message": "Send the stale pending draft.",
            "resolved_recipients": ["customer@example.com"],
            "resolved_subject": "Stale pending draft",
            "resolved_body": "This stale draft must not hijack explicit global mode.",
        },
    }
    original_persist_confirmation_for_seed = main_module._persist_confirmation_object
    seeded_pending = original_persist_confirmation_for_seed(
        session_id=session_id,
        conversation_id=conversation_id,
        confirmation_payload=pending_confirmation_payload,
        actor_context=actor_context,
    )
    _assert_true(seeded_pending, "seeded pending confirmation")

    request = SimpleNamespace(headers={})
    planner_calls: list[dict[str, Any]] = []
    original_orchestrate = main_module.orchestrate_agent_request
    original_route = main_module.route_agent_request
    original_plan_multi = main_module.plan_multi_agent_dag_request
    original_write_memory = main_module._write_unified_conversation_memory
    original_persist_confirmation = main_module._persist_confirmation_object
    original_persist_answer_artifact = main_module._persist_answer_artifact_object
    original_write_dynamic_turn_memory = main_module.write_dynamic_turn_memory
    original_enqueue_summary = main_module.enqueue_conversation_memory_summary

    def _slow_route(**_: Any) -> dict[str, Any]:
        return {
            "route_mode": "slow",
            "intent": "enterprise_fact",
            "required_grounding": "tool",
            "recommended_tool": "enterprise_rag_query",
            "router_reason": "contextual chat regression",
            "degraded_from": "none",
        }

    def _fake_orchestrate(**kwargs: Any) -> dict[str, Any]:
        planner_calls.append(kwargs)
        observations = list(kwargs.get("initial_observations") or [])
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": kwargs["conversation_id"],
            "request_id": f"request-contextual-{len(planner_calls)}-{suffix}",
            "message": kwargs["message"],
            "safe_message": kwargs["safe_message"],
            "display_message": kwargs["display_message"],
            "answer": "Stubbed contextual answer.",
            "intent": kwargs.get("router_intent") or "enterprise_fact",
            "routing_source": "contextual_chat_regression",
            "routing_confidence": 0.91,
            "routing_reason": kwargs.get("router_reason") or "contextual chat regression",
            "candidate_intents": ["enterprise_fact"],
            "mode_used": "react_controller",
            "tool_calls": [{"tool_name": "enterprise_rag_query", "success": True, "status": "completed", "result": {}}],
            "retrieved_evidence": [],
            "needs_clarification": False,
            "clarification_question": None,
            "privacy": {},
            "context_budget": {},
            "citations": [],
            "memory_context": {},
            "memory_hits": 0,
            "merged_memory_hits": 0,
            "context_sources": [],
            "workspace_memory_hits": 0,
            "transcript_hits": 0,
            "user_model_used": False,
            "reflection_notes": None,
            "upload_context": dict(kwargs.get("upload_context") or {}),
            "route_mode": "slow",
            "router_intent": kwargs.get("router_intent") or "enterprise_fact",
            "router_reason": kwargs.get("router_reason") or "",
            "required_grounding": kwargs.get("required_grounding") or "tool",
            "fast_path_used": False,
            "degraded_from": "none",
            "recommended_tool": kwargs.get("recommended_tool") or "",
            "planner_type": "contextual_chat_regression",
            "task_plan": {"tool_observations": observations},
            "subtask_results": [],
            "aggregation_strategy": "stub",
            "partial_failures": [],
            "react_trace": [],
            "loop_step_count": 0,
            "termination_reason": "direct_answer",
            "pending_confirmation": {},
            "confirmation_payload": {},
            "final_answer_source": "contextual_chat_regression",
            "memory_reads": [],
            "tool_observations": observations,
            "actor_context": kwargs.get("actor_context") or {},
            "node_latencies_ms": {"total": 1.0},
            "token_in": 0,
            "token_out": 0,
            "estimated_cost": 0.0,
        }

    main_module.orchestrate_agent_request = _fake_orchestrate
    main_module.route_agent_request = _slow_route
    main_module.plan_multi_agent_dag_request = lambda **_: None
    main_module._write_unified_conversation_memory = lambda *_args, **_kwargs: (f"turn-contextual-{len(planner_calls)}", True)
    main_module._persist_confirmation_object = lambda **_: None
    main_module._persist_answer_artifact_object = lambda **_: {}
    main_module.write_dynamic_turn_memory = lambda **_: {"memory_scope": "session", "identifiers": {}}
    main_module.enqueue_conversation_memory_summary = lambda *_args, **_kwargs: None
    try:
        contextual_response = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=session_id,
                conversation_id=conversation_id,
                message="Using the active customer thread and latest brief, answer the EU failover question.",
                tenant_id=actor_context["tenant_id"],
                user_id=actor_context["user_id"],
                workspace_id=actor_context["workspace_id"],
                roles=["admin"],
            ),
            request,
        )
        draft_response = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=session_id,
                conversation_id=conversation_id,
                message="Using the active customer thread and latest brief, draft a concise reply.",
                tenant_id=actor_context["tenant_id"],
                user_id=actor_context["user_id"],
                workspace_id=actor_context["workspace_id"],
                roles=["admin"],
            ),
            request,
        )
        global_response = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=session_id,
                conversation_id=conversation_id,
                message="confirm",
                tenant_id=actor_context["tenant_id"],
                user_id=actor_context["user_id"],
                workspace_id=actor_context["workspace_id"],
                roles=["admin"],
                global_mode=True,
            ),
            request,
        )
        missing_thread_response = main_module.agent_chat(
            UnifiedAgentRequest(
                session_id=session_id,
                conversation_id=conversation_id,
                message="Using the requested customer thread, answer the latest question.",
                tenant_id=actor_context["tenant_id"],
                user_id=actor_context["user_id"],
                workspace_id=actor_context["workspace_id"],
                roles=["admin"],
                thread_id=f"missing-thread-{suffix}",
            ),
            request,
        )
    finally:
        main_module.orchestrate_agent_request = original_orchestrate
        main_module.route_agent_request = original_route
        main_module.plan_multi_agent_dag_request = original_plan_multi
        main_module._write_unified_conversation_memory = original_write_memory
        main_module._persist_confirmation_object = original_persist_confirmation
        main_module._persist_answer_artifact_object = original_persist_answer_artifact
        main_module.write_dynamic_turn_memory = original_write_dynamic_turn_memory
        main_module.enqueue_conversation_memory_summary = original_enqueue_summary

    contextual_observations = list(contextual_response.tool_observations or [])
    contextual_types = [str(item.get("observation_type") or "") for item in contextual_observations]
    _assert_true("active_communication_thread" in contextual_types, "contextual active thread observation")
    _assert_true("communication_brief" in contextual_types, "contextual brief observation")
    active_thread_observation = next(item for item in contextual_observations if item.get("observation_type") == "active_communication_thread")
    brief_observation = next(item for item in contextual_observations if item.get("observation_type") == "communication_brief")
    _assert_equal(dict(active_thread_observation.get("payload") or {}).get("thread_id"), thread_id, "contextual thread id")
    _assert_equal(dict(brief_observation.get("payload") or {}).get("brief_id"), brief.brief_id, "contextual latest brief id")
    _assert_equal(dict(brief_observation.get("payload") or {}).get("thread_ref", {}).get("thread_id"), thread_id, "contextual brief thread ref")
    _assert_equal(active_thread_observation.get("actor_context", {}).get("tenant_id"), actor_context["tenant_id"], "thread actor context")
    _assert_equal(brief_observation.get("actor_context", {}).get("tenant_id"), actor_context["tenant_id"], "brief actor context")
    _assert_equal(
        dict(planner_calls[0].get("upload_context") or {}).get("agent_chat_context", {}).get("thread_id"),
        thread_id,
        "planner active thread context",
    )
    _assert_equal(
        dict(planner_calls[0].get("upload_context") or {}).get("agent_chat_context", {}).get("brief_id"),
        brief.brief_id,
        "planner latest brief context",
    )
    draft_observations = list(draft_response.tool_observations or [])
    draft_types = [str(item.get("observation_type") or "") for item in draft_observations]
    _assert_true("active_communication_thread" in draft_types, "draft active thread observation")
    _assert_true("communication_brief" in draft_types, "draft latest brief observation")

    global_observations = list(global_response.tool_observations or [])
    global_types = [str(item.get("observation_type") or "") for item in global_observations]
    _assert_true("global_entry" in global_types, "global entry observation")
    _assert_true("active_communication_thread" not in global_types, "global mode does not bind active thread")
    global_planner_call = next(
        item for item in planner_calls if str(item.get("message") or "") == "confirm"
    )
    _assert_equal(global_response.intent, "enterprise_fact", "global mode reaches generic planner")
    _assert_equal(global_response.task_id, None, "global mode does not create stale pending task")
    _assert_equal(
        dict(global_planner_call.get("upload_context") or {}).get("agent_chat_context", {}).get("global_mode"),
        True,
        "planner global mode context",
    )
    _assert_equal(
        dict(global_planner_call.get("upload_context") or {}).get("agent_chat_context", {}).get("thread_id"),
        "",
        "planner global mode thread id",
    )
    missing_thread_observations = list(missing_thread_response.tool_observations or [])
    missing_thread_types = [str(item.get("observation_type") or "") for item in missing_thread_observations]
    _assert_true("active_object_resolution_failed" in missing_thread_types, "missing thread resolution failure observation")
    failed_observation = next(
        item for item in missing_thread_observations if item.get("observation_type") == "active_object_resolution_failed"
    )
    _assert_equal(
        dict(failed_observation.get("payload") or {}).get("requested_thread_id"),
        f"missing-thread-{suffix}",
        "missing thread requested id",
    )
    _assert_equal(
        dict(failed_observation.get("payload") or {}).get("reason"),
        "requested_thread_not_found",
        "missing thread failure reason",
    )

    return {
        "ok": True,
        "case": "contextual_chat",
        "thread_id": thread_id,
        "brief_id": brief.brief_id,
        "contextual_observation_types": contextual_types,
        "draft_observation_types": draft_types,
        "global_observation_types": global_types,
        "missing_thread_observation_types": missing_thread_types,
    }


def run_workspace_thread_inbox() -> dict[str, Any]:
    from fastapi.testclient import TestClient
    import psycopg

    import app.main as main_module
    from app.communication.brief_store import upsert_communication_brief
    from app.communication.thread_store import set_active_communication_thread
    from app.communication.types import CommunicationBrief, CommunicationThreadRef
    from app.config import get_settings
    from app.mail.draft_store import bind_mail_draft_task, upsert_mail_draft
    from app.task_store import create_dlp_task

    suffix = uuid4().hex[:8]
    session_id = f"session-workspace-inbox-{suffix}"
    conversation_id = f"conversation-workspace-inbox-{suffix}"
    actor_context = {
        "tenant_id": "local-dev",
        "user_id": "local-user",
        "workspace_id": "workspace-thread-inbox",
        "roles": ["admin", "mail_sender"],
        "session_id": session_id,
        "conversation_id": conversation_id,
    }
    headers = {
        "X-Tenant-Id": actor_context["tenant_id"],
        "X-User-Id": actor_context["user_id"],
        "X-Workspace-Id": actor_context["workspace_id"],
        "X-Roles": ",".join(actor_context["roles"]),
    }
    thread_id = f"thread-workspace-inbox-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-workspace-inbox-1-{suffix}",
        uid=f"uid-workspace-inbox-1-{suffix}",
        thread_id=thread_id,
        provider_thread_id=f"provider-{thread_id}",
        sender="customer@example.com",
        recipients="rep@example.com",
        subject="Thread inbox workspace renewal",
        received_at="2026-06-18T10:00:00+00:00",
        summary="Customer asks for a renewal reply and expects EU failover details.",
        risk_hint="medium",
    )
    _assert_true(set_active_communication_thread(thread_id, actor_context=actor_context), "workspace active thread")
    thread_ref = CommunicationThreadRef(
        thread_id=thread_id,
        source="mail",
        subject="Thread inbox workspace renewal",
        participants=["customer@example.com", "rep@example.com"],
        last_message_at="2026-06-18T10:00:00+00:00",
        status="open",
        latest_summary="Customer asks for a renewal reply and expects EU failover details.",
        risk_hint="medium",
        actor_context=actor_context,
    )
    brief = CommunicationBrief(
        brief_id=f"brief-workspace-inbox-{suffix}",
        conversation_id=conversation_id,
        thread_ref=thread_ref,
        employee_goal="Prepare a grounded renewal reply.",
        customer_context_summary="Customer needs renewal and EU failover details.",
        grounding_refs=[{"doc_id": "enterprise-eu-failover", "title": "Enterprise EU failover"}],
        must_include=["EU failover"],
        must_avoid=["unapproved commitments"],
        open_questions=["Confirm renewal date"],
        recommended_next_action="draft_reply",
        source_observation_ids=["obs-workspace-inbox"],
        confidence=0.89,
        actor_context=actor_context,
    )
    stored_brief = upsert_communication_brief(
        brief,
        actor_context=actor_context,
        refresh_reason="workspace_thread_inbox_regression",
    )
    _assert_true(stored_brief, "workspace persisted brief")
    stored_draft = upsert_mail_draft(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan={
            "draft_id": f"draft-workspace-inbox-{suffix}",
            "mail_action_type": "send_reply",
            "status": "pending_confirmation",
            "resolved_recipients": ["customer@example.com"],
            "resolved_subject": "Re: Thread inbox workspace renewal",
            "resolved_body": "Draft preview body scoped to the selected thread.",
            "thread_ref": thread_ref.to_dict(),
            "source_brief_id": brief.brief_id,
        },
        actor_context=actor_context,
        status="pending_confirmation",
    )
    _assert_true(stored_draft, "workspace draft preview")
    created_task = create_dlp_task(
        session_id=session_id,
        conversation_id=conversation_id,
        message_raw="Draft preview body scoped to the selected thread.",
        request_message="Send the selected thread reply.",
        delivery_subject="Re: Thread inbox workspace renewal",
        delivery_body="Draft preview body scoped to the selected thread.",
        destination_email="customer@example.com",
        status="pending_approval",
        domain_action="mail_send",
        domain_payload={},
        mail_draft_id=str(stored_draft["draft_id"]),
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
    )
    _assert_true(created_task, "workspace task progress")
    bound_selected_draft = bind_mail_draft_task(
        str(stored_draft["draft_id"]),
        actor_context=actor_context,
        confirmation_key=str(stored_draft["confirmation_key"]),
        task_id=str(created_task["task_id"]),
    )
    _assert_equal(
        (bound_selected_draft or {}).get("status"),
        "queued_dlp",
        "selected draft queued after DLP bind",
    )
    other_thread_id = f"thread-workspace-other-{suffix}"
    _seed_thread_store_message(
        actor_context=actor_context,
        message_id=f"msg-workspace-other-1-{suffix}",
        uid=f"uid-workspace-other-1-{suffix}",
        thread_id=other_thread_id,
        provider_thread_id=f"provider-{other_thread_id}",
        sender="other-customer@example.com",
        recipients="rep@example.com",
        subject="Other thread in same conversation",
        received_at="2026-06-18T10:05:00+00:00",
        summary="Other customer thread must not contaminate the selected workspace.",
        risk_hint="low",
    )
    other_thread_ref = CommunicationThreadRef(
        thread_id=other_thread_id,
        source="mail",
        subject="Other thread in same conversation",
        participants=["other-customer@example.com", "rep@example.com"],
        last_message_at="2026-06-18T10:05:00+00:00",
        status="open",
        latest_summary="Other customer thread must not contaminate the selected workspace.",
        risk_hint="low",
        actor_context=actor_context,
    )
    other_brief = CommunicationBrief(
        brief_id=f"brief-workspace-other-{suffix}",
        conversation_id=conversation_id,
        thread_ref=other_thread_ref,
        employee_goal="Prepare an unrelated reply.",
        customer_context_summary="This brief belongs to the other thread.",
        recommended_next_action="draft_reply",
        source_observation_ids=["obs-workspace-other"],
        confidence=0.75,
        actor_context=actor_context,
    )
    upsert_communication_brief(
        other_brief,
        actor_context=actor_context,
        refresh_reason="workspace_thread_inbox_contamination_regression",
    )
    other_draft = upsert_mail_draft(
        session_id=session_id,
        conversation_id=conversation_id,
        mail_plan={
            "draft_id": f"draft-workspace-other-{suffix}",
            "mail_action_type": "send_reply",
            "status": "pending_confirmation",
            "resolved_recipients": ["other-customer@example.com"],
            "resolved_subject": "Re: Other thread in same conversation",
            "resolved_body": "This other draft must not appear for the selected thread.",
            "thread_ref": other_thread_ref.to_dict(),
            "source_brief_id": other_brief.brief_id,
        },
        actor_context=actor_context,
        status="pending_confirmation",
    )
    _assert_true(other_draft, "other workspace draft")
    other_task = create_dlp_task(
        session_id=session_id,
        conversation_id=conversation_id,
        message_raw="This other draft must not appear for the selected thread.",
        request_message="Send the other thread reply.",
        delivery_subject="Re: Other thread in same conversation",
        delivery_body="This other draft must not appear for the selected thread.",
        destination_email="other-customer@example.com",
        status="pending_approval",
        domain_action="mail_send",
        domain_payload={},
        mail_draft_id=str(other_draft["draft_id"]),
        tenant_id=actor_context["tenant_id"],
        user_id=actor_context["user_id"],
        workspace_id=actor_context["workspace_id"],
    )
    _assert_true(other_task, "other workspace task")
    bound_other_draft = bind_mail_draft_task(
        str(other_draft["draft_id"]),
        actor_context=actor_context,
        confirmation_key=str(other_draft["confirmation_key"]),
        task_id=str(other_task["task_id"]),
    )
    _assert_equal(
        (bound_other_draft or {}).get("status"),
        "queued_dlp",
        "other draft queued after DLP bind",
    )
    with psycopg.connect(get_settings().postgres_dsn) as conn:
        conn.execute(
            """
            UPDATE mail_drafts
            SET status = 'queued_dlp', updated_at = %s
            WHERE draft_id = %s
            """,
            ("2026-06-18T10:06:00+00:00", stored_draft["draft_id"]),
        )
        conn.execute(
            """
            UPDATE mail_drafts
            SET status = 'queued_dlp', updated_at = %s
            WHERE draft_id = %s
            """,
            ("2026-06-18T10:07:00+00:00", other_draft["draft_id"]),
        )
        conn.commit()

    client = TestClient(main_module.app)
    deep_link_response = client.get(
        "/internal/communication/workspace",
        params={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "thread_id": other_thread_id,
        },
        headers=headers,
    )
    _assert_equal(deep_link_response.status_code, 200, "workspace deep-link API status")
    deep_link_workspace = deep_link_response.json()
    _assert_equal(
        (deep_link_workspace.get("selected_thread") or {}).get("thread_id"),
        other_thread_id,
        "workspace deep-link displays requested thread",
    )
    _assert_equal(
        (deep_link_workspace.get("active_thread") or {}).get("thread_id"),
        thread_id,
        "workspace GET does not overwrite active thread",
    )
    active_after_deep_link = client.get("/internal/communication/threads/active", headers=headers)
    _assert_equal(active_after_deep_link.status_code, 200, "active thread API status after deep-link")
    _assert_equal(
        (active_after_deep_link.json().get("active_thread") or {}).get("thread_id"),
        thread_id,
        "active thread survives read-only workspace GET",
    )
    response = client.get(
        "/internal/communication/workspace",
        params={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "thread_id": thread_id,
            "refresh": "false",
        },
        headers=headers,
    )
    _assert_equal(response.status_code, 200, "workspace API status")
    workspace = response.json()
    _assert_true(workspace.get("ok"), "workspace ok")
    thread_ids = [item.get("thread_id") for item in workspace.get("threads") or []]
    _assert_true(thread_id in thread_ids, "workspace thread list includes selected thread")
    _assert_equal(
        (workspace.get("selected_thread") or {}).get("thread_id"),
        thread_id,
        "workspace selected thread detail",
    )
    _assert_equal(
        (workspace.get("active_thread") or {}).get("thread_id"),
        thread_id,
        "workspace active thread",
    )
    _assert_equal(
        (workspace.get("latest_brief") or {}).get("brief", {}).get("brief_id"),
        brief.brief_id,
        "workspace active brief",
    )
    _assert_equal(
        (workspace.get("copilot_context") or {}).get("thread_id"),
        thread_id,
        "workspace Copilot thread context",
    )
    _assert_equal(
        (workspace.get("copilot_context") or {}).get("brief_id"),
        brief.brief_id,
        "workspace Copilot brief context",
    )
    _assert_equal(
        (workspace.get("draft_preview") or {}).get("draft_id"),
        stored_draft["draft_id"],
        "workspace draft preview state",
    )
    _assert_true(
        (workspace.get("draft_preview") or {}).get("draft_id") != other_draft["draft_id"],
        "workspace excludes other thread draft preview",
    )
    task_ids = [item.get("task_id") for item in workspace.get("task_progress") or []]
    _assert_true(created_task["task_id"] in task_ids, "workspace task progress state")
    _assert_true(other_task["task_id"] not in task_ids, "workspace excludes other thread task progress")
    _assert_equal(
        (workspace.get("governance_boundary") or {}).get("high_risk_approval_surface"),
        "8512_governance_console",
        "workspace governance boundary",
    )

    refreshed = client.get(
        "/internal/communication/workspace",
        params={"session_id": session_id, "conversation_id": conversation_id, "refresh": "false"},
        headers=headers,
    )
    _assert_equal(refreshed.status_code, 200, "workspace refresh API status")
    _assert_equal(
        (refreshed.json().get("selected_thread") or {}).get("thread_id"),
        thread_id,
        "workspace selected thread survives refresh via active store",
    )

    return {
        "ok": True,
        "case": "workspace_thread_inbox",
        "thread_id": thread_id,
        "brief_id": brief.brief_id,
        "draft_id": stored_draft["draft_id"],
        "task_id": created_task["task_id"],
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
        "brief_store": run_brief_store,
        "thread_store": run_thread_store,
        "mail_closeout": run_mail_closeout,
        "subordinate_inputs": run_subordinate_inputs,
        "retirement": run_retirement,
        "runtime_brief_closeout": run_runtime_brief_closeout,
        "grounded_reply_from_thread": run_grounded_reply_from_thread,
        "explicit_prior_answer_with_active_brief": run_explicit_prior_answer_with_active_brief,
        "default_closeout_prefers_brief_with_generic_overlap": run_default_closeout_prefers_brief_with_generic_overlap,
        "prior_answer_without_topic_overlap": run_prior_answer_without_topic_overlap,
        "polish_rewrite_from_active_brief": run_polish_rewrite_from_active_brief,
        "contextual_chat": run_contextual_chat,
        "workspace_thread_inbox": run_workspace_thread_inbox,
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
