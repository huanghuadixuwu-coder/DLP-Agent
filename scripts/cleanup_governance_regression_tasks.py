from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.task_store import list_dlp_tasks, reject_task


REGRESSION_SESSION_PREFIXES = (
    "session-mail-preview-",
    "session-a-",
    "session-b-",
    "session-other-",
)


def main() -> None:
    rejected: list[str] = []
    for task in list_dlp_tasks(status="pending_approval"):
        session_id = str(task.get("session_id") or "")
        if not session_id.startswith(REGRESSION_SESSION_PREFIXES):
            continue
        task_id = str(task.get("task_id") or "")
        if task_id and reject_task(task_id, "regression_cleanup", "cleanup after governance regression"):
            rejected.append(task_id)
    print(json.dumps({"ok": True, "rejected_task_ids": rejected}, ensure_ascii=False))


if __name__ == "__main__":
    main()
