# Mail Provider Strategy

## First-Phase Providers

### Current IMAP/SMTP

Use current code path for real mailbox sync and real send.

Strengths:

- Already works in Docker.
- Already connected to DLP task flow.
- Fastest path to preserve existing behavior.

Limits:

- Thread and label support may be partial.
- Provider-native search is limited.
- Some metadata must be normalized locally.

### Fake Provider Harness

Add deterministic provider behavior for tests.

The fake provider must support:

- Search/read canned messages.
- Thread and label capabilities toggled on/off.
- Send success.
- SMTP timeout.
- Uncertain duplicate send result.
- Auth expired.
- Rate limited.

## Provider Interface

Provider adapters return structured results only.

Core methods:

- `search_messages`
- `read_message`
- `list_threads`
- `read_thread`
- `sync_mailbox`
- `send_message`
- `apply_label`
- `get_health`

## Future Providers

Future adapters may include:

- Tencent Enterprise Mail
- Google Workspace
- Microsoft Graph / Exchange

These should plug into the same domain model, state machine, and harness.

## Source of Truth

Provider-native ids are stored when available. Local normalized ids are always present for internal routing.

If a provider cannot write labels or thread state, the observation must include `provider_write_supported=false`.
