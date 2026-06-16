# Baseline Governance

## 1. Baseline Roles

- Product / Requirement Baseline: problem, accepted behavior, success evidence,
  non-goals, workflow constraints, and approved requirement/spec intent.
- Architecture / Runtime Boundary Baseline: canonical owner, contract,
  source-of-truth boundary, dependency direction, compatibility, runtime-ready
  boundary, and retirement state.

## 2. Design Defect

A confirmed error, gap, contradiction, or wrong abstraction in the relevant
requirement, design, or baseline.

- Fix the defective requirement/design/baseline first.
- Then align implementation to the corrected baseline.
- Do not patch implementation around a defective baseline.

## 3. Implementation Drift

Implementation, plan, review, or documentation has deviated from a confirmed,
correct, unchanged requirement or architecture baseline.

- Return to baseline via the simplest stable path.
- Do not update baseline to match drift without explicit review.

## 4. Compatibility Aliases

- Architecture Defect = architecture-scoped Design Defect.
- Architecture Drift = architecture-scoped Implementation Drift.
- New findings should report Design Defect / Implementation Drift plus
  `scope: requirements | architecture | both`.

## 5. Baseline Check Protocol

Before non-trivial changes:

1. Read the latest Product / Requirement Baseline candidate.
2. Read the latest Architecture / Runtime Boundary Baseline candidate.
3. Compare current work against requirement acceptance and architecture owner /
   contract boundaries.
4. Check for new anti-patterns not recorded in the known list.
5. Report: aligned / Design Defect / Implementation Drift /
   missing-authority / needs-clarification, with
   `scope: requirements | architecture | both`.

## 6. Architecture Review - 7 Dimensions

After each non-trivial change:

1. Ownership integrity: every component has exactly one canonical owner.
2. Module boundaries: no unauthorized cross-module coupling.
3. Contract changes: API/signature/behavior contract changes are documented.
4. Cascade proliferation: no unjustified cascading dependency chains.
5. Dependency direction: dependencies flow toward stability.
6. Retirement completeness: old owners/fallbacks/paths are removed or scheduled.
7. Entropy flow: net complexity decreases or stays flat with justification.

## 7. Hard Boundaries

- `BASELINE-GOVERNANCE.md` is the constitution for this project's Aegis
  workspace.
- Baseline snapshots in `baseline/` are evidence, not authority.
- ADRs recorded later may capture decisions, but they do not replace baseline
  governance.
- This file is never auto-updated. Changes require explicit user review.
