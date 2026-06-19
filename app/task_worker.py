from __future__ import annotations

import re
import hashlib
import json
import time
from typing import Any

from celery import Task

from app.dlp_runtime import max_risk_level, model_summary_and_risk, retrieve_dlp_evidence
from app.dlp_scenarios import normalize_fault_injection
from app.inbound_mail import generate_daily_mail_digest, sync_inbound_mail
from app.mcp_client import call_mcp_tool
from app.metrics import record_fault_injection, record_mail_dlq_created, record_task_degradation, record_task_retry
from app.orchestration.observations import make_typed_observation
from app.privacy_lab import scan_sensitive_message
from app.resilience import make_failure_observation
from app.task_events import build_task_event, publish_task_event
from app.task_queue import celery_app, enqueue_email_send_task
from app.task_store import add_task_event, create_mail_dlq_entry, get_dlp_task, set_task_status, task_communication_provenance, update_task
from app.upload_blob_store import load_upload_blob
from app.orchestration.tool_discovery import dispatch_tool_call
from app.orchestration.types import OrchestrationContext


TEMPORARY_PROVIDER_TOKENS = ("timeout", "timed out", "tempor", "refused", "unavailable", "reset", "limit", "quota", "429")
PERMANENT_PROVIDER_TOKENS = ("auth", "credential", "password", "invalid recipient", "mailbox unavailable", "550", "553", "format")


def _actor_context_from_task(task: dict[str, Any]) -> dict[str, Any]:
    actor_context = {
        "tenant_id": str(task.get("tenant_id") or ""),
        "user_id": str(task.get("user_id") or ""),
        "workspace_id": str(task.get("workspace_id") or ""),
        "session_id": str(task.get("session_id") or ""),
        "conversation_id": str(task.get("conversation_id") or ""),
    }
    provenance = task_communication_provenance(task)
    if provenance.get("thread_id"):
        actor_context["thread_id"] = str(provenance["thread_id"])
    if provenance.get("brief_id"):
        actor_context["brief_id"] = str(provenance["brief_id"])
    return actor_context


def _with_task_communication_provenance(observation: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    provenance = task_communication_provenance(task)
    if not provenance:
        return observation
    enriched = dict(observation or {})
    enriched_payload = dict(enriched.get("payload") or {})
    enriched_payload.update(provenance)
    enriched["payload"] = enriched_payload
    enriched_provenance = dict(enriched.get("provenance") or {})
    enriched_provenance.update({key: value for key, value in provenance.items() if key in {"thread_id", "brief_id", "communication_context"}})
    enriched["provenance"] = enriched_provenance
    enriched_actor = dict(enriched.get("actor_context") or {})
    if provenance.get("thread_id"):
        enriched_actor["thread_id"] = str(provenance["thread_id"])
    if provenance.get("brief_id"):
        enriched_actor["brief_id"] = str(provenance["brief_id"])
    enriched["actor_context"] = enriched_actor
    return enriched


def _payload_digest_for_task(task: dict[str, Any], *, operation: str) -> str:
    payload = {
        "operation": operation,
        "task_id": str(task.get("task_id") or ""),
        "idempotency_key": str(task.get("idempotency_key") or ""),
        "destination_email": str(task.get("destination_email") or ""),
        "delivery_subject": str(task.get("delivery_subject") or ""),
        "delivery_body": str(task.get("delivery_body") or ""),
        "mail_draft_id": str(task.get("mail_draft_id") or ""),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _create_mail_dlq_for_task(
    task: dict[str, Any],
    *,
    operation: str,
    error: str,
    attempt_count: int,
    safe_replay_allowed: bool,
    recovery_hint: str,
) -> dict[str, Any]:
    provenance = task_communication_provenance(task)
    entry = create_mail_dlq_entry(
        task_id=str(task["task_id"]),
        operation=operation,
        payload_digest=_payload_digest_for_task(task, operation=operation),
        last_error=error,
        attempt_count=attempt_count,
        safe_replay_allowed=safe_replay_allowed,
        recovery_hint=recovery_hint,
        actor_context=_actor_context_from_task(task),
        payload_snapshot={
            "task_type": str(task.get("task_type") or ""),
            "status": str(task.get("status") or ""),
            "delivery_status": str(task.get("delivery_status") or ""),
            "destination_email": str(task.get("destination_email") or ""),
            "mail_draft_id": str(task.get("mail_draft_id") or ""),
            "idempotency_key": str(task.get("idempotency_key") or ""),
            **provenance,
        },
    )
    record_mail_dlq_created(operation, safe_replay_allowed)
    return entry


def _looks_like_instruction_only(text: str) -> bool:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    if re.fullmatch(
        r"(please\s+)?(send|forward)\s+(this|it)(\s+(note|text|message|paragraph))?\s+to\s+\S+[.!?]?",
        lowered,
    ):
        return True
    if re.fullmatch(
        r"(please\s+)?send\s+the\s+following(\s+(note|text|content|paragraph))?(\s+to\s+\S+)?[.!?]?",
        lowered,
    ):
        return True
    if re.fullmatch(r"帮我把如下(文段|内容)发送(?:到\S+)?[。.!！?？]?", cleaned):
        return True
    if re.fullmatch(r"帮我发一下这个[。.!！?？]?", cleaned):
        return True
    return False


def _draft_summary(text: str, limit: int = 420) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned or _looks_like_instruction_only(cleaned):
        return "No substantial outbound content was provided."
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}..."


def _is_template_summary(summary: str, redacted_text: str) -> bool:
    cleaned = " ".join((summary or "").split()).strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    redacted_lower = " ".join((redacted_text or "").split()).lower()
    template_markers = (
        "the request is asking to send text",
        "actual content being sent is not visible",
        "please send this note to",
        "please summarize these",
        "summary draft:",
    )
    if any(marker in lowered for marker in template_markers):
        return True
    if lowered.startswith("summary:") and len(cleaned) < 80:
        return True
    if redacted_lower and "content:" in lowered and redacted_lower not in lowered and len(cleaned) < 120:
        return True
    return False


def _publish_snapshot(task: dict[str, Any], event_type: str, message: str) -> None:
    provenance = task_communication_provenance(task)
    publish_task_event(
        build_task_event(
            task_id=str(task["task_id"]),
            status=str(task["status"]),
            event_type=event_type,
            message=message,
            risk_level=str(task.get("risk_level", "")),
            delivery_error=str(task.get("delivery_error", "")),
            delivery_status=str(task.get("delivery_status", "")),
            thread_id=str(provenance.get("thread_id") or ""),
            brief_id=str(provenance.get("brief_id") or ""),
            communication_context=dict(provenance.get("communication_context") or {}),
        )
    )


def _append_runtime_event(
    task: dict[str, Any],
    *,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    add_task_event(str(task["task_id"]), event_type, "worker", {"message": message, **(details or {})})
    publish_task_event(
        build_task_event(
            task_id=str(task["task_id"]),
            status=str(task["status"]),
            event_type=event_type,
            message=message,
            risk_level=str(task.get("risk_level", "")),
            delivery_error=str(task.get("delivery_error", "")),
            delivery_status=str(task.get("delivery_status", "")),
        )
    )


def _email_subject(task: dict[str, Any]) -> str:
    subject = str(task.get("delivery_subject") or "").strip()
    if subject:
        return subject
    source_filename = str(task.get("source_filename") or "").strip()
    if source_filename:
        return f"附件：{source_filename}"
    return f"外发内容 - {task.get('task_id', '')}"


def _task_attachment_payload(task: dict[str, Any]) -> list[dict[str, str]]:
    if str(task.get("attachment_strategy", "")) != "attach_original_upload":
        return []
    blob_id = str(task.get("attachment_blob_id") or "").strip()
    if blob_id:
        blob = load_upload_blob(blob_id)
        if blob:
            return [
                {
                    "filename": str(blob.get("filename") or task.get("attachment_filename") or "uploaded-content.bin"),
                    "content_type": str(blob.get("content_type") or task.get("attachment_content_type") or "application/octet-stream"),
                    "content_bytes": blob.get("content_bytes") or b"",
                }
            ]
    content = str(task.get("attachment_content") or "").strip()
    if not content:
        return []
    return [
        {
            "filename": str(task.get("attachment_filename") or task.get("source_filename") or "uploaded-content.txt"),
            "content_type": str(task.get("attachment_content_type") or "text/plain"),
            "content": content,
        }
    ]


def _review_content(task: dict[str, Any]) -> str:
    parts = [str(task.get("message_raw") or "").strip()]
    for attachment in _task_attachment_payload(task):
        if attachment.get("content"):
            parts.append(str(attachment.get("content") or "").strip())
    return "\n\n".join(part for part in parts if part)


def _email_body(task: dict[str, Any]) -> str:
    body = str(task.get("delivery_body") or "").strip()
    if body:
        return body
    fallback = str(task.get("draft_summary") or "").strip()
    if fallback:
        return fallback
    return str(task.get("message_redacted") or task.get("message_raw") or "").strip()


def _classify_provider_error(error: str) -> str:
    lowered = (error or "").lower()
    if any(token in lowered for token in TEMPORARY_PROVIDER_TOKENS):
        return "provider_temporary"
    if any(token in lowered for token in PERMANENT_PROVIDER_TOKENS):
        return "provider_permanent"
    return "provider_permanent" if lowered else "transient_infra"


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in reasons:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def _finalize_worker_failure(task: dict[str, Any], exc: Exception) -> dict[str, Any] | None:
    error = str(exc)
    current_status = str(task.get("status", ""))
    task_id = str(task["task_id"])
    if current_status in {"sending", "delivery_deferred", "queued_for_send", "approved", "send_failed"}:
        category = _classify_provider_error(error)
        task = set_task_status(
            task_id,
            "send_failed",
            actor="worker",
            event_type="send_failed",
            event_message="Outbound email failed after worker retries were exhausted.",
            extra_updates={
                "delivery_status": "send_failed",
                "delivery_error": error,
                "final_result": f"Email send failed after retries: {error}",
                "last_error_category": category,
                "manual_handover_required": category == "provider_temporary",
                "next_recommended_action": (
                    "Retry send later or switch to manual handover."
                    if category == "provider_temporary"
                    else "Review recipient, SMTP settings, or content policy before retrying."
                ),
            },
            details={"error": error, "worker_failure": True},
        )
        if task:
            dlq = _create_mail_dlq_for_task(
                task,
                operation="send_email_smtp",
                error=error,
                attempt_count=0,
                safe_replay_allowed=category == "provider_temporary",
                recovery_hint=(
                    "retry_send_with_same_idempotency_key"
                    if category == "provider_temporary"
                    else "manual_handover_or_repair_configuration"
                ),
            )
            task = update_task(
                task_id,
                status="dead_letter",
                delivery_status="dead_letter",
                domain_result={
                    **dict(task.get("domain_result") or {}),
                    "dlq_entry": dlq,
                },
            ) or task
            _publish_snapshot(task, "send_failed", "Outbound email failed after worker retries were exhausted.")
        return task

    task = set_task_status(
        task_id,
        "failed",
        actor="worker",
        event_type="failed",
        event_message="DLP risk processing failed after worker retries were exhausted.",
        extra_updates={
            "delivery_status": "failed",
            "delivery_error": error,
            "final_result": f"DLP processing failed after retries: {error}",
            "last_error_category": "transient_infra",
            "next_recommended_action": "Inspect worker logs, retrieval service, and model provider health before retrying.",
        },
        details={"error": error, "worker_failure": True},
    )
    if task:
        _publish_snapshot(task, "failed", "DLP risk processing failed after worker retries were exhausted.")
    return task


class GovernedTaskBase(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):  # type: ignore[override]
        if getattr(self.request, "retries", 0) < getattr(self, "max_retries", 0):
            return super().on_failure(exc, task_id, args, kwargs, einfo)
        dlp_task_id = str(args[0]) if args else ""
        if dlp_task_id:
            task = get_dlp_task(dlp_task_id)
            if task:
                _finalize_worker_failure(task, exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
        super().on_failure(exc, task_id, args, kwargs, einfo)


def _run_rule_retrieval_model_pipeline(task: dict[str, Any], fault_injection: dict[str, Any]) -> dict[str, Any]:
    queue_delay_seconds = int(fault_injection.get("force_queue_delay_seconds", 0) or 0)
    if queue_delay_seconds > 0:
        record_fault_injection("force_queue_delay_seconds")
        _append_runtime_event(
            task,
            event_type="fault_injected_queue_delay",
            message=f"Injected queue delay of {queue_delay_seconds} seconds.",
            details={"queue_delay_seconds": queue_delay_seconds},
        )
        time.sleep(queue_delay_seconds)

    review_content = _review_content(task)
    rule_result = scan_sensitive_message(
        review_content,
        source_filename=str(task.get("source_filename", "")),
        source_content_type=str(task.get("source_content_type", "")),
    )

    redacted_text = str(rule_result.get("redacted_text", ""))
    risk_reasons = list(rule_result.get("risk_reasons", []))
    redactions = list(rule_result.get("redactions", []))
    rule_risk_level = str(rule_result.get("risk_level", "low"))

    retrieved_evidence: list[dict[str, Any]] = []
    retrieval_status = "not_requested"
    summary_status = "not_requested"
    degradation_reasons: list[str] = []
    last_error_category = ""
    draft_summary = _draft_summary(redacted_text)
    model_risk_level = "low"
    model_reasons: list[str] = []

    if fault_injection.get("force_retrieval_empty"):
        record_fault_injection("force_retrieval_empty")
        retrieval_status = "empty"
        degradation_reasons.append("Injected retrieval empty; fallback switched to rule_only.")
        last_error_category = "transient_infra"
        _append_runtime_event(
            task,
            event_type="fault_injected_retrieval_empty",
            message="Injected empty retrieval result; risk review will continue in rule_only mode.",
        )
    else:
        try:
            retrieved_evidence = retrieve_dlp_evidence(redacted_text)
            retrieval_status = "completed" if retrieved_evidence else "empty"
            if not retrieved_evidence:
                degradation_reasons.append("No policy evidence was retrieved; fallback switched to rule_only.")
                last_error_category = last_error_category or "transient_infra"
        except Exception as exc:
            retrieval_status = "failed"
            degradation_reasons.append(f"Policy evidence retrieval failed; fallback switched to rule_only. {exc}")
            last_error_category = last_error_category or "transient_infra"

    force_rule_only = bool(fault_injection.get("force_rule_only_mode"))
    if force_rule_only:
        record_fault_injection("force_rule_only_mode")
        degradation_reasons.append("Lab replay requested rule_only risk assessment.")
        _append_runtime_event(
            task,
            event_type="forced_rule_only_mode",
            message="Lab replay forced rule_only risk assessment mode.",
        )

    if fault_injection.get("force_model_timeout"):
        record_fault_injection("force_model_timeout")
        summary_status = "failed"
        degradation_reasons.append("Injected model timeout; fallback switched to rule_only.")
        last_error_category = "provider_temporary"
        _append_runtime_event(
            task,
            event_type="fault_injected_model_timeout",
            message="Injected model timeout; risk review will continue in rule_only mode.",
        )
    elif force_rule_only or retrieval_status != "completed":
        summary_status = "degraded_rule_only"
    else:
        try:
            model_result = model_summary_and_risk(
                redacted_text=redacted_text,
                evidence=retrieved_evidence,
                requested_action=str(task.get("requested_action", "summarize_and_send")),
                destination_email=str(task.get("destination_email", "")),
                source_filename=str(task.get("source_filename", "")),
            )
            candidate_summary = str(model_result.get("summary") or "").strip()
            if candidate_summary and not _is_template_summary(candidate_summary, redacted_text):
                draft_summary = candidate_summary
            else:
                degradation_reasons.append("Model summary output looked generic; fallback switched to rule_only summary.")
                last_error_category = last_error_category or "provider_temporary"
                summary_status = "degraded_rule_only"
            model_risk_level = str(model_result.get("risk_level", "low"))
            model_reasons = [str(item) for item in model_result.get("risk_reasons", []) if str(item).strip()]
            if summary_status != "degraded_rule_only":
                summary_status = "completed"
        except Exception as exc:
            summary_status = "failed"
            degradation_reasons.append(f"Model summary or risk scoring failed; fallback switched to rule_only. {exc}")
            last_error_category = last_error_category or "provider_temporary"

    evidence_titles = [str(item.get("title", "")).strip() for item in retrieved_evidence if str(item.get("title", "")).strip()]
    if evidence_titles:
        risk_reasons.append(f"Supporting policy evidence: {', '.join(evidence_titles[:3])}.")

    final_risk_level = rule_risk_level
    if summary_status == "completed":
        final_risk_level = max_risk_level(rule_risk_level, model_risk_level)
        risk_reasons.extend(model_reasons)
    elif degradation_reasons:
        record_task_degradation("rule_only")

    risk_reasons = _dedupe_reasons(risk_reasons)
    approval_required = final_risk_level in {"high", "critical"}
    sender_review_required = final_risk_level == "medium"
    final_result = (
        "High-risk outbound content detected. Waiting for governance approval."
        if approval_required
        else "Medium-risk outbound content detected. Waiting for sender safety confirmation."
        if sender_review_required
        else "Low-risk task passed DLP review and is queued for outbound delivery."
    )

    updates: dict[str, Any] = {
        "risk_level": final_risk_level,
        "message_redacted": redacted_text,
        "risk_reasons": risk_reasons,
        "redactions": redactions,
        "retrieved_evidence": retrieved_evidence,
        "draft_summary": draft_summary,
        "approval_required": approval_required,
        "final_result": final_result,
        "retrieval_status": retrieval_status,
        "summary_status": summary_status,
        "degradation_mode": "rule_only" if degradation_reasons else "",
        "fallback_reason": " ".join(degradation_reasons),
        "last_error_category": last_error_category,
        "next_recommended_action": (
            "Review rule_only output and approval decision."
            if degradation_reasons
            else ""
        ),
    }
    return updates


@celery_app.task(
    bind=True,
    base=GovernedTaskBase,
    name="app.task_worker.process_dlp_outbound_task",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def process_dlp_outbound_task(self, task_id: str) -> dict[str, Any]:
    task = get_dlp_task(task_id)
    if not task:
        return {"ok": False, "error": "Unknown task_id"}
    if task["status"] not in {"queued", "failed"}:
        return {"ok": True, "skipped": True, "status": task["status"]}

    if self.request.retries:
        record_task_retry("risk")

    task = set_task_status(
        task_id,
        "processing",
        actor="worker",
        event_type="processing_started",
        event_message="Worker started DLP risk assessment.",
        extra_updates={"delivery_status": "not_sent"},
    )
    if not task:
        return {"ok": False, "error": "Task vanished during processing"}
    _publish_snapshot(task, "processing_started", "Worker started DLP risk assessment.")

    fault_injection = normalize_fault_injection(task.get("fault_injection"))
    updates = _run_rule_retrieval_model_pipeline(task, fault_injection)
    task = update_task(task_id, **updates)
    if not task:
        return {"ok": False, "error": "Task vanished after risk analysis"}

    if str(task.get("risk_level") or "") == "medium":
        sender_observation = _with_task_communication_provenance(make_typed_observation(
            observation_type="sender_safety_confirmation",
            source="dlp_worker",
            status="blocked",
            grounding_kind="guardrail",
            summary="Medium-risk outbound content is waiting for sender safety confirmation.",
            payload={
                "task_id": task_id,
                "risk_level": "medium",
                "confirmation_required": True,
                "confirmation_surface": "8511_sender_workspace",
                "risk_reasons": list(task.get("risk_reasons") or []),
                "next_recommended_action": "Sender should review the redacted content before confirming delivery.",
            },
            provenance={"source": "dlp_worker"},
            confidence=0.98,
            actor_context=_actor_context_from_task(task),
            success=True,
        ), task)
        task = set_task_status(
            task_id,
            "sender_review_required",
            actor="worker",
            event_type="sender_review_required",
            event_message="Medium-risk outbound content detected. Waiting for sender safety confirmation.",
            extra_updates={
                "delivery_status": "sender_review_required",
                "domain_result": {
                    "ok": False,
                    "blocked_by": "sender_safety_confirmation",
                    "sender_safety_observation": sender_observation,
                },
                "next_recommended_action": "Review the redacted content in the sender workspace before confirming delivery.",
            },
            details={"risk_reasons": task.get("risk_reasons", []), "sender_safety_observation": sender_observation},
        )
        if task:
            _publish_snapshot(task, "sender_review_required", "Medium-risk outbound content is waiting for sender safety confirmation.")
        return {"ok": True, "status": "sender_review_required"}

    if bool(task.get("approval_required", False)):
        recovery_observation = _with_task_communication_provenance(make_typed_observation(
            observation_type="governance_recovery",
            source="dlp_worker",
            status="blocked",
            grounding_kind="guardrail",
            summary="High-risk outbound content is blocked pending human approval.",
            payload={
                "task_id": task_id,
                "risk_level": str(task.get("risk_level") or ""),
                "approval_required": True,
                "recovery_strategy": "human_approval_required_before_send",
                "risk_reasons": list(task.get("risk_reasons") or []),
                "next_recommended_action": "Review the redacted content and approve or reject the outbound request.",
            },
            provenance={"source": "dlp_worker"},
            confidence=0.98,
            actor_context=_actor_context_from_task(task),
            success=True,
        ), task)
        task = set_task_status(
            task_id,
            "pending_approval",
            actor="worker",
            event_type="pending_approval",
            event_message="Sensitive outbound content detected. Waiting for human approval.",
            extra_updates={
                "delivery_status": "pending_approval",
                "domain_result": {
                    "ok": False,
                    "blocked_by": "dlp_high_risk",
                    "recovery_observation": recovery_observation,
                },
                "next_recommended_action": "Review the redacted content and approve or reject the outbound request.",
            },
            details={"risk_reasons": task.get("risk_reasons", []), "recovery_observation": recovery_observation},
        )
        if task:
            _publish_snapshot(task, "pending_approval", "Sensitive outbound content detected. Waiting for approval.")
        return {"ok": True, "status": "pending_approval"}

    task = set_task_status(
        task_id,
        "queued_for_send",
        actor="worker",
        event_type="queued_for_send",
        event_message="Low-risk task queued for outbound email sending.",
        extra_updates={"delivery_status": "queued_for_send"},
    )
    if task:
        _publish_snapshot(task, "queued_for_send", "Low-risk task queued for outbound email sending.")
    try:
        enqueue_email_send_task(task_id)
    except Exception as exc:
        error = str(exc)
        failure_observation = _with_task_communication_provenance(make_failure_observation(
            service="celery",
            operation="enqueue_email_send_task",
            error=error,
            fallback_strategy="mark_send_failed_and_return_recovery_observation",
            retry_count=0,
            actor_context=_actor_context_from_task(task),
            severity="high",
        ), task)
        task = set_task_status(
            task_id,
            "send_failed",
            actor="worker",
            event_type="enqueue_email_failed",
            event_message="Email send task could not be queued.",
            extra_updates={
                "delivery_status": "send_failed",
                "delivery_error": error,
                "final_result": "Email send task could not be queued for worker execution.",
                "last_error_category": "transient_infra",
                "manual_handover_required": True,
                "next_recommended_action": "Check Redis/Celery email worker health, then retry or use manual handover.",
                "domain_result": {
                    "ok": False,
                    "error": error,
                    "recovery_observation": failure_observation,
                },
            },
            details={"error": error, "recovery_observation": failure_observation},
        )
        if task:
            _publish_snapshot(task, "enqueue_email_failed", "Email send task could not be queued.")
        return {"ok": False, "status": "send_failed", "error": error}
    return {"ok": True, "status": "queued_for_send"}


@celery_app.task(
    bind=True,
    base=GovernedTaskBase,
    name="app.task_worker.send_dlp_email_task",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_dlp_email_task(self, task_id: str) -> dict[str, Any]:
    task = get_dlp_task(task_id)
    if not task:
        return {"ok": False, "error": "Unknown task_id"}
    if task["status"] == "sent":
        return {"ok": True, "skipped": True, "status": "sent"}
    if task["status"] not in {"queued_for_send", "approved", "send_failed", "delivery_deferred"}:
        return {"ok": True, "skipped": True, "status": task["status"]}

    if self.request.retries:
        record_task_retry("email")

    task = set_task_status(
        task_id,
        "sending",
        actor="worker",
        event_type="sending",
        event_message="Worker is sending the outbound email.",
        extra_updates={"delivery_status": "sending"},
    )
    if not task:
        return {"ok": False, "error": "Task vanished during send"}
    _publish_snapshot(task, "sending", "Worker is sending the outbound email.")

    fault_injection = normalize_fault_injection(task.get("fault_injection"))
    if fault_injection.get("force_smtp_fail"):
        record_fault_injection("force_smtp_fail")
        error = "Injected SMTP failure for scenario replay."
        failure_observation = _with_task_communication_provenance(make_failure_observation(
            service="smtp",
            operation="send_email_smtp",
            error=error,
            fallback_strategy="mark_send_failed_and_require_retry_or_manual_handover",
            retry_count=0,
            actor_context=_actor_context_from_task(task),
            severity="medium",
        ), task)
        task = set_task_status(
            task_id,
            "send_failed",
            actor="worker",
            event_type="send_failed",
            event_message="Outbound email failed to send.",
            extra_updates={
                "delivery_status": "send_failed",
                "delivery_result": "",
                "delivery_error": error,
                "smtp_provider": "smtp_injected",
                "final_result": "Email send failed in scenario replay.",
                "last_error_category": "provider_temporary",
                "manual_handover_required": False,
                "next_recommended_action": "Use retry-send or manual handover once governance actions are enabled.",
                "domain_result": {
                    "ok": False,
                    "error": error,
                    "failure_observation": failure_observation,
                    "recovery_observation": failure_observation,
                },
            },
            details={"error": error, "injected": True, "recovery_observation": failure_observation},
        )
        if task:
            _publish_snapshot(task, "send_failed", "Outbound email failed to send.")
        return {"ok": False, "status": "send_failed", "error": error}

    if fault_injection.get("force_smtp_uncertain"):
        record_fault_injection("force_smtp_uncertain")
        error = "Injected SMTP uncertain result after provider accepted payload."
        failure_observation = _with_task_communication_provenance(make_failure_observation(
            service="smtp",
            operation="send_email_smtp",
            error=error,
            fallback_strategy="mark_delivery_uncertain_and_require_manual_verification",
            retry_count=int(self.request.retries or 0),
            actor_context=_actor_context_from_task(task),
            severity="high",
        ), task)
        task = set_task_status(
            task_id,
            "delivery_uncertain",
            actor="worker",
            event_type="delivery_uncertain",
            event_message="SMTP provider result is uncertain; automatic replay is blocked.",
            extra_updates={
                "delivery_status": "delivery_uncertain",
                "delivery_result": "",
                "delivery_error": error,
                "smtp_provider": "smtp_injected",
                "final_result": "Email delivery result is uncertain and requires manual verification before replay.",
                "last_error_category": "provider_uncertain",
                "manual_handover_required": True,
                "next_recommended_action": "Check provider outbox/logs before any replay; do not resend automatically.",
                "domain_result": {
                    "ok": False,
                    "error": error,
                    "uncertain": True,
                    "failure_observation": failure_observation,
                    "recovery_observation": failure_observation,
                },
            },
            details={"error": error, "injected": True, "uncertain": True, "recovery_observation": failure_observation},
        )
        if task:
            dlq = _create_mail_dlq_for_task(
                task,
                operation="send_email_smtp",
                error=error,
                attempt_count=int(self.request.retries or 0),
                safe_replay_allowed=False,
                recovery_hint="manual_verify_provider_outbox_before_replay",
            )
            task = update_task(
                task_id,
                status="dead_letter",
                delivery_status="delivery_uncertain",
                domain_result={
                    **dict(task.get("domain_result") or {}),
                    "dlq_entry": dlq,
                },
            ) or task
            _publish_snapshot(task, "delivery_uncertain", "SMTP provider result is uncertain; automatic replay is blocked.")
        return {"ok": False, "status": "delivery_uncertain", "error": error}

    try:
        tool_result = call_mcp_tool(
            "send_email_smtp",
            {
                "to_email": str(task["destination_email"]),
                "subject": _email_subject(task),
                "body": _email_body(task),
                "attachments": _task_attachment_payload(task),
            },
        )
    except Exception as exc:
        error = str(exc)
        category = _classify_provider_error(error)
        if category == "provider_temporary" and self.request.retries < self.max_retries:
            task = set_task_status(
                task_id,
                "delivery_deferred",
                actor="worker",
                event_type="delivery_deferred",
                event_message="Temporary SMTP issue detected. Delivery deferred for retry.",
                extra_updates={
                    "delivery_status": "delivery_deferred",
                    "delivery_error": error,
                    "last_error_category": category,
                    "manual_handover_required": False,
                    "next_recommended_action": "Worker will retry automatically. If retries are exhausted, switch to manual handover.",
                    "final_result": f"Outbound delivery deferred: {error}",
                },
                details={"error": error},
            )
            if task:
                _publish_snapshot(task, "delivery_deferred", "Temporary SMTP issue detected. Delivery deferred for retry.")
            raise RuntimeError(error) from exc
        failure_observation = _with_task_communication_provenance(make_failure_observation(
            service="smtp",
            operation="send_email_smtp",
            error=error,
            fallback_strategy="mark_send_failed_and_require_retry_or_manual_handover",
            retry_count=0,
            actor_context=_actor_context_from_task(task),
        ), task)
        task = set_task_status(
            task_id,
            "send_failed",
            actor="worker",
            event_type="send_failed",
            event_message="Outbound email failed to send.",
            extra_updates={
                "delivery_status": "send_failed",
                "delivery_error": error,
                "last_error_category": category,
                "manual_handover_required": category == "provider_temporary",
                "next_recommended_action": (
                    "Retry send later or switch to manual handover."
                    if category == "provider_temporary"
                    else "Review recipient, SMTP settings, or content policy before retrying."
                ),
                "final_result": f"Email send failed: {error}",
                "domain_result": {
                    "ok": False,
                    "error": error,
                    "failure_observation": failure_observation,
                    "recovery_observation": failure_observation,
                },
            },
            details={"error": error, "recovery_observation": failure_observation},
        )
        if task:
            _publish_snapshot(task, "send_failed", "Outbound email failed to send.")
        return {"ok": False, "status": "send_failed", "error": error}

    payload = tool_result.get("result") if tool_result.get("ok") else {"ok": False, "error": tool_result.get("error", "")}
    success = bool(payload.get("ok"))
    uncertain_result = bool(payload.get("uncertain") or payload.get("status") == "delivery_uncertain")
    provider = str(payload.get("provider") or "smtp")
    sent_at = str(payload.get("sent_at") or "")
    error = str(payload.get("error") or "")
    error_category = _classify_provider_error(error) if error else ""
    attachments_sent = int(payload.get("attachments_sent") or 0)
    delivery_result = (
        f"Redacted summary sent to {task['destination_email']}."
        if attachments_sent <= 0
        else f"Redacted summary sent to {task['destination_email']} with {attachments_sent} attachment(s)."
    ) if success else ""

    if success and not uncertain_result:
        task = set_task_status(
            task_id,
            "sent",
            actor="worker",
            event_type="sent",
            event_message="Outbound email was sent successfully.",
            extra_updates={
                "delivery_status": "sent",
                "delivery_result": delivery_result,
                "delivery_error": "",
                "smtp_provider": provider,
                "sent_at": sent_at,
                "final_result": delivery_result,
                "manual_handover_required": False,
                "next_recommended_action": "",
            },
        )
        if task:
            _publish_snapshot(task, "sent", "Outbound email was sent successfully.")
        return {"ok": True, "status": "sent"}

    if uncertain_result:
        error = error or "SMTP provider returned an uncertain delivery result."
        failure_observation = _with_task_communication_provenance(dict(payload.get("failure_observation") or make_failure_observation(
            service="smtp",
            operation="send_email_smtp",
            error=error,
            fallback_strategy="mark_delivery_uncertain_and_require_manual_verification",
            retry_count=int((payload.get("resilience") or {}).get("retry_count") or 0) if isinstance(payload.get("resilience"), dict) else 0,
            actor_context=_actor_context_from_task(task),
        )), task)
        task = set_task_status(
            task_id,
            "delivery_uncertain",
            actor="worker",
            event_type="delivery_uncertain",
            event_message="SMTP provider returned an uncertain delivery result.",
            extra_updates={
                "delivery_status": "delivery_uncertain",
                "delivery_result": "",
                "delivery_error": error,
                "smtp_provider": provider,
                "final_result": f"Email delivery uncertain: {error}",
                "last_error_category": "provider_uncertain",
                "manual_handover_required": True,
                "next_recommended_action": "Check provider outbox/logs before any replay; do not resend automatically.",
                "domain_result": {
                    "ok": False,
                    "error": error,
                    "uncertain": True,
                    "failure_observation": failure_observation,
                    "recovery_observation": failure_observation,
                },
            },
            details={"error": error, "uncertain": True, "recovery_observation": failure_observation},
        )
        if task:
            dlq = _create_mail_dlq_for_task(
                task,
                operation="send_email_smtp",
                error=error,
                attempt_count=int((payload.get("resilience") or {}).get("retry_count") or 0) if isinstance(payload.get("resilience"), dict) else 0,
                safe_replay_allowed=False,
                recovery_hint="manual_verify_provider_outbox_before_replay",
            )
            task = update_task(
                task_id,
                status="dead_letter",
                delivery_status="delivery_uncertain",
                domain_result={
                    **dict(task.get("domain_result") or {}),
                    "dlq_entry": dlq,
                },
            ) or task
            _publish_snapshot(task, "delivery_uncertain", "SMTP provider returned an uncertain delivery result.")
        return {"ok": False, "status": "delivery_uncertain", "error": error}

    if error_category == "provider_temporary" and self.request.retries < self.max_retries:
        task = set_task_status(
            task_id,
            "delivery_deferred",
            actor="worker",
            event_type="delivery_deferred",
            event_message="Temporary SMTP issue detected. Delivery deferred for retry.",
            extra_updates={
                "delivery_status": "delivery_deferred",
                "delivery_result": "",
                "delivery_error": error,
                "smtp_provider": provider,
                "final_result": f"Outbound delivery deferred: {error}",
                "last_error_category": error_category,
                "manual_handover_required": False,
                "next_recommended_action": "Worker will retry automatically. If retries are exhausted, switch to manual handover.",
            },
            details={"error": error},
        )
        if task:
            _publish_snapshot(task, "delivery_deferred", "Temporary SMTP issue detected. Delivery deferred for retry.")
        raise RuntimeError(error or "Temporary SMTP delivery error.")

    failure_observation = _with_task_communication_provenance(dict(payload.get("failure_observation") or make_failure_observation(
        service="smtp",
        operation="send_email_smtp",
        error=error,
        fallback_strategy="mark_send_failed_and_require_retry_or_manual_handover",
        retry_count=int((payload.get("resilience") or {}).get("retry_count") or 0) if isinstance(payload.get("resilience"), dict) else 0,
        actor_context=_actor_context_from_task(task),
    )), task)
    task = set_task_status(
        task_id,
        "send_failed",
        actor="worker",
        event_type="send_failed",
        event_message="Outbound email failed to send.",
        extra_updates={
            "delivery_status": "send_failed",
            "delivery_result": "",
            "delivery_error": error,
            "smtp_provider": provider,
            "final_result": f"Email send failed: {error}",
            "last_error_category": error_category,
            "manual_handover_required": error_category == "provider_temporary",
            "next_recommended_action": (
                "Retry send later or switch to manual handover."
                if error_category == "provider_temporary"
                else "Review recipient, SMTP settings, or content policy before retrying."
            ),
            "domain_result": {
                "ok": False,
                "error": error,
                "failure_observation": failure_observation,
                "recovery_observation": failure_observation,
            },
        },
        details={"error": error, "recovery_observation": failure_observation},
    )
    if task:
        dlq = _create_mail_dlq_for_task(
            task,
            operation="send_email_smtp",
            error=error,
            attempt_count=int((payload.get("resilience") or {}).get("retry_count") or 0) if isinstance(payload.get("resilience"), dict) else 0,
            safe_replay_allowed=error_category == "provider_temporary",
            recovery_hint=(
                "retry_send_with_same_idempotency_key"
                if error_category == "provider_temporary"
                else "manual_handover_or_repair_configuration"
            ),
        )
        task = update_task(
            task_id,
            status="dead_letter",
            delivery_status="dead_letter",
            domain_result={
                **dict(task.get("domain_result") or {}),
                "dlq_entry": dlq,
            },
        ) or task
        _publish_snapshot(task, "send_failed", "Outbound email failed to send.")
    return {"ok": False, "status": "send_failed", "error": error}


@celery_app.task(name="app.task_worker.sync_inbound_mail_task")
def sync_inbound_mail_task(actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return sync_inbound_mail(actor_context=actor_context)


@celery_app.task(name="app.task_worker.generate_daily_mail_digest_task")
def generate_daily_mail_digest_task(actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return generate_daily_mail_digest(actor_context=actor_context)


@celery_app.task(name="app.task_worker.enterprise_rag_ingest_task")
def enterprise_rag_ingest_task(payload: dict[str, Any]) -> dict[str, Any]:
    from app.enterprise_rag.ingestion.indexer import ingest_enterprise_rag_bench

    payload = dict(payload or {})
    return ingest_enterprise_rag_bench(
        mode=str(payload.get("mode") or "sample"),
        documents_path=payload.get("documents_path"),
        questions_path=payload.get("questions_path"),
        limit=int(payload.get("limit") or 200),
        reset=bool(payload.get("reset", False)),
        actor_context=dict(payload.get("actor_context") or {}),
    )


@celery_app.task(name="app.task_worker.enterprise_rag_benchmark_task")
def enterprise_rag_benchmark_task(payload: dict[str, Any]) -> dict[str, Any]:
    from app.enterprise_rag.eval.benchmark_runner import run_benchmark_sample

    payload = dict(payload or {})
    return run_benchmark_sample(
        questions_path=payload.get("questions_path"),
        limit=max(1, min(int(payload.get("limit") or 20), 100)),
        top_k=max(1, min(int(payload.get("top_k") or 8), 30)),
    )


@celery_app.task(bind=True, name="app.task_worker.enterprise_rag_query_task")
def enterprise_rag_query_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    from app.enterprise_rag.core.service import answer_enterprise_question, build_public_enterprise_query_payload

    payload = dict(payload or {})
    self.update_state(state="PROGRESS", meta={"stage": "retrieval_and_answer"})
    result = answer_enterprise_question(
        str(payload.get("question") or ""),
        source_types=list(payload.get("source_types") or []),
        top_k=max(1, min(int(payload.get("top_k") or 8), 30)),
        session_id=str(payload.get("session_id") or ""),
        conversation_id=str(payload.get("conversation_id") or ""),
        actor_context=dict(payload.get("actor_context") or {}),
        compose_answer=True,
        correlation_id=str(payload.get("correlation_id") or ""),
    )
    return build_public_enterprise_query_payload(result, include_debug_details=bool(payload.get("include_debug_details", False)))


@celery_app.task(name="app.task_worker.write_conversation_memory_summary_task")
def write_conversation_memory_summary_task(payload: dict[str, Any]) -> dict[str, Any]:
    from app.conversation_memory import write_turn_summary
    from app.metrics import record_turn_summary_write

    written = write_turn_summary(**dict(payload or {}))
    if written:
        record_turn_summary_write()
    return {"ok": bool(written), "written": bool(written)}


@celery_app.task(
    bind=True,
    base=GovernedTaskBase,
    name="app.task_worker.process_domain_meeting_task",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def process_domain_meeting_task(self, task_id: str) -> dict[str, Any]:
    task = get_dlp_task(task_id)
    if not task:
        return {"ok": False, "error": "Unknown task_id"}
    if str(task.get("task_type") or "") != "domain_meeting":
        return {"ok": True, "skipped": True, "status": task.get("status"), "reason": "not_domain_meeting"}
    if task["status"] == "completed":
        return {"ok": True, "skipped": True, "status": "completed"}
    if task["status"] not in {"queued", "failed"}:
        return {"ok": True, "skipped": True, "status": task["status"]}

    if self.request.retries:
        record_task_retry("meeting")

    action = str(task.get("domain_action") or "").strip()
    stored_payload = dict(task.get("domain_payload") or {})
    payload = _domain_tool_payload(stored_payload)
    if not action.startswith("meeting_"):
        task = set_task_status(
            task_id,
            "failed",
            actor="worker",
            event_type="domain_action_rejected",
            event_message="Meeting worker rejected a non-meeting domain action.",
            extra_updates={
                "domain_result": {"ok": False, "error": "invalid_meeting_action", "action": action},
                "delivery_status": "failed",
                "delivery_error": "invalid_meeting_action",
                "final_result": "Meeting worker rejected a non-meeting domain action.",
            },
            details={"action": action},
        )
        return {"ok": False, "status": "failed", "error": "invalid_meeting_action"}

    worker_actor_context = {
        "tenant_id": str(task.get("tenant_id") or ""),
        "user_id": str(task.get("user_id") or ""),
        "workspace_id": str(task.get("workspace_id") or ""),
        "session_id": str(task.get("session_id") or ""),
        "conversation_id": str(task.get("conversation_id") or ""),
        "roles": ["admin"],
    }
    context = OrchestrationContext(
        session_id=str(task.get("session_id") or ""),
        conversation_id=str(task.get("conversation_id") or ""),
        message=str(task.get("message_raw") or ""),
        safe_message=str(task.get("message_raw") or ""),
        display_message=str(task.get("request_message") or task.get("message_raw") or ""),
        actor_context=worker_actor_context,
    )
    meeting_context = _meeting_context_from_payload(stored_payload, payload, task, context)
    missing_meeting_context = _missing_meeting_escalation_context(action, meeting_context)
    if missing_meeting_context:
        error = "meeting_escalation_context_required"
        summary = "Meeting worker rejected a non-global meeting escalation without thread and brief context."
        task = set_task_status(
            task_id,
            "failed",
            actor="worker",
            event_type="meeting_context_rejected",
            event_message=summary,
            extra_updates={
                "domain_result": {
                    "ok": False,
                    "action": action,
                    "status": "blocked",
                    "error": error,
                    "missing_fields": missing_meeting_context,
                    "communication_role": "escalation_provider",
                    "communication_input_kind": "meeting_escalation_candidate",
                    "communication_closeout_owner": "mail_agent",
                    "result": {},
                    "post_confirm_results": [],
                    "brief_update": {},
                },
                "delivery_status": "failed",
                "delivery_error": error,
                "final_result": summary,
                "last_error_category": "invalid_parameters",
                "manual_handover_required": False,
                "next_recommended_action": "Select an active communication thread and brief, then request meeting escalation again.",
            },
            details={"action": action, "missing_fields": missing_meeting_context},
        )
        if task:
            _publish_snapshot(task, "meeting_context_rejected", summary)
        return {
            "ok": False,
            "status": "failed",
            "error": error,
            "missing_fields": missing_meeting_context,
        }

    task = set_task_status(
        task_id,
        "processing",
        actor="worker",
        event_type="meeting_processing_started",
        event_message="Worker started Tencent Meeting domain action.",
        extra_updates={"delivery_status": "processing"},
        details={"action": action},
    )
    if not task:
        return {"ok": False, "error": "Task vanished during meeting processing"}
    _publish_snapshot(task, "meeting_processing_started", "Worker started Tencent Meeting domain action.")

    dispatched = dispatch_tool_call(action, payload, context, {}, allow_side_effects=True)
    result = dict(dispatched.get("result") or {})
    if action.startswith("meeting_"):
        result.setdefault("communication_role", "escalation_provider")
        result.setdefault("communication_input_kind", "meeting_result")
        result.setdefault("communication_closeout_owner", "mail_agent")
        result["thread_id"] = str(meeting_context.get("thread_id") or "")
        result["source_brief_id"] = str(meeting_context.get("source_brief_id") or "")
        result["brief_id"] = str(meeting_context.get("brief_id") or meeting_context.get("source_brief_id") or "")
        result["idempotency_key"] = str(meeting_context.get("idempotency_key") or "")
        result["actor_context"] = dict(meeting_context.get("actor_context") or {})
    ok = bool(dispatched.get("ok"))
    post_confirm_results = _run_post_confirm_domain_steps(
        action=action,
        stored_payload=stored_payload,
        tool_payload=payload,
        tool_result=result,
        context=context,
    ) if ok else []
    brief_update = _record_meeting_result_brief_state(
        meeting_context=meeting_context,
        meeting_result=result,
        task_id=task_id,
    ) if ok else {}
    status = "completed" if ok else "failed"
    error = str(dispatched.get("error") or result.get("error") or "")
    summary = str(result.get("summary") or result.get("message") or error or f"{action} completed.")
    task = set_task_status(
        task_id,
        status,
        actor="worker",
        event_type="meeting_completed" if ok else "meeting_failed",
        event_message=summary,
        extra_updates={
            "domain_result": {
                "ok": ok,
                "action": action,
                "communication_role": "escalation_provider",
                "communication_input_kind": "meeting_result",
                "communication_closeout_owner": "mail_agent",
                "result": result,
                "error": error,
                "post_confirm_results": post_confirm_results,
                "brief_update": brief_update,
            },
            "delivery_status": status,
            "delivery_result": summary if ok else "",
            "delivery_error": "" if ok else error,
            "final_result": summary,
            "last_error_category": "" if ok else _classify_provider_error(error),
            "manual_handover_required": bool(error),
            "next_recommended_action": "" if ok else "Review Tencent Meeting provider response and retry or create the meeting manually.",
        },
        details={"action": action, "ok": ok, "error": error},
    )
    if task:
        _publish_snapshot(task, "meeting_completed" if ok else "meeting_failed", summary)
    return {"ok": ok, "status": status, "error": error, "result": result, "post_confirm_results": post_confirm_results}


def _domain_tool_payload(stored_payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(stored_payload.get("tool_input"), dict):
        return dict(stored_payload.get("tool_input") or {})
    return {str(key): value for key, value in stored_payload.items() if not str(key).startswith("_")}


def _meeting_context_from_payload(
    stored_payload: dict[str, Any],
    tool_payload: dict[str, Any],
    task: dict[str, Any],
    context: OrchestrationContext,
) -> dict[str, Any]:
    server_global_authorized = _server_global_authorized_from_stored_payload(stored_payload)
    tool_actor = tool_payload.get("actor_context") if isinstance(tool_payload.get("actor_context"), dict) else {}
    stored_actor = stored_payload.get("actor_context") if isinstance(stored_payload.get("actor_context"), dict) else {}
    actor_context_source = ""
    if tool_actor:
        actor_context = dict(tool_actor)
        actor_context_source = "tool_payload"
    elif stored_actor:
        actor_context = dict(stored_actor)
        actor_context_source = "stored_payload"
    else:
        actor_context = dict(context.actor_context or {})
    if not actor_context:
        actor_context = _actor_context_from_task(task)
    source_brief_id = str(
        tool_payload.get("source_brief_id")
        or tool_payload.get("brief_id")
        or stored_payload.get("source_brief_id")
        or stored_payload.get("brief_id")
        or ""
    ).strip()
    thread_id = str(tool_payload.get("thread_id") or stored_payload.get("thread_id") or "").strip()
    return {
        "thread_id": thread_id,
        "source_brief_id": source_brief_id,
        "brief_id": source_brief_id,
        "idempotency_key": str(tool_payload.get("idempotency_key") or stored_payload.get("idempotency_key") or task.get("idempotency_key") or "").strip(),
        "actor_context": actor_context,
        "actor_context_source": actor_context_source,
        "server_global_authorized": server_global_authorized,
    }


def _server_global_authorized_from_stored_payload(stored_payload: dict[str, Any]) -> bool:
    return (
        bool(stored_payload.get("_server_global_authorized"))
        and str(stored_payload.get("_server_global_authorized_by") or "") == "agent_chat_context"
    )


def _missing_meeting_escalation_context(action: str, meeting_context: dict[str, Any]) -> list[str]:
    if action != "meeting_create_tencent_meeting":
        return []
    server_global_authorized = bool(meeting_context.get("server_global_authorized"))
    missing: list[str] = []
    if not server_global_authorized and not str(meeting_context.get("thread_id") or "").strip():
        missing.append("thread_id")
    if not server_global_authorized and not str(meeting_context.get("source_brief_id") or "").strip():
        missing.append("source_brief_id")
    if not str(meeting_context.get("idempotency_key") or "").strip():
        missing.append("idempotency_key")
    actor_context = dict(meeting_context.get("actor_context") or {})
    actor_context_provided = bool(str(meeting_context.get("actor_context_source") or "").strip())
    if not actor_context_provided or not all(str(actor_context.get(key) or "").strip() for key in ("tenant_id", "user_id", "workspace_id")):
        missing.append("actor_context")
    return missing


def _record_meeting_result_brief_state(
    *,
    meeting_context: dict[str, Any],
    meeting_result: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    source_brief_id = str(meeting_context.get("source_brief_id") or "").strip()
    thread_id = str(meeting_context.get("thread_id") or "").strip()
    if not source_brief_id or not thread_id:
        return {}
    try:
        from app.communication.brief_service import record_meeting_result_on_brief

        stored = record_meeting_result_on_brief(
            source_brief_id=source_brief_id,
            thread_id=thread_id,
            meeting_result=meeting_result,
            task_id=task_id,
            actor_context=dict(meeting_context.get("actor_context") or {}),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "thread_id": thread_id, "source_brief_id": source_brief_id}
    brief_payload = dict((stored or {}).get("brief") or {})
    return {
        "ok": bool(stored),
        "thread_id": str((stored or {}).get("thread_id") or thread_id),
        "brief_id": str(brief_payload.get("brief_id") or source_brief_id),
        "version": int((stored or {}).get("version") or 0),
        "refresh_reason": str((stored or {}).get("refresh_reason") or ""),
        "recommended_next_action": str(brief_payload.get("recommended_next_action") or ""),
    }


def _run_post_confirm_domain_steps(
    *,
    action: str,
    stored_payload: dict[str, Any],
    tool_payload: dict[str, Any],
    tool_result: dict[str, Any],
    context: OrchestrationContext,
) -> list[dict[str, Any]]:
    dag_plan = dict(stored_payload.get("_dag_plan") or {})
    subtasks = list(dag_plan.get("subtasks") or [])
    if not subtasks:
        return []
    idempotency_key = str(tool_payload.get("idempotency_key") or stored_payload.get("idempotency_key") or "")
    confirmed_task_id = ""
    for item in subtasks:
        if not isinstance(item, dict):
            continue
        parameters = dict(item.get("parameters") or item.get("input") or {})
        if str(item.get("action") or item.get("capability") or "") != action:
            continue
        if idempotency_key and str(parameters.get("idempotency_key") or item.get("idempotency_key") or "") != idempotency_key:
            continue
        confirmed_task_id = str(item.get("task_id") or "")
        break
    if not confirmed_task_id:
        return []

    dependency_payloads = {
        confirmed_task_id: {
            **tool_result,
            "confirmed_action": action,
            "idempotency_key": idempotency_key,
        }
    }
    outputs: list[dict[str, Any]] = []
    for item in subtasks:
        if not isinstance(item, dict):
            continue
        dependencies = [str(dep) for dep in list(item.get("dependencies") or [])]
        post_action = str(item.get("action") or item.get("capability") or "")
        if confirmed_task_id not in dependencies or post_action == action:
            continue
        dispatched = dispatch_tool_call(
            post_action,
            dict(item.get("parameters") or item.get("input") or {}),
            context,
            dependency_payloads,
            allow_side_effects=False,
        )
        outputs.append(
            {
                "task_id": str(item.get("task_id") or ""),
                "action": post_action,
                "ok": bool(dispatched.get("ok")),
                "result": dict(dispatched.get("result") or {}),
                "error": str(dispatched.get("error") or ""),
                "observation_type": str(dispatched.get("observation_type") or ""),
            }
        )
    return outputs
