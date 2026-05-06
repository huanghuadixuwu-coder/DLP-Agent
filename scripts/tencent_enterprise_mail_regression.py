from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

import httpx


BASE_URL = os.getenv("SECURE_MAIL_AGENT_BASE_URL") or os.getenv("API_BASE_URL") or "http://localhost:8010"
RECIPIENT = os.getenv("REGRESSION_RECIPIENT") or "17388861183@163.com"
TIMEOUT = httpx.Timeout(60.0, connect=20.0)
TASK_TERMINAL_STATUSES = {"sent", "rejected", "send_failed", "failed"}


def _request(method: str, path: str, **kwargs: Any) -> Any:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT) as client:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _poll_task(task_id: str, *, expected: set[str] | None = None, timeout_seconds: int = 180) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        task = _request("GET", f"/tasks/{task_id}")
        status = str(task.get("status", ""))
        if expected and status in expected:
            return task
        if status in TASK_TERMINAL_STATUSES:
            return task
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for task {task_id}")


def _summary_has_css_noise(summary_payload: dict[str, Any]) -> bool:
    candidates = list(summary_payload.get("important_messages") or []) + list(summary_payload.get("recent_messages") or [])
    for item in candidates[:5]:
        text = " ".join(
            [
                str(item.get("subject", "")),
                str(item.get("summary", "")),
                str(item.get("snippet", "")),
            ]
        ).lower()
        if "<style" in text or "font-size" in text or "@media" in text or "{ padding" in text:
            return True
    return False


def main() -> int:
    session_id = f"regression_{uuid.uuid4().hex[:8]}"
    conversation = _request("POST", "/conversations", json={"session_id": session_id, "title": "Tencent mailbox regression"})
    conversation_id = str(conversation["conversation_id"])

    report: dict[str, Any] = {
        "base_url": BASE_URL,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "recipient": RECIPIENT,
        "checks": {},
    }

    sync_result = _request("POST", "/mail/inbound/sync", json={})
    _assert(bool(sync_result.get("ok")), f"Inbound sync failed: {sync_result}")
    report["checks"]["inbound_sync"] = sync_result

    digest_result = _request("POST", "/mail/inbound/digest", json={})
    report["checks"]["daily_digest"] = digest_result

    summary = _request("GET", "/mail/inbound/summary")
    _assert(not _summary_has_css_noise(summary), "Inbound summary still contains CSS/template noise.")
    report["checks"]["inbound_summary"] = {
        "total": summary.get("total", 0),
        "important_count": summary.get("important_count", 0),
        "top_subjects": [item.get("subject") for item in (summary.get("important_messages") or [])[:3]],
    }

    chat_result = _request(
        "POST",
        "/agent/chat",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message": (
                f"请把以下公开项目同步纪要总结后发送到 {RECIPIENT}："
                "本周公开联调进展顺利，计划和里程碑保持不变，下周继续推进测试与验收。"
            ),
            "mode": "auto",
            "show_steps": False,
        },
    )
    chat_task_id = str(chat_result.get("task_id") or "")
    _assert(bool(chat_task_id), f"Chat outbound task was not created: {chat_result}")
    chat_task = _poll_task(chat_task_id, expected={"sent", "pending_approval", "queued_for_send"})
    chat_initial_status = str(chat_task.get("status"))
    if chat_initial_status == "pending_approval":
        _request("POST", f"/tasks/{chat_task_id}/approve", json={"actor": "codex_regression", "reason": "chat flow regression"})
        chat_task = _poll_task(chat_task_id, expected={"sent"})
    _assert(str(chat_task.get("status")) == "sent", f"Chat outbound task did not send successfully: {chat_task}")
    report["checks"]["chat_outbound"] = {
        "task_id": chat_task_id,
        "initial_status": chat_initial_status,
        "status": chat_task.get("status"),
        "delivery_status": chat_task.get("delivery_status"),
        "smtp_provider": chat_task.get("smtp_provider"),
    }

    approved_task_seed = _request(
        "POST",
        "/tasks/dlp-outbound",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "destination_email": RECIPIENT,
            "message": "请把以下运维日志总结后外发。张三手机号 13800000000，API_KEY=sk-123456，数据库连接串 db_url=mysql://root:pass@example.internal/prod。",
            "requested_action": "summarize_and_send",
        },
    )
    approved_task_id = str(approved_task_seed["task_id"])
    approved_task_pending = _poll_task(approved_task_id, expected={"pending_approval"})
    _assert(str(approved_task_pending.get("status")) == "pending_approval", f"Approved path task did not reach pending approval: {approved_task_pending}")
    approved_task = _request("POST", f"/tasks/{approved_task_id}/approve", json={"actor": "codex_regression", "reason": ""})
    approved_task_final = _poll_task(approved_task_id, expected={"sent"})
    _assert(str(approved_task_final.get("status")) == "sent", f"Approved task did not send successfully: {approved_task_final}")
    report["checks"]["approve_path"] = {
        "task_id": approved_task_id,
        "approval_status": approved_task.get("status"),
        "final_status": approved_task_final.get("status"),
        "delivery_status": approved_task_final.get("delivery_status"),
    }

    rejected_task_seed = _request(
        "POST",
        "/tasks/dlp-outbound",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "destination_email": RECIPIENT,
            "message": "请把以下客户清单发送给外部。联系人李四，手机号 13900000000，合同金额 500000，secret=prod-secret-value。",
            "requested_action": "summarize_and_send",
        },
    )
    rejected_task_id = str(rejected_task_seed["task_id"])
    rejected_task_pending = _poll_task(rejected_task_id, expected={"pending_approval"})
    _assert(str(rejected_task_pending.get("status")) == "pending_approval", f"Rejected path task did not reach pending approval: {rejected_task_pending}")
    rejected_task = _request(
        "POST",
        f"/tasks/{rejected_task_id}/reject",
        json={"actor": "codex_regression", "reason": "regression reject path"},
    )
    _assert(str(rejected_task.get("status")) == "rejected", f"Reject task failed: {rejected_task}")
    report["checks"]["reject_path"] = {
        "task_id": rejected_task_id,
        "final_status": rejected_task.get("status"),
        "delivery_status": rejected_task.get("delivery_status"),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"REGRESSION_FAILED: {exc}", file=sys.stderr)
        raise
