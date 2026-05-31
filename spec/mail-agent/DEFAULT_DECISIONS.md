# Mail Agent Default Decisions

These decisions are locked for the first implementation slice unless the user explicitly reopens them.

## Provider

Use current IMAP/SMTP plus a fake provider harness first.

Reason: the current project already has real mailbox sync and SMTP send. Fake provider gives deterministic failure, concurrency, and replay coverage.

## Workspace Suite

Docs, Drive, Sheets, Slides, and Forms are future Workspace/Document domain agents.

Mail Agent may reference their output as `attachment_ref`, `document_export_ref`, or `body_source_ref`.

## HTML

First phase supports inbound sanitized HTML plus plain text view. Outbound remains plain text first, with schema fields reserved for HTML/multipart.

## Thread and Label

Use provider-native ids when available and local normalized fallback when not available.

## Send Governance

All real send actions require:

- Persistent draft
- User confirmation
- DLP task
- Approval when risk requires it
- Auditable task event trail

## Memory

Task summaries may enter memory. Raw mail body, raw attachment body, high-risk content, and policy-changing preferences must not enter active memory by default.

## Reliability

First reliability slice prioritizes:

- Idempotency key
- Checkpoint before side effects
- Dead letter queue
- Replay-safe harness cases
