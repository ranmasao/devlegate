---
"type": "devlegate.ticket"
"title": "Retire completed execution worktrees without losing recovery evidence"
"depends_on": ["TASK-061"]
---

## Milestone

Devlegate 0.5.7 — execution workspace lifecycle and bounded disk usage.

## Scheduling

**Unblocked for the current 0.5.7 iteration.** The former 0.5.6 deferral
has elapsed and TASK-061 is integrated. Reuse its workflow
recovery/reconciliation boundary rather than introducing independent cleanup
authority. Do not modify the historical 0.5.6 release line.

## Goal

Reclaim on-disk Git worktree checkouts for tickets whose final result has
been demonstrably accepted and integrated into the product branch, without
erasing the committed execution history, provenance, report, or any recovery
state still needed by Devlegate.

Preserve the distinction between a *ticket's isolated working checkout*
and the *durable execution evidence*. A completed ticket need not retain
another physical checkout of the same repository indefinitely.

## Observed accumulation — 2026-10-12

The dogfood host's `~/.local/state/devlegate/worktrees` contains sibling
execution worktree directories grouped by project-instance identifier:

    worktrees/<instance-key>/control
    worktrees/<instance-key>/work/TASK-001
    worktrees/<instance-key>/work/TASK-054
    worktrees/<other-instance-key>/work/LAB-106
    ...

The user observed nearly **2 GiB** accumulated under worktrees.

The observed inventory shows:

- Devlegate's project has 43 registered `work/TASK-*` checkouts,
  including many tickets already in `kanban/done` (e.g. TASK-001);
- a separate rslab2 project has 62 `work/{LAB,DOC}-*` directories;
- the worktrees are **siblings**, not recursively nested repositories;
- the additional Git worktree checkouts share the repository's object
  database, but each contains a real filesystem checkout and initialized
  submodules that consume disk space;
- a separate `/tmp/opencode/devlegate-parent` registration is marked
  prunable by Git, but lies outside Devlegate's managed execution root
  and must not be treated as an owned cleanup target by default.

The current `ExecutionWorkspaceManager.retire()` validates a known
workspace then removes the registered Git worktree, but ordinary successful
ticket completion does not call it. Existing retirement paths mainly
include explicit `drop` and zero-delta historical execution handling.
Thus normal operation retains checkouts after completed integration.

## Scope and authoritative decision

1. Define an explicit **eligible for workspace retirement** state based
   on durable, reconciled facts. In particular, the ticket must have
   reached `done` through a proven accepted-integration transaction, and
   all associated current execution work/worker/reconciliation/continuation
   dependencies on its checkout must have been conclusively discharged.
   Worker result `completed` or control status `review`/ `accepted`
   alone are **not** sufficient.
2. Validate exact instance root, ticket ID, registered Git worktree
   location, branch identity and expected HEAD/provenance before any
   deletion. Never operate on guessed paths or on any other project
   instance's checkout.
3. Verify no live/indeterminate execution owner, pending worker admission,
   unfinished execution, unresolved `failed`/interrupted operation,
   publication/accepted-integration transaction, retry, forced retry, or
   reconciliation could still rely on the checkout. Ambiguous state must
   fail closed and retain the tree.
4. Reuse the existing `ExecutionWorkspaceManager` registration/path,
   clean-tree and submodule safety checks, and the single serialized
   mutation-owner model. Use the existing verified worktree-retirement
   operation or a narrow equivalent. Do not use bare `rm -rf`,
   global `git worktree prune`, speculative orphan cleanup or a
   second background actor with independent mutation authority.
5. Do **not** delete committed history, execution report artifacts,
   control history, evidence refs, or needed work branches as a side
   effect of removing the physical checkout. Branch-ref retirement,
   Git object garbage collection and retention policy are separate
   decisions and must not be silently coupled to disk checkout cleanup.
6. Skip dirty/unregistered/mismatched/symlinked/modified trees rather
   than forcing deletion; surface the exact blocked reason. Be careful
   that initialized submodules require proper Git-aware worktree
   retirement, not recursive filesystem unlinking.

## Lifecycle and crash consistency

- Trigger safe retirement after a committed/observable accepted-to-done
  integration, not in the middle of the product/control Git transaction.
- If a service restart or crash occurs between proving eligibility and
  actually retiring the checkout, resume or re-evaluate safely under the
  owner lock. Do not retire a checkout whose status or identity changed.
- A completed retirement should be idempotent. Missing registration
  and absent path for an already retired ticket are not a failure.
- Pending, failed or stale cleanup attempts must not bring down a hosted
  Devlegate service or block unrelated runnable tickets, but must never
  conceal ownership/state corruption as a successful cleanup.
- Keep explicit `drop`, zero-delta retirement and recovery semantics
  intact. Do not turn `review` or interrupted work into disposable state.
- Provide concise observability for retired/skipped/blocked decisions,
  including evidence of bytes or worktrees reclaimed where practical.

## Existing historical worktrees

Provide a **conservative, bounded reconciliation sweep** for already
completed tickets whose worktrees were never retired. It must use the
same eligibility/provenance checks as new retirements and must not infer
eligibility merely from a directory name, an old timestamp, or the
presence of `kanban/done` without appropriate integration evidence.

Expose a read-only list / dry-run summary of eligible and skipped
worktrees before applying the historical cleanup. The initial historical
sweep should be operator-initiated or explicitly enabled by an operator;
do not silently attempt to delete the entire backlog on first service
startup.

Do not discover or clean unrelated `/tmp/opencode` worktrees or other
project instance keys. Any stale external Git registration is a separate
ownership question.

## Worker test scope — focused verification, full CI afterward

This cleanup touches ownership-sensitive Git worktree retirement. Use `tests/TEST_BOUNDARIES.md` and `tests/TEST_PLACEMENT.md` to keep direct semantic proofs and required real-worktree/submodule companions. Preferred local commands:

```sh
./dev test -q tests/test_execution_workspace_submodules.py tests/test_git.py
./dev test -q tests/test_control_workspace.py -k "worktree or branch or drop or cleanup"
./dev test -q tests/test_control_recovery.py -k "integrat or retirement or accepted or drop"
./dev lint
```

Add targeted positive/negative tests for new retirement and historical dry-run behavior in their narrow owning modules, including subprocess/host tests only where the actual service/restart boundary matters. These existing-file filters are initial regression selections, not substitutes for new behavior coverage.

**Do not run by default:** unfiltered `./dev test`, `./dev test-parallel`, `./dev check`, `./dev coverage`, full `test_cli_topology.py`, or unrelated packaging tests. GitHub Actions is responsible for full exact-checkpoint pytest with coverage and Ruff; the reviewer verifies its result. Report focused commands/results and any proof gaps without spending the worker execution budget repeatedly timing out on the entire suite. Do not modify/retire worktrees beyond the admitted project instance while testing.

## Required regressions

1. Ticket remains `todo` or `review` after a checkpoint: retained.
2. Ticket in `accepted` awaiting integration: retained.
3. Fully proven accepted-to-done integration with a clean owned
   checkout: retirement succeeds and Git registration disappears.
4. Ordinary successful execution without accepted integration:
   not eligible.
5. Dirty, symlinked, unregistered or mismatched branch/HEAD:
   fail closed with preserved evidence.
6. Active, indeterminate or still-owned worker boundary and any
   pending retry/reconciliation/recovery: checkout retained.
7. A different instance's registered worktree and external/prunable
   Git registrations: never touched.
8. Process restart/crash during retirement and repeated cleanup:
   idempotent with no duplicate or unsafe effects.
9. Historical `done` inventory: dry run reports candidates/skips;
   authorized sweep frees only proven-safe checkouts.
10. Preserved execution reports, control provenance, commit/branch
    evidence and ability to inspect historical changes after cleanup.
11. Existing submodule preparation, zero-delta and explicit-drop
    regressions remain green.

## Acceptance criteria

- Newly integrated `done` tickets no longer accumulate permanent
  physical execution worktrees solely because of normal completion.
- The dogfood historical accumulation can be reduced through the
  bounded, proven-safe sweep without disrupting still-recoverable work.
- No running worker or unresolved Git/provenance state is destroyed.
- Retention of Git history and report evidence is independent of
  physical workspace lifetime.
- Runtime/status diagnostics accurately distinguish eligible,
  retired, skipped and blocked cleanup decisions.
- Full authoritative CI with tests, coverage, licensing and Ruff
  passes on the exact final checkpoint.

## Non-goals

- No 0.5.6 release changes, including TASK-054 and TASK-062.
- No generic GC of Git objects, work branches, logs, caches or
  user-owned temporary files.
- No new worktree isolation layout or redesign of worker cgroups.
- No migration machinery for unrelated historical runtime formats.
- No cleanup of arbitrary Git worktree registrations outside the
  exact managed project execution root.

## Reviewer decision — 2026-10-17, execution e3ed8fab: REWORK REQUIRED

**Execution:** `e3ed8fab841949d9bebcf0500206c8fd`. **Checkpoint:** `b1d452e66e36cd692c72493f357ecf801ccd7ee2`. **Product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. Worker claimed `completed`; the implementation changes only `src/devlegate/runtime.py` and `tests/test_control_workspace.py`, adding one new dry-run negative regression. Keep these artifacts and worktree intact. The work is useful but **not acceptable yet**: the cleanup code crosses a published-transaction boundary unsafely and the historical sweep is not yet operator-accessible/authorized.

### Blocker 1 — worktree cleanup must not turn successful accepted integration into a failure

`_integrate_accepted()` now calls `_retire_integrated_workspace()` *synchronously after* `_complete_accepted()` has already created/pushed the canonical `accepted -> done` control commit (and after the product checkpoint was published). `_retire_integrated_workspace()` raises `WorkflowBlockedError` for any dirty, mismatched, missing/ambiguous or unexpected checkout, and `ExecutionWorkspaceManager._validate_existing()` / `.retire()` can raise `ExecutionWorkspaceError`. The existing integration caller catches only `DevlegateError`, `OSError`, `ExecutionReportError`; workspace-specific errors can escape the service loop. Worse, an optional cleanup failure leaves `accepted_integration` pending even though `done`/product publication already succeeded, potentially blocking unrelated tickets indefinitely.

**Required:** make post-integration cleanup nonfatal and isolated from the already committed publication. Preserve and finalize the accepted-integration durable state regardless of an ordinary workspace retention/cleanup refusal; record an actionable skipped/blocked cleanup diagnostic. Do not try to undo published commits or force-delete unknown worktrees. On restart, re-prove eligibility before a separate safe retry; maintain exact checkpoint/report and Git evidence. Exercise success, dirty/mismatched checkout, Git remove failure, post-publication crash/replay, and a subsequent unrelated runnable ticket. The invariant is **integration succeeds and evidence persists even when cleanup fails**.

### Blocker 2 — historical sweep has no operator-facing, owner-admitted interface

The new `ServiceEngine.cleanup_workspaces(apply=False|True)` method is not wired into CLI/IPC dispatch, owner serialization or an operator-authorized workflow. Its `apply=True` branch can invoke mutating `git worktree remove` without a demonstrated owner/lock/admission gate. The task requires an operator-initiated or explicitly enabled initial historical sweep, read-only inventory/dry-run and a bounded apply surface. It must not create a second ad-hoc mutable engine from a CLI process.

**Required:** provide a concrete supported operator UX that lists dry-run candidates and performs an explicit requested apply **through the existing service owner**, or a provably equivalent authorized operation, with safe bounded enumeration, precise status and repeated-run behavior. Apply must revalidate each target before effect; no arbitrary `rm -rf`, global prune or cleanup of external/different-project registrations. Preserve read-only operation when not applying; avoid a speculative framework.

### Blocker 3 — historical evidence and coverage are not sufficient

`_find_accepted_integration_commit()` scans `git log --all -- kanban/done/<ticket>.md` and checks that some exact accepted-to-done control commit exists. This alone does not establish that the commit belongs to the **current canonical control lineage** and corresponds to the specific execution report/checkpoint/registered HEAD being retired. Restrict historical eligibility to the authoritative integrated ticket history plus proven exact execution identity; preserve branch/ref history, reports, pins and all unsettled retries/reconciliation. Missing/unreadable Git history must fail closed, not look like absence eligible for deletion.

Only **one** new test (`test_workspace_cleanup_dry_run_retains_unintegrated_execution`) was added. Add focused positive and negative tests for accepted-to-done retirement, cleanup refusal without blocking publication, clean/dirty/wrong/occupied/symlinked registrations, another instance, active/unknown worker, recovery and repeat/idempotent execution, operator dry-run/apply and historical integration provenance. Use existing `tests/TEST_BOUNDARIES.md` for engine, IPC-owner and selected process-topology proof as needed. Do not duplicate the full suite.

### Worker verification and reviewer gate

The worker report says `./dev lint` was unavailable because `.venv` was missing, despite other claimed focused lint results. When local tools are available, use `./dev setup` once and run the ticket's **selected** tests and `./dev lint`; otherwise report the exact environment limitation. **Full pytest/coverage and Ruff belong to GitHub Actions** on the exact new checkpoint; do not repeatedly run full local pytest. CI alone does not waive the blockers above. Track the current checkpoint CI: [GitHub Actions #39012520391](https://github.com/ranmasao/devlegate/actions/runs/39012520391).

**Disposition:** return the SAME TASK-063 from `review` to `todo`, preserve the worker branch and all execution reports, and do not integrate the checkpoint until the cleanup publication boundary, authorization and regression proof are corrected.
