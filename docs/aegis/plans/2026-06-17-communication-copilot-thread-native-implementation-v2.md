# Communication Copilot Thread-Native Implementation Plan V2

Goal:

- Complete the missing thread-native parts of the approved Communication Copilot design spec.
- Treat the first implementation plan as a foundation slice, not as complete spec fulfillment.
- Make `communication_thread` the selectable, persistent work object; make `communication_brief` a refreshable workspace artifact; make `8511` behave as a thread inbox plus Copilot side panel without changing its visual style.

Architecture:

- Supervisor remains the orchestration owner.
- Mail Agent remains the communication closeout owner.
- EnterpriseRAG remains the grounding owner.
- Meeting Agent remains the escalation provider.
- Governance/DLP remains the side-effect and recovery boundary.
- New corrective flow: `thread inbox -> active communication_thread -> communication_brief -> Copilot action -> mail_draft or meeting_escalation -> governed_delivery_task`.

Tech Stack:

- Python
- FastAPI
- Streamlit
- PostgreSQL-backed stores following existing `inbound_mail_store.py` and `mail/draft_store.py` patterns
- Celery + Redis
- Chroma + SQLite FTS for EnterpriseRAG
- Docker Compose

Baseline/Authority Refs:

- `docs/aegis/specs/2026-06-16-communication-copilot-design.md`
- `docs/aegis/work/2026-06-17-communication-copilot-spec-plan-gap-audit.md`
- `docs/aegis/plans/2026-06-17-communication-copilot-implementation.md`
- `spec/AGENT_UI_SPEC.md`
- `spec/MAIL_AGENT_V2_SPEC.md`
- `spec/mail-agent/UI_CONTRACT.md`
- `spec/mail-agent/IMPLEMENTATION_SEQUENCE.md`
- `总体要求.md`
- `todolist.md`
- `problem_todolist.md`

Compatibility Boundary:

- Docker-only verification remains mandatory.
- Local Docker Compose is the active acceptance baseline while the remote server is offline.
- User-visible answer, clarification, draft preview, and recovery guidance remain renderer-generated from typed observations.
- The UI may change state layout and data flow, but it must not change the established visual style without explicit user approval.
- Side effects remain gated by permissions, confirmation, DLP/governance, idempotency, and audited task state.
- Existing Mail Agent V2 M1-M6 behavior must keep passing.
- Enterprise facts must remain grounded in EnterpriseRAG citations.
- Governance remains a separate review/recovery surface.

Verification:

- `docker compose ps api worker redis postgres chroma`
- `docker compose up -d` if required services are stopped
- `docker compose exec -T api python -m compileall -q app scripts web`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case all`
- `docker compose exec -T api python scripts/mail_authoring_contract_regression.py`
- `docker compose exec -T api python scripts/mail_m6_ui_governance_regression.py`
- Browser QA on `http://localhost:8511/` after thread workspace tasks

## Why This Plan Exists

The first implementation plan created useful foundation pieces but did not fully satisfy the approved design spec. The missing pieces are product-level and source-of-truth-level, not cosmetic:

- The thread is not yet the dominant persistent work object.
- The brief is not yet a refreshable workspace artifact.
- The 8511 user surface is not yet a true thread inbox plus Copilot side panel.
- `/agent/chat` can still behave like a general chat endpoint instead of a contextual Copilot interaction endpoint.

## Architecture Integrity Lens

Invariant:

- Communication work must be anchored to a `communication_thread` unless the user explicitly enters a global ask mode.

Canonical owners:

- `app/communication/thread_store.py` owns active thread registry and thread selection state.
- `app/communication/brief_store.py` owns persisted communication brief snapshots and refresh metadata.
- `app/communication/brief_service.py` owns brief assembly.
- Mail Agent owns external reply/follow-up closeout.
- UI displays state and invokes actions; it never owns business wording or governance decisions.

Responsibility overlap to remove:

- `app/main.py` currently derives communication workspace state from result/debug payloads. That is useful as a compatibility bridge but not sufficient as the source of truth.
- `web/streamlit_app.py` currently shows inbound mail status and workspace indicators, but it does not own a thread-native work loop.

Higher-level simplification:

- Add narrow communication store and API boundaries instead of extending scattered helpers in `app/main.py`.

Retirement falsifier:

- If a user can select a customer thread in 8511, ask a grounded product question, draft a reply, refresh the page, and continue from the same thread/brief without relying on old chat debug payloads, the new owner path is working.

Verdict:

- Proceed with a corrective thread-native plan.

## Plan Pressure Test

- Owner / contract / retirement:
  - The missing owner is active thread and brief persistence. Add it before more UI or routing patches.
- Architecture integrity / higher-level path:
  - A dedicated communication store and API boundary is safer than adding more branches to `app/main.py`.
- Verification scope:
  - Must test thread list, thread detail, active thread binding, brief refresh, RAG-to-reply, meeting escalation, governed send, refresh persistence, and recovery.
- Task executability:
  - Executable if split into store, API, runtime binding, UI state flow, provider flows, governance, retirement, and QA.
- Pressure result:
  - proceed

## Plan-Time Complexity Check

- Target files:
  - `app/main.py`
  - `web/streamlit_app.py`
  - `app/communication/*`
  - `scripts/communication_copilot_regression.py`
- Existing size / shape signals:
  - `app/main.py` is large and mixed-responsibility.
  - `web/streamlit_app.py` already has task progress, inbox status, and debug panels.
- Owner fit:
  - Thread selection and brief persistence belong in `app/communication/`, not in UI session state or conversation debug.
- Add-in-place risk:
  - High for `app/main.py`; moderate for `web/streamlit_app.py`.
- Better file boundary:
  - Add `app/communication/thread_store.py`, `app/communication/brief_store.py`, and a focused thread workspace regression script.
- Recommendation:
  - add owner files and keep `app/main.py` as API composition glue only.

## Corrective Spec Coverage Matrix

| Requirement | V2 task |
|---|---|
| Customer thread is the default work anchor | Task 1, Task 2, Task 3, Task 4 |
| `communication_thread` is the active customer object | Task 1 |
| `communication_brief` is stable and refreshable | Task 2 |
| `/agent/chat` is contextual Copilot endpoint | Task 3 |
| 8511 is thread inbox plus Copilot side panel | Task 4 |
| RAG grounds thread reply/follow-up | Task 5 |
| Meeting is a communication escalation path | Task 6 |
| Governance remains separate and auditable | Task 7 |
| Legacy chat-first carriers retire only after proof | Task 8 |
| Acceptance is Docker and browser proven | Task 9 |

## Tasks

### Task 1: Add communication thread source of truth

Files:

- Create `app/communication/thread_store.py`
- Modify `app/communication/types.py`
- Modify `app/communication/thread_context.py`
- Modify `app/inbound_mail_store.py`
- Modify `app/main.py`
- Modify `scripts/communication_copilot_regression.py`

Why:

- The spec defines `communication_thread` as the active customer communication object. The current implementation has a reference object and recent inbound mail helpers, but no durable actor-scoped active thread source of truth.

Impact / Compatibility:

- Adds a communication projection over existing inbound mail threads.
- Does not replace the inbound mail provider or Mail Agent provider abstraction.
- Does not change front-end visual style.

Verification:

- `docker compose exec -T api python -m compileall -q app scripts`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case thread_store`

Steps:

- [x] Write test: add `thread_store` to `scripts/communication_copilot_regression.py`; seed two inbound threads for two actors, list them, select one active thread, and verify actor/workspace isolation.
- [x] Verify RED: run `docker compose exec -T api python scripts/communication_copilot_regression.py --case thread_store` and confirm the case fails because the communication thread store and active selection do not exist.
- [x] Minimal code: create `app/communication/thread_store.py` with `init_communication_thread_store()`, `upsert_thread_projection()`, `list_communication_threads()`, `get_communication_thread()`, `set_active_communication_thread()`, and `get_active_communication_thread()`.
- [x] Minimal code: extend `CommunicationThreadRef` with fields needed for workspace display: `status`, `source_message_ids`, `latest_summary`, `risk_hint`, and `updated_at`.
- [x] Minimal code: wire `inbound_mail_store.list_recent_inbound_threads()` into thread projection refresh without duplicating raw body storage.
- [x] Minimal code: add internal API functions in `app/main.py` for thread list, thread detail, active thread selection, and active thread read.
- [x] Verify GREEN: rerun compile and the `thread_store` regression in Docker.
- [x] Commit: `git add app/communication app/inbound_mail_store.py app/main.py scripts/communication_copilot_regression.py && git commit -m "Add communication thread source of truth"`

Repair Track:

- Root cause: the first plan introduced a thread reference but not a durable active work object.
- Stable repair: actor-scoped thread registry and active thread binding under `app/communication/`.

Retirement Track:

- Old owner/fallback: deriving active thread only from recent debug payloads or recent inbound mail guesses.
- Trigger: remove debug-derived active thread as a source of truth after Task 3 and Task 4 pass.

### Task 2: Persist and refresh communication briefs

Files:

- Create `app/communication/brief_store.py`
- Modify `app/communication/brief_service.py`
- Modify `app/communication/observations.py`
- Modify `app/main.py`
- Modify `scripts/communication_copilot_regression.py`

Why:

- The spec calls `communication_brief` the stable intermediate object. The foundation slice assembled it per request, which is insufficient for refresh, thread switching, draft continuation, and side panel display.

Impact / Compatibility:

- Keeps the existing dataclass contract.
- Adds persisted snapshots and refresh metadata.
- Does not make the UI the source of truth.

Verification:

- `docker compose exec -T api python -m compileall -q app scripts`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case brief_store`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case brief_assembly`

Steps:

- [x] Write test: add `brief_store`; create an active thread, build a brief, persist it, refresh it after adding a new message, and verify the same actor can retrieve the latest brief while a different actor cannot.
- [x] Verify RED: run the new case and confirm there is no persisted brief lifecycle.
- [x] Minimal code: create `app/communication/brief_store.py` with `init_communication_brief_store()`, `upsert_communication_brief()`, `get_communication_brief()`, `get_latest_brief_for_thread()`, and `refresh_brief_for_thread()`.
- [x] Minimal code: update `brief_service` so brief refresh receives `thread_id`, `actor_context`, `employee_goal`, and grounding refs, then persists a snapshot.
- [x] Minimal code: update observation builders to include `brief_persistence_source`, `brief_version`, `thread_id`, and `refresh_reason`.
- [x] Verify GREEN: rerun compile, `brief_store`, and `brief_assembly`.
- [x] Commit: `git add app/communication app/main.py scripts/communication_copilot_regression.py && git commit -m "Persist communication briefs for thread workspace"`

Repair Track:

- Root cause: request-time brief assembly cannot support the thread-native workspace promised by the spec.
- Stable repair: persisted brief snapshots with actor/workspace isolation and refresh metadata.

Retirement Track:

- Old owner/fallback: per-turn debug payload as the only place where brief state exists.
- Trigger: stop using debug payload as the canonical brief source after Task 4 workspace state reads from the brief store.

### Task 3: Make `/agent/chat` a contextual Copilot endpoint

Files:

- Modify `app/main.py`
- Modify `app/orchestration/service.py`
- Modify `app/orchestration/observations.py`
- Modify `app/continuation_state.py`
- Modify `scripts/agent_runtime_regression.py`
- Modify `scripts/communication_copilot_regression.py`

Why:

- The spec says `/agent/chat` remains useful as a contextual interaction endpoint, not the product center. It must resolve active thread and brief before generic planning unless the user explicitly starts a global ask.

Impact / Compatibility:

- Keeps `/agent/chat` route and request shape compatible.
- Adds optional `thread_id` and `global_mode` handling without breaking current clients.
- Reduces stale draft and stale continuation traps by making active object resolution explicit.

Verification:

- `docker compose exec -T api python -m compileall -q app scripts`
- `docker compose exec -T api python scripts/agent_runtime_regression.py`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case contextual_chat`

Steps:

- [x] Write test: add `contextual_chat`; select an active thread, ask a grounded product question, request a reply draft, and verify the emitted observations reference the active thread and latest brief.
- [x] Write test: extend `contextual_chat` with a global ask that does not bind to the active thread and verify it is marked `global_entry`.
- [x] Verify RED: run the case and confirm `/agent/chat` can still operate without stable active object resolution.
- [x] Minimal code: add an active object resolution step before planner execution in `app/main.py`.
- [x] Minimal code: emit typed observations for `active_communication_thread`, `communication_brief`, `global_entry`, and `active_object_resolution_failed`.
- [x] Minimal code: update continuation state so a stale mail draft cannot hijack a fresh thread-grounded question.
- [x] Verify GREEN: rerun compile, agent runtime regression, and `contextual_chat`.
- [x] Commit: `git add app/main.py app/orchestration app/continuation_state.py scripts/agent_runtime_regression.py scripts/communication_copilot_regression.py && git commit -m "Make agent chat contextual to communication threads"`

Repair Track:

- Root cause: `/agent/chat` still behaves like a general chat route in important paths.
- Stable repair: active thread and brief resolution become a first-class pre-planner step.

Retirement Track:

- Old owner/fallback: chat-first route fallback and stale pending-object dominance.
- Trigger: remove chat-first fallback once Task 9 proves thread and global entries both work.

### Task 4: Reframe 8511 as thread inbox plus Copilot side panel

Files:

- Modify `web/streamlit_app.py`
- Modify `app/main.py`
- Modify `scripts/communication_copilot_regression.py`
- Create `scripts/communication_workspace_browser_check.py`

Why:

- The spec explicitly prefers a thread inbox plus Copilot side panel. The existing 8511 surface shows useful panels, but it is not yet organized around selecting and working inside a customer thread.

Impact / Compatibility:

- Changes state and flow only.
- Does not change the visual style, typography, theme, or CSS direction without approval.
- Keeps governance and raw diagnostics separate.

Verification:

- `docker compose exec -T api python -m compileall -q app scripts web`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case workspace_thread_inbox`
- `docker compose exec -T api python scripts/communication_workspace_browser_check.py --url http://web:8501`
- Manual browser QA at `http://localhost:8511/`

Steps:

- [x] Write test: add `workspace_thread_inbox`; assert thread list, selected thread detail, active brief, Copilot input context, draft preview state, and task progress can be loaded from backend state.
- [x] Verify RED: run the workspace case and confirm the current workspace is not a full thread inbox plus side panel state flow.
- [x] Minimal code: add API calls in `web/streamlit_app.py` for thread list, thread selection, thread detail, brief refresh, and contextual Copilot submit.
- [x] Minimal code: organize existing visible areas into thread list, thread/context detail, and Copilot/action panel while preserving the current visual styling.
- [x] Minimal code: keep task progress on 8511 sender-facing only; keep high-risk approval and raw diagnostics in 8512.
- [x] Verify GREEN: rerun compile and `workspace_thread_inbox`.
- [x] Verify browser: run the browser check and manually confirm thread selection survives refresh.
- [x] Commit: `git add web/streamlit_app.py app/main.py scripts/communication_copilot_regression.py scripts/communication_workspace_browser_check.py && git commit -m "Reframe user workspace around communication threads"`

Repair Track:

- Root cause: old plan compressed a product surface requirement into a vague workspace task.
- Stable repair: real thread list, thread detail, and Copilot action panel backed by communication store state.

Retirement Track:

- Old owner/fallback: debug-derived workspace cards and chat-shell-first entry.
- Trigger: demote them after thread inbox acceptance passes.

### Task 5: Thread-scoped grounding to reply and follow-up

Files:

- Modify `app/enterprise_rag/core/service.py`
- Modify `app/communication/brief_service.py`
- Modify `app/mail/source_resolver.py`
- Modify `app/main.py`
- Modify `scripts/communication_copilot_regression.py`
- Modify `scripts/enterprise_rag_regression.py`

Why:

- RAG is subordinate to communication closeout. It should provide grounded facts into the active thread's brief and then Mail Agent should turn the brief into an outbound artifact.

Impact / Compatibility:

- Keeps dense/sparse/reranker architecture unchanged.
- Does not hard-code answer text.
- Preserves citations and evidence limits.

Verification:

- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case grounded_reply_from_thread`
- `docker compose exec -T api python scripts/mail_reference_authoring_regression.py`

Steps:

- [x] Write test: add `grounded_reply_from_thread`; select a thread, ask a product question, refresh the brief with grounding refs, request an email reply, and verify the draft body is non-empty, non-duplicated, citation-backed, and scoped to the active thread.
- [x] Verify RED: run the case and confirm current paths can still draft from a raw answer artifact without stable active thread/brief binding.
- [x] Minimal code: convert EnterpriseRAG output into grounding refs on the active brief, including citation ids and missing aspects.
- [x] Minimal code: update Mail source resolution so `communication_brief` remains the preferred closeout source and raw answer artifacts are fallback-only compatibility shims.
- [x] Minimal code: prevent unrelated assistant greetings, duplicate answer bodies, and old conversation summaries from entering the external body source.
- [x] Verify GREEN: rerun RAG, grounded reply, and mail reference regressions.
- [x] Commit: `git add app/enterprise_rag/core/service.py app/communication app/mail/source_resolver.py app/main.py scripts/communication_copilot_regression.py scripts/enterprise_rag_regression.py && git commit -m "Ground thread replies through communication briefs"`

Repair Track:

- Root cause: prior mail/RAG integration could still close out from raw answer text instead of stable communication context.
- Stable repair: thread-scoped grounding enters the brief; Mail Agent closes out from the brief.

Retirement Track:

- Old owner/fallback: raw RAG answer artifact as direct external mail body source.
- Trigger: keep only for explicit compatibility until Task 8 retirement.

### Task 6: Thread-scoped meeting escalation

Files:

- Modify `app/orchestration/domain_agents.py`
- Modify `app/orchestration/dag_executor.py`
- Modify `app/task_worker.py`
- Modify `app/communication/brief_service.py`
- Modify `app/main.py`
- Modify `scripts/meeting_worker_regression.py`
- Modify `scripts/cross_domain_workflow_regression.py`
- Modify `scripts/communication_copilot_regression.py`

Why:

- Meeting is a communication upgrade path, not a separate generic tool demo. Meeting proposals and results should attach to the active thread and brief.

Impact / Compatibility:

- Keeps Tencent Meeting provider boundary and confirmation requirements.
- Does not bypass side-effect controls.

Verification:

- `docker compose exec -T api python scripts/meeting_worker_regression.py`
- `docker compose exec -T api python scripts/cross_domain_workflow_regression.py`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case thread_meeting_escalation`

Steps:

- [x] Write test: add `thread_meeting_escalation`; start from a selected thread and brief, request a meeting, verify the meeting proposal references the brief, requires confirmation, and does not create a provider object before confirmation.
- [x] Write test: complete the async meeting worker path and verify meeting result can update the brief and produce a follow-up mail draft candidate.
- [x] Verify RED: run the case and confirm meeting output is not fully anchored to active thread and brief state.
- [x] Minimal code: require `source_brief_id`, `thread_id`, `actor_context`, and idempotency key on meeting escalation tasks.
- [x] Minimal code: write meeting result observations back to the brief store as next-action state.
- [x] Minimal code: expose meeting result as a Mail Agent closeout candidate without making Meeting Agent own final wording.
- [x] Verify GREEN: rerun meeting, cross-domain, and thread meeting regressions.
- [x] Commit: `git add app/orchestration app/task_worker.py app/communication app/main.py scripts/meeting_worker_regression.py scripts/cross_domain_workflow_regression.py scripts/communication_copilot_regression.py && git commit -m "Anchor meeting escalation to communication threads"`

Repair Track:

- Root cause: meeting flow can work as a tool path without fully proving it is subordinate to communication closeout.
- Stable repair: meeting proposal and result are attached to thread/brief state and returned as typed observations.

Retirement Track:

- Old owner/fallback: generic meeting creation flow that is disconnected from communication closeout.
- Trigger: keep only if explicitly invoked as global tool mode; otherwise require thread/brief context.

### Task 7: Governance and recovery in thread-native flow

Files:

- Modify `app/task_store.py`
- Modify `app/task_worker.py`
- Modify `app/main.py`
- Modify `web/streamlit_app.py`
- Modify `web/governance_console.py`
- Modify `scripts/mail_m6_ui_governance_regression.py`
- Modify `scripts/communication_copilot_regression.py`

Why:

- Governance already exists, but spec acceptance requires recovery and audit inside the communication closeout loop, not only standalone mail scenarios.

Impact / Compatibility:

- Preserves 8511/8512 separation.
- Keeps sender safety confirmation on 8511 and high-risk exception approval on 8512.
- Does not expose raw diagnostics on 8511.

Verification:

- `docker compose exec -T api python scripts/mail_m6_ui_governance_regression.py`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case thread_governance_recovery`

Steps:

- [x] Write test: add `thread_governance_recovery`; from an active thread, create a draft, trigger DLP medium risk, perform sender safety confirmation, and verify task audit references thread and brief ids.
- [x] Write test: trigger high risk, provider failure, SMTP failure, and worker unavailable states; verify each returns recovery observations tied to the communication thread.
- [x] Verify RED: run the case and confirm at least one recovery path lacks thread/brief provenance.
- [x] Minimal code: add `thread_id`, `brief_id`, and `communication_context` to governed delivery task metadata.
- [x] Minimal code: ensure 8511 displays sender-facing progress and 8512 displays approval/recovery detail without changing visual style.
- [x] Verify GREEN: rerun governance and thread recovery regressions.
- [x] Commit: `git add app/task_store.py app/task_worker.py app/main.py web/streamlit_app.py web/governance_console.py scripts/mail_m6_ui_governance_regression.py scripts/communication_copilot_regression.py && git commit -m "Tie governance recovery to communication threads"`

Repair Track:

- Root cause: governance state can be correct but detached from the thread-native product flow.
- Stable repair: every governed side effect carries thread and brief provenance.

Retirement Track:

- Old owner/fallback: task-only user progress that cannot explain which communication thread produced the side effect.
- Trigger: demote after thread recovery acceptance passes.

### Task 8: Retire superseded chat-first and raw artifact carriers

Files:

- Modify `app/main.py`
- Modify `app/orchestration/service.py`
- Modify `app/orchestration/registry.py`
- Modify `app/mail/source_resolver.py`
- Modify `problem_todolist.md`
- Modify `todolist.md`
- Modify `README.md`

Why:

- After the thread-native owner path exists, legacy carriers should not remain active runtime owners.

Impact / Compatibility:

- Medium runtime risk.
- Requires all prior Docker regressions to pass before deletion.
- Keeps explicit global ask mode and governance recovery mode.

Verification:

- `docker compose exec -T api python -m compileall -q app scripts web`
- `docker compose exec -T api python scripts/agent_runtime_regression.py`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case all`
- `docker compose exec -T api python scripts/mail_authoring_contract_regression.py`
- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`
- `rg -n "chat-first|legacy_orchestration|legacy_aggregator|raw answer artifact|assistant_last_answer_default" app README.md problem_todolist.md todolist.md`

Steps:

- [x] Write test: add `legacy_retirement_thread_native`; assert thread-native closeout does not depend on raw answer artifact fallback or chat-first carrier paths.
- [x] Verify RED: run the retirement search and regression to identify remaining active carriers.
- [x] Minimal code: delete carriers proven safe by Tasks 1-7; convert any still-needed boundary into a named compatibility shim with replacement owner and removal trigger.
- [x] Minimal code: update `problem_todolist.md`, `todolist.md`, and `README.md` to reflect the new thread-native state.
- [x] Verify GREEN: rerun compile, all communication regression cases, mail, RAG, and search verification.
- [x] Commit: `git add app README.md problem_todolist.md todolist.md scripts/communication_copilot_regression.py scripts/continuation_state_regression.py scripts/mail_meeting_source_candidate_regression.py scripts/mail_thread_source_candidate_regression.py && git commit -m "Retire chat-first communication carriers"`

Repair Track:

- Root cause: old plan allowed compatibility shims to survive because their replacement product path was incomplete.
- Stable repair: delete only after thread-native path is green.

Retirement Track:

- Old owner/fallback: raw answer artifact closeout, chat-first path naming, and debug payload source-of-truth.
- Trigger: completion of Tasks 1-7.

### Task 9: Full Docker and browser acceptance gate

Files:

- Modify `scripts/communication_copilot_regression.py`
- Modify `scripts/communication_workspace_browser_check.py`
- Modify `docs/aegis/work/2026-06-17-communication-copilot-spec-plan-gap-audit.md`
- Modify `todolist.md`
- Modify `problem_todolist.md`

Why:

- The old plan failed because it did not prove spec coverage. Final acceptance must prove every high-level spec requirement against the thread-native flow.

Impact / Compatibility:

- No new feature code unless a regression exposes a missing acceptance signal.
- Produces evidence for completion and avoids another false sense of done.

Verification:

- `docker compose ps api worker redis postgres chroma`
- `docker compose exec -T api python -m compileall -q app scripts web`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case all`
- `docker compose exec -T api python scripts/mail_m6_ui_governance_regression.py`
- `docker compose exec -T api python scripts/cross_domain_workflow_regression.py`
- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`
- Browser QA at `http://localhost:8511/`

Steps:

- [x] Write test: add an acceptance summary mode to `communication_copilot_regression.py` that reports thread inbox, active thread, brief persistence, grounded reply, meeting escalation, governed send, recovery, and retirement status.
- [x] Verify RED: run the summary and confirm any missing spec item is reported as not covered.
- [x] Minimal code: add only missing test harness signals or tracker updates required to make acceptance evidence explicit.
- [x] Verify GREEN: run the full Docker command set and browser QA.
- [x] Update `docs/aegis/work/2026-06-17-communication-copilot-spec-plan-gap-audit.md` with final evidence links and residual risks.
- [x] Commit: `git add scripts docs/aegis/work todolist.md problem_todolist.md && git commit -m "Record thread-native Communication Copilot acceptance"`

Repair Track:

- Root cause: prior self-review claimed spec coverage without evidence.
- Stable repair: the acceptance gate is itself a spec coverage matrix.

Retirement Track:

- Old owner/fallback: plan-only claims of coverage.
- Trigger: all matrix rows have evidence from Docker regression or browser QA.

## Required Browser QA Scenarios

Browser QA must preserve the current visual style and test state flow, not redesign the UI.

- Open `http://localhost:8511/`.
- Sync or seed inbound mail.
- Select a customer thread from the thread inbox.
- Confirm the thread detail pane shows sanitized thread context.
- Ask a grounded product/company question while the thread is active.
- Confirm the Copilot panel shows a brief-backed answer or suggestion.
- Ask to draft a reply from the grounded result.
- Confirm draft preview is non-empty, non-duplicated, and scoped to the selected thread.
- Refresh the page and verify selected thread and latest brief recover.
- Confirm a medium-risk send on 8511 and verify audit.
- Trigger high-risk send and verify 8511 waits for governance while 8512 owns approval.
- Trigger provider failure and verify recovery observation instead of fake success.
- Escalate the thread to a meeting and verify no meeting is created before confirmation.

## Risks

- Persisting communication briefs can create duplicate truth if brief refresh does not clearly record source observations and version.
- Thread inbox UI work can accidentally become a redesign. The plan explicitly forbids visual style changes.
- Adding active thread resolution can break global ask workflows if explicit global mode is not preserved.
- Removing raw answer artifact closeout too early can break existing mail convenience paths.

## Retirement

- Retire debug payload as source-of-truth for active thread and brief.
- Retire raw answer artifact direct closeout after thread-grounded brief closeout passes.
- Retire chat-first product language from active docs and runtime diagnostics.
- Keep historical references only in audit or archive context.

## Self-Review

- Spec coverage: every high-level requirement from the approved design spec is mapped to a V2 task in the corrective coverage matrix.
- Vague-marker scan: no unresolved planning markers are used.
- Type consistency: new stores extend existing `app/communication/` dataclass contracts and actor-context patterns.
- Compatibility: Docker-only verification, observation-first wording, governance boundaries, RAG evidence boundaries, and UI style preservation are explicit.
- Complexity: new source-of-truth logic goes into `app/communication/` rather than expanding `app/main.py`.
- Architecture integrity: active thread and brief persistence are the missing higher-level owners.
- Verification: every task has exact Docker commands and final browser QA scenarios.
- Dual-track: each task includes repair and retirement tracks.

## Execution Options

Recommended execution:

- Use `aegis:subagent-driven-development`.
- Dispatch one subagent per task.
- Review after each task before starting the next.
- Do not merge Task 8 retirement until Tasks 1-7 are green in Docker.

Inline execution is possible, but it is riskier because `app/main.py`, `web/streamlit_app.py`, and communication stores are high-overlap areas.
