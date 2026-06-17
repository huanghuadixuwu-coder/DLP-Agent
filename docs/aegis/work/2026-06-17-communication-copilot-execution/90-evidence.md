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

Task 1 evidence:
- Worker framing commit: `da9be5c`.
- Worker inventory reference fix commit: `f0df72e`.
- Spec compliance reviewer passed Task 1 and confirmed only `README.md`, `todolist.md`, and `problem_todolist.md` changed.
- Code/document quality reviewer passed after stale README line references were marked as historical pre-Task-1 locations.
- `rg -n "LeetCode|chat-first|RAG Agent|统一聊天入口|缁熶竴鑱婂ぉ鍏ュ彛" README.md todolist.md problem_todolist.md`: no matches after Task 1.
- `git diff --check -- README.md todolist.md problem_todolist.md`: passed.

Task 2 evidence:
- Worker contract commit: `040b85e`.
- Spec compliance reviewer passed Task 2 and confirmed required dataclasses, observation builders, and regression script behavior.
- Code quality reviewer passed Task 2 and confirmed no runtime/API/DB/frontend/style changes.
- `docker compose exec -T api python -m compileall -q app`: passed.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case contracts_import`: passed.
- `rg -n "communication_brief|communication_thread" app`: new contract package references only.
- `git diff --check -- app/communication scripts/communication_copilot_regression.py`: passed.

Task 3 evidence:
- Worker assembly commit: `dbcb20f`.
- Spec compliance reviewer passed Task 3 and confirmed explicit thread resolution uses existing inbound store APIs.
- Code quality reviewer passed Task 3 and confirmed the implementation is bounded, structural, and free of user-visible templates.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case contracts_import`: passed with `ok: true`.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case brief_assembly`: passed with `ok: true`.
- `docker compose exec -T api python -m compileall -q app scripts`: passed in the worker verification.
