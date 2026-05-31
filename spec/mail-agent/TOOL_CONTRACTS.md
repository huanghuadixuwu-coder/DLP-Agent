# Mail Agent Tool Contracts

All tools must be registered through `@register_tool` and return typed observations. User-visible wording is generated later by the renderer.

## Read-Only Tools

### `mail_search_messages`

Search synchronized or provider-backed messages.

- Input: `query`, `sender`, `since`, `until`, `labels`, `limit`
- Observation: `mail_search`
- Side effects: none

### `mail_read_message`

Read a single canonical message.

- Input: `message_id`
- Observation: `mail_message`
- Side effects: none

### `mail_list_threads`

List threads by query, label, participant, or time.

- Input: `query`, `participant`, `labels`, `limit`
- Observation: `mail_thread_list`
- Side effects: none

### `mail_read_thread`

Read thread messages and thread metadata.

- Input: `thread_id`
- Observation: `mail_thread`
- Side effects: none

### `mail_summarize_thread`

Produce structured summary observation from a thread.

- Input: `thread_id`, `summary_goal`
- Observation: `mail_thread_summary`
- Side effects: none

## Draft Tools

### `mail_build_send_draft`

Create a new persistent draft.

- Input: `to`, `subject_hint`, `content_goal`, `source_refs`, `attachments`
- Observation: `mail_draft`
- Side effects: local draft write only

### `mail_build_reply_draft`

Create a reply draft from a message or thread.

- Input: `message_id | thread_id`, `reply_goal`, `source_refs`
- Observation: `mail_draft`
- Side effects: local draft write only

### `mail_patch_draft`

Apply a structured patch to a pending draft.

- Input: `draft_id`, `patch_instruction`
- Observation: `mail_draft_patch`
- Side effects: local draft write only

## Governed Side-Effect Tools

### `mail_request_send_confirmation`

Move a draft to pending confirmation.

- Input: `draft_id`
- Observation: `mail_confirmation_required`
- Requires confirmation: yes

### `mail_submit_dlp_task`

Submit confirmed draft into DLP workflow.

- Input: `draft_id`, `confirmation_payload`
- Observation: `dlp_task_state`
- Side effects: task store write, queue enqueue

### `mail_apply_label`

Apply local or provider label.

- Input: `message_id | thread_id`, `label`, `idempotency_key`
- Observation: `mail_label_update`
- Side effects: provider or local state write

### `mail_sync_inbound`

Trigger mailbox sync.

- Input: `mailbox`, `since`, `limit`
- Observation: `mail_sync_state`
- Side effects: local cache write

## MCP Exposure

- Read-only tools may expose MCP.
- Draft tools may expose MCP if they never send.
- Send, provider label write, archive, delete, and other external side effects must return confirmation-required when invoked through MCP.
