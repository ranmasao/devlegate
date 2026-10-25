---
"type": "devlegate.ticket"
"title": "Retire done-ticket execution worktrees and refs using published Git ancestry"
---

## Incident and objective

Real self-hosting diagnosis on 2026-10-25 found **57** cleanup candidates and **57 remaining** in the Devlegate project, with **56 blocked**; the managed worktree directory across projects occupied ~2.4 GB. Representative clean, registered, local/remote-ref-aligned, `done` worktrees TASK-001 and TASK-066 are skipped because historical acceptance/review evidence cannot be reconstructed by the modern `_accepted_execution_report()` / `resolve_accepted_execution_report()` path. TASK-088 has a nonstandard historical acceptance and different worktree HEAD versus its completed report; no deletion may be inferred from the report alone. Other refusal examples: TASK-054 malformed evidence; TASK-061 changed review; TASK-073 not done. This is a **functional resource-retirement defect**, not merely the journal visibility task TASK-093.

The cleanup decision should answer whether *current worktree contents and execution refs are still needed*, not re-adjudicate the complete historical correctness of admission, curator review, or acceptance. Do not introduce explicit ticket classification (audit-only versus code-changing), synthetic acceptance evidence or a mandatory legacy provenance migration.

## One common eligibility rule

For a candidate `devlegate/work/<ticket-id>` resource, permit retirement **if and only if** all of the following can be established against fresh observations:

1. **Durably done**: the canonical control ticket currently exists in `kanban/done`; no conflicting live/requeued generation. An `accepted`, `todo`, `review` or missing ticket is not sufficient.
2. **Exact managed identity**: the workspace, if present, is a Devlegate-owned, canonically named, registered worktree at the deterministic path for the correct repository and ticket, on the expected branch. Match worktree HEAD and every existing local/remote execution ref to the exact observed SHA. Reject changed, foreign, aliased, symlinked, ambiguous or mismatched ownership. Missing resources after partial retirement are handled as already retired, **not** guessed from path names.
3. **No unsaved work**: the existing checkout is clean, including staged, unstaged, untracked and relevant submodule modifications; no new or alternate commit is present outside the observed expected HEAD. A dirty checkout is preserved, even if its current HEAD is an ancestor of product.
4. **Published product retention**: the exact **current execution worktree/ref HEAD** (not merely an earlier report checkpoint or base) is equal to, or an ancestor of, the **freshly fetched/verified remote product-branch HEAD**. Treat unknown, unavailable, divergent or insufficiently observed history as blocked. This condition naturally covers zero-delta executions (`worktree HEAD == original base`) and committed changes already incorporated in product. Squash/cherry-pick similarity is not ancestry and must not bypass this test.
5. **No active dependencies**: current durable lifecycle, executing/owned worker, pending admission, report publication, integration, rework, automatic retry, reconciliation or recovery cannot still need this workspace or refs. Recheck service ownership and current durable state rather than concluding from `done` alone.

No special `control-only` or `audit-only` classification. A control-plane report may exist with **zero product changes**; it is valid to retire if the same ancestry rule holds. Persisted execution reports, curator reviews, lifecycle/control history and diagnostics remain untouched.

## Execution and interrupted-cleanup behavior

- **Immediately after a ticket is durably integrated into `done`**, attempt retirement using the unified rule. Failure to clean up must not undo a completed product/control integration; record a specific retryable or blocked diagnostic.
- **On each service start/restart**, independently schedule a **background, bounded cleanup pass** after the service has established its normal single-owner authority and core readiness. Do not delay service availability or worker admission for a potentially slow network/Git scan. Respect existing owner locks, state/WAL rules, serialized mutation boundaries and operator requests; no unowned independent daemon or thread mutating refs.
- Continue periodic bounded passes while candidates remain (including > one cohort); ensure a permanently blocked early candidate cannot starve later candidates. Preserve/advance `workspace_cleanup_cursor` appropriately and avoid busy polling. A restart must resume outstanding work without a special operator command.
- Make cleanup **idempotent and crash-convergent at every stage**: before worktree removal, after worktree removal but before local ref removal, after local ref removal but before remote ref removal, after a remote deletion succeeded but before acknowledgement/state update. Re-observe exact remaining ownership, current remote product ancestry and expected refs for each subsequent effect. A missing already-retired worktree/ref is success only when independently safe to recognize; never use absence as authority to delete a different ref.
- Perform worktree retirement through Git's safe managed-worktree operations (not raw recursive delete of arbitrary directories); remove local branch only after proving it is unoccupied; remove upstream branch only with compare-and-delete/lease against its exact current expected old SHA. Race/conflict/remote uncertainty is blocked/retryable, not force deletion.
- Read-only status and dry-run cleanup must apply the **same eligibility model** as apply, with per-ticket reason and remaining resource states. Keep aggregate inventory and status aligned; integrate with TASK-093 logging without requiring TASK-093 as a dependency.

## Reproduction and acceptance

1. Reproduce the 2026-10-25 observations for **TASK-001** (legacy completed report), **TASK-066** (multiple execution generations, historical review-content difference) and **TASK-088** (manual acceptance, HEAD not equal to completed report checkpoint). A case is eligible only after **actual published-product ancestry and all other invariants** are verified; do not assert eligibility on the historical facts alone.
2. Add real-Git integration tests for clean `done` worktrees at equal-to-base HEAD (zero-delta audit/test ticket) and a changed HEAD already published in product. Both must be retired even when historical review/admission proof is unavailable; control-plane evidence is retained.
3. Negative cases: dirty/untracked/submodule changes, task not done or requeued, worker or pending recovery, worktree/ref mismatch, changed remote product/ref, absent/ambiguous Git evidence, corrupt report where it affects other active safety obligations, foreign worktree, divergent/squash-only unpublished HEAD. None may suffer destructive mutation.
4. Crash-injection tests at **each** worktree/local-ref/remote-ref retirement boundary, with restart replay and externally changed refs, prove convergent partial retirement and fail-closed lease behavior.
5. Show startup-triggered nonblocking background cleanup and routine post-`done` cleanup using a controlled scheduler/owner; verify progress across > one cohort and repeated permanent blockers, absence of starvation and reasonable backoff/no journal flooding.
6. Demonstrate focused tests and full exact-SHA CI; report before/after dry-run decisions and resource states for representative reproductions. Keep tests reproducible without depending on the operator's actual home directory.

## Scope and priority

**High-priority operational bugfix.** Change cleanup eligibility/replay/scheduling and necessary tests/docs, **not** acceptance or product-integration authority. Do not delete live user resources during development, weaken single-owner/lease safety, blanket-reset worktrees, or delete historical control-plane records. Separate from TASK-093's logging-only task and TASK-085's broader recovery research.
