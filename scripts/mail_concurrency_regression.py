from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mail.fake_provider import FakeMailProvider
from app.mail.fixtures import FIXTURE_TENANT_ID, FIXTURE_WORKSPACE_ID, fixture_actor_context
from app.mail.harness import MailHarness


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    cases: list[str] = []
    actor = fixture_actor_context()
    provider = FakeMailProvider()
    harness = MailHarness(provider)
    created = harness.build_send_draft(
        actor_context=actor,
        conversation_id=str(actor["conversation_id"]),
        to=["concurrency@example.com"],
        subject="Concurrency harness",
        body_text="Public concurrency harness payload.",
    )
    draft_id = str(created.get("draft_ref") or "")
    _assert(bool(draft_id), f"draft creation failed: {created}")
    version = int(created["payload"]["draft"]["version"])

    other_user = {
        **actor,
        "user_id": "other-mail-user",
    }
    other_tenant = {
        **actor,
        "tenant_id": "other-mail-tenant",
        "user_id": "other-tenant-user",
    }
    user_denied = harness.get_draft_snapshot(draft_id, actor_context=other_user)
    tenant_denied = harness.get_draft_snapshot(draft_id, actor_context=other_tenant)
    _assert(user_denied["status"] == "permission_denied", f"cross-user draft leak: {user_denied}")
    _assert(tenant_denied["status"] == "permission_denied", f"cross-tenant draft leak: {tenant_denied}")
    cases.extend(["isolation.cross_user_denied", "isolation.cross_tenant_denied"])

    patches = [
        {"subject": "Concurrency harness patched subject"},
        {"body_text": "Public concurrency harness patched body."},
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda patch: harness.patch_draft(
                    draft_id,
                    actor_context=actor,
                    patch=patch,
                    expected_version=version,
                ),
                patches,
            )
        )
    statuses = sorted(str(item.get("status") or "") for item in results)
    _assert(statuses == ["conflict", "draft_ready"], f"concurrent patch should yield one conflict: {results}")
    cases.append("concurrency.stale_patch_conflict")

    pending = harness.request_send_confirmation(draft_id, actor_context=actor)
    _assert(pending["status"] == "pending_confirmation", f"confirmation failed: {pending}")
    first = harness.submit_dlp_result(draft_id, actor_context=actor, risk="low", confirmed=True)
    second = harness.submit_dlp_result(draft_id, actor_context=actor, risk="low", confirmed=True)
    _assert(first["status"] == "sent", f"first send failed: {first}")
    _assert(second["status"] == "sent", f"duplicate confirmation response mismatch: {second}")
    _assert(second["payload"]["deduplicated"] is True, f"duplicate confirmation should reuse result: {second}")
    _assert(provider.sent_count == 1, f"duplicate confirmation caused duplicate send: {provider.sent_messages}")
    cases.append("concurrency.duplicate_confirmation_deduplicated")

    queue_health = harness.queue_health()
    _assert(queue_health["statuses"] == {"sent": 1}, f"queue health mismatch: {queue_health}")
    cases.append("observability.queue_health")

    print(
        json.dumps(
            {
                **harness.report(ok=True, cases_run=cases),
                "provider_sent_count": provider.sent_count,
                "patch_statuses": statuses,
                "fixture_scope": {
                    "tenant_id": FIXTURE_TENANT_ID,
                    "workspace_id": FIXTURE_WORKSPACE_ID,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
