---
"type": "devlegate.ticket"
"title": "Contain expected execution workspace preparation failures without terminating service"
"depends_on": ["TASK-071"]
---

## Incident and scope

On 2026-10-15, Devlegate 0.5.7.dev0 at product revision `507da8f926f9` was recovering a pre-worker `TASK-071` execution. The control branch had safely fast-forwarded from `a935c8e` to `491e902` (only `kanban/backlog/TASK-074.md` added). `ServiceEngine._prepare_execution_workspace()` raised `ExecutionWorkspaceError("control revision changed after execution was planned; refusing stale execution")`. The calling site in `_run_iteration_body()` caught only `ExecutionWorkspaceConflict`, so the error escaped the polling loop and crashed the systemd service (exit status 1).

This ticket fixes **exception containment and accurate service state**, not the revision-adoption policy; routine product/control advancement belongs to TASK-076.

## Required work

1. Audit `_prepare_execution_workspace()` and its immediate scheduler/hosted entrypoints for expected `ExecutionWorkspaceError` subclasses, including branch ancestry/ownership, persisted binding mismatch, changed control revision, unsafe worktree and failed preparation. Map expected pre-worker preparation refusals into the existing `WorkspacePreparationBlockedError` / `WorkflowBlockedError` handling so hosted polling remains alive, reports a specific blocked reason and stays operator-responsive. Do not apply a blanket `except Exception` or conceal programming defects.
2. Preserve the exact admitted execution identity, WAL/state and checkpoint/recovery evidence on refusal. No worker launch, silent generation restart, branch reset, remote rewrite, forced retry, state deletion, or implicit authorization.
3. Verify the same condition is represented consistently in `devlegate status`, service logs and one-shot / interactive operator commands. Avoid an unbounded hot retry loop and duplicate log spam while the blocking state remains unchanged. Permit normal resumption after an authorized correction without restarting the service.
4. Audit adjacent pre-worker error translation boundaries for equivalent crash paths, but keep the code change narrowly scoped. Distinguish true infrastructure/owner failures from ordinary recoverable admission refusal; preserve shutdown cancellation semantics and intentional fatal invariants.

## Acceptance

- Regression reproduces the 2026-10-15 control-revision mismatch during workspace preparation, and a second non-conflict `ExecutionWorkspaceError`: hosted polling stays alive, publishes `blocked` with an actionable reason, and keeps status/IPC responsive.
- The same failure cannot mark an execution as worker-running or launch a duplicate worker. The exact persisted binding and Git refs remain unchanged on refusal.
- After making the binding admissible through an authorized operation, a subsequent iteration resumes correctly without process restart; blocked diagnostics clear.
- Existing conflict handling, `once`, retry/operator error reporting, cancellation and concurrent-owner safety remain intact. Focused tests plus full exact-checkpoint pytest and Ruff green.
- Do **not** add broad automatic recovery or weaken control/product admission checks here; TASK-076 defines normal revision advancement.
