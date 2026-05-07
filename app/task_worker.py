from __future__ import annotations

import re
import time
from typing import Any

from celery import Task

from app.dlp_runtime import max_risk_level, model_summary_and_risk, retrieve_dlp_evidence
from app.dlp_scenarios import normalize_fault_injection
from app.inbound_mail import generate_daily_mail_digest, sync_inbound_mail
from app.mcp_client import call_mcp_tool
from app.metrics import record_fault_injection, record_task_degradation, record_task_retry
from app.privacy_lab import scan_sensitive_message
from app.task_events import build_task_event, publish_task_event
from app.task_queue import celery_app, enqueue_email_send_task
from app.task_store import add_task_event, get_dlp_task, set_task_status, update_task
from app.upload_blob_store import load_upload_blob


TEMPORARY_PROVIDER_TOKENS = ("timeout", "timed out", "tempor", "refused", "unavailable", "reset", "limit", "quota", "429")
PERMANENT_PROVIDER_TOKENS = ("auth", "credential", "password", "invalid recipient", "mailbox unavailable", "550", "553", "format")


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
    approval_required = final_risk_level in {"medium", "high", "critical"}
    final_result = (
        "Sensitive outbound content detected. Waiting for human approval."
        if approval_required
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

    if bool(task.get("approval_required", False)):
        task = set_task_status(
            task_id,
            "pending_approval",
            actor="worker",
            event_type="pending_approval",
            event_message="Sensitive outbound content detected. Waiting for human approval.",
            extra_updates={"delivery_status": "pending_approval"},
            details={"risk_reasons": task.get("risk_reasons", [])},
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
    enqueue_email_send_task(task_id)
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
            },
            details={"error": error, "injected": True},
        )
        if task:
            _publish_snapshot(task, "send_failed", "Outbound email failed to send.")
        return {"ok": False, "status": "send_failed", "error": error}

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
            },
            details={"error": error},
        )
        if task:
            _publish_snapshot(task, "send_failed", "Outbound email failed to send.")
        return {"ok": False, "status": "send_failed", "error": error}

    payload = tool_result.get("result") if tool_result.get("ok") else {"ok": False, "error": tool_result.get("error", "")}
    success = bool(payload.get("ok"))
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

    if success:
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
        },
        details={"error": error},
    )
    if task:
        _publish_snapshot(task, "send_failed", "Outbound email failed to send.")
    return {"ok": False, "status": "send_failed", "error": error}


@celery_app.task(name="app.task_worker.sync_inbound_mail_task")
def sync_inbound_mail_task() -> dict[str, Any]:
    return sync_inbound_mail()


@celery_app.task(name="app.task_worker.generate_daily_mail_digest_task")
def generate_daily_mail_digest_task() -> dict[str, Any]:
    return generate_daily_mail_digest()
