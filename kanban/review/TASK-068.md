---
"type": "devlegate.ticket"
"title": "Reduce Git operation indirection and consolidate reusable Git mechanics"
"depends_on": ["TASK-067"]
---

## Milestone

Devlegate 0.5.7 — follow-up to TASK-067. Preserve the established `git.run_git()` and `git.resolve_commit()`; do not redo their implementation or reopen completed TASK-061 recovery extraction.

## Goal

Continue the Git infrastructure refactoring **where it produces concrete KISS/DRY benefits**. The first pass consolidated subprocess execution, but the recovery/reconciliation modules still reach Git via `owner.recovery_git()`, `owner.reconciliation_git()` and small forwarders in `ServiceEngine`, while other paths call Git primitives directly.

Reduce unnecessary callback/protocol indirection and duplicated Git-only mechanics **without moving domain decisions or weakening the single-owner transaction model**. Aim for cohesive, readable control flow rather than a large catalog of one-command helpers.

## Investigate current call paths first

Review real implementations and callers in:
- `src/devlegate/git.py`, `runtime.py`, `execution_workspace.py`;
- `recovery_transactions.py`, `recovery_workspace.py`, `recovery_workflow.py`;
- `reconciliation_transactions.py`, `reconciliation.py`;
- any additional existing low-level Git helpers.

Trace `owner.recovery_git`, `owner.reconciliation_git`, `_git` and similar forwarding methods through to `run_git()`. Identify whether each actually enforces locking, authorization, special error translation, tests' monkeypatch seam, or other real invariants.

Do not infer safety just from a one-line method body. Distinguish the **capability boundary for a mutation** from the **technical execution of a Git command**. Preserve any boundary that enforces ownership or a security condition.

## Implementation

1. Replace provably redundant Git execution forwarding layers with direct use of the common `git.run_git()` or a small shared Git primitive. Where tests patch old `_git` seams, adapt them minimally and keep equivalent assertions. Avoid adding a new injected-runner framework or another Protocol solely for test compatibility.
2. Search for repeated domain-independent Git mechanics (ref/commit resolution, parent enumeration, linear history inspection, worktree status, safe ref CAS) and consolidate **only** cases with identical semantics, including success/failure, missing-ref behavior and required expected-old checks.
3. Keep Git infrastructure unaware of ticket IDs, workflow state, WAL, reconciliation stages, evidence/provenance policy or service lifecycle. A helper may know how to perform CAS, but must not decide whether Devlegate is authorized to mutate a ref.
4. Preserve ServiceEngine ownership of serialized state transitions and mutation admission; recovery/reconciliation continue to own WAL, safe rollback/replay, published rewrite authorization, evidence pin-before-publish and lease/CAS checks. A pure Git helper can be called directly only when it does not bypass such enforcement.
5. Prefer ordinary functions and small, self-contained helpers. Do not create a framework, class hierarchy, GitPython/pygit2 dependency or separate package. Do not duplicate primitives already supplied by `git.py`.
6. Remove stale imports/Protocol members/forwarders actually made obsolete. Avoid cosmetic churn, renaming unrelated methods, or additional abstraction that increases call depth.

Do **not** change unrelated `_run_iteration_body()` control flow, TASK-063 worktree retirement, TASK-066 log reader, packaging, CLI semantics or the public state schema.

## Tests and proof

- This is behavior-preserving refactoring. **Rely on existing** unit, Git/workspace, recovery/reconciliation, crash/replay, IPC and publication regression coverage.
- Add only small unit tests for newly factored primitives not already adequately exercised; add a focused behavioral regression only if you discover a specific untested invariant.
- Do not generate new broad integration or E2E suites simply because code moved.
- Run full pytest and Ruff for the **exact checkpoint** in CI.

## Acceptance criteria

- Report concrete Git call chains before/after and which pass-through layers were eliminated (or retained with a precise safety reason).
- Reused low-level primitives represent actual duplicated mechanics, not arbitrary one-line aliases.
- The recovery/reconciliation algorithms are no more coupled to `ServiceEngine` for raw Git execution than required for controlled effects. Existing owner guarantees remain intact.
- No unrelated behavior changes or dependencies; full suite and Ruff green.
- If the audit finds nothing further safely removable, provide specific evidence and stop rather than manufacture abstractions.

The worker report should be concise and distinguish actual reduction in call indirection/duplication from purely moving LOC.
