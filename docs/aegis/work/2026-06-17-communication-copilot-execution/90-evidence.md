# Evidence Bundle Draft

Initial evidence:
- Plan file exists and has been revised to include confirmed implementation decisions.
- Current branch is `codex/mail-agent-v2-m1` and not main/master.

Task 0A evidence:
- Worker commits: `ba4ba05`, `1e1d028`, `5031ecb`.
- Spec compliance review passed after the controller clarified that checkpoint commits were coordination evidence, not part of the Task 0A deliverable.
- Code/document quality review passed.

Task 0B evidence:
- Plan scope commit: `903215f`.
- Worker implementation commit: `eed74e2`.
- Tracker reference repair commits: `63aa5d2`, `63beb1a`.
- Spec compliance reviewer passed Task 0B and confirmed the diff touched only allowed backend/docs files, preserved active runtime dependencies as compatibility shims, and did not alter frontend style.
- Code quality reviewer passed Task 0B after stale tracker line references were fixed.

Docker verification evidence for Task 0B:
- `docker compose ps api worker redis postgres chroma`: services up.
- `docker compose exec -T api python -m compileall -q app scripts web`: passed.
- `docker compose exec -T api python scripts/agent_runtime_regression.py`: passed.
- `docker compose exec -T api python scripts/mail_authoring_contract_regression.py`: passed.
- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`: blocked by missing `/app/questions.parquet`; recorded as an environment/data gap, not hidden as success.
