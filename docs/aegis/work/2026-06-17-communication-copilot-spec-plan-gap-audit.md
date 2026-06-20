# Communication Copilot Spec-To-Plan Gap Audit

Status: corrective audit
Date: 2026-06-17
Scope: compare the approved Communication Copilot design spec with the first implementation plan and the current implemented foundation.

## Summary

The approved design spec was broader than the first implementation plan. The plan implemented a useful backend foundation, but it did not fully implement the thread-native product surface described by the spec.

The issue was a planning coverage failure, not primarily an execution failure. The execution work followed the first plan reasonably closely; the plan itself compressed product-level requirements into backend migration tasks and did not prove requirement-by-requirement coverage.

## Root Cause

The old plan claimed spec coverage through Tasks 0-7, but it did not include a traceability matrix. Because of that, several spec requirements were satisfied only by naming the concept rather than by defining concrete tasks, source-of-truth ownership, UI flow, and Docker/browser acceptance.

Concrete planning failures:

- `communication_thread` was introduced as an internal reference object, but not as the persistent dominant work object for the user workspace.
- `communication_brief` was implemented as a request-time runtime object, but the spec needs it to behave like a stable intermediate object that can survive thread selection, draft creation, follow-up, and refresh.
- The preferred product surface, "thread inbox plus Copilot side panel", was reduced to one vague workspace task.
- `/agent/chat` was reframed as contextual, but the plan did not require every Copilot action to bind to an active thread or explicitly declare that it is a global entry.
- Acceptance focused on backend regressions and one workspace smoke path, not on thread inbox, thread detail, side panel, active thread switching, and thread-scoped closeout.

## What The First Plan Did Deliver

These assets are useful and should be preserved:

- `app/communication/` package with thread and brief contracts.
- Communication brief assembly and observation builders.
- Mail Agent consuming communication brief candidates for closeout.
- EnterpriseRAG and Meeting Agent demoted toward subordinate provider roles.
- `communication_copilot_regression.py` cases for contracts, brief assembly, mail closeout, workspace flow, and runtime closeout wiring.
- Initial 8511 debug/state indicators for communication workspace state.
- Legacy identity cleanup and retirement momentum.

These are foundation pieces. They are not the complete thread-native Communication Copilot.

## Spec Coverage Matrix

| Spec requirement | Evidence in spec | First plan coverage | Current status | Gap | Required corrective task |
|---|---|---|---|---|---|
| Internal employee Communication Copilot for external communication | Sections 1-2 | Covered by docs/product framing tasks | Partial | Runtime surface can still behave like chat-first flow | Plan v2 Task 3 and Task 4 |
| Work from a customer communication thread | Section 2, Section 5.1 | Task 3 brief assembly and Task 6 workspace flow | Partial | No durable active thread source-of-truth and no full thread selection lifecycle | Plan v2 Task 1 and Task 2 |
| Customer threads and inbound communication context are in scope | Section 3 | Mentioned through inbound thread context | Partial | Inbound thread data exists, but the user workspace is not organized as thread inbox first | Plan v2 Task 1 and Task 4 |
| `communication_thread` is the active customer communication object | Section 5.1 | `CommunicationThreadRef` dataclass | Partial | Reference object exists, but it is not the dominant persisted work object | Plan v2 Task 1 |
| `grounding_bundle` supplies facts, citations, limits, provenance | Section 5.2 | EnterpriseRAG subordinate role task | Partial | Grounding can feed briefs, but thread-scoped grounding acceptance is not complete | Plan v2 Task 5 |
| `communication_brief` is the stable intermediate object | Section 5.3 | Request-time dataclass and assembly | Partial | Brief is not persisted or refreshable as a workspace artifact | Plan v2 Task 2 |
| Mail Agent receives brief and owns closeout | Sections 5.4 and 6.2 | Task 4 | Mostly covered | Needs thread workspace acceptance to prove closeout starts from active thread | Plan v2 Task 3 and Task 5 |
| Meeting escalation is a communication upgrade path | Sections 5.5 and 6.4 | Task 5 | Partial | Meeting flow exists, but thread/brief anchored escalation needs stronger acceptance | Plan v2 Task 6 |
| Governed delivery task owns side effects | Section 5.6 | Existing Mail Agent V2 and governance work | Mostly covered | Must be verified in thread-native flows, not only standalone mail flows | Plan v2 Task 7 |
| Supervisor owns active object resolution and continuation flow | Section 6.1 | General orchestration tasks | Partial | Active thread resolution is not a first-class pre-planner step | Plan v2 Task 3 |
| Runtime loop starts with active object resolution | Section 7 | Mentioned, not enforced | Partial | Active object resolution can be bypassed by generic chat or stale continuation paths | Plan v2 Task 3 |
| Mixed entry is allowed, but thread/context is default anchor | Section 7 | Task 6 broad workspace task | Partial | Default anchor is not enforced by UI/API contract | Plan v2 Task 3 and Task 4 |
| 8511 should be a thread inbox plus Copilot side panel | Section 8.1 | One vague workspace task | Not covered enough | Missing thread list/detail/Copilot panel acceptance | Plan v2 Task 4 |
| `/agent/chat` is contextual Copilot endpoint, not product center | Section 8.2 | Mentioned in Task 6 | Partial | Needs active thread binding and explicit global ask mode | Plan v2 Task 3 |
| Governance console remains separate | Section 8.3 | Existing M6 governance work | Mostly covered | Must be verified under thread-native delivery and recovery flows | Plan v2 Task 7 |
| Legacy chat-first carriers retired | Section 9 and Phase D | Tasks 0 and 7 | Partial | Some legacy carriers may remain because thread-native replacement is incomplete | Plan v2 Task 8 |
| Docker-only verification | Section 10 | Covered | Covered | Keep as invariant | All Plan v2 tasks |
| LLM renders user-visible wording from observations | Section 10 | Covered in plan | Mostly covered | Need thread side panel and brief output to remain observation-first | Plan v2 Task 4 and Task 5 |
| Acceptance covers thread drafting, grounding, send lifecycle, meeting escalation, recovery, retirement | Section 13 | Covered broadly | Partial | No explicit thread inbox and active thread switching acceptance | Plan v2 Task 9 |

## Planning Process Failure

The `aegis:writing-plans` skill requires spec coverage: each requirement must point to a task. The first plan did not enforce that requirement. It used a summary self-review instead of a real requirement-to-task matrix.

The plan should have been titled as a foundation slice or Phase 1 plan. Because it was titled as the Communication Copilot implementation plan, it implied full spec coverage while only delivering the backend foundation.

## Corrective Direction

Write a Plan v2 that is explicitly scoped to the missing product-level requirements:

- Make `communication_thread` a persistent, selectable, actor-scoped work object.
- Make `communication_brief` a refreshable workspace artifact, not only a request-time dataclass.
- Make 8511 a thread-native workspace without changing its visual style.
- Make `/agent/chat` operate against an active thread by default, with explicit global mode when no thread applies.
- Prove all of this with Docker regressions and browser QA scenarios.

## Non-Goals For The Corrective Plan

- Do not rewrite Mail Agent V2 from scratch.
- Do not replace EnterpriseRAG, Meeting Agent, DLP, or governance.
- Do not change the existing visual style of 8511.
- Do not add generic Workspace/Document platform scope.
- Do not hard-code user-visible answers to make demonstrations pass.

## Task 9 Final Acceptance Evidence

Date: 2026-06-20
Status: done with one unrelated regression concern

Thread-native acceptance summary is now explicit in
`scripts/communication_copilot_regression.py --case acceptance_summary`. The
summary reports each high-level requirement as `covered` or `not_covered` and
exits non-zero if any requirement is missing.

Covered acceptance requirements:

- Thread inbox: `workspace_thread_inbox` proves thread list, selected thread,
  selected-thread draft preview, and selected-thread task progress.
- Active thread: `workspace_thread_inbox` proves active thread storage and
  selected thread recovery after refresh.
- Brief persistence: `workspace_thread_inbox` proves latest persisted brief and
  Copilot context `brief_id` recovery.
- Grounded reply: `grounded_reply_from_thread` proves `communication_brief`
  source selection, `recipient_ready_summary`, non-empty draft body, and no raw
  assistant-answer leakage.
- Meeting escalation: `thread_meeting_escalation` proves confirmation is
  required before meeting creation, meeting task parameters are thread/brief
  scoped, and Mail Agent owns closeout.
- Governed send: `thread_governance_recovery` proves medium sender
  confirmation, high-risk governance boundary, and thread/brief audit
  provenance.
- Recovery: `thread_governance_recovery` proves provider failure, SMTP failure,
  and worker enqueue failure recovery observations.
- Retirement: `legacy_retirement_thread_native` plus `retirement` prove generic
  thread closeout uses `communication_brief` instead of raw assistant answer
  artifacts and retired orchestration fallback values remain absent.

Docker evidence captured:

- `docker compose ps api worker redis postgres chroma`: all five services were
  running.
- `docker compose exec -T api python -m compileall -q app scripts web`: passed.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case acceptance_summary`:
  passed with `missing_requirements: []`.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case all`:
  passed, including the new `acceptance_summary` case.
- `docker compose exec -T api python scripts/mail_m6_ui_governance_regression.py`:
  passed.
- `docker compose exec -T api python scripts/cross_domain_workflow_regression.py`:
  passed under the current M6 governed-send contract: medium-risk outbound
  content returns `sender_review_required`, `/tasks/{task_id}/sender-safety-confirm`
  queues it for send, and final SMTP delivery reaches `sent`.
- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`:
  command passed, but sample quality remains a data-governance residual risk
  (`average_doc_recall=0.0`, `average_evidence_fact_coverage=0.0`,
  `average_answer_fact_coverage=0.0` in this local sample run).
- Browser smoke at `http://localhost:8511/`: host returned HTTP 200 after
  starting the `web` service. `scripts/communication_workspace_browser_check.py`
  seeded a browser thread, selected it, verified latest brief recovery, and
  emitted the required scenario coverage map.

Residual risks:

- Browser QA is script-assisted rather than a human-visible exploratory pass:
  the check proves page availability and backend workspace state, while the
  grounded reply, governance, recovery, and meeting scenarios are proven through
  Docker regressions and surfaced in the browser scenario map.
- EnterpriseRAG sample metrics should not be overclaimed as answer-quality
  proof; this acceptance only proves the Communication Copilot thread-native
  coverage signals and records the existing dataset/retrieval quality risk.
