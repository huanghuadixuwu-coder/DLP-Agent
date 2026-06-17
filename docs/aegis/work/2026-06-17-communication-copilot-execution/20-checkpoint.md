# Todo Checkpoint Draft

Current todo:
- Start Task 1 reset top-level product framing and freeze the migration boundary.

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

Active slice:
- Task 1 preparation.

Evidence refs:
- Spec compliance reviewer confirmed Task 0A deliverable files only: `problem_todolist.md`, `todolist.md`.
- Quality reviewer passed Task 0A and confirmed inventory is actionable for Task 0B.
- `problem_todolist.md` Issue 26 now contains the classified legacy carrier inventory and regression map.
- `todolist.md` section 19 records Task 0A and Task 0B progress.
- Task 0B touched only allowlisted backend/docs files and did not change frontend style.
- Task 0B Docker verification passed for compileall, agent runtime regression, and mail authoring regression.
- Task 0B EnterpriseRAG regression could not complete because `/app/questions.parquet` is missing in the active Docker container; the data gap is recorded in `problem_todolist.md` and `todolist.md`.

Blocked-on items:
- EnterpriseRAG benchmark/regression data is missing from Docker (`/app/questions.parquet` and matching documents); this blocks RAG-specific regression evidence but not Task 1 docs framing.

Next step:
- Dispatch Task 1 worker with docs-only scope: reset top-level product framing while preserving migration boundaries and without touching frontend style.

DriftCheckDraft:
- Scope: still executing approved Communication Copilot plan.
- Compatibility: no UI style files changed in Task 0A or Task 0B.
- Retirement: Task 0B followed the inventory categories; active runtime dependencies remain quarantined rather than deleted.
- Decision: continue.
