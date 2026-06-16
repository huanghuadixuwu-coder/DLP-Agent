# Aegis Workspace

This directory holds design-time authority artifacts for the project.

Purpose:

- Record product and architecture baselines before medium/high-complexity changes.
- Keep durable design specs separate from implementation notes and trackers.
- Make it easy to tell which documents define the intended system shape.

Structure:

- `INDEX.md`: list of Aegis artifacts in this workspace.
- `BASELINE-GOVERNANCE.md`: local constitution for baseline checks.
- `baseline/`: evidence snapshots of the current project shape.
- `specs/`: approved or review-ready specs that should guide planning.

This workspace does not replace `总体要求.md`, `README.md`, `todolist.md`, or
`problem_todolist.md`. It complements them by pinning product and architecture
decisions that need a durable owner.
