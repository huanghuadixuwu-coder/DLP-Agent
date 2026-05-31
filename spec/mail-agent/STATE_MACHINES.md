# Mail Agent State Machines

## Draft Lifecycle

```mermaid
stateDiagram-v2
    [*] --> draft_building
    draft_building --> needs_clarification: missing fields
    needs_clarification --> draft_ready: user supplements
    draft_building --> draft_ready: complete
    draft_ready --> pending_confirmation: user asks to send
    pending_confirmation --> draft_ready: user edits
    pending_confirmation --> queued_dlp: user confirms
    queued_dlp --> dlp_processing
    dlp_processing --> queued_send: low risk
    dlp_processing --> sender_review_required: medium risk
    dlp_processing --> governance_review_required: high risk
    dlp_processing --> blocked: critical risk
    sender_review_required --> draft_ready: sender revises
    sender_review_required --> cancelled: sender cancels
    sender_review_required --> queued_send: sender confirms again
    governance_review_required --> draft_ready: sender revises
    governance_review_required --> cancelled: sender cancels
    governance_review_required --> queued_send: governance approves exception
    governance_review_required --> rejected: governance rejects
    queued_send --> sending
    sending --> sent
    sending --> failed
    failed --> retryable: retry allowed
    retryable --> queued_send
    failed --> dead_letter: retries exhausted
```

## Provider Sync

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> syncing
    syncing --> synced
    syncing --> sync_failed
    sync_failed --> retry_scheduled
    retry_scheduled --> syncing
    sync_failed --> degraded: repeated failure
    degraded --> syncing: provider restored
```

## Confirmation Boundary

No external send can occur before `pending_confirmation -> queued_dlp`.

Confirmation must bind:

- `draft_id`
- `idempotency_key`
- `actor_context`
- `resolved recipients`
- `subject`
- `body_text`
- `attachments`
- `source_policy`

## DLP Boundary

DLP result cannot be overwritten by LLM output. The renderer may explain the result, but task state is the source of truth.

## Risk Routing

| Risk | Required action |
| --- | --- |
| `low` | Send after the initial explicit confirmation and DLP pass. |
| `medium` | Sender reviews the warning on `8511`, then revises, cancels, or confirms again. |
| `high` | Escalate to `8512` for governance exception approval or rejection. |
| `critical` | Block by default. Revise or cancel; no normal approval release. |

## Replay Boundary

Replay is allowed only from checkpointed states:

- `queued_dlp`
- `queued_send`
- `delivery_deferred`
- `dead_letter`

Replay requires idempotency checks before any provider call.
