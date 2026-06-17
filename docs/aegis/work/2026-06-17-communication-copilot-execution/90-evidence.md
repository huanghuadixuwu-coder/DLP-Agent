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

Task 4 evidence:
- Worker closeout commit: `f39ba37`.
- Fix commits: `0e35004`, `2cde2ea`, `f2180aa`.
- Spec compliance reviewer passed after `mail_reference_authoring_regression.py` was restored.
- Code quality reviewer passed after explicit upload/current-source and anchored prior-assistant-answer precedence were restored before `communication_brief`.
- `docker compose exec -T api python scripts/mail_reference_authoring_regression.py`: passed with `ok: true`.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case mail_closeout`: passed with `ok: true`.
- Worker verification also passed `mail_authoring_contract_regression.py`, `contracts_import`, `brief_assembly`, compileall, and diff-check for allowed files.

Task 5 evidence:
- Worker subordinate-provider commit: `dc23773`.
- Spec compliance reviewer passed and confirmed RAG grounding metadata, Meeting escalation metadata, domain-agent metadata, and Mail closeout ownership.
- Code quality reviewer passed; residual drift risk is repeated role/kind literals across integration boundaries.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case subordinate_inputs`: passed in worker verification.
- `docker compose exec -T api python scripts/meeting_worker_regression.py`: passed in worker verification.
- `docker compose exec -T api python scripts/cross_domain_workflow_regression.py`: passed in worker verification.
- `docker compose exec -T api python scripts/enterprise_rag_regression.py --limit 4`: blocked only by missing `/app/questions.parquet`.

Task 6 evidence:
- Worker workspace-framing commit: `2a83a7a`.
- Copy consistency polish commit: `814aa0d`.
- Spec compliance reviewer passed and confirmed allowed file scope, no frontend style/theme changes, `/agent/chat` communication workspace state, backend-owned workspace authority, and separate governance surface.
- Code quality reviewer passed and confirmed no fixed business-answer templates, no style/layout/theme/color changes, and preserved governance separation.
- Narrow re-review passed after changing the merge button copy from `合并所选对话` to `合并所选沟通线程`.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case workspace_flow`: passed with `workspace_kind=communication_thread_context` and `primary_work_object=communication_brief`.
- `docker compose exec -T api python -m compileall -q app scripts web`: passed.
- Worker verification also passed `agent_runtime_regression.py`, `contracts_import`, `brief_assembly`, `mail_closeout`, `subordinate_inputs`, `governance_preview_ui_regression.py`, and `git diff --check`.

Task 7 evidence:
- Worker retirement commit: `fd19c0a`.
- Retirement scan hardening commit: `f64d84f`.
- Spec compliance reviewer passed and confirmed the old planner/executor/aggregator fallback was removed, ReAct failures now return typed recovery, and non-goal active dependency surfaces were preserved.
- Code quality reviewer passed and confirmed renderer-owned generic recovery output, consistent downstream response shape, clean imports, and no frontend/style changes.
- Narrow re-review passed after adding uppercase `ENABLE_LEGACY_ORCHESTRATION_FALLBACK` to the retirement scan.
- `docker compose exec -T api python -m compileall -q app scripts web`: passed.
- `docker compose exec -T api python scripts/agent_runtime_regression.py`: passed with `ok: true`.
- `docker compose exec -T api python scripts/communication_copilot_regression.py --case retirement`: passed with `mode_used=react_recovery` and `final_answer_source=orchestration_recovery_renderer`.
- `rg -n "LeetCode|legacy_orchestration|legacy_aggregator|RAG Agent" app README.md`: no active matches.
- `git diff --check`: passed with Windows LF/CRLF warnings only.
