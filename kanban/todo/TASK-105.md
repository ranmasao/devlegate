---
"type": "devlegate.ticket"
"title": "Iteratively audit runtime decomposition and DRY boundaries before final Git story acceptance"
"depends_on": ["TASK-080", "TASK-081", "TASK-098"]
---

## Purpose and milestone

Devlegate 0.5.7 Git-scope structural audit, **before** final TASK-073 story acceptance. This is one research ticket with deliberate reviewer-directed rework generations. Do not interpret green CI, absence of raw Git argv, or individual accepted Git tickets as proof that `ServiceEngine` is adequately decomposed.

**Audit only.** Do not refactor runtime code in this ticket. Produce an evidence-linked design report that the reviewer can interrogate over successive executions. If implementation is justified, propose separate narrow tickets after the analysis stabilizes.

## Immutable execution and iterative review contract

A ticket already admitted to execution is immutable. **Do not edit this ticket's source while an execution/rework generation is active**, including to extend its scope. The reviewer records each iteration's new questions, missing proofs, and acceptance conditions in a standalone review artifact, then transitions `review -> todo` via normal rework mechanics. The next worker consumes that review feedback through Devlegate's existing rework prompt/provenance flow.

Retain a cumulative, versioned report in the product branch at `docs/architecture/runtime-decomposition-audit.md`. Each execution updates that document without destroying prior evidence or rationale. Cite exact source paths, functions, checkpoint/product HEAD and measured snapshots; clearly distinguish observed evidence from hypotheses. The next execution should answer **only the bounded questions identified by reviewer feedback**, while preserving and correcting prior findings. If the feedback cannot be delivered or prior report cannot be located, report `incomplete` with an explicit blocker; do not improvise a new study.

This is a research/review process, not a mandate to trigger indefinite rework. The reviewer accepts once the evidence is adequate to choose implementation boundaries and risks; no implementation is required for ticket acceptance.

## First execution — narrow question only

**Question:** Which large `src/devlegate/runtime.py` regions currently mix independent responsibilities or reproduce the same proof/transaction algorithm that already exists elsewhere, and which are genuinely owner-only coordination?

1. Read the exact integrated product HEAD, `src/devlegate/runtime.py`, existing `git.py`, `execution_workspace.py`, `recovery_workflow.py`, `recovery_transactions.py`, `reconciliation_transactions.py`, `control_git_workflow.py`, and `docs/GIT_COMMAND_INVENTORY.md`. Do not rely on stale measured line numbers or a worker branch from earlier tickets.
2. Inventory **the 10–15 highest-impact runtime functions**, with actual LOC, main responsibilities, dependencies on `_state`/WAL/locks/runner and Git/worktree capabilities, other callers, and existing equivalent domain helpers. Include, if still applicable, `cleanup_workspaces`, `_assert_cleanup_has_no_execution_dependency`, `_workspace_cleanup_cohort`, `_retire_execution_refs`, `_prepare_execution_workspace`, reconciliation and interruption recovery paths.
3. For each, classify: **owner policy/serialization**, **domain transaction**, **read-only observation/proof**, **low-level Git semantic**, or **mixed**. Mark concrete instances of duplicated validation, ref/evidence observation, state transitions or effect choreography with at least two exact call-site references; distinguish genuine duplication from necessarily different safety gates.
4. Propose **at most three** leading extraction/refactoring seams, with present and proposed responsibility owners, minimum function/data contract and preserved safety invariants. Identify which can be done independently and which cross WAL, crash replay, locks or state mutation. Do not supply a large speculative refactor patch or generic manager framework.
5. State what is **not yet known** and the highest-value questions for the next review iteration. Explicitly list risks of cosmetic forwarding wrappers, circular imports, excessive Protocol growth, hidden Git subprocesses or duplicated state machines.

The first report must be useful on its own; do not begin by refactoring. Keep source analysis bounded and prioritize concrete dependencies and control flow over broad LOC rankings.

## Subsequent executions (reviewer directed)

Only the reviewer selects each next narrow investigation via REWORK feedback. Suitable investigations include actual control/data-flow for one candidate seam, shared vs intentionally separate provenance proofs, cleanup mutation race/ownership boundaries, exception and shutdown propagation, or a before/after decomposition plan. Every round must produce a substantive updated report with explicit answers and remaining uncertainty.

Do not expand into other runtime domains or start implementation without an independent follow-up ticket. Treat reviewer questions as scoped instructions within this ticket's fixed audit goal, never as authorization to weaken transactional proofs.

## Safety and acceptance

- Preserve the distinction between read-only observation, guarded effect, durable WAL transition, crash-replay proof and policy authority. An extracted helper must not implicitly fetch, mutate refs or write `_state` merely to shorten `runtime.py`.
- No reduction in LOC or import-edge count alone establishes improved decomposition; require an identifiable ownership boundary, diminished duplication or coupling, and measurable unchanged commands/effects.
- The report must reference existing regression tests and identify additional tests necessary for future extraction; do not duplicate broad E2E suites.
- Since this is docs-only research, run focused static/document checks as relevant; do not run full pytest or coverage as a ritual. If the execution changes code contrary to scope, reject it.
- The eventual final report should provide a prioritized **finite** extraction plan with scope, dependency order, risks, effect accounting approach and acceptance checks. Include explicit `retain in ServiceEngine` conclusions where appropriate, not an unconditional demand to shrink the module.
- Reviewer must not ACCEPT until the requested questions are answered and the report permits a defensible implementation/no-change decision. Worker may honestly claim `incomplete` at any research stage; reviewer makes the disposition based on evidence, not the literal claim alone.
- TASK-073 remains the final story-level human-curator gate. This audit is input evidence, not automatic approval of TASK-073.
