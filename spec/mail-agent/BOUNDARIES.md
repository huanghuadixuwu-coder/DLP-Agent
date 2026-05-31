# Mail Agent Boundaries

## Mail Agent Owns

- Inbound mail search, read, thread view, and summary observations.
- Outbound draft creation, patching, confirmation, DLP submission, approval tracking, and send task tracking.
- Mail-specific provenance: body source, attachment source, thread id, label, mailbox, provider message id.
- Mail provider health observations.
- Mail-specific failure recovery observations.

## Mail Agent Does Not Own

- Docs, Sheets, Slides, Forms CRUD.
- Drive-wide file management beyond attachment/source references.
- Enterprise knowledge retrieval beyond requesting EnterpriseRAG observations.
- Calendar or meeting creation beyond consuming Calendar/Meeting observations for invitation drafts.
- Long-term memory policy beyond emitting safe task summaries and memory candidates.

## Cross-Domain Pattern

When a user asks for a mixed task, the Supervisor should build a DAG:

1. Workspace/Document Agent creates or exports content.
2. Meeting/Calendar Agent creates meeting resources.
3. EnterpriseRAG supplies grounded context.
4. Mail Agent builds and governs the outbound draft.
5. DLP/Compliance Agent evaluates send risk.

Agents exchange typed observations and references, not natural-language chats.

## First-Phase Boundary

First phase locks the mail core:

- IMAP/SMTP current provider path.
- Fake provider harness path.
- Persistent draft state.
- DLP/approval/send state machine.
- HTML/plain text normalization at safe depth.
- Thread/label metadata model.

Provider expansion and Workspace suite CRUD come after this core is stable.
