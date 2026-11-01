---
"type": "devlegate.ticket"
"title": "Unify failed-execution supersession, retry eligibility and scheduler admission"
---

## Problem and reproduced sequence
On Devlegate 0.5.7, TASK-134 had a persisted failed execution `3cb61e2d366042aeab97caf3f4702ece`. It was then moved from `todo` to `backlog` (control `36d4ef8`), and back to `todo` (control `ca394d4`) after an unrelated operational blocker was fixed. The code and control trees were clean, `status` listed TASK-134 as eligible, and `plan` said `run-worker`, but scheduler iterations repeatedly logged `execution failed for TASK-134; automatic retry suppressed; explicit retry is required`. Meanwhile, `retry` without arguments said `no current executions are retryable or recoverable`. An operator had to stop the service and remove a stale `failed_executions[TASK-134]` entry in SQLite with a guarded one-shot repair. That is not an acceptable normal workflow.

## Goal
Make admission, planning/status and explicit retry agree about the *same* current failure/generation. When Git history proves a previous failure is superseded by an intentional ticket lifecycle transition, it must not indefinitely block a new eligible `todo` generation. A genuinely current failure must still require explicit recovery authorization; a changed HEAD or fingerprint alone must never be treated as proof of supersession.

## Implementation contract
1. Audit and reconcile `ServiceEngine._failure_superseded()`, `_evaluate_failed_executions()`, `_retry_candidates()`, the published snapshot and the raw `failed_executions` gate in `_run_iteration_body()`. Use one narrow owner-validated proof/decision, or equivalently shared semantics, for failed-generation eligibility. Read-only status projection must not authorize mutation; perform fresh proof under the serialized owner at admission.
2. Recognize a **proven first-parent, immutable ticket transition** that supersedes a previous failed `todo` generation, including `todo → backlog → todo`. Retain the historical failure, report, execution ID, worktree, refs and checkpoints for audit. Do not erase failure history to make `plan` true. Prevent repeated unauthorized retries of the *same* failure generation.
3. Fail closed if Git history is missing, divergent, dirty, uncertain, if control/product observations conflict, if ticket bytes were edited unexpectedly, or if a worker/reconciliation/retry/recovery/retirement intent still holds the generation. Keep explicit `retry` for recoverable *current* failures with exact product/control/fingerprint binding. No automatic retry merely because a remote ref advanced or the service restarted.
4. Make `status`/`plan` actionable rather than misleading: an otherwise eligible ticket cannot appear as immediately runnable while an effective retained failed-generation barrier silently prevents scheduler admission. Ensure the nonretryable reason is diagnosable where relevant, without manufacturing an interactive retry candidate.
5. Add isolated real-Git + persisted-state regressions for: original TASK-134 state sequence, failed generation without transition, proven deferral/requeue, unrelated control updates, altered ticket blob, stale local/remote state, unobservable Git ancestry, repeated polls/restarts, and concurrent/active worker ownership. Assert no mutation of historical execution evidence or unrelated ticket failures.

## Boundaries / acceptance
Python stdlib runtime, no migrations, no manual SQLite repair as a product workflow, no bypass of WAL/CAS or ownership checks, no opportunistic deletion of resource refs/worktrees. Do not modify TASK-134 execution resources or rewrite `devlegate/control` history. Coordinate the acceptance scenario with TASK-138, which independently repairs review provenance after a failed execution. Focused tests, configured exact-checkpoint CI and a concise explanation of generation-supersession invariants are required.
