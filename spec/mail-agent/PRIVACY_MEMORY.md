# Mail Privacy and Memory Boundary

## Privacy Boundary

Mail content is high-sensitivity data by default.

DLP scans:

- Subject
- Body text
- Recipient fields
- Attachment text previews when available
- Source metadata needed for policy

HTML view is converted to safe text for DLP.

## Risk Routing

- `low`: initial explicit send confirmation is sufficient after DLP pass.
- `medium`: sender must review the warning and confirm again on `8511`, or revise/cancel.
- `high`: sender may revise/cancel; exception release requires governance action on `8512`.
- `critical`: blocked by default; normal approval does not release it.

All confirmations, governance actions, revisions, and cancellations are audited.

## Memory Boundary

Allowed active memory:

- Writing style preference
- Signature preference
- Non-sensitive task summary
- User preference about notification style

Pending or rejected memory:

- Requests to bypass approval
- Requests to weaken DLP policy
- Raw sensitive body content
- Raw attachment body content
- Auto-send preferences
- Broad recipient defaults that could cause accidental disclosure

## Conversation Memory

Conversation memory may store a compact task summary:

- `sent a low-risk test email`
- `drafted a reply to customer thread`
- `meeting invitation pending approval`

It must not store raw body unless the user explicitly asks and policy allows it.

## Enterprise Fact Boundary

Memory can provide context and preference. Enterprise facts still require EnterpriseRAG citations.
