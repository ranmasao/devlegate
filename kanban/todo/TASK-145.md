---
"type": "devlegate.ticket"
"title": "Resume deferred failed worker through ordinary backlog-to-todo requeue"
"depends_on": ["TASK-137", "TASK-138"]
---

## Milestone

Fix the normal deferred-ticket lifecycle, not a new operator-facing historical recovery subsystem.

## Goal

When a ticket was previously reviewed/rejected, its next worker execution failed for a transport/infrastructure reason after checkpointing committed work, and the operator later moves that unchanged ticket `todo → backlog → todo`, the **ordinary scheduler** must attempt a safe new worker execution. Preserve all committed code from the latest failed checkpoint and the genuine preceding curator feedback. If the product base advanced, transplant that code **once** onto the current product base with the existing `ExecutionWorkspaceManager.prepare_rework()`/reconciliation facilities; otherwise reuse the validated checkpoint. No new CLI/IPC commands, interactive inspections, execution-ID arguments, or authorization flags.

## Context

TASK-137 already established that a proven deferral/requeue supersedes the stale failure gate. TASK-138 already recognizes `review → todo → failed → backlog → todo` as a valid historical review disposition, but `ServiceEngine._latest_reviewed_rework_report()` currently rejects the newer failed generation before normal work preparation. Meanwhile `_prepare_execution_workspace()` prepares rework from a **reviewed** predecessor and would not necessarily preserve the **later failed** checkpoint. The existing `ExecutionWorkspaceManager.prepare_rework()` already replays a proven linear worker delta onto a new product base via an internally guarded cherry-pick and verifies exact patch-equivalent replay. Reuse that mechanism wherever safe; do not introduce a second full rewrite implementation merely to use the word rebase.

Concrete retained scenario (tests MUST use disposable repositories, not these real resources): TASK-134 reviewed execution `0c944f7c8bb741a4a367c95c4cfc10d0` has genuine curator feedback. Later transport-failed execution `3cb61e2d366042aeab97caf3f4702ece` has checkpoint `7c8274a837bd5579b3ac607bd570ff8d14e5016b`, containing 18 linear commits after `5b81966afab8eac2d021332c22a04a34361736ef`. The product independently advanced to `d0572c6f7704b138ed9e7cabb33ba4c025001a89`. A user should need only the usual `backlog → todo` disposition for Devlegate to **attempt** continuation.

Earlier TASK-139 was given an unsuitable operator-repair specification and then amended *after several reviewed generations*, so its old review attribution cannot be assumed valid for its new spec. TASK-139 and the superseded TASK-140..144 series remain in backlog. This fresh ticket implements the corrected requirement against the **integrated product**, without importing unaccepted task-branch code.

## Required behavior

1. **Identify the right generations in normal owner scheduling.** On a runnable `todo` ticket, prove the exact immutable Git-control report chronology and first-parent requeue, including unchanged ticket blob, a real previous review/rework publication (when applicable), and the latest failed execution after that review. The failed report must stay `failed` and must not become a fake reviewed report. The genuine earlier review supplies feedback; the latest failed execution supplies code checkpoint, admitted base and ref/worktree identities. If there is no earlier genuine review, do not invent feedback. Do not select generations by filename sort.
2. **Continue automatically without treating history as permission to destroy resources.** Under the existing serialized owner, reject dirty/foreign/locked/missing or mismatched execution worktree, branch, Git common-dir, submodules, observed local/remote ref, live worker, conflicting intent, invalid ancestry, changed ticket or uncertain product/control generation. Historical reports alone are not proof of uninterrupted workspace ownership and do not grant a synthetic BOUND record. Fail closed with an actionable reason if current resources cannot be safely reclaimed. Do not require operator commands or a new manual authorization protocol.
3. **Carry forward the exact committed code once.** If the failed checkpoint is already based on the admitted current product head, reuse the clean validated workspace without a port. Otherwise pin the failed checkpoint and displaced remote/local evidence before effect, then reuse the **existing** `prepare_rework()` or factored reconciliation mechanisms for one lineage transplant onto the current clean product base. Verify the full cumulative linear worker delta survives (or prove it was already integrated). Guard local change with expected-old CAS and any remote branch replacement with an exact observed lease. Do not add another redundant rebase after `prepare_rework()` already cherry-picked the same delta. On conflict, stop without losing the prior checkpoint or historical evidence; durable stage/replay must not repeat a completed transplant.
4. **Keep feedback and source-code predecessors separate.** The new `execution_id` must receive the genuine feedback of the reviewed execution but resume code from the **later failed checkpoint**. In particular, the current `rework_provenance.previous_execution_id` is also used to check `review` binding during prepared-worktree recovery: do not simply replace it with the failed execution ID and create a mismatched review binding. Bind review identity and code-predecessor identity separately and preserve both across restart/prompt delivery. Do not reset the workspace back to the earlier reviewed checkpoint.
5. **Normal outcome and diagnostics.** Start a fresh worker through the standard lifecycle after safe preparation, and leave earlier reports, feedback, control history and evidence refs unchanged. Repeated service polls/restarts must not launch duplicate workers. If continuation cannot safely proceed, the scheduler/status must report the actual blocker rather than claiming runnable success. Existing normal rework, current failed-generation retry, active reconciliation and unrelated reset behavior must retain their semantics.

## Acceptance criteria

- Disposable real-Git full workflow `reviewed → rework → later transport-failed execution with two or more committed changes → todo/backlog/todo → ordinary scheduler iteration` starts a **new** worker (no manual commands or flags). Its workspace contains the entire failed checkpoint delta and its prompt includes the original genuine curator feedback.
- With unchanged current product base, no rebase/cherry-pick is performed. With advanced product base, the existing code transplant executes at most once and old checkpoint is pinned/reachable; no follow-up redundant rebase.
- A valid no-prior-review failed case either continues without invented feedback when independently proven safe, or has a precise explicit blocker if ownership/generation proof cannot be established. Never invent a review or BOUND record.
- Dirty/foreign/locked/changed/missing worktree, stale or raced refs, worker presence, uncertain observations, Git conflict, modified ticket, invalid review history and crash/restart all fail closed, preserving prior refs, evidence and worker absence.
- No new user-facing CLI/IPC mode or extra arguments are introduced.

## Required regressions

Use disposable real Git repositories and persisted owner-state/IPC tests, not real TASK-134 resources. Cover review+failed+deferral/requeue, same base and newer product base, preserving multilineage worker changes and genuine prompt feedback, absent genuine review, false review attribution, changed ticket, unknown/missing/dirty/locked/recreated workspace, submodule mismatch, wrong local/remote ref and concurrent lease change, rebase/cherry-pick conflict, crash before and after pin/worktree transition and worker launch, replay/idempotence, and normal rework/no-failure behavior. Assert old report/review bytes and original failed checkpoint/evidence remain immutable. Run focused test suite, Ruff and full configured CI on final checkpoint.

## Boundaries

**No new operator inspection/repair command or authorizations.** No generic history-migration system, no fallback that erases failed code, no TASK-134-specific production conditional, no rewriting old reviews/reports, no manual SQLite edits, and no reset/force retry. Keep one owner for Git effects and fail closed on truly unproven resource ownership.
