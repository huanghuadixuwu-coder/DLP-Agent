# Mail Agent Domain Model

## MailAccount

Represents an accessible mailbox identity.

- `account_id`
- `tenant_id`
- `user_id`
- `workspace_id`
- `provider`: `imap_smtp | fake | google_workspace | exchange | tencent_enterprise_mail`
- `email_address`
- `display_name`
- `capabilities`
- `auth_ref`
- `status`: `active | disabled | auth_expired`

## MailMessage

Canonical single-message record.

- `message_id`
- `provider_message_id`
- `thread_id`
- `provider_thread_id`
- `mailbox`
- `labels`
- `sender`
- `to`
- `cc`
- `bcc`
- `subject`
- `received_at`
- `sent_at`
- `snippet`
- `body_text`
- `body_html_sanitized`
- `body_preview`
- `attachments`
- `headers_json`
- `risk_hint`
- `is_seen`
- `source_policy`

## MailThread

Conversation-level view.

- `thread_id`
- `provider_thread_id`
- `subject_normalized`
- `participants`
- `message_ids`
- `last_message_at`
- `unread_count`
- `labels`
- `summary_observation_ref`
- `open_actions`

## MailDraft

Persistent editable draft.

- `draft_id`
- `conversation_id`
- `draft_kind`: `new_message | reply | forward | meeting_invitation | rag_grounded_mail`
- `status`: see `STATE_MACHINES.md`
- `to`
- `cc`
- `bcc`
- `subject`
- `body_text`
- `body_html`
- `body_format`: `plain | html | multipart`
- `attachments`
- `source_refs`
- `body_sources`
- `source_policy`
- `missing_fields`
- `requires_confirmation`
- `requires_dlp`
- `created_from_observations`
- `last_patch_request`
- `actor_context`

## MailOperationTask

Provider-facing operation state.

- `operation_id`
- `task_id`
- `task_type`: `mail_send | mail_reply | mail_forward | mail_label_update | mail_sync`
- `draft_id`
- `status`
- `dlp_task_id`
- `provider_operation_id`
- `idempotency_key`
- `attempt_count`
- `last_error`
- `recovery_hint`

## MailObservation

Typed observation returned to the agent loop and renderer.

- `observation_type`
- `status`
- `source_tool`
- `summary`
- `provenance`
- `confidence`
- `message_refs`
- `thread_refs`
- `draft_ref`
- `task_ref`
- `risk`
- `missing_fields`
- `constraints`
- `side_effects`
- `next_actions`
- `actor_context`
- `debug`
