from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mail.current_provider import CurrentImapSmtpMailProvider
from app.mail.domain import MailDraft
from app.mail.fake_provider import FAILURE_MODES, FakeMailProvider
from app.mail.fixtures import fixture_actor_context
from app.mail.provider_contract import run_mail_provider_contract


CURRENT_ACTOR_CONTEXT = {
    "tenant_id": "local-dev",
    "workspace_id": "default",
    "user_id": "provider-contract-user",
    "roles": ["mail:read", "mail:send"],
}
FAKE_ACTOR_CONTEXT = dict(fixture_actor_context())


def _failure_mode_report() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for mode in sorted(FAILURE_MODES - {"healthy"}):
        provider = FakeMailProvider(failure_mode=mode)
        health = provider.get_health()
        search = provider.search_messages(actor_context=FAKE_ACTOR_CONTEXT)
        send = provider.send_message(
            MailDraft(
                conversation_id="provider-failure-contract",
                to=["contract-recipient@example.com"],
                subject="Failure mode contract",
                body_text="body",
                actor_context=FAKE_ACTOR_CONTEXT,
            ),
            idempotency_key=f"failure-{mode}",
            actor_context=FAKE_ACTOR_CONTEXT,
        )
        observations = [
            health.to_observation(observation_type="mail_provider.failure_health", actor_context=FAKE_ACTOR_CONTEXT),
            search.to_observation(observation_type="mail_provider.failure_search", actor_context=FAKE_ACTOR_CONTEXT),
            send.to_observation(observation_type="mail_provider.failure_send", actor_context=FAKE_ACTOR_CONTEXT),
        ]
        reports.append(
            {
                "mode": mode,
                "health_status": health.status,
                "search_status": search.status,
                "send_status": send.status,
                "observations_have_recovery": all(
                    bool(item.get("success")) or bool(item.get("recovery_observation")) for item in observations
                ),
            }
        )
    return reports


def main() -> int:
    fake_report = run_mail_provider_contract(FakeMailProvider(), actor_context=FAKE_ACTOR_CONTEXT)
    current_report = run_mail_provider_contract(
        CurrentImapSmtpMailProvider(allow_external_send=False),
        actor_context=CURRENT_ACTOR_CONTEXT,
    )
    failure_modes = _failure_mode_report()
    ok = bool(fake_report.get("ok")) and bool(current_report.get("ok")) and all(
        item.get("observations_have_recovery") for item in failure_modes
    )
    result = {
        "ok": ok,
        "providers_checked": [fake_report.get("provider"), current_report.get("provider")],
        "fake_provider": fake_report,
        "current_provider": current_report,
        "failure_modes": failure_modes,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
