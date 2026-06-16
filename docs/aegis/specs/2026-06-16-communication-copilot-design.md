# Communication Copilot Design Spec

Status: review-ready
Date: 2026-06-16
Design type: product and architecture design spec
Primary owner target: Communication Copilot platform
Subdomain owner target: Mail Agent V2 as communication closeout owner

## 1. Purpose

This spec defines the next stable product baseline for the project.

The project should evolve into an internal-employee Communication Copilot that
helps users process customer threads, ground answers in enterprise knowledge,
turn that context into better outbound communication, and safely execute follow
up actions through governance boundaries.

This spec becomes the top-level product and architecture baseline for the
refactor. Existing subdomain specs remain useful, but they should align under
this document.

## 2. Product Definition

The product is an internal-employee external communication Copilot.

Primary user:

- Internal employee such as presales, support, customer success, or sales.

Primary use:

- Work from a customer communication thread.
- Ask product/company questions during that workflow.
- Turn the grounded result into a better reply, follow-up email, or next-step
  proposal.
- Escalate to a meeting when needed.
- Keep all real side effects inside confirmation, DLP, governance, and task
  audit boundaries.

One-line definition:

- Mail Agent is the main closeout agent.
- RAG, thread context, and meeting capabilities exist mainly to help produce a
  better external reply or follow-up action.

## 3. Product Scope

In scope:

- Customer threads and inbound communication context
- Enterprise grounding for product/company facts
- Reply drafting, follow-up drafting, recap drafting
- Meeting escalation as a communication upgrade path
- Governance, confirmation, DLP, send, recovery, and audit

Out of scope for this refactor:

- Generic all-purpose enterprise assistant positioning
- External customer-facing self-service chatbot as the main product
- Workspace/document platform expansion as a mainline feature
- Triggered/proactive platform work as the first refactor target
- Broad provider expansion unrelated to the communication closeout loop

## 4. Core Design Principles

- Docker is the only valid verification environment.
- Code owns state, constraints, permissions, source boundaries, tool calls, and
  observations.
- LLMs own normal user-visible wording from structured observations.
- Bad-case fixes should improve structured state or observations, not add fixed
  user-facing business copy.
- The system remains agent-first; UI is a work surface, not the business owner.

## 5. Primary Work Objects

### 5.1 communication_thread

Canonical meaning:

- The active customer communication object being worked on.

Examples:

- Inbound email thread
- Related prior replies
- Meeting notes relevant to that customer conversation
- Uploaded materials associated with the exchange

Responsibilities:

- Anchor the work to a real customer communication context
- Provide the dominant workflow entry
- Feed context into brief creation

Non-responsibilities:

- It does not decide the final external wording
- It does not own enterprise truth

### 5.2 grounding_bundle

Canonical meaning:

- A grounded evidence package produced by EnterpriseRAG and other trusted
  evidence sources.

Contains:

- Canonical facts
- Citations
- Missing aspects
- Confidence
- Evidence boundary
- Provenance

Responsibilities:

- Supply what can be safely said
- Make evidence limits explicit

Non-responsibilities:

- It does not own final email wording
- It does not own thread state

### 5.3 communication_brief

Canonical meaning:

- The stable intermediate object that expresses what the employee should
  communicate, why, based on which facts, and what the next move is.

Suggested fields:

- brief id
- actor context
- thread ref
- customer ask
- internal response goal
- thread summary
- grounding refs
- must-include points
- must-avoid points
- unresolved questions
- recommended response mode
- next action candidates
- provenance refs

Responsibilities:

- Merge thread context, grounding, employee intent, and response direction
- Act as the standard input to Mail Agent
- Give renderer a stable, non-fragmented object

This is the key new canonical abstraction for the refactor.

### 5.4 mail_draft

Canonical meaning:

- The governed outbound communication artifact prepared for an external reply or
  follow-up.

Responsibilities:

- Receive the communication brief
- Represent editable, confirmable, governable outbound content
- Enter patch / confirm / DLP / send lifecycle

### 5.5 meeting_escalation_candidate

Canonical meaning:

- A structured suggestion or prepared action to escalate the communication into
  a meeting.

Responsibilities:

- Represent meeting as a communication upgrade path
- Stay subordinate to the Mail Agent closeout loop

### 5.6 governed_delivery_task

Canonical meaning:

- The only real execution object for side-effectful outbound delivery.

Responsibilities:

- Carry confirmation state
- Carry DLP/governance state
- Carry send/failure/recovery state
- Provide auditability

## 6. Owner Hierarchy

### 6.1 Supervisor

Owns:

- Intent understanding
- Active object resolution
- Plan and continuation flow
- Domain call ordering
- State transition orchestration

Does not own:

- Final customer reply wording
- Enterprise fact truth
- Governance decisions

### 6.2 Mail Agent

Owns:

- Communication closeout
- Draft creation and revision
- Conversion from brief to outward communication artifact
- Handoff into governed delivery

This is the primary business owner in the refactor.

### 6.3 EnterpriseRAG

Owns:

- Enterprise grounding
- Evidence retrieval
- Canonical facts and citations
- Explicit evidence limits

Does not own:

- Final external communication wording
- Communication lifecycle state

### 6.4 Meeting Agent

Owns:

- Meeting creation/cancel/detail provider interactions
- Structured meeting proposal and result state

Does not own:

- Communication closeout
- Final reply content

### 6.5 Memory

Owns:

- Communication continuity
- Customer context continuity
- Working context and follow-up state

Does not own:

- Enterprise truth
- Governance truth
- Communication closeout decisions

### 6.6 Governance / DLP

Owns:

- Risk boundaries
- Approval and exception state
- Delivery execution boundaries
- Recovery and audit state

Does not own:

- Business wording
- Grounding truth

## 7. Main Runtime Contract

Preferred high-level loop:

```text
active object resolution
-> build/refresh communication_brief
-> Mail Agent chooses response path
-> renderer presents reply/draft/clarification/next step
-> if side effect requested: confirmation -> DLP/governance -> delivery or recovery
```

Allowed entry points:

- Thread-first workflow
- Global question/ask workflow
- Draft continuation workflow
- Governance/task recovery workflow

Dominant mode:

- Mixed entry, but thread/context is the default work anchor.

## 8. UI And Runtime Entry

### 8.1 Product Surface

The preferred user workspace should be a thread inbox plus Copilot side panel.

Expected areas:

- Thread list
- Thread/context detail pane
- Copilot panel for brief, grounding-backed suggestions, draft preview,
  clarifications, next-step recommendations, and escalation proposals

### 8.2 API Role

`/agent/chat` remains useful as a runtime interaction endpoint.

It should not remain the top-level product identity.

Expected role after refactor:

- contextual Copilot interaction endpoint
- brief refinement endpoint
- continuation input endpoint
- action request entry tied to a thread/draft/task context

### 8.3 Governance Surface

The governance console remains a separate recovery and review surface.

It owns:

- high-risk and exception review
- DLP state
- send failures
- queue/provider/worker health
- replay/recovery/audit

It does not own normal communication workflow.

## 9. Legacy Governance And Retirement Principles

The refactor should not begin with random cleanup. It should use the new top
level spec to decide what stays.

Keep as core assets:

- EnterpriseRAG grounding pipeline
- Mail Agent V2
- DLP / governance / governed delivery task flow
- Meeting escalation path
- Continuation state and pending objects
- Supervisor / domain-agent / DAG structure
- Typed observation / verifier / trace evaluator / harness investment

Put on strong retirement review:

- Historical LeetCode naming and product identity remnants
- Hard-coded user-visible answer paths in registry/fallback layers
- Compat-only orchestration/fallback carriers that no longer support the new
  closeout loop
- Chat-first product framing that conflicts with thread-driven communication
  work

Demote to future interfaces:

- Workspace/document expansion
- TriggerDefinition and proactive work
- ReplyChannel separation work
- Broad OpenAPI tool factory work
- Provider expansion unrelated to the communication closeout loop

## 10. Compatibility Boundaries

Must stay true during and after refactor:

- Docker-only verification
- LLM-rendered user-visible wording from observations
- Permission/confirmation/DLP/governance boundaries for side effects
- Enterprise facts still require grounded evidence from EnterpriseRAG
- Existing Mail Agent V2 subdomain contracts remain valid unless explicitly
  revised

## 11. Spec Hierarchy

This document becomes the top-level authority for the refactor.

Expected subordinate specs:

- `spec/MAIL_AGENT_V2_SPEC.md`
- Future EnterpriseRAG grounding sub-spec if needed
- Future thread workspace/UI contract spec if needed
- Future governance/delivery lifecycle brief if needed

Mail Agent V2 remains valid, but it should be treated as a subdomain spec under
this product-level baseline.

## 12. Recommended Refactor Sequence

### Phase A: Authority Reset

- Adopt this top-level spec
- Align README and top-level product framing
- Build legacy inventory from the new owner hierarchy

### Phase B: Runtime Owner Consolidation

- Introduce/normalize canonical use of `communication_thread`
- Introduce/normalize `communication_brief`
- Ensure Mail Agent owns communication closeout
- Ensure EnterpriseRAG stays grounding-only

### Phase C: Entry And Workspace Reshape

- Reframe `8511` as the communication workspace
- Keep governance as a separate review/recovery surface
- Reduce chat-first product framing

### Phase D: Retirement And Simplification

- Retire obsolete compat carriers
- Clean naming/identity remnants
- Remove dead paths after harness coverage proves safety

## 13. Harness And Acceptance Direction

The refactor should preserve the project's harness mindset.

Acceptance should be proved by Docker regressions that cover:

- Thread-driven reply drafting
- Grounding-backed response generation
- Draft patch/confirm/governed send lifecycle
- Meeting escalation from a communication brief
- Recovery from DLP/provider/worker failure
- Legacy-path retirement without regression of the main loop

## 14. ADR Signals

This spec introduces durable architecture signals:

- Mail Agent is the communication closeout owner.
- `communication_brief` is the main intermediate runtime object.
- EnterpriseRAG is the grounding owner, not the final communication owner.
- `/agent/chat` is a runtime interaction endpoint, not the product center.
- `8511` should evolve into the communication workspace.
- Governance remains a distinct review and recovery surface.

## 15. Explicit Non-Goals

- Do not turn the product into a generic assistant platform during this refactor.
- Do not optimize around new provider expansion before the core loop is clean.
- Do not solve quality issues by hard-coding visible answers.
- Do not let UI shape become the source of truth for business behavior.

## 16. TaskIntentDraft Appendix

Outcome:

- Refactor the project into a Mail-Agent-centered Communication Copilot.

Success evidence:

- Product positioning, owner hierarchy, work objects, and refactor order are
  pinned before implementation planning.

Stop condition:

- Approved design spec ready for implementation planning.

Non-goals:

- No implementation changes during design approval.

## 17. BaselineReadSetHint Appendix

Primary references:

- `总体要求.md`
- `README.md`
- `spec/MAIL_AGENT_V2_SPEC.md`
- `todolist.md`
- `problem_todolist.md`

Key authority gap addressed here:

- Missing top-level product/architecture baseline above the existing subdomain
  specs.

## 18. ImpactStatementDraft Appendix

Affected layers:

- Product framing
- Runtime owner hierarchy
- Entry surfaces
- Mail/RAG/Meeting boundaries
- Legacy retirement path

Key invariant:

- The communication closeout loop remains governed, grounded, and
  observation-first.
