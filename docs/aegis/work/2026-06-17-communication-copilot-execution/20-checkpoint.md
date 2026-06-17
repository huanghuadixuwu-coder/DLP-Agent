# Todo Checkpoint Draft

Current todo:
- Start Task 6 reframe `/agent/chat` and workspace around communication work.

Completed todos:
- Committed finalized plan edits: `e756302`.
- Task 0A worker produced inventory commit: `ba4ba05`.
- Task 0A coverage fix commit: `1e1d028`.
- Task 0A quality fix commit: `5031ecb`.
- Task 0A spec compliance review passed after corrected deliverable boundary.
- Task 0A code/document quality review passed.
- Controller checkpoint commits `4c6125f` and `e96bbb3` updated this work record during review; they are coordination evidence, not part of the Task 0A worker deliverable.
- Task 0B worker quarantined/remediated classified legacy carriers in `eed74e2`.
- Task 0B tracker line-reference fixes landed in `63aa5d2` and `63beb1a`.
- Task 0B spec compliance review passed after line-reference repair.
- Task 0B code/document quality review passed after stale tracker references were removed.
- Task 1 reframed top-level docs around Communication Copilot in `da9be5c`.
- Task 1 inventory reference repair landed in `f0df72e`.
- Task 1 spec compliance and code/document quality reviews passed.
- Task 2 added canonical communication contracts and contract regression in `040b85e`.
- Task 2 spec compliance and code quality reviews passed.
- Task 3 added communication thread resolution and brief assembly in `dbcb20f`.
- Task 3 spec compliance and code quality reviews passed.
- Task 4 made Mail Agent consume `communication_brief` as the closeout source across `f39ba37`, `0e35004`, `2cde2ea`, and `f2180aa`.
- Task 4 spec compliance and code quality reviews passed after source-precedence regressions were fixed.
- Task 5 normalized EnterpriseRAG and Meeting as subordinate communication providers in `dc23773`.
- Task 5 spec compliance and code quality reviews passed.

Active slice:
- Task 6 preparation.

Evidence refs:
- Spec compliance reviewer confirmed Task 0A deliverable files only: `problem_todolist.md`, `todolist.md`.
- Quality reviewer passed Task 0A and confirmed inventory is actionable for Task 0B.
- `problem_todolist.md` Issue 26 now contains the classified legacy carrier inventory and regression map.
- `todolist.md` section 19 records Task 0A and Task 0B progress.
- Task 0B touched only allowlisted backend/docs files and did not change frontend style.
- Task 0B Docker verification passed for compileall, agent runtime regression, and mail authoring regression.
- Task 0B EnterpriseRAG regression could not complete because `/app/questions.parquet` is missing in the active Docker container; the data gap is recorded in `problem_todolist.md` and `todolist.md`.
- Task 1 touched only `README.md`, `todolist.md`, and `problem_todolist.md`.
- Task 1 search verification removed conflicting top-level `LeetCode`, `chat-first`, `RAG Agent`, and `统一聊天入口` framing from active docs.
- Task 1 clarified that current README framing is Communication Copilot and that pre-Task-1 README identity references are historical inventory, not current carriers.
- Task 2 added `app/communication/` contracts without API, DB, runtime, frontend, or style changes.
- Task 2 Docker verification passed for compileall and `scripts/communication_copilot_regression.py --case contracts_import`.
- Task 3 added `app/communication/thread_context.py` and `app/communication/brief_service.py`.
- Task 3 Docker verification passed for `contracts_import` and `brief_assembly` regression cases.
- Task 3 did not change public API, DB schema, frontend/style, inbound store persistence, memory behavior, or EnterpriseRAG query behavior.
- Task 4 Docker verification passed for `mail_reference_authoring_regression.py` and `communication_copilot_regression.py --case mail_closeout` after final fixes.
- Task 4 preserved explicit upload/current-source precedence and anchored prior-assistant-answer precedence before `communication_brief`.
- Task 4 did not commit frontend/style/docs/data transcript changes.
- Task 5 Docker verification passed for communication subordinate input regression, meeting worker regression, and cross-domain workflow regression.
- Task 5 added structural subordinate role metadata for RAG grounding and Meeting escalation without making either final closeout owner.
- Task 5 EnterpriseRAG full regression remains blocked only by missing `/app/questions.parquet`.

Blocked-on items:
- EnterpriseRAG benchmark/regression data is missing from Docker (`/app/questions.parquet` and matching documents); this blocks full RAG-specific regression evidence but not Task 6 UI/workspace framing.

Next step:
- Dispatch Task 6 worker to reframe `/agent/chat` and the workspace around communication work without changing front-end visual style.

DriftCheckDraft:
- Scope: still executing approved Communication Copilot plan.
- Compatibility: no UI style files changed in Task 0A, Task 0B, or Task 1.
- Retirement: Task 0B followed the inventory categories; active runtime dependencies remain quarantined rather than deleted.
- Product boundary: Task 1 changed docs only; no runtime ownership was moved yet.
- Contract boundary: Task 2 introduced additive internal types only; no active behavior moved yet.
- Assembly boundary: Task 3 introduced internal brief assembly only; no public closeout ownership moved yet.
- Closeout boundary: Task 4 moved Mail closeout toward `communication_brief` while preserving existing Mail/DLP confirmation lifecycle.
- Subordinate boundary: Task 5 marked RAG as grounding provider and Meeting as escalation provider; Mail remains closeout owner.
- Decision: continue.
