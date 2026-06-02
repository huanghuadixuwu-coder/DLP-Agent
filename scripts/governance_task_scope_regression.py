from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import app
from app.task_store import create_dlp_task


def _headers(*, tenant_id: str, user_id: str, workspace_id: str, roles: str) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant_id,
        "X-User-Id": user_id,
        "X-Workspace-Id": workspace_id,
        "X-Roles": roles,
    }


def _task_ids(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {str(item.get("task_id") or "") for item in response.json()}


def main() -> None:
    suffix = uuid4().hex[:10]
    tenant_id = f"tenant-governance-scope-{suffix}"
    workspace_id = f"workspace-governance-scope-{suffix}"
    other_workspace_id = f"workspace-governance-other-{suffix}"
    task_a = create_dlp_task(
        session_id=f"session-a-{suffix}",
        conversation_id=f"conv-a-{suffix}",
        message_raw="workspace task a",
        destination_email="a@example.com",
        status="pending_approval",
        tenant_id=tenant_id,
        user_id=f"user-a-{suffix}",
        workspace_id=workspace_id,
    )
    task_b = create_dlp_task(
        session_id=f"session-b-{suffix}",
        conversation_id=f"conv-b-{suffix}",
        message_raw="workspace task b",
        destination_email="b@example.com",
        status="pending_approval",
        tenant_id=tenant_id,
        user_id=f"user-b-{suffix}",
        workspace_id=workspace_id,
    )
    task_other = create_dlp_task(
        session_id=f"session-other-{suffix}",
        conversation_id=f"conv-other-{suffix}",
        message_raw="other workspace task",
        destination_email="other@example.com",
        status="pending_approval",
        tenant_id=tenant_id,
        user_id=f"user-other-{suffix}",
        workspace_id=other_workspace_id,
    )

    with TestClient(app) as client:
        admin_ids = _task_ids(
            client.get(
                "/tasks?status=pending_approval",
                headers=_headers(
                    tenant_id=tenant_id,
                    user_id=f"governance-admin-{suffix}",
                    workspace_id=workspace_id,
                    roles="admin,approver",
                ),
            )
        )
        viewer_ids = _task_ids(
            client.get(
                "/tasks?status=pending_approval",
                headers=_headers(
                    tenant_id=tenant_id,
                    user_id=f"user-a-{suffix}",
                    workspace_id=workspace_id,
                    roles="viewer",
                ),
            )
        )

    assert str(task_a["task_id"]) in admin_ids, admin_ids
    assert str(task_b["task_id"]) in admin_ids, admin_ids
    assert str(task_other["task_id"]) not in admin_ids, admin_ids
    assert str(task_a["task_id"]) in viewer_ids, viewer_ids
    assert str(task_b["task_id"]) not in viewer_ids, viewer_ids
    assert str(task_other["task_id"]) not in viewer_ids, viewer_ids

    print(
        json.dumps(
            {
                "ok": True,
                "admin_workspace_task_count": len(admin_ids),
                "viewer_task_count": len(viewer_ids),
                "admin_sees_cross_user_same_workspace": True,
                "admin_cross_workspace_blocked": True,
                "viewer_cross_user_blocked": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
