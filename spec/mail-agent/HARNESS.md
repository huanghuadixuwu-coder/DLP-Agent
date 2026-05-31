# Mail Harness

Mail Harness is a first-phase feature. It is the proof that Mail Agent V2 behaves like an enterprise system under failure, concurrency, and replay.

## Goals

- Verify tool contracts.
- Verify state transitions.
- Verify DLP and confirmation boundaries.
- Verify provider failure recovery.
- Verify idempotency and duplicate-send prevention.
- Verify renderer receives observations instead of hidden templates.

## Core Harness Cases

### Read Path

- Search recent mail.
- Read a single message.
- Read a thread.
- Summarize a thread.
- Provider unavailable falls back to local cache when possible.

### Draft Path

- Build new send draft.
- Build reply draft.
- Patch recipient.
- Patch body.
- Patch subject.
- Missing fields produce structured observation.

### Governance Path

- Unconfirmed draft never sends.
- Confirmed low-risk draft enters DLP and send queue.
- Medium-risk draft enters sender safety confirmation on `8511`.
- Sender can revise, cancel, or confirm a medium-risk task again; the action is audited.
- High-risk draft enters governance review on `8512`.
- Governance rejection blocks high-risk send.
- Governance exception approval allows high-risk send.
- Critical-risk draft is blocked by default and cannot use the normal approval release path.

### Failure Path

- SMTP timeout retries.
- Retries exhausted enters DLQ.
- Worker unavailable produces recovery observation.
- DLP model failure degrades safely.
- Provider auth expired blocks provider operations.
- Duplicate confirmation does not duplicate send.

### Concurrency Path

- Two users cannot see each other drafts.
- Two tenants cannot query each other mail tasks.
- Two edits to the same draft are serialized or conflict-marked.
- Queue depth and rate limit observations are emitted.

## Required Docker Commands

- `docker compose run --rm --no-deps api python -m compileall -q app scripts`
- `docker compose run --rm --no-deps api python scripts/mail_harness_regression.py`
- `docker compose run --rm --no-deps api python scripts/mail_provider_failure_regression.py`
- `docker compose run --rm --no-deps api python scripts/mail_concurrency_regression.py`

The scripts are implemented and run inside Docker as the first Mail Agent V2 regression layer.

## Harness Output

Each harness run should print:

- `ok`
- `cases_run`
- `cases_failed`
- `state_transitions_checked`
- `idempotency_checks`
- `provider_failures_checked`
- `queue_health`
- `prometheus_snapshot`
