# Mail Reliability

## First Reliability Slice

The first implementation slice prioritizes:

- Idempotency
- Checkpointing
- Dead letter queue
- Replay safety

These matter most because duplicate external email sends are hard to undo.

## Idempotency

Every side-effectful provider operation must include an `idempotency_key`.

The key should bind:

- actor context
- draft id
- operation type
- confirmation id
- normalized recipient list
- payload digest

Duplicate confirmations with the same key must return the existing task or provider result.

## Checkpoints

Checkpoint before:

- DLP queue enqueue
- approval transition
- send queue enqueue
- provider send call
- provider label/archive write

## Dead Letter Queue

DLQ entry fields:

- `task_id`
- `operation`
- `payload_digest`
- `last_error`
- `attempt_count`
- `safe_replay_allowed`
- `recovery_hint`
- `actor_context`

Replay requires permission and idempotency checks.

## Backpressure

Backpressure should protect:

- LLM renderer
- DLP worker
- SMTP provider
- mailbox sync
- provider search/read when external

When overloaded, tools return typed observations with queue or rate-limit state. The renderer explains next steps.
