from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


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


def run_retirement() -> dict[str, Any]:
    retired_tokens = [
        "legacy_orchestration",
        "legacy_aggregator",
        "enable_legacy_orchestration_fallback",
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
        return {
            "answer": "Structured recovery answer.",
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
        "mail_closeout": run_mail_closeout,
        "subordinate_inputs": run_subordinate_inputs,
        "retirement": run_retirement,
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
