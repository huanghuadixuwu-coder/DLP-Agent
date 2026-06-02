from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.continuation_state import PendingObject, resolve_continuation


def main() -> None:
    pending_objects = [
        PendingObject(
            object_id="mail-draft:live-probe",
            object_type="mail_draft",
            status="active",
            allowed_continuations=("patch", "cancel"),
            salience=0.9,
            payload={
                "draft_id": "live-probe",
                "resolved_recipients": ["first@example.com"],
                "resolved_subject": "Old subject",
                "resolved_body": "Old body.",
            },
        )
    ]
    new_task = resolve_continuation(
        "Please send the selected information to second@example.com",
        pending_objects,
    )
    patch = resolve_continuation(
        "Please make the current draft more concise",
        pending_objects,
    )
    if new_task.mode not in {"new_task", "ambiguous"}:
        raise RuntimeError(f"new outbound request was not classified as a new task: {new_task}")
    if patch.mode not in {"continue_existing", "ambiguous"}:
        raise RuntimeError(f"draft edit was not classified as a patch: {patch}")
    if patch.mode == "continue_existing" and dict(patch.parameters.get("constraints") or {}).get("length") != "brief":
        raise RuntimeError(f"draft edit did not produce a structured brief constraint: {patch}")
    if patch.mode == "ambiguous" and patch.source != "state_resolver_classifier_fallback":
        raise RuntimeError(f"draft edit degraded without an explicit safe fallback: {patch}")
    print(
        json.dumps(
            {
                "ok": True,
                "classifier_health": "healthy" if patch.mode == "continue_existing" else "degraded_safe_ambiguity",
                "new_task": new_task.to_dict(),
                "patch": patch.to_dict(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
