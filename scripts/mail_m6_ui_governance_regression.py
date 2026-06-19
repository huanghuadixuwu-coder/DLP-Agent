from __future__ import annotations

import uuid
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.main as main
from app.main import app
from app.task_store import create_dlp_task, create_mail_dlq_entry, update_task


ADMIN_HEADERS = {
    "X-Tenant-Id": "local-dev",
    "X-User-Id": "m6-governance-regression",
    "X-Workspace-Id": "default",
    "X-Roles": "admin,approver,viewer,user,mail_sender",
}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_ui_contract() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    governance_console = (ROOT / "web" / "governance_console.py").read_text(encoding="utf-8")
    user_workspace = (ROOT / "web" / "streamlit_app.py").read_text(encoding="utf-8")

    assert_true("governance-web:" in compose, "docker-compose must define governance-web service")
    assert_true("GOVERNANCE_WEB_HOST_PORT" in compose, "governance-web host port must be configurable")
    assert_true("PUBLIC_GOVERNANCE_BASE_URL" in user_workspace, "user workspace must use a configurable governance URL")
    assert_true("/admin/mail-dlq" in governance_console, "governance console must expose DLQ diagnostics")
    assert_true("/admin/mail-provider-health" in governance_console, "governance console must expose provider health")
    assert_true("/admin/queue-health" in governance_console, "governance console must expose queue health")
    assert_true("/tasks/{task.get('task_id')}/approve" in governance_console, "governance console must own approval controls")

    v2_start = user_workspace.index("def render_realtime_task_panel_v2")
    v2_end = user_workspace.index('if "session_id" not in st.session_state:')
    v2_block = user_workspace[v2_start:v2_end]
    assert_true("def render_realtime_task_panel(" not in user_workspace, "8511 must not retain the legacy realtime task panel")
    assert_true("def render_legacy_task_inspector" not in user_workspace, "8511 must not retain the legacy debug inspector")
    assert_true("sender_review_required" in v2_block, "8511 task panel must expose sender safety review state")
    assert_true("sender-safety-confirm" in v2_block, "8511 task panel must allow sender safety confirmation")
    assert_true("data-action=\"sender-confirm\"" in v2_block, "8511 sender safety action must be explicit")
    assert_true("/tasks/${encodeURIComponent(taskId)}/approve" not in v2_block, "8511 must not call high-risk approval endpoint")
    assert_true("/tasks/${encodeURIComponent(taskId)}/reject" not in v2_block, "8511 must not call high-risk rejection endpoint")
    assert_true("治理台审核" in v2_block, "8511 task panel must show waiting-for-governance-review")
    assert_true("批准并真实发送" not in v2_block, "8511 v2 task panel must not expose high-risk approve button")
    assert_true("驳回并终止" not in v2_block, "8511 v2 task panel must not expose high-risk reject button")
    assert_true("完整治理、DLQ、Provider、Queue 与诊断请打开独立治理台" in user_workspace, "user workspace must point to the independent governance console")


def assert_admin_endpoints() -> None:
    client = TestClient(app)
    health = client.get("/admin/mail-provider-health", headers=ADMIN_HEADERS)
    assert_true(health.status_code == 200, f"provider health failed: {health.text}")
    assert_true((health.json().get("observation") or {}).get("observation_type") == "mail_provider_health", "provider health must return typed observation")

    queue = client.get("/admin/queue-health", headers=ADMIN_HEADERS)
    assert_true(queue.status_code == 200, f"queue health failed: {queue.text}")
    assert_true("queue_status" in queue.json(), "queue health must expose queue_status")

    harness = client.get("/admin/mail-harness-summary", headers=ADMIN_HEADERS)
    assert_true(harness.status_code == 200, f"harness summary failed: {harness.text}")
    assert_true(len(harness.json().get("harnesses") or []) >= 3, "harness summary must expose M3-M5 harness commands")


def assert_dlq_replay_contract() -> None:
    client = TestClient(app)
    run_id = uuid.uuid4().hex[:10]
    unsafe_task = create_dlp_task(
        session_id=f"m6-session-{run_id}",
        conversation_id=f"m6-conv-{run_id}",
        message_raw="unsafe dlq replay sample",
        destination_email="qa@example.com",
        status="dead_letter",
        delivery_subject="M6 unsafe replay",
        delivery_body="unsafe",
        tenant_id="local-dev",
        user_id="m6-governance-regression",
        workspace_id="default",
    )
    unsafe_entry = create_mail_dlq_entry(
        task_id=str(unsafe_task["task_id"]),
        operation="send_email",
        last_error="simulated unsafe replay",
        safe_replay_allowed=False,
        recovery_hint="manual review required",
    )
    blocked = client.post(f"/admin/mail-dlq/{unsafe_entry['dlq_id']}/replay", headers=ADMIN_HEADERS)
    assert_true(blocked.status_code == 200, f"unsafe replay endpoint failed: {blocked.text}")
    assert_true(blocked.json().get("ok") is False, "unsafe replay must be blocked")
    assert_true((blocked.json().get("replay") or {}).get("status") == "blocked", "unsafe replay status must be blocked")

    safe_task = create_dlp_task(
        session_id=f"m6-session-{run_id}",
        conversation_id=f"m6-conv-safe-{run_id}",
        message_raw="safe dlq replay sample",
        destination_email="qa@example.com",
        status="dead_letter",
        delivery_subject="M6 safe replay",
        delivery_body="safe",
        tenant_id="local-dev",
        user_id="m6-governance-regression",
        workspace_id="default",
    )
    update_task(str(safe_task["task_id"]), delivery_status="dead_letter")
    safe_entry = create_mail_dlq_entry(
        task_id=str(safe_task["task_id"]),
        operation="send_email",
        last_error="simulated retry exhaustion",
        safe_replay_allowed=True,
        recovery_hint="safe to replay after provider recovers",
    )

    original_enqueue = main.enqueue_email_send_task
    main.enqueue_email_send_task = lambda task_id: str(task_id)
    try:
        replayed = client.post(f"/admin/mail-dlq/{safe_entry['dlq_id']}/replay", headers=ADMIN_HEADERS)
    finally:
        main.enqueue_email_send_task = original_enqueue
    assert_true(replayed.status_code == 200, f"safe replay endpoint failed: {replayed.text}")
    assert_true(replayed.json().get("ok") is True, "safe replay must be accepted")
    assert_true((replayed.json().get("observation") or {}).get("status") == "worker_queued", "safe replay must expose worker_queued observation")

    listed = client.get("/admin/mail-dlq", headers=ADMIN_HEADERS)
    assert_true(listed.status_code == 200, f"dlq list failed: {listed.text}")
    assert_true("entries" in listed.json(), "dlq list must expose entries")


if __name__ == "__main__":
    assert_ui_contract()
    assert_admin_endpoints()
    assert_dlq_replay_contract()
    print({"ok": True, "regression": "mail_m6_ui_governance"})
