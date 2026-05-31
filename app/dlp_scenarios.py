from __future__ import annotations

from copy import deepcopy
from typing import Any


TERMINAL_EVALUATION_STATUSES = {
    "pending_approval",
    "needs_clarification",
    "input_invalid",
    "sent",
    "send_failed",
    "rejected",
    "failed",
}

EVENT_STATUS_MAP = {
    "queued": "queued",
    "processing_started": "processing",
    "needs_clarification": "needs_clarification",
    "input_invalid": "input_invalid",
    "pending_approval": "pending_approval",
    "approved": "approved",
    "queued_for_send": "queued_for_send",
    "delivery_deferred": "delivery_deferred",
    "sending": "sending",
    "sent": "sent",
    "send_failed": "send_failed",
    "rejected": "rejected",
    "failed": "failed",
}


SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario_id": "public_meeting_low_risk_auto_send",
        "name": "Public Meeting Notes Auto Send",
        "category": "baseline",
        "description": "Public, low-risk text should pass risk review and send automatically.",
        "message": "Please summarize these public meeting notes and send them to the partner email.",
        "uploaded_filename": "public_meeting_notes.txt",
        "uploaded_content_type": "text/plain",
        "uploaded_text": (
            "Weekly public sync notes: roadmap check-in, delivery timeline remains unchanged, "
            "no customer secrets or credentials were discussed."
        ),
        "destination_email": "partner@example.com",
        "requested_action": "summarize_and_send",
        "fault_injection": {
            "force_smtp_fail": False,
            "force_model_timeout": False,
            "force_retrieval_empty": False,
            "force_rule_only_mode": False,
            "force_queue_delay_seconds": 0,
        },
        "expected_outcome": {
            "risk_level": "low",
            "approval_required": False,
            "expected_status_path": ["queued", "processing", "queued_for_send", "sending", "sent"],
            "terminal_status": "sent",
            "delivery_status": "sent",
        },
    },
    {
        "scenario_id": "customer_contacts_pending_approval",
        "name": "Customer Contacts Pending Approval",
        "category": "policy",
        "description": "Customer contact information should be redacted and held for human approval.",
        "message": "Please summarize the following customer incident update and send it externally.",
        "uploaded_filename": "customer_incident.log",
        "uploaded_content_type": "text/plain",
        "uploaded_text": (
            "Customer Zhang San requested an outage update. Phone 13800000000. "
            "Email zhangsan@example.com. Waiting for external communication."
        ),
        "destination_email": "vendor@example.com",
        "requested_action": "summarize_and_send",
        "fault_injection": {
            "force_smtp_fail": False,
            "force_model_timeout": False,
            "force_retrieval_empty": False,
            "force_rule_only_mode": False,
            "force_queue_delay_seconds": 0,
        },
        "expected_outcome": {
            "risk_level": "medium",
            "approval_required": True,
            "expected_status_path": ["queued", "processing", "pending_approval"],
            "terminal_status": "pending_approval",
            "delivery_status": "pending_approval",
        },
    },
    {
        "scenario_id": "api_secret_pending_approval",
        "name": "API Secret Pending Approval",
        "category": "policy",
        "description": "Secrets should be classified high risk and blocked pending approval.",
        "message": "Please summarize this troubleshooting note and send it to the external mailbox.",
        "uploaded_filename": "secret_incident.md",
        "uploaded_content_type": "text/markdown",
        "uploaded_text": (
            "Partner debugging notes: api_key=sk-prod-1234567890, secret rotation pending, "
            "customer impact limited to staging access."
        ),
        "destination_email": "security-review@example.com",
        "requested_action": "summarize_and_send",
        "fault_injection": {
            "force_smtp_fail": False,
            "force_model_timeout": False,
            "force_retrieval_empty": False,
            "force_rule_only_mode": False,
            "force_queue_delay_seconds": 0,
        },
        "expected_outcome": {
            "risk_level": "high",
            "approval_required": True,
            "expected_status_path": ["queued", "processing", "pending_approval"],
            "terminal_status": "pending_approval",
            "delivery_status": "pending_approval",
        },
    },
    {
        "scenario_id": "smtp_failure_after_low_risk_review",
        "name": "SMTP Failure After Low Risk Review",
        "category": "fault_injection",
        "description": "A low-risk task should reach sending and fail safely when SMTP is injected to fail.",
        "message": "Please summarize the release note and send it out.",
        "uploaded_filename": "release_note.txt",
        "uploaded_content_type": "text/plain",
        "uploaded_text": (
            "Release note draft: maintenance completed successfully, no customer data included, "
            "no credentials involved."
        ),
        "destination_email": "ops-broadcast@example.com",
        "requested_action": "summarize_and_send",
        "fault_injection": {
            "force_smtp_fail": True,
            "force_model_timeout": False,
            "force_retrieval_empty": False,
            "force_rule_only_mode": False,
            "force_queue_delay_seconds": 0,
        },
        "expected_outcome": {
            "risk_level": "low",
            "approval_required": False,
            "expected_status_path": ["queued", "processing", "queued_for_send", "sending", "send_failed"],
            "terminal_status": "send_failed",
            "delivery_status": "send_failed",
            "last_error_category": "provider_temporary",
        },
    },
    {
        "scenario_id": "model_timeout_rule_only_fallback",
        "name": "Model Timeout With Rule Only Fallback",
        "category": "fault_injection",
        "description": "Injected dependency failure should degrade into rule-only mode and still produce a risk decision.",
        "message": "Please summarize this outbound log and send it externally.",
        "uploaded_filename": "dependency_timeout.log",
        "uploaded_content_type": "text/plain",
        "uploaded_text": (
            "Outbound request context: customer callback number 13911112222, "
            "no secrets included, message requires external summary."
        ),
        "destination_email": "fallback-review@example.com",
        "requested_action": "summarize_and_send",
        "fault_injection": {
            "force_smtp_fail": False,
            "force_model_timeout": True,
            "force_retrieval_empty": False,
            "force_rule_only_mode": True,
            "force_queue_delay_seconds": 0,
        },
        "expected_outcome": {
            "risk_level": "medium",
            "approval_required": True,
            "expected_status_path": ["queued", "processing", "pending_approval"],
            "terminal_status": "pending_approval",
            "delivery_status": "pending_approval",
            "degradation_mode": "rule_only",
            "last_error_category": "provider_temporary",
        },
    },
    {
        "scenario_id": "queue_delay_observability",
        "name": "Queue Delay Observability",
        "category": "fault_injection",
        "description": "Injected queue delay should be visible while still completing the normal low-risk flow.",
        "message": "Please summarize the public maintenance update and send it out.",
        "uploaded_filename": "maintenance_notice.txt",
        "uploaded_content_type": "text/plain",
        "uploaded_text": (
            "Maintenance completed, services restored, public status page already updated. "
            "No private customer data is present."
        ),
        "destination_email": "status-page@example.com",
        "requested_action": "summarize_and_send",
        "fault_injection": {
            "force_smtp_fail": False,
            "force_model_timeout": False,
            "force_retrieval_empty": False,
            "force_rule_only_mode": False,
            "force_queue_delay_seconds": 5,
        },
        "expected_outcome": {
            "risk_level": "low",
            "approval_required": False,
            "expected_status_path": ["queued", "processing", "queued_for_send", "sending", "sent"],
            "terminal_status": "sent",
            "delivery_status": "sent",
        },
    },
]


def list_dlp_scenarios() -> list[dict[str, Any]]:
    return deepcopy(SCENARIOS)


def get_dlp_scenario(scenario_id: str) -> dict[str, Any] | None:
    for item in SCENARIOS:
        if item["scenario_id"] == scenario_id:
            return deepcopy(item)
    return None


def normalize_fault_injection(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(raw or {})
    return {
        "force_smtp_fail": bool(raw.get("force_smtp_fail", False)),
        "force_smtp_uncertain": bool(raw.get("force_smtp_uncertain", False)),
        "force_model_timeout": bool(raw.get("force_model_timeout", False)),
        "force_retrieval_empty": bool(raw.get("force_retrieval_empty", False)),
        "force_rule_only_mode": bool(raw.get("force_rule_only_mode", False)),
        "force_queue_delay_seconds": max(0, int(raw.get("force_queue_delay_seconds", 0) or 0)),
    }


def build_status_path(events: list[dict[str, Any]]) -> list[str]:
    path: list[str] = []
    for event in events:
        details = dict(event.get("details_json") or {})
        status = str(details.get("status") or EVENT_STATUS_MAP.get(str(event.get("event_type", "")), ""))
        if not status:
            continue
        if path and path[-1] == status:
            continue
        path.append(status)
    return path


def evaluate_scenario_task(
    task: dict[str, Any],
    *,
    expected_outcome: dict[str, Any] | None,
    status_path: list[str],
) -> dict[str, Any]:
    expected = dict(expected_outcome or {})
    if not expected:
        return {"status": "not_applicable", "passed": None, "checks": [], "summary": "No expected outcome configured."}

    current_status = str(task.get("status", ""))
    expected_path = [str(item) for item in expected.get("expected_status_path", [])]
    checks: list[dict[str, Any]] = []

    def add_check(name: str, actual: Any, expected_value: Any) -> None:
        if expected_value in (None, "", []):
            return
        passed = actual == expected_value
        checks.append(
            {
                "name": name,
                "actual": actual,
                "expected": expected_value,
                "passed": passed,
            }
        )

    add_check("risk_level", str(task.get("risk_level", "")), expected.get("risk_level"))
    if "approval_required" in expected:
        add_check("approval_required", bool(task.get("approval_required", False)), bool(expected.get("approval_required")))
    add_check("delivery_status", str(task.get("delivery_status", "")), expected.get("delivery_status"))
    add_check("degradation_mode", str(task.get("degradation_mode", "")), expected.get("degradation_mode"))
    add_check("last_error_category", str(task.get("last_error_category", "")), expected.get("last_error_category"))
    add_check("terminal_status", current_status, expected.get("terminal_status"))

    if expected_path:
        settled = current_status in TERMINAL_EVALUATION_STATUSES
        if settled:
            path_passed = status_path == expected_path
        else:
            path_passed = status_path == expected_path[: len(status_path)]
        checks.append(
            {
                "name": "status_path",
                "actual": status_path,
                "expected": expected_path,
                "passed": path_passed,
            }
        )

    if current_status in TERMINAL_EVALUATION_STATUSES:
        passed = all(item["passed"] for item in checks)
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "checks": checks,
            "summary": "Scenario replay matched expected outcome." if passed else "Scenario replay diverged from expected outcome.",
        }

    matches_so_far = all(item["passed"] for item in checks if item["name"] != "terminal_status")
    return {
        "status": "running",
        "passed": None,
        "checks": checks,
        "summary": "Scenario replay is still running." if matches_so_far else "Scenario replay is in progress but has already diverged.",
    }
