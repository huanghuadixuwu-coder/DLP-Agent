from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mail.fake_provider import FakeMailProvider
from app.mail.fixtures import fixture_actor_context
from app.mail.harness import MailHarness


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _ready_draft(harness: MailHarness, actor: dict, *, suffix: str) -> str:
    created = harness.build_send_draft(
        actor_context=actor,
        conversation_id=str(actor["conversation_id"]),
        to=[f"{suffix}@example.com"],
        subject=f"Provider failure {suffix}",
        body_text=f"Public provider failure payload for {suffix}.",
    )
    draft_id = str(created.get("draft_ref") or "")
    _assert(bool(draft_id), f"draft creation failed: {created}")
    pending = harness.request_send_confirmation(draft_id, actor_context=actor)
    _assert(pending["status"] == "pending_confirmation", f"confirmation failed: {pending}")
    return draft_id


def main() -> None:
    cases: list[str] = []
    actor = fixture_actor_context()
    provider = FakeMailProvider()
    harness = MailHarness(provider)

    provider.set_failure_mode("unavailable")
    cached = harness.search_messages(query="onboarding", actor_context=actor)
    _assert(cached["status"] == "degraded", f"cache fallback should be degraded: {cached}")
    _assert(cached["payload"]["fallback_strategy"] == "local_cache", f"cache fallback missing: {cached}")
    _assert("message_customer_followup_1" in cached["message_refs"], f"cached result mismatch: {cached}")
    cases.append("provider_unavailable.local_cache_fallback")

    provider.set_failure_mode("timeout")
    timeout_draft = _ready_draft(harness, actor, suffix="timeout")
    timeout = harness.submit_dlp_result(timeout_draft, actor_context=actor, risk="low", confirmed=True)
    _assert(timeout["status"] == "delivery_deferred", f"SMTP timeout should defer: {timeout}")
    _assert(timeout["payload"]["provider_result"]["error_code"] == "smtp_timeout", f"timeout code mismatch: {timeout}")
    provider.set_failure_mode("healthy")
    timeout_retry = harness.retry_delivery(timeout_draft, actor_context=actor)
    _assert(timeout_retry["status"] == "sent", f"timeout recovery failed: {timeout_retry}")
    cases.append("smtp_timeout.retry_recovery")

    provider.set_failure_mode("auth_expired")
    auth_draft = _ready_draft(harness, actor, suffix="auth")
    auth = harness.submit_dlp_result(auth_draft, actor_context=actor, risk="low", confirmed=True)
    _assert(auth["status"] == "failed", f"auth expiry should block send: {auth}")
    _assert(auth["payload"]["provider_result"]["error_code"] == "auth_expired", f"auth code mismatch: {auth}")
    cases.append("auth_expired.block_provider_operation")

    provider.set_failure_mode("uncertain")
    uncertain_draft = _ready_draft(harness, actor, suffix="uncertain")
    sends_before_uncertain = provider.sent_count
    uncertain = harness.submit_dlp_result(uncertain_draft, actor_context=actor, risk="low", confirmed=True)
    _assert(uncertain["status"] == "delivery_deferred", f"uncertain result should defer: {uncertain}")
    _assert(uncertain["payload"]["provider_result"]["uncertain"] is True, f"uncertain marker missing: {uncertain}")
    uncertain_retry = harness.retry_delivery(uncertain_draft, actor_context=actor)
    _assert(uncertain_retry["status"] == "sent", f"uncertain replay failed: {uncertain_retry}")
    _assert(uncertain_retry["payload"]["deduplicated"] is True, f"uncertain replay must dedupe: {uncertain_retry}")
    _assert(provider.sent_count == sends_before_uncertain + 1, f"uncertain replay duplicated send: {provider.sent_messages}")
    cases.append("duplicate_uncertainty.idempotent_replay")

    provider.set_failure_mode("rate_limited")
    limited = harness.search_messages(query="invoice", actor_context=actor)
    _assert(limited["status"] == "degraded", f"rate-limit fallback should degrade: {limited}")
    _assert(limited["payload"]["recovery_observation"]["payload"]["retryable"] is True, f"rate-limit retry marker missing: {limited}")
    cases.append("rate_limited.cache_fallback")

    print(
        json.dumps(
            {
                **harness.report(ok=True, cases_run=cases),
                "provider_sent_count": provider.sent_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
