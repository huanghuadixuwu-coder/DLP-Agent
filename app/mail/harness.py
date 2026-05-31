from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from app.mail.domain import MailDraft, MailMessage, MailObservation, MailOperationTask, new_mail_id
from app.mail.fixtures import build_fixture_messages
from app.mail.provider import MailProvider, MailProviderResponse


TERMINAL_DRAFT_STATUSES = {"sent", "cancelled", "rejected"}
REVIEWABLE_DRAFT_STATUSES = {"sender_review_required", "governance_review_required", "blocked"}


@dataclass(slots=True)
class MailHarnessCounters:
    state_transitions_checked: list[str] = field(default_factory=list)
    idempotency_checks: int = 0
    provider_failures_checked: int = 0
    scope_denials: int = 0
    patch_conflicts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_transitions_checked": list(self.state_transitions_checked),
            "idempotency_checks": self.idempotency_checks,
            "provider_failures_checked": self.provider_failures_checked,
            "scope_denials": self.scope_denials,
            "patch_conflicts": self.patch_conflicts,
        }


class MailHarness:
    """Deterministic Mail Agent state harness.

    The harness intentionally keeps state in memory. M2 replaces this store with
    persistent draft state while preserving the same transitions and observations.
    """

    def __init__(self, provider: MailProvider, *, cached_messages: list[MailMessage] | None = None) -> None:
        self.provider = provider
        self._drafts: dict[str, MailDraft] = {}
        self._operations: dict[str, MailOperationTask] = {}
        self._operation_by_key: dict[str, str] = {}
        self._audit: list[dict[str, Any]] = []
        self._cache = {item.message_id: deepcopy(item) for item in (cached_messages or build_fixture_messages())}
        self._lock = RLock()
        self.counters = MailHarnessCounters()

    @staticmethod
    def _actor_scope(actor_context: dict[str, Any] | None) -> tuple[str, str, str]:
        actor = actor_context or {}
        return (
            str(actor.get("tenant_id") or ""),
            str(actor.get("workspace_id") or ""),
            str(actor.get("user_id") or ""),
        )

    def _scope_allowed(self, draft: MailDraft, actor_context: dict[str, Any] | None) -> bool:
        return self._actor_scope(draft.actor_context) == self._actor_scope(actor_context)

    def _audit_transition(self, draft: MailDraft, previous: str, current: str, *, reason: str) -> None:
        if previous == current:
            return
        transition = f"{previous}->{current}"
        self.counters.state_transitions_checked.append(transition)
        self._audit.append(
            {
                "draft_id": draft.draft_id,
                "transition": transition,
                "reason": reason,
                "version": draft.version,
                "actor_context": deepcopy(draft.actor_context),
            }
        )

    def _observe(
        self,
        *,
        observation_type: str,
        status: str,
        summary: str,
        actor_context: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        message_refs: list[str] | None = None,
        thread_refs: list[str] | None = None,
        draft_ref: str = "",
        task_ref: str = "",
        risk: str = "",
        missing_fields: list[str] | None = None,
        constraints: list[dict[str, Any]] | None = None,
        side_effects: list[dict[str, Any]] | None = None,
        next_actions: list[str] | None = None,
        debug: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return MailObservation(
            observation_type=observation_type,
            status=status,
            source_tool="mail_harness",
            summary=summary,
            provenance={"source": "mail_harness", "provider": self.provider.provider_name},
            confidence=1.0,
            message_refs=list(message_refs or []),
            thread_refs=list(thread_refs or []),
            draft_ref=draft_ref,
            task_ref=task_ref,
            risk=risk,
            missing_fields=list(missing_fields or []),
            constraints=list(constraints or []),
            side_effects=list(side_effects or []),
            next_actions=list(next_actions or []),
            actor_context=deepcopy(actor_context or {}),
            debug=deepcopy(debug or {}),
            payload=deepcopy(payload or {}),
        ).to_dict()

    def _scope_denied(self, *, draft_id: str, actor_context: dict[str, Any] | None) -> dict[str, Any]:
        self.counters.scope_denials += 1
        return self._observe(
            observation_type="permission_denied",
            status="permission_denied",
            summary="Draft scope does not match the requesting actor.",
            actor_context=actor_context,
            draft_ref=draft_id,
            constraints=[{"kind": "actor_scope", "allowed": False}],
            next_actions=["use_owned_draft"],
        )

    @staticmethod
    def _provider_failure(response: MailProviderResponse) -> dict[str, Any]:
        return {
            "observation_type": "dependency_failure",
            "status": response.status,
            "source": response.provider,
            "summary": response.error_message,
            "payload": {
                "service": response.provider,
                "operation": response.operation,
                "error_code": response.error_code,
                "fallback_strategy": "use_local_cache_when_available",
                "retryable": response.retryable,
                "uncertain": response.uncertain,
            },
            "success": False,
        }

    def search_messages(self, *, query: str = "", limit: int = 20, actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.provider.search_messages(query=query, limit=limit, actor_context=actor_context)
        if response.ok:
            messages = list(response.data.get("messages") or [])
            return self._observe(
                observation_type="mail_search",
                status="completed",
                summary=f"Found {len(messages)} provider-backed mail messages.",
                actor_context=actor_context,
                payload={"messages": messages, "provider_status": response.status},
                message_refs=[str(item.get("message_id") or "") for item in messages],
            )
        self.counters.provider_failures_checked += 1
        normalized = str(query or "").strip().lower()
        cached = list(self._cache.values())
        if normalized:
            cached = [
                item
                for item in cached
                if normalized in " ".join([item.sender, item.subject, item.body_text, item.snippet]).lower()
            ]
        messages = [item.to_dict() for item in cached[: max(1, int(limit))]]
        return self._observe(
            observation_type="mail_search",
            status="degraded",
            summary=f"Provider search failed; returned {len(messages)} locally cached messages.",
            actor_context=actor_context,
            payload={
                "messages": messages,
                "provider_status": response.status,
                "fallback_strategy": "local_cache",
                "recovery_observation": self._provider_failure(response),
            },
            message_refs=[str(item.get("message_id") or "") for item in messages],
            constraints=[{"kind": "provider_degraded", "cache_only": True}],
            next_actions=["retry_provider_later"],
        )

    def read_message(self, message_id: str, *, actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.provider.read_message(message_id, actor_context=actor_context)
        if response.ok:
            message = dict(response.data.get("message") or {})
            return self._observe(
                observation_type="mail_message",
                status="completed",
                summary="Loaded provider-backed mail message.",
                actor_context=actor_context,
                payload={"message": message, "provider_status": response.status},
                message_refs=[message_id],
            )
        self.counters.provider_failures_checked += 1
        cached = self._cache.get(message_id)
        if cached:
            return self._observe(
                observation_type="mail_message",
                status="degraded",
                summary="Provider read failed; returned locally cached mail message.",
                actor_context=actor_context,
                payload={
                    "message": cached.to_dict(),
                    "provider_status": response.status,
                    "fallback_strategy": "local_cache",
                    "recovery_observation": self._provider_failure(response),
                },
                message_refs=[message_id],
                constraints=[{"kind": "provider_degraded", "cache_only": True}],
            )
        return self._observe(
            observation_type="mail_message",
            status=response.status,
            summary=response.error_message,
            actor_context=actor_context,
            payload={"provider_status": response.status, "recovery_observation": self._provider_failure(response)},
            message_refs=[message_id],
        )

    def read_thread(self, thread_id: str, *, actor_context: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.provider.read_thread(thread_id, actor_context=actor_context)
        if not response.ok:
            self.counters.provider_failures_checked += 1
            return self._observe(
                observation_type="mail_thread",
                status=response.status,
                summary=response.error_message,
                actor_context=actor_context,
                payload={"recovery_observation": self._provider_failure(response)},
                thread_refs=[thread_id],
            )
        thread = dict(response.data.get("thread") or {})
        messages = list(response.data.get("messages") or [])
        return self._observe(
            observation_type="mail_thread",
            status="completed",
            summary=f"Loaded mail thread with {len(messages)} messages.",
            actor_context=actor_context,
            payload={"thread": thread, "messages": messages},
            message_refs=[str(item.get("message_id") or "") for item in messages],
            thread_refs=[thread_id],
        )

    def build_send_draft(
        self,
        *,
        actor_context: dict[str, Any],
        conversation_id: str,
        to: list[str] | None = None,
        subject: str = "",
        body_text: str = "",
        draft_kind: str = "new_message",
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        draft = MailDraft(
            conversation_id=conversation_id,
            draft_kind=draft_kind,
            to=list(to or []),
            subject=subject,
            body_text=body_text,
            source_refs=list(source_refs or []),
            source_policy={
                "attachment_source": "attachment_only",
                "body_source": "user_explicit_only",
                "allow_attachment_body_only_if_explicit": True,
            },
            body_sources=[{"kind": "body_source", "role": "explicit_body", "policy": "user_explicit_only"}] if body_text else [],
            actor_context=deepcopy(actor_context),
        )
        with self._lock:
            self._drafts[draft.draft_id] = draft
            self._audit_transition(draft, "draft_building", draft.status, reason="build_send_draft")
        return self._observe(
            observation_type="mail_draft",
            status=draft.status,
            summary="Mail draft state created.",
            actor_context=actor_context,
            payload={"draft": draft.to_dict()},
            draft_ref=draft.draft_id,
            missing_fields=draft.missing_fields,
            next_actions=["patch_draft"] if draft.missing_fields else ["request_send_confirmation"],
        )

    def get_draft_snapshot(self, draft_id: str, *, actor_context: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if not draft:
                return self._observe(
                    observation_type="mail_draft",
                    status="not_found",
                    summary="Draft not found.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            if not self._scope_allowed(draft, actor_context):
                return self._scope_denied(draft_id=draft_id, actor_context=actor_context)
            return self._observe(
                observation_type="mail_draft",
                status=draft.status,
                summary="Mail draft state loaded.",
                actor_context=actor_context,
                payload={"draft": draft.to_dict()},
                draft_ref=draft_id,
                missing_fields=draft.missing_fields,
            )

    def patch_draft(
        self,
        draft_id: str,
        *,
        actor_context: dict[str, Any],
        patch: dict[str, Any],
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if not draft:
                return self._observe(
                    observation_type="mail_draft_patch",
                    status="not_found",
                    summary="Draft not found.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            if not self._scope_allowed(draft, actor_context):
                return self._scope_denied(draft_id=draft_id, actor_context=actor_context)
            if expected_version is not None and int(expected_version) != draft.version:
                self.counters.patch_conflicts += 1
                return self._observe(
                    observation_type="mail_draft_patch",
                    status="conflict",
                    summary="Draft patch rejected because the expected version is stale.",
                    actor_context=actor_context,
                    payload={"draft": draft.to_dict(), "expected_version": expected_version, "actual_version": draft.version},
                    draft_ref=draft_id,
                    next_actions=["reload_draft", "retry_patch"],
                )
            if draft.status in TERMINAL_DRAFT_STATUSES:
                return self._observe(
                    observation_type="mail_draft_patch",
                    status="blocked",
                    summary="Terminal draft cannot be patched.",
                    actor_context=actor_context,
                    payload={"draft": draft.to_dict()},
                    draft_ref=draft_id,
                )
            previous = draft.status
            for key in ("to", "cc", "bcc", "subject", "body_text", "body_html", "body_format"):
                if key in patch:
                    setattr(draft, key, deepcopy(patch[key]))
            draft.last_patch_request = deepcopy(patch)
            draft.status = "draft_building"
            draft.refresh_missing_fields()
            draft.touch()
            self._audit_transition(draft, previous, draft.status, reason="patch_draft")
            return self._observe(
                observation_type="mail_draft_patch",
                status=draft.status,
                summary="Mail draft patch applied.",
                actor_context=actor_context,
                payload={"draft": draft.to_dict()},
                draft_ref=draft_id,
                missing_fields=draft.missing_fields,
                next_actions=["patch_draft"] if draft.missing_fields else ["request_send_confirmation"],
            )

    def request_send_confirmation(self, draft_id: str, *, actor_context: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if not draft:
                return self._observe(
                    observation_type="mail_confirmation_required",
                    status="not_found",
                    summary="Draft not found.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            if not self._scope_allowed(draft, actor_context):
                return self._scope_denied(draft_id=draft_id, actor_context=actor_context)
            draft.refresh_missing_fields()
            if draft.missing_fields:
                return self._observe(
                    observation_type="mail_confirmation_required",
                    status="needs_clarification",
                    summary="Draft is incomplete and cannot enter confirmation.",
                    actor_context=actor_context,
                    payload={"draft": draft.to_dict()},
                    draft_ref=draft_id,
                    missing_fields=draft.missing_fields,
                    next_actions=["patch_draft"],
                )
            previous = draft.status
            draft.status = "pending_confirmation"
            draft.touch()
            self._audit_transition(draft, previous, draft.status, reason="request_send_confirmation")
            return self._observe(
                observation_type="mail_confirmation_required",
                status=draft.status,
                summary="Explicit sender confirmation is required before DLP submission.",
                actor_context=actor_context,
                payload={"draft": draft.to_dict()},
                draft_ref=draft_id,
                side_effects=[{"kind": "external_send", "allowed": False, "confirmation_required": True}],
                next_actions=["confirm_send", "patch_draft", "cancel"],
            )

    @staticmethod
    def _risk_level(value: str) -> str:
        normalized = str(value or "low").strip().lower()
        return normalized if normalized in {"low", "medium", "high", "critical"} else "critical"

    @staticmethod
    def _idempotency_key(draft: MailDraft) -> str:
        actor = draft.actor_context
        binding = {
            "tenant_id": actor.get("tenant_id"),
            "workspace_id": actor.get("workspace_id"),
            "user_id": actor.get("user_id"),
            "draft_id": draft.draft_id,
            "operation": "mail_send",
            "to": sorted(draft.to),
            "cc": sorted(draft.cc),
            "bcc": sorted(draft.bcc),
            "subject": draft.subject,
            "body_text": draft.body_text,
        }
        digest = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return f"mail-send:{digest}"

    def submit_dlp_result(
        self,
        draft_id: str,
        *,
        actor_context: dict[str, Any],
        risk: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if not draft:
                return self._observe(
                    observation_type="dlp_task_state",
                    status="not_found",
                    summary="Draft not found.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            if not self._scope_allowed(draft, actor_context):
                return self._scope_denied(draft_id=draft_id, actor_context=actor_context)
            if not confirmed:
                return self._observe(
                    observation_type="dlp_task_state",
                    status="blocked",
                    summary="Unconfirmed draft cannot enter the DLP queue.",
                    actor_context=actor_context,
                    payload={"draft": draft.to_dict()},
                    draft_ref=draft_id,
                    side_effects=[{"kind": "external_send", "allowed": False, "confirmation_required": True}],
                )
            if draft.status == "sent":
                self.counters.idempotency_checks += 1
                return self._observe(
                    observation_type="mail_send_state",
                    status="sent",
                    summary="Duplicate confirmation reused the completed send result.",
                    actor_context=actor_context,
                    payload={"draft": draft.to_dict(), "deduplicated": True},
                    draft_ref=draft_id,
                )
            if draft.status != "pending_confirmation":
                return self._observe(
                    observation_type="dlp_task_state",
                    status="blocked",
                    summary="Draft is not waiting for sender confirmation.",
                    actor_context=actor_context,
                    payload={"draft": draft.to_dict()},
                    draft_ref=draft_id,
                )
            normalized_risk = self._risk_level(risk)
            previous = draft.status
            target = {
                "low": "queued_send",
                "medium": "sender_review_required",
                "high": "governance_review_required",
                "critical": "blocked",
            }[normalized_risk]
            draft.status = target
            draft.touch()
            self._audit_transition(draft, previous, target, reason=f"dlp_risk:{normalized_risk}")
            if normalized_risk == "low":
                return self._deliver(draft, actor_context=actor_context)
            return self._observe(
                observation_type="dlp_task_state",
                status=target,
                summary="DLP risk routing completed.",
                actor_context=actor_context,
                payload={"draft": draft.to_dict()},
                draft_ref=draft_id,
                risk=normalized_risk,
                side_effects=[{"kind": "external_send", "allowed": False, "risk": normalized_risk}],
                next_actions={
                    "medium": ["sender_confirm_again", "patch_draft", "cancel"],
                    "high": ["governance_exception_approve", "governance_reject", "patch_draft", "cancel"],
                    "critical": ["patch_draft", "cancel"],
                }[normalized_risk],
            )

    def sender_review(self, draft_id: str, *, actor_context: dict[str, Any], action: str) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if not draft:
                return self._observe(
                    observation_type="sender_safety_review",
                    status="not_found",
                    summary="Draft not found.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            if not self._scope_allowed(draft, actor_context):
                return self._scope_denied(draft_id=draft_id, actor_context=actor_context)
            if draft.status != "sender_review_required":
                return self._observe(
                    observation_type="sender_safety_review",
                    status="blocked",
                    summary="Draft is not waiting for sender safety review.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            normalized = str(action or "").strip().lower()
            previous = draft.status
            if normalized == "confirm":
                draft.status = "queued_send"
                draft.touch()
                self._audit_transition(draft, previous, draft.status, reason="sender_review_confirm")
                return self._deliver(draft, actor_context=actor_context)
            if normalized == "cancel":
                draft.status = "cancelled"
            elif normalized == "revise":
                draft.status = "draft_ready"
            else:
                return self._observe(
                    observation_type="sender_safety_review",
                    status="blocked",
                    summary="Unknown sender safety review action.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            draft.touch()
            self._audit_transition(draft, previous, draft.status, reason=f"sender_review_{normalized}")
            return self._observe(
                observation_type="sender_safety_review",
                status=draft.status,
                summary="Sender safety review state updated.",
                actor_context=actor_context,
                payload={"draft": draft.to_dict()},
                draft_ref=draft_id,
            )

    def governance_review(self, draft_id: str, *, actor_context: dict[str, Any], action: str) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if not draft:
                return self._observe(
                    observation_type="governance_review",
                    status="not_found",
                    summary="Draft not found.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            roles = {str(item).lower() for item in list((actor_context or {}).get("roles") or [])}
            same_workspace = self._actor_scope(draft.actor_context)[:2] == self._actor_scope(actor_context)[:2]
            if not same_workspace or not roles.intersection({"approver", "admin"}):
                return self._scope_denied(draft_id=draft_id, actor_context=actor_context)
            if draft.status != "governance_review_required":
                return self._observe(
                    observation_type="governance_review",
                    status="blocked",
                    summary="Draft is not waiting for governance review.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            normalized = str(action or "").strip().lower()
            previous = draft.status
            if normalized == "approve":
                draft.status = "queued_send"
                draft.touch()
                self._audit_transition(draft, previous, draft.status, reason="governance_exception_approve")
                return self._deliver(draft, actor_context=draft.actor_context)
            if normalized == "reject":
                draft.status = "rejected"
                draft.touch()
                self._audit_transition(draft, previous, draft.status, reason="governance_reject")
                return self._observe(
                    observation_type="governance_review",
                    status=draft.status,
                    summary="Governance review state updated.",
                    actor_context=actor_context,
                    payload={"draft": draft.to_dict()},
                    draft_ref=draft_id,
                )
            return self._observe(
                observation_type="governance_review",
                status="blocked",
                summary="Unknown governance review action.",
                actor_context=actor_context,
                draft_ref=draft_id,
            )

    def retry_delivery(self, draft_id: str, *, actor_context: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if not draft:
                return self._observe(
                    observation_type="mail_send_state",
                    status="not_found",
                    summary="Draft not found.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            if not self._scope_allowed(draft, actor_context):
                return self._scope_denied(draft_id=draft_id, actor_context=actor_context)
            if draft.status not in {"delivery_deferred", "queued_send", "sent"}:
                return self._observe(
                    observation_type="mail_send_state",
                    status="blocked",
                    summary="Draft is not at a replay-safe send checkpoint.",
                    actor_context=actor_context,
                    draft_ref=draft_id,
                )
            return self._deliver(draft, actor_context=actor_context)

    def _deliver(self, draft: MailDraft, *, actor_context: dict[str, Any]) -> dict[str, Any]:
        idempotency_key = draft.idempotency_key or self._idempotency_key(draft)
        draft.idempotency_key = idempotency_key
        operation_id = self._operation_by_key.get(idempotency_key)
        operation = self._operations.get(operation_id or "")
        if operation and operation.status == "sent":
            self.counters.idempotency_checks += 1
            return self._observe(
                observation_type="mail_send_state",
                status="sent",
                summary="Existing provider send result reused.",
                actor_context=actor_context,
                payload={"draft": draft.to_dict(), "operation": operation.to_dict(), "deduplicated": True},
                draft_ref=draft.draft_id,
                task_ref=operation.operation_id,
            )
        if not operation:
            operation = MailOperationTask(
                operation_id=new_mail_id("mailop"),
                task_id=new_mail_id("task"),
                task_type="mail_send",
                draft_id=draft.draft_id,
                status="sending",
                idempotency_key=idempotency_key,
                actor_context=deepcopy(draft.actor_context),
            )
            self._operations[operation.operation_id] = operation
            self._operation_by_key[idempotency_key] = operation.operation_id
        operation.attempt_count += 1
        operation.status = "sending"
        operation.touch()
        previous = draft.status
        draft.status = "sending"
        draft.touch()
        self._audit_transition(draft, previous, draft.status, reason="provider_send_start")
        response = self.provider.send_message(draft, idempotency_key=idempotency_key, actor_context=draft.actor_context)
        if response.ok:
            operation.status = "sent"
            operation.provider_operation_id = str(response.data.get("provider_operation_id") or "")
            operation.last_error = ""
            operation.recovery_hint = ""
            operation.touch()
            previous = draft.status
            draft.status = "sent"
            draft.touch()
            self._audit_transition(draft, previous, draft.status, reason="provider_send_completed")
            if response.status == "deduplicated":
                self.counters.idempotency_checks += 1
            return self._observe(
                observation_type="mail_send_state",
                status="sent",
                summary="Provider send completed.",
                actor_context=actor_context,
                payload={
                    "draft": draft.to_dict(),
                    "operation": operation.to_dict(),
                    "provider_result": response.to_dict(),
                    "deduplicated": response.status == "deduplicated",
                },
                draft_ref=draft.draft_id,
                task_ref=operation.operation_id,
                side_effects=[{"kind": "external_send", "allowed": True, "completed": True}],
            )
        self.counters.provider_failures_checked += 1
        deferred = response.retryable or response.uncertain
        operation.status = "delivery_deferred" if deferred else "failed"
        operation.last_error = response.error_message
        operation.recovery_hint = "retry_delivery" if deferred else "repair_provider_configuration"
        operation.touch()
        previous = draft.status
        draft.status = operation.status
        draft.touch()
        self._audit_transition(draft, previous, draft.status, reason=f"provider_send_{response.error_code}")
        return self._observe(
            observation_type="mail_send_state",
            status=draft.status,
            summary="Provider send did not complete successfully.",
            actor_context=actor_context,
            payload={
                "draft": draft.to_dict(),
                "operation": operation.to_dict(),
                "provider_result": response.to_dict(),
                "recovery_observation": self._provider_failure(response),
            },
            draft_ref=draft.draft_id,
            task_ref=operation.operation_id,
            side_effects=[{"kind": "external_send", "allowed": False, "uncertain": response.uncertain}],
            next_actions=["retry_delivery"] if deferred else ["repair_provider_configuration"],
        )

    def queue_health(self) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        for operation in self._operations.values():
            statuses[operation.status] = statuses.get(operation.status, 0) + 1
        return {
            "provider": self.provider.provider_name,
            "operations": len(self._operations),
            "statuses": statuses,
            "provider_health": self.provider.get_health().to_dict(),
        }

    def prometheus_snapshot(self) -> dict[str, int]:
        return {
            "mail_harness_state_transitions_total": len(self.counters.state_transitions_checked),
            "mail_harness_idempotency_checks_total": self.counters.idempotency_checks,
            "mail_harness_provider_failures_total": self.counters.provider_failures_checked,
            "mail_harness_scope_denials_total": self.counters.scope_denials,
            "mail_harness_patch_conflicts_total": self.counters.patch_conflicts,
        }

    def report(self, *, ok: bool, cases_run: list[str], cases_failed: list[str] | None = None) -> dict[str, Any]:
        return {
            "ok": ok,
            "cases_run": list(cases_run),
            "cases_failed": list(cases_failed or []),
            "state_transitions_checked": list(self.counters.state_transitions_checked),
            "idempotency_checks": self.counters.idempotency_checks,
            "provider_failures_checked": self.counters.provider_failures_checked,
            "queue_health": self.queue_health(),
            "prometheus_snapshot": self.prometheus_snapshot(),
        }
