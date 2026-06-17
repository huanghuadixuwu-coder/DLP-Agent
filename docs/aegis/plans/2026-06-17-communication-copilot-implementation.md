# Communication Copilot Implementation Plan

Goal:

- Implement the approved Communication Copilot design so the project becomes a
  Mail-Agent-centered external communication Copilot for internal employees,
  with thread/context as the dominant work anchor and EnterpriseRAG serving as
  grounding support.

Architecture:

- Agent-first runtime
- Mail Agent as communication closeout owner
- Supervisor as orchestration owner
- EnterpriseRAG as grounding owner
- Meeting Agent as escalation owner
- Governance/DLP as side-effect boundary owner
- `communication_thread -> communication_brief -> mail_draft ->
  governed_delivery_task` as the main business flow

Tech Stack:

- Python
- FastAPI
- Streamlit
- Celery + Redis
- PostgreSQL + SQLite stores + Chroma
- Docker Compose

Baseline/Authority Refs:

- `docs/aegis/specs/2026-06-16-communication-copilot-design.md`
- `docs/aegis/baseline/2026-06-16-initial-baseline.md`
- `docs/aegis/BASELINE-GOVERNANCE.md`
- `总体要求.md`
- `spec/MAIL_AGENT_V2_SPEC.md`
- `problem_todolist.md`
- `todolist.md`

Compatibility Boundary:

- Verification stays Docker-only, using local Docker Compose as the active
  acceptance baseline.
- User-visible wording remains LLM-rendered from observations.
- Side effects remain gated by permission, confirmation, DLP/governance, and
  auditable task state.
- Enterprise facts continue to require grounded evidence from EnterpriseRAG.
- Existing Mail Agent V2 contracts remain valid unless explicitly replaced.

Confirmed implementation decisions:

- Legacy cleanup: Task 0B may hard-delete only `dead_identity` and `safe_delete`
  items. Any path still used by Mail/RAG/DLP runtime must become an explicit
  `compat_shim` until its replacement owner path passes Docker regressions.
- `communication_brief` lifecycle: v1 is an internal `@dataclass(slots=True)`
  object assembled per request. It does not get a new database table; only
  references and debug/provenance metadata may be copied into observations or
  task metadata.
- 8511 UI timing: do not reshape the workspace until Task 2 through Task 5
  backend contracts and Docker regressions are green. When UI work begins,
  preserve the existing visual style and change only required state/flow
  behavior.

Verification:

- `docker compose ps api worker redis postgres chroma`; if services are stopped,
  run `docker compose up -d` before Docker verification.
- `docker compose exec -T api python -m compileall -q app scripts web`
- Existing regression scripts remain the first safety net.
- New refactor work should add local-Docker regressions rather than rely on
  ad hoc manual checks.
- Browser QA uses Standard scope after each runtime-facing phase: core 8511
  communication workflows, console/network errors, task state, governance
  visibility, and recovery behavior.

## Plan Basis

Facts:

- The top-level design spec is approved.
- Mail and DLP runtime paths are already more mature than the overall product
  framing.
- `app/main.py` is the current composition root and also holds too much
  behavior.
- The codebase still contains historical LeetCode naming and legacy
  orchestration/fallback carriers.
- The active acceptance environment is local Docker Compose.

Assumptions:

- We can preserve most current Mail/DLP/RAG capabilities by re-owning them
  under the new Communication Copilot baseline instead of rewriting them from
  scratch.
- Thread and communication-brief concepts can be introduced incrementally
  without breaking the current worker/task lifecycle.

Unknowns:

- How much existing inbox/thread data normalization already maps cleanly onto
  `communication_thread`.
- Whether the Streamlit workspace should be incrementally reshaped or split by
  feature flags during the migration.

Ripple Signal Triage:

- `app/main.py` changes will affect routing, draft continuation, and API
  responses.
- `app/orchestration/service.py` and `app/orchestration/registry.py` changes
  will affect fallback behavior and user-visible wording paths.
- `web/streamlit_app.py` changes will affect the primary user workspace.

Dual-track need:

- Yes. This work needs both a repair track and a retirement track.
- Retirement starts first, but as inventory/quarantine rather than blind
  deletion: remove dead identity/wording immediately, and put active legacy
  carriers behind regression coverage before deleting them.
- Repair track: introduce the new canonical owner hierarchy and objects.
- Final retirement track: delete active legacy carriers only after replacement
  owner paths are green in Docker.

## Files

Expected new owner files:

- `app/communication/__init__.py`
- `app/communication/types.py`
- `app/communication/thread_context.py`
- `app/communication/brief_service.py`
- `app/communication/observations.py`
- `scripts/communication_copilot_regression.py`

Expected modified files:

- `app/main.py`
- `app/orchestration/service.py`
- `app/orchestration/registry.py`
- `app/orchestration/final_renderer.py`
- `app/orchestration/observations.py`
- `app/inbound_mail_store.py`
- `app/mail/source_resolver.py`
- `app/mail/domain.py`
- `app/enterprise_rag/core/service.py`
- `web/streamlit_app.py`
- `README.md`
- `problem_todolist.md`
- `todolist.md`

Expected retirement-review files:

- `app/__init__.py`
- `app/config.py`
- `app/hermes_memory.py`
- `app/orchestration/tools/orchestration_tools.py`

## Architecture Integrity Lens

Invariant:

- Mail Agent is the only canonical communication closeout owner.

Canonical owner / contract:

- `communication_thread` is the dominant work object.
- `communication_brief` is the canonical intermediate object.
- `mail_draft` remains the governed outbound artifact.
- `governed_delivery_task` remains the execution object for side effects.

Responsibility overlap:

- Today overlap exists between thread summary handling, RAG answer ownership,
  and mail source resolution inside `app/main.py` and orchestration layers.

Higher-level simplification:

- Introduce a dedicated `app/communication/` owner package instead of adding
  more cross-cutting logic to `app/main.py`.

Retirement / falsifier:

- If new behavior still requires direct chat-first business branching in
  `app/orchestration/registry.py`, the owner split is incomplete.

Verdict:

- Proceed, but add a new owner package and plan retirement from the current
  chat-first carriers.

## Plan Pressure Test

- Owner / contract / retirement:
  - Clear owner shift is required and old carriers need a retirement track.
- Architecture integrity / higher-level path:
  - A dedicated communication package is the cleaner path than adding more
    branches to `app/main.py`.
- Verification scope:
  - Must cover thread-driven drafting, grounding-backed drafting, meeting
    escalation, governed send, and regression against legacy behavior.
- Task executability:
  - Executable if we phase by legacy quarantine, contract, orchestration, UI,
    then final retirement.
- Pressure result:
  - proceed, with legacy carrier quarantine as the first implementation step

## Plan-Time Complexity Check

- Target files:
  - `app/main.py`
  - `app/orchestration/service.py`
  - `app/orchestration/registry.py`
  - `web/streamlit_app.py`
- Existing size / shape signals:
  - `app/main.py` is a very large mixed-responsibility file.
  - Orchestration layers still contain legacy wording and fallback carriers.
- Owner fit:
  - New communication domain concepts do not fit cleanly as more helpers inside
    `app/main.py`.
- Add-in-place risk:
  - High. Adding more logic in place will preserve the exact complexity this
    refactor is supposed to reduce.
- Better file boundary:
  - Add `app/communication/` and move new canonical contracts there.
- Recommendation:
  - add owner file

## Legacy Search Policy

Denylist:

- Runtime code must not expose historical product identity or old closeout
  ownership: `LeetCode`, `RAG Agent`, `chat-first`, `legacy_aggregator`,
  `legacy_orchestration`.
- User-visible docs and active product framing must not describe the system as a
  LeetCode agent, generic chat shell, or RAG-first product.

Allowlist:

- Historical migration notes may mention removed LeetCode V1 behavior if they
  are clearly labeled as historical.
- Completed todolist entries may mention already-removed legacy APIs.
- Baseline/audit docs may mention old names as evidence, not as active product
  identity.

Verification rule:

- Search results are acceptable only when each hit is allowlisted historical
  context or a quarantined compatibility shim with an explicit replacement owner
  and removal trigger.

## Tasks

### Task 0A: Legacy carrier inventory report

Files:

- Modify `problem_todolist.md`
- Modify `todolist.md`

Why:

- Starting with legacy cleanup is directionally correct because old chat-first
  and product-identity paths keep pulling new work back into the previous
  architecture.
- The first step must be inventory only: classify each carrier before any
  runtime deletion so Mail/DLP/RAG behavior does not break accidentally.

Impact / Compatibility:

- No runtime code changes.
- No front-end style changes.

Verification:

- `rg -n "LeetCode|legacy_orchestration|legacy_aggregator|RAG Agent|chat-first" app README.md todolist.md problem_todolist.md`
- `rg -n "final_answer_source|mode_used|enable_legacy_orchestration_fallback|legacy_" app`
- `git diff --check -- problem_todolist.md todolist.md`

Steps:

- [ ] Produce a legacy carrier inventory table with `carrier`, `location`, `category`, `current behavior`, `replacement owner`, `required regression`, and `removal trigger`.
- [ ] Categories are fixed as `dead_identity`, `safe_delete`, `compat_shim`, or `active_runtime_dependency`.
- [ ] Identify current Mail/DLP/RAG regressions that protect any `active_runtime_dependency`.
- [ ] Record the inventory in `problem_todolist.md` and implementation status in `todolist.md`.
- [ ] Commit: `git add problem_todolist.md todolist.md && git commit -m "Inventory legacy carriers before communication refactor"`

Repair Track:

- Root cause: old carriers are not merely cosmetic; some still influence routing,
  fallback wording, and closeout ownership.
- Stable repair: separate dead legacy from active compatibility before touching
  runtime code.

Retirement Track:

- Old owner/fallback: historical identity, chat-first wording, direct answer
  closeout, and legacy orchestration fallback carriers.
- Trigger: Task 0B may only delete or quarantine items classified by this
  inventory.

### Task 0B: Safe-delete and quarantine classified legacy carriers

Files:

- Modify `app/orchestration/registry.py`
- Modify `app/orchestration/service.py`
- Modify `app/main.py`
- Modify `app/config.py`
- Modify `README.md`
- Modify `problem_todolist.md`
- Modify `todolist.md`

Why:

- After Task 0A classifies the carriers, dead identity and safe-delete items can
  be removed immediately.
- Active runtime dependencies must be quarantined as explicit compatibility
  shims with replacement owner and removal trigger, not silently left behind.

Impact / Compatibility:

- Low runtime impact for `dead_identity` and `safe_delete` categories.
- Medium runtime impact for `compat_shim`; must preserve current Mail/DLP/RAG
  behavior under existing regressions.
- No front-end style changes.

Verification:

- `docker compose ps api worker redis postgres chroma`; if services are stopped, run `docker compose up -d` before Docker verification.
- `docker compose exec -T api python -m compileall -q app scripts web`
- `docker compose exec -T api python scripts/agent_runtime_regression.py`
- `docker compose exec -T api python scripts/mail_authoring_contract_regression.py`
- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`
- `rg -n "LeetCode|legacy_orchestration|legacy_aggregator|RAG Agent|chat-first" app README.md todolist.md problem_todolist.md`

Steps:

- [ ] Verify RED: run Task 0A searches and regressions to confirm the classified carriers still exist.
- [ ] Minimal cleanup: delete only `dead_identity` and `safe_delete` items from the inventory.
- [ ] Quarantine `compat_shim` items with comments or explicit diagnostics naming replacement owner and removal trigger.
- [ ] Do not delete `active_runtime_dependency` items until their replacement owner path exists and has passing Docker regressions.
- [ ] Verify GREEN: rerun compile, runtime regression, mail regression, RAG regression, and legacy search.
- [ ] Commit: `git add app README.md todolist.md problem_todolist.md && git commit -m "Quarantine legacy carriers before communication refactor"`

Repair Track:

- Root cause: old carriers are not merely cosmetic; some still influence routing,
  fallback wording, and closeout ownership.
- Stable repair: remove dead legacy now and make surviving compatibility explicit
  until the new owner path replaces it.

Retirement Track:

- Old owner/fallback: historical identity, chat-first wording, direct answer
  closeout, and legacy orchestration fallback carriers.
- Trigger: immediately delete dead carriers; defer active carrier deletion until
  the replacement communication owner path is proven.

### Task 1: Reset top-level product framing and freeze the migration boundary

Files:

- Modify `README.md`
- Modify `todolist.md`
- Modify `problem_todolist.md`

Why:

- The code already contains stronger communication workflows than the current
  top-level product story. The docs need to stop describing a chat-first or
  RAG-first system before implementation starts moving.

Impact / Compatibility:

- No runtime impact.
- Prevents design drift during implementation.

Verification:

- `rg -n "LeetCode|chat-first|RAG Agent|统一聊天入口" README.md todolist.md problem_todolist.md`
- `git diff --check -- README.md todolist.md problem_todolist.md`

Steps:

- [ ] Write test: define the wording targets and red flags in the three docs.
- [ ] Verify RED: search output still shows product-center wording that conflicts with the approved spec.
- [ ] Minimal code: update wording so the Communication Copilot story, local Docker baseline, and Mail-Agent-centered ownership are the dominant top-level framing.
- [ ] Verify GREEN: rerun the `rg` and `git diff --check` commands and confirm only intentional historical references remain.
- [ ] Commit: `git add README.md todolist.md problem_todolist.md && git commit -m "Reframe product docs around communication copilot"`

Repair Track:

- Root cause: docs and trackers lag behind the approved product baseline.
- Stable repair: update only product framing and baseline text, not runtime details.

Retirement Track:

- Old owner/fallback: chat-first product framing in top-level docs.
- Trigger: remove or demote it as soon as the new framing is written.

### Task 2: Introduce the canonical communication domain contracts

Files:

- Create `app/communication/__init__.py`
- Create `app/communication/types.py`
- Create `app/communication/observations.py`
- Create `scripts/communication_copilot_regression.py`

Why:

- The refactor needs a single canonical place for `communication_thread`,
  `communication_brief`, `meeting_escalation_candidate`, and any supporting
  observation shapes.

Impact / Compatibility:

- Adds new internal contracts without changing the current API surface yet.
- Keeps future work from encoding these concepts ad hoc in `app/main.py`.
- Uses internal `@dataclass(slots=True)` contracts, matching existing
  `app/mail/domain.py`, `app/orchestration/types.py`, and
  `app/enterprise_rag/core/types.py` style.
- Does not create a new database table in v1. `communication_brief` is assembled
  per request and may be copied into observations/task metadata for traceability.

V1 contract defaults:

- `CommunicationThreadRef`: `thread_id`, `source`, `subject`, `participants`,
  `last_message_at`, `actor_context`.
- `CommunicationBrief`: `brief_id`, `conversation_id`, `thread_ref`,
  `employee_goal`, `customer_context_summary`, `grounding_refs`, `must_include`,
  `must_avoid`, `open_questions`, `recommended_next_action`,
  `source_observation_ids`, `confidence`, `created_at`, `actor_context`.
- `MeetingEscalationCandidate`: `candidate_id`, `source_brief_id`, `topic`,
  `attendees`, `time_window`, `reason`, `confirmation_required`.
- Observation builders serialize these dataclasses with `asdict(...)` into
  `TypedObservation.payload`; user-visible wording remains renderer-owned.

Verification:

- `docker compose exec -T api python -m compileall -q app`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case contracts_import`
- `rg -n "communication_brief|communication_thread" app`

Steps:

- [ ] Write test: create `scripts/communication_copilot_regression.py` with a minimal `contracts_import` case that imports the new types and observation builders.
- [ ] Verify RED: run compile/import verification and confirm the symbols do not exist yet.
- [ ] Minimal code: add internal dataclass contracts and observation builders under `app/communication/`.
- [ ] Verify GREEN: rerun compile/import verification and confirm the new symbols are present and importable.
- [ ] Commit: `git add app/communication scripts/communication_copilot_regression.py && git commit -m "Add communication domain contracts"`

Repair Track:

- Root cause: no canonical owner exists for the new work objects from the approved spec.
- Stable repair: add a dedicated owner package instead of expanding `app/main.py`.

Retirement Track:

- Old owner/fallback: scattered thread summary and response intent handling in orchestration/main helpers.
- Trigger: once the new contracts are in use, old scattered carriers should be retired.

### Task 3: Build communication-thread resolution and brief assembly

Files:

- Create `app/communication/thread_context.py`
- Create `app/communication/brief_service.py`
- Modify `app/inbound_mail_store.py`
- Modify `app/conversation_memory.py`
- Modify `app/enterprise_rag/core/service.py`

Why:

- The runtime needs a clean path that resolves the active communication object
  and assembles a stable `communication_brief` from thread context, grounding,
  and current employee intent.

Impact / Compatibility:

- New internal assembly layer.
- Existing mail and RAG flows should remain callable while the brief path is
  introduced.

Verification:

- `docker compose exec -T api python -m compileall -q app scripts`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case brief_assembly`

Steps:

- [ ] Write test: add a local-Docker regression that builds a brief from a representative thread plus a grounded question.
- [ ] Verify RED: run the regression and confirm no brief assembly path exists.
- [ ] Minimal code: implement thread-context loading, grounding bundle wiring, and brief assembly services under `app/communication/`.
- [ ] Verify GREEN: rerun compile and the brief regression until the brief object is emitted with the expected refs and no direct user wording leakage.
- [ ] Commit: `git add app/communication app/inbound_mail_store.py app/conversation_memory.py app/enterprise_rag/core/service.py scripts/communication_copilot_regression.py && git commit -m "Assemble communication briefs from thread and grounding"`

Repair Track:

- Root cause: RAG, thread, and memory context are currently merged too late and too inconsistently.
- Stable repair: assemble a single canonical brief before Mail Agent closeout.

Retirement Track:

- Old owner/fallback: direct thread-summary-to-draft or RAG-answer-to-draft flows.
- Trigger: remove ad hoc callsites after the brief path is adopted.

### Task 4: Make Mail Agent consume communication briefs as the closeout owner

Files:

- Modify `app/mail/domain.py`
- Modify `app/mail/source_resolver.py`
- Modify `app/main.py`
- Modify `app/orchestration/observations.py`
- Modify `app/orchestration/final_renderer.py`

Why:

- Mail Agent has to become the unambiguous closeout owner instead of receiving
  scattered upstream artifacts and deciding too much from raw thread/RAG state.

Impact / Compatibility:

- Changes internal flow into draft creation and preview.
- Must preserve current draft/patch/confirm/DLP lifecycle.

Verification:

- `docker compose exec -T api python scripts/mail_authoring_contract_regression.py`
- `docker compose exec -T api python scripts/mail_reference_authoring_regression.py`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case mail_closeout`

Steps:

- [ ] Write test: extend mail authoring regressions to accept a communication brief as the preferred source contract.
- [ ] Verify RED: run the selected regressions and confirm Mail Agent still depends on older fragmented source paths.
- [ ] Minimal code: thread the brief into Mail Agent closeout, keep drafts observation-first, and keep renderer input grounded in brief plus observations.
- [ ] Verify GREEN: rerun the regressions and confirm draft creation, patch, confirmation, and body rendering still work with the new closeout contract.
- [ ] Commit: `git add app/mail/domain.py app/mail/source_resolver.py app/main.py app/orchestration/observations.py app/orchestration/final_renderer.py scripts/communication_copilot_regression.py && git commit -m "Make Mail Agent the communication closeout owner"`

Repair Track:

- Root cause: communication closeout ownership is currently split across mail, RAG output, and orchestration.
- Stable repair: make Mail Agent consume `communication_brief` directly.

Retirement Track:

- Old owner/fallback: direct raw answer artifact closeout paths that bypass the brief model.
- Trigger: demote or delete once brief-driven paths are green.

### Task 5: Re-scope EnterpriseRAG and Meeting Agent to subordinate roles

Files:

- Modify `app/enterprise_rag/core/service.py`
- Modify `app/orchestration/domain_agents.py`
- Modify `app/orchestration/dag_executor.py`
- Modify `app/main.py`
- Modify `app/task_worker.py`

Why:

- EnterpriseRAG should stay the grounding owner, and Meeting Agent should stay
  an escalation owner. Neither should behave like the final closeout owner.

Impact / Compatibility:

- Mostly internal contract cleanup.
- Must preserve current evidence quality and current meeting worker behavior.

Verification:

- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`
- `docker compose exec -T api python scripts/meeting_worker_regression.py`
- `docker compose exec -T api python scripts/cross_domain_workflow_regression.py`

Steps:

- [ ] Write test: add assertions in cross-domain and RAG regressions that the produced state is a grounding bundle or escalation candidate, not a final communication owner artifact.
- [ ] Verify RED: run the regressions and confirm at least one path still treats RAG or meeting output as the direct closeout owner.
- [ ] Minimal code: normalize subordinate contracts and ensure orchestration passes them into brief or Mail Agent closeout rather than surfacing them as final business owners.
- [ ] Verify GREEN: rerun RAG, meeting, and cross-domain regressions until ownership boundaries are visible in the observations and outputs.
- [ ] Commit: `git add app/enterprise_rag/core/service.py app/orchestration/domain_agents.py app/orchestration/dag_executor.py app/main.py app/task_worker.py scripts/communication_copilot_regression.py && git commit -m "Normalize RAG and meeting as subordinate communication providers"`

Repair Track:

- Root cause: product ownership and closeout ownership are still blurred.
- Stable repair: preserve capabilities while demoting their business role.

Retirement Track:

- Old owner/fallback: paths where RAG output behaves like the end product.
- Trigger: delete or narrow those paths after regression coverage lands.

### Task 6: Reframe `/agent/chat` and the user workspace around communication work

Files:

- Modify `app/main.py`
- Modify `web/streamlit_app.py`
- Modify `web/governance_console.py`
- Modify `app/orchestration/service.py`

Why:

- The user surface needs to become thread/context-driven without turning the UI
  into the business owner.

Impact / Compatibility:

- User-facing workspace changes.
- Governance surface stays separate.
- `/agent/chat` remains, but it becomes a contextual interaction endpoint.

Verification:

- `docker compose exec -T api python scripts/agent_runtime_regression.py`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case workspace_flow`
- `docker compose exec -T api python scripts/governance_preview_ui_regression.py`

Steps:

- [ ] Write test: add a workspace regression that starts from a thread context and drives Copilot actions through `/agent/chat`.
- [ ] Verify RED: run the regression and confirm the workspace still behaves like a general chat shell rather than a communication workspace.
- [ ] Minimal code: reframe API and Streamlit state so thread/context becomes the dominant work surface while preserving observation-first rendering and governance separation.
- [ ] Verify GREEN: rerun the workspace, runtime, and governance regressions until thread-first user flow works without moving business ownership into the UI.
- [ ] Commit: `git add app/main.py app/orchestration/service.py web/streamlit_app.py web/governance_console.py scripts/communication_copilot_regression.py && git commit -m "Reframe workspace around communication threads"`

Repair Track:

- Root cause: product framing and primary workspace still carry chat-first assumptions.
- Stable repair: change the work surface, not the authority boundaries.

Retirement Track:

- Old owner/fallback: UI and API framing that still imply general chat as the product center.
- Trigger: remove or demote after the new thread-driven workspace is stable.

### Task 7: Final retirement of active legacy carriers after proof

Files:

- Modify `app/orchestration/registry.py`
- Modify `app/orchestration/service.py`
- Modify `app/__init__.py`
- Modify `app/config.py`
- Modify `README.md`
- Modify `problem_todolist.md`
- Modify `todolist.md`

Why:

- Task 0 removes dead legacy and quarantines active compatibility shims. This
  final task deletes the remaining active legacy carriers after the
  communication owner path has replaced them.

Impact / Compatibility:

- High conceptual impact, moderate runtime risk.
- Must be done only after prior tasks are green.
- Should remove compatibility shims rather than discover new legacy categories.

Verification:

- `docker compose exec -T api python -m compileall -q app scripts web`
- `docker compose exec -T api python scripts/agent_runtime_regression.py`
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case retirement`
- `rg -n "LeetCode|legacy_orchestration|legacy_aggregator|RAG Agent" app README.md`

Steps:

- [ ] Write test: add assertions that no remaining runtime path depends on legacy closeout wording or old product identity carriers.
- [ ] Verify RED: run the selected regressions and search commands to confirm only Task 0 quarantined shims remain.
- [ ] Minimal code: remove the quarantined shims whose replacement owner paths are now green.
- [ ] Verify GREEN: rerun compile, runtime, regression, and search verification until only intentional historical references remain.
- [ ] Commit: `git add app/orchestration/registry.py app/orchestration/service.py app/__init__.py app/config.py README.md problem_todolist.md todolist.md scripts/communication_copilot_regression.py && git commit -m "Retire legacy communication carriers"`

Repair Track:

- Root cause: historical product identity and fallback paths still influence runtime behavior.
- Stable repair: remove active legacy owners only after the new owner path is proven.

Retirement Track:

- Old owner/fallback: historical LeetCode identity, chat-first wording, and legacy orchestration carriers.
- Trigger: delete once the new communication owner path is verified locally in Docker.

## Verification And QA Gates

Docker regression gate:

- Applies to Task 0A through Task 7.
- Always start with `docker compose ps api worker redis postgres chroma`; if
  services are stopped, run `docker compose up -d` before verification.
- Use Docker-only commands. Do not use host Python as a fallback.
- Task 0B, Task 4, Task 5, and Task 7 must rerun existing Mail/RAG/runtime
  regressions because they touch active ownership or carrier paths.
- Task 2 must create `scripts/communication_copilot_regression.py` with
  `contracts_import`; Task 3 extends it with `brief_assembly`; later tasks add
  `mail_closeout`, `workspace_flow`, and `retirement`.

Browser Standard QA gate:

- Applies only after runtime-facing phases: Task 0B, Task 4, Task 6, and Task 7.
- Open `http://localhost:8511/` and test the user-facing communication flow,
  task state, governance visibility, console errors, and network errors.
- Do not change front-end visual style unless the issue is functional,
  state-related, or blocks user completion.
- Final Task 7 QA accepts no unresolved Critical, High, or Medium issues.

Required browser scenarios:

- Ask an EnterpriseRAG question, then send "that information / previous
  solution / this plan" to an email address; body must be correct, non-empty,
  non-duplicated, and free of unrelated assistant greetings.
- Start a new RAG question while a mail draft is pending; the RAG question must
  not be swallowed by stale draft continuation state.
- Send inline text such as `body is "test"` or equivalent Chinese phrasing; it
  must be treated as body content, not as an attachment clarification.
- Exercise DLP high risk, SMTP failure, provider unavailable, and worker
  unavailable paths; each must return recovery observations rather than
  pretending success.
- Check browser console and network panel after core interactions; no critical
  JavaScript errors or task API `Failed to fetch` failures may remain.

QA outputs:

- Health score and severity counts.
- Screenshot evidence for each Critical, High, and Medium issue.
- Top 3 fixes or blockers.
- Deferred Low issues recorded in `problem_todolist.md`.

## Risks

- `app/main.py` may resist incremental cleanup because it currently owns too many paths.
- Introducing `communication_brief` could accidentally duplicate draft intent if the boundary with Mail Agent is not kept sharp.
- Workspace reshaping could drift into UI-owned logic if backend contracts are not introduced first.
- Legacy retirement could break obscure fallback paths if regressions stay too narrow.

## Retirement

- Historical LeetCode identity should move to historical docs only.
- Legacy orchestration fallback should remain only if a proven compatibility case
  survives after the new communication-owner path is in place.
- Any path that produces fixed user-visible wording from code instead of LLM
  observations should be retired unless it is a strict governance or permission
  boundary.

## Self-Review

- Spec coverage: covered by Tasks 0-7, the QA gate, and the explicit
  owner/runtime/UI/retirement split.
- Placeholder scan: no placeholder markers remain.
- Type consistency: the plan centralizes new runtime objects in
  `app/communication/`.
- Compatibility: Docker-only local baseline, observation-first wording,
  governance boundaries, and RAG evidence boundaries are preserved.
- Complexity: the plan avoids adding more logic into `app/main.py` as the main
  owner path.
- Verification: each task includes exact commands, and runtime-facing phases now
  have Standard browser QA checkpoints.
- Dual-track: repair and retirement tracks are called out per task.
