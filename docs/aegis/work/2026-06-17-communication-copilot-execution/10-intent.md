# Task Intent Draft

Requested outcome: Execute the Communication Copilot implementation plan with subagent-driven development, starting from legacy carrier inventory/quarantine and preserving Docker-first verification.

Scope:
- Execute the approved plan in `docs/aegis/plans/2026-06-17-communication-copilot-implementation.md`.
- Start with Task 0A and Task 0B before later communication contracts.
- Use subagents for bounded implementation/review slices.

Non-goals:
- Do not use host Python as acceptance baseline.
- Do not alter front-end visual style.
- Do not hard-delete active Mail/RAG/DLP runtime dependencies before replacement owner paths pass regressions.

Baseline refs:
- `docs/aegis/plans/2026-06-17-communication-copilot-implementation.md`
- `docs/aegis/specs/2026-06-16-communication-copilot-design.md`
- `README.md`
- `problem_todolist.md`
- `todolist.md`

Impact statement:
- The first implementation slice is low runtime risk because Task 0A is inventory-only.
- Later Task 0B touches runtime carrier paths and must be regression-protected.
