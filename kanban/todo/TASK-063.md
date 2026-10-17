---
"type": "devlegate.ticket"
"title": "Automatically retire integrated execution Git resources"
"depends_on": ["TASK-061"]
---

## Milestone and authoritative scope — revised 2026-10-17

Devlegate 0.5.7. TASK-061 is already integrated. **This section and all
sections before the historical review archive supersede conflicting older
TASK-063 instructions**, including the old prohibitions on deleting work
branches, operator-only historical cleanup, and the mandatory public
`cleanup-workspaces` command. Keep the existing worker code, execution
checkpoints, tests and review history as recoverable inputs; redesign and
complete this SAME TASK-063, not a follow-on ticket. Do not modify 0.5.6.

### Actual accumulation and motivation

On 2026-10-17, live GitHub inspection of `ranmasao/devlegate` showed
**80 remote branches**, including **61 `devlegate/work/TASK-*` branches**;
**58** of those belong to the **59** tickets currently in canonical
`kanban/done`. The other three worker branches belong to not-yet-done
TASK-063, TASK-073, TASK-079. The control branch also contains **62**
`executions/TASK-*` folders; the latter are durable execution evidence,
**not** targets for deletion.

The prior 2026-10-12 dogfood-host inventory recorded almost **2 GiB** in
`~/.local/state/devlegate/worktrees`, 43 registered Devlegate execution
checkouts and 62 checkouts for a *different project instance*, with real
submodule checkouts. These host counts are historical, not a fresh filesystem
measurement. A stale external `/tmp/opencode/devlegate-parent` Git
registration is **outside** the owned workspace root and must remain outside
automatic cleanup.

**Legacy evidence matters:** the original TASK-001 control integration commit
is titled `Devlegate integrate TASK-001` and lacks modern
`Devlegate-Execution` and `Devlegate-Checkpoint` message trailers; its
execution report and remote worker branch do exist. New cleanup should reclaim
historical artifacts whenever independent, exact product/control/report/ref
evidence safely proves eligibility; don't assume every old done transition
has the current message format. Truly ambiguous cases stay blocked and are
reported, not guessed or forcibly removed.

### Interpretation clarification — universal cleanup, not a legacy mode (2026-10-17)

**One age-agnostic reconciler is the requirement.** Discover and evaluate every owned disposable execution worktree, Git registration and local/upstream work branch regardless of when it was created, whether it came from an old or current Devlegate version, and whether the integration commit contains modern trailers. Historical/trailerless executions are **test fixtures for the same owner-driven reconciler**, not a separate cleanup subsystem, one-time migration, operator command, opt-in or weaker deletion policy. The owner must attempt to prove eligibility for all discovered resources, not prefilter by age/format or the presence of a cleanup receipt.

**Attempted evaluation is not forced deletion.** If equivalent independent control/product/report/ref/ownership evidence proves the exact integrated execution and no current dependency remains, reclaim it automatically; otherwise retain the resource, report the concrete missing or conflicting evidence and retry later. A particular early ticket such as TASK-001 need not be deleted when its available evidence cannot establish identity; merely being old or lacking trailers must not itself rule out proof. Test both safely provable old data and genuinely ambiguous old data **through the same automatic path**.

## Required steady-state contract

After Devlegate **proves** a particular execution's checkpoint was published
into the authoritative product branch and its exact `accepted -> done`
transition was published to canonical control, AND proves no live or pending
execution/recovery/retry/reconciliation depends on it, that execution must
eventually leave **none** of these transient resources:

1. No physical owned execution worktree, including submodule checkouts.
2. No Git worktree registration for that execution.
3. No corresponding **local** `refs/heads/devlegate/work/<ticket>` branch.
4. No corresponding **upstream** `refs/heads/devlegate/work/<ticket>` branch.
5. No equivalent disposable per-execution refs/checkouts introduced by this
   implementation merely to avoid deleting the four resources above.

This covers **already completed historic tickets** as well as all new
integrations. Normal operation must **not** require a CLI invocation,
operator approval, dry-run review or a previously persisted cleanup request.
Cleanup does NOT delete the published product commits, canonical Kanban
history, durable execution reports, necessary immutable proof records, or
unrelated refs/branches/worktrees. Object database GC and generic cache/log
retention are outside this ticket. Distinguish genuine recovery/evidence refs
(such as a special authorized recovery ref) from disposable work branches;
never delete permanent evidence by a broad namespace pattern.

An ambiguous or changed identity is **blocked** (with an actionable
diagnostic), not an invitation to force deletion. An unsafe checkout must
not be deleted, even if the ticket is `done`. Once ownership/evidence is
again safely provable, it should be retried automatically.

## Automatic reconciliation, not a one-shot deletion hook

Implement **one shared, idempotent reconciliation mechanism** for live and
historical cleanup. Determine missing work from **durable published product
and canonical control facts plus actual Git registrations, local refs and
freshly observed remote refs**, not only a `cleanup_pending` marker.

It must run:

- **Immediately after** the durable product/control integration completes
  (best effort, independently of whether service finalization crashes).
- **At each service startup/restart**, autonomously, including a crash
  immediately after the `done` commit and **before any cleanup intention
  could be recorded**.
- **Periodically / opportunistically in the running hosted service**, with a
  bounded low-priority pass and fair yielding to normal ticket execution.
  Continue additional passes until safe backlog is drained, and revisit
  blocked/remote-unavailable cases later without hot-looping.

Use the existing serialized service owner, instance lock and mutation
authority; a background reconciliation job is logical service maintenance,
**not** an independent process or actor mutating the same repository
concurrently. Background failures cannot roll back a published integration,
leave it perpetually pending, crash the hosted owner, or prevent another
runnable ticket; record and retry them conservatively.

**Discover resource-only leftovers.** A branch can remain locally or remotely
even after its worktree has already been removed; enumerate and evaluate
those branches independently of live worktree registrations. Also detect
stale registrations and interrupted partial removals without traversing or
deleting arbitrary directories.

## Exact proof and deletion stages

- Scope to the configured repository, authoritative product/control branches,
  project instance and canonical `devlegate/work/<ticket>` identity. Prove
  the exact execution generation, accepted-to-done control lineage,
  checkpoint presence in the freshly observed **published** product
  ancestry, immutable report identity, actual registered path and/or
  branch SHA, and absence of unsettled owners. Do not infer ownership from
  a matching name or age. Do not delete another project's instance.
- Handle legacy done generations with stronger equivalent evidence where
  available, rather than depending unconditionally on modern commit trailers.
  If such evidence is insufficient, report exactly what is missing and retain
  the resource. Never mistake an older done event for proof about a newer
  execution of the same ticket ID.
- Safely retire the **registered clean worktree first**, using existing
  `ExecutionWorkspaceManager` / submodule and symlink protections; then
  release its **local branch** after rechecking its exact SHA, that no
  worktree still uses it and that its commit is proven integrated; then
  release the **remote branch** with a fresh exact-ref observation and a
  compare-and-delete lease against its expected SHA. The precise order of
  the final two stages may vary, but each stage must be independently safe,
  re-observed and retryable. No blind `git branch -D`, speculative
  `git push --delete`, bare `rm -rf`, global `git worktree prune`,
  arbitrary Git GC or broad remote namespace deletion.
- Absent path/registration/local ref/remote ref is a **completed substep**,
  provided no conflicting ownership evidence exists. A changed/mismatched
  branch or dirty/unknown/occupied/symlinked checkout is **blocked**,
  retains data and leaves other cleanup work runnable. A remote outage is
  retriable, not evidence that a branch is absent.
- Preserve accurate observability per *resource* and ticket: discovered,
  eligible, removed, already absent, blocked with reason, pending retry.
  Count reclaimed resources accurately; do not misreport completion just
  because one substep succeeded.

Git cannot atomically remove all these resources. A durable queue/progress
receipt **may** help diagnostics, but must **not** be the only mechanism
for rediscovery or resumption. A process may stop after any effect, including
after published `done` and before queue/admission; the next run reconstructs
remaining work by reconciliation. Avoid an oversized general-purpose
cleanup scheduler.

For automatic passes, the **internal** bounded scan/processing budget is
a per-pass fairness and latency control, never a lifetime cap. The owner
must progress through all safely eligible resources, retry blocked resources
later, and continue normal ticket scheduling. Do not expose internal pass
budgets, cursor or candidate cohort as public cleanup CLI options.

## CLI / owner IPC surface: `devlegate clean` — user decision 2026-10-17

**Cleanup is autonomous. The CLI is for observation, NOT for manually
advancing the reconciler.** Replace the provisional public
`cleanup-workspaces` and `clean sweep` interfaces as follows:

- `devlegate clean` (no arguments): show a **read-only** owner-consistent
  inventory and summary of remaining owned worktrees/registrations, local
  and upstream disposable work refs, eligible / pending / blocked work,
  reclaimed resources and clear reasons why specific remnants remain.
  Reuse existing table/JSON output conventions.
- `devlegate clean status`: optional explicit alias of exactly the same
  read-only status operation if retaining a `clean` subcommand group is
  useful; do not require `status` when `clean` alone suffices.

**Do NOT expose `devlegate clean sweep`, public `--limit`, `--apply`,
or a public mutating `clean-sweep` IPC method.** The older public
`cleanup-workspaces` command must remain absent. No `clean ticket`
mutation is required by this task. Remove obsolete CLI/parser/IPC/dispatch
paths and update help, tests, docs. Internal method names may remain if
useful; retire or isolate now-unused operator mutation receipts without
damaging historical receipt recovery or existing runtime safety.

The service itself owns discovery, scheduling, retries and bounded work;
manual cleanup should not be needed even after crashes. The status path
must make no filesystem/Git/ref/receipt mutations, must not race the live
service owner via shared mutable observation fields, and must not report a
clean state if a remote cannot be observed or an internal reporting budget
only covered a subset. Use a published owner-safe snapshot or equivalent
coherent read-only mechanism. Preserve safe failure diagnostics.

## Verification and acceptance tests

Use existing Git test fixtures, owner IPC and process-restart harnesses.
Run focused local tests and lint; authoritative full tests/coverage/Ruff
should pass in exact-checkpoint GitHub Actions. Required real-Git proofs:

1. A properly published `accepted -> done` integration automatically
   retires **worktree registration + checkout + local branch + upstream
   branch**; durable report, product commit and canonical control history
   remain and are queryable.
2. Crash **right after `done` but before scheduling cleanup**; fresh
   service startup discovers all leftovers and finishes without a recorded
   pending cleanup. Also crash between worktree/local-ref/remote-ref
   deletion, then restart. Exercise already-absent each-stage cases.
3. After initial backlog discovery, autonomous bounded background passes
   converge on old safe done tickets while normal execution remains
   runnable. Include a remote-branch-only orphan and a
   locally-registered-worktree-only remainder.
4. For a historic done ticket, verify actual safely authorized deletion
   in a live owner pass, not just a direct `manager.retire()` call. Test a
   **legacy done transition without modern trailers** where equivalent
   exact evidence suffices; and a legacy/foreign/mismatched case where
   deletion must be refused.
5. A completed but still-active/recoverable `review`/`accepted`/`todo`
   execution, a requeued ticket ID with a different execution generation,
   wrong or moved remote ref, dirty/unregistered/symlinked worktree,
   ambiguous root or foreign project instance must be preserved with
   actionable reason.
6. Git observation error, remote outage and `worktree remove` refusal
   after published `done` cannot undo completion or block the next ticket;
   retry after restart resolves when conditions are restored.
7. `devlegate clean` (and optional `clean status`) are complete,
   coherent, strictly read-only inventory operations; no public sweep,
   apply or limit survives. Verify CLI help and IPC unknown-method refusal
   for retired public methods, and read-only status while the hosted
   owner is simultaneously reconciling (no shared mutable-state race).
   Status/IPC reporting is accurate after partial effects, unreachable
   upstream, restart and internal pagination. Preserve drop, zero-delta,
   recovery, submodules and previous targeted regressions.
8. With a **blocked earliest candidate** and at least one **fully proven
   later eligible candidate**, repeated owner-hosted automatic passes must
   really remove the later worktree and both branches (not merely rotate a
   cursor). A fresh hosted owner after restart must continue progressing
   with no manual request; unchanged blocked work cannot monopolize it.

**Acceptance means observable convergence:** on a safely proven completed
execution there are no remaining `work/<ticket>` local/upstream branches,
registered worktrees or owned checkout directories; no manual cleanup is
needed even if the process stopped before recording intent. Verify legacy
backlog actually shrinks and report uncleared unsafe artifacts explicitly.
Successful CI alone does not establish this contract.

## Test budget and non-goals

Use `tests/TEST_BOUNDARIES.md`, `tests/TEST_PLACEMENT.md` and targeted
real-Git + service-owner tests; prefer focused `./dev test` selections and
`./dev lint`. Do **not** repeatedly run unfiltered local suites; full
exact-checkpoint verification belongs to CI. Do not rewrite 0.5.6,
redesign worker cgroups or Git workspace topology, delete arbitrary
`/tmp/opencode` registrations or other project's workspaces, or perform
global Git GC, log/cache cleanup or unbounded sweeping.

## Historical reviewer decisions (preserved; superseded where conflicting)

The dated notes below explain past worker checkpoints and earlier reviewer
decisions. Their instructions to preserve worker branches, require
operator-only historical cleanup, expose `cleanup-workspaces` / `clean
sweep`, or bind the aggregate lifetime deletion count to a manually
admitted cleanup request are **obsolete under the 2026-10-17 user decision
above**.
Preserve their technical observations as regression context, not as
the current product requirements.

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

## Reviewer decision — 2026-10-17, execution f116b412: REWORK REQUIRED

**Execution:** `f116b412d37b403da8ebe5a590ca0888`; **checkpoint:** `963d47ae829a659ca0729bc66f5a7550b44b4a67`; **base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. Worker reports `completed`, with no remaining items. Retain the previous work and the current changes; make a **narrow follow-up** in the same TASK-063.

### Improvements verified

- `_integrate_accepted()` now treats post-publication workspace-retirement refusal as optional and continues to finalize the already published accepted-to-done lifecycle.
- `devlegate cleanup-workspaces [--apply] [--limit N]` is exposed through CLI and IPC; the effect goes through the existing single service owner, not a second process independently mutating Git.
- Historical candidate discovery now uses `HEAD` rather than `git log --all`, and `_accepted_execution_report()` provides acceptance-bound execution provenance rather than trusting an arbitrary matching report.
- New IPC validation tests reject an oversized limit and verify request routing. Do not undo these changes.

### Blocker 1 — dry-run is not read-only; receipt/effect replay is incomplete

The CLI dry-run sends `cleanup-workspaces` with `mutable=True`. `_record_operator_admission()` persists a `mutable_receipts` entry in SQLite **before** `cleanup_workspaces()` performs even its dry-run. This violates the requirement for a genuinely read-only inventory. Furthermore a timeout/crash/error after the receipt is committed but before or during apply means replay of the same request ID returns only `{"accepted": true, "ticket_id": "__workspaces__"}`, **not the cleanup decisions or a durable in-progress/completed result**, and does not safely continue the unfinished sweep. That is not sufficient idempotency/observability for a mutation that may delete multiple worktrees.

**Required:** make the dry-run nonmutating in both Git and runtime persistence while keeping serialization/consistent observations. For apply, define and test a narrow durable result/progress or conservative re-observation/retry protocol under the existing owner that never mistakes admission for completed cleanup; do not add a generic job engine. Demonstrate behavior after request-ID replay, timeout/process loss and partial removal. The CLI should accurately report actual completed results or explicit indeterminate/partial status.

### Blocker 2 — cleanup refusal diagnostics are not an exposed verified operational state

The post-publication wrapper catches **all `Exception`** and silently converts programming errors or unexpected corruption into ordinary `blocked` cleanup, despite the ticket's requirement not to conceal ownership/state corruption. It sets `self._state["workspace_cleanup"]`, but no `status`/projection code consumes that value, and no schema validation identifies a durable pending/skipped cleanup record or proven repeated cleanup after restart. A log line is useful but insufficient as the sole ongoing status/diagnostic surface. The outer `_save_state` eventually retains the field, but it is not inspectable via a documented user operation except resweeping.

**Required:** contain expected checkout/Git/OS cleanup failures explicitly, without disguising unrelated programming failures as a normal refusal or endangering already-published integration. Publish clear actionable `retired/skipped/blocked` result status with exact ticket/evidence identity, and demonstrate it can be queried or safely re-observed after restart. Keep accepted integration finalized even if worktree removal refuses.

### Blocker 3 — missing real ownership, deletion and crash-replay regression proof

This checkpoint adds only two IPC tests using a **monkeypatched** `submit_cleanup_workspaces`. The prior checkpoint added one `test_workspace_cleanup_dry_run_retains_unintegrated_execution`. These do not prove the critical new behavior of `manager.retire()` / `git worktree remove --force` under the live service owner.

Add focused real-Git/engine and owner/IPC tests for: clean `done` checkout removed with Git registration gone, exact report/Git evidence and branch retained; dirty/occupied/symlinked/wrong-HEAD tree skipped without destroying data; failed Git removal *after done publication* still completes integration and allows a subsequent ticket; historical dry-run and explicit apply with a proven eligible `done` ticket; mismatch between current accepted execution and historical checkout; different instance retained; idempotent repeat/partial apply and restart. Cover emitted result/status and real owner admission (a stub-only dispatch test is insufficient). Keep test scope narrow rather than repeating full local pytest.

### CI and disposition

New exact checkpoint CI [run #39013150019](https://github.com/ranmasao/devlegate/actions/runs/39013150019) was **in_progress at review**. Full successful pytest/coverage and Ruff at the final rework SHA are mandatory, but alone cannot substitute for these safety tests. Worker should use the documented focused test commands; prepare `.venv` via `./dev setup` only if necessary to run them.

**Disposition:** return the SAME TASK-063 from `review` to `todo`. Preserve checkpoint, all reports, previous rework, and Git history; do not integrate unsafe historical cleanup as-is.

## Reviewer decision — 2026-10-17, execution 1d6cae0f: REWORK REQUIRED

**Execution** `1d6cae0f6732455f88846a8049dd245e`, **checkpoint** `3310ca9f23185eadaf7f2e5a9cbf728c646b4a0c`, **product base** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. Worker claim `completed`, `remaining=[]`, report in `executions/TASK-063/`. Preserve all existing checkpoints, receipts, Git history, reporting and the newly implemented cleanup functionality; this is **focused completion of the same ticket**, not a redesign.

### Improvements preserved

Third checkpoint separates dry-run from persistent mutable admission (`command.mutable=False`), persists apply results in mutable receipts and re-observes incomplete admitted apply, exposes `workspace_cleanup` through the status projection and IPC decoding, and narrows post-publication cleanup exception handling. Preserve those changes, including canonical-history/accepted-report checks and owner-serialized apply from preceding checkpoint.

### Blocking exact-SHA CI regressions

[GitHub Actions run #39013762950](https://github.com/ranmasao/devlegate/actions/runs/39013762950) on **exact SHA `3310ca9f2318`** finished **failure**: `1241 passed, 2 failed, 1 skipped`; Ruff was skipped, so cannot claim lint success based on this CI.

1. `tests/test_ipc_client.py::test_status_round_trip_preserves_complete_view`: expected status keys now lack newly exposed `workspace_cleanup`. Update the contract test for the real new field and verify round-trip of nonempty status/cleanup decision, not merely weaken expected-key assertions.
2. `tests/test_ipc_owner.py::test_reconcile_resume_admission_persists_receipt_with_resolving_state`: `src/devlegate/runtime.py::_record_operator_admission` now accesses `command.mutable`; existing test's `SimpleNamespace` fixture does not contain the newly introduced field. Adjust the fixture to the real operator protocol (or implement an explicit safe default if genuinely required for production compatibility). Preserve normal reconcile-resume receipt persistence; do not alter its semantics simply to appease the test.

Require **full exact-checkpoint CI passing both pytest/coverage and Ruff** on the next worker SHA. Run only selected local tests and lint, not repetitive full local pytest.

### Remaining essential cleanup safety proofs

This checkpoint adds only one assertion to `test_workspace_cleanup_dry_run_retains_unintegrated_execution`. Across all three worker checkpoints the new tests chiefly cover negative dry-run plus mocked IPC dispatch; they **do not prove safe real-Git worktree deletion** or interrupted multi-target apply. Add focused and realistic integration tests using existing `control_fixture`, `git`, host/owner test harness and real `git worktree list/remove` as appropriate:

- Accepted-to-done with a valid, clean owned worktree -> removed and unregistered, but exact execution report, work branch, control and product history remain intact. Retained or dirty/changed worktree -> cleanup refusal only, accepted integration still reaches `done` and next ticket is runnable. Include an injected `worktree remove` failure.
- Operator `cleanup-workspaces` **dry-run through the live service owner** produces inventory without changes to SQLite receipts or filesystem. Explicit `--apply` removes only a proven historic done-worktree, never review/accepted/active execution, other project instance, or unowned/symlinked checkout; output distinguishes eligible, skipped, retired.
- For multiple eligible worktrees, interrupt after partial apply / before durable result publication, then recover using the same request identity (and also a new explicit request). Re-observe actual Git registrations, never delete anything twice or return a fabricated completed outcome. Verify completed replay returns the saved decisions. Cover process owner authorization and service restart if the existing harness supports it without flaky sleeps.
- Verify `workspace_cleanup` status projection/IPC round-trip with actual retired and blocked outcomes after persistence/restart. Identify unexpected Git observation failure as blocked/refused, not successful removal. Existing submodule and explicit-drop behavior must remain intact.

Do not add a new generic cleanup scheduler, global prune/GC, or cross-project cleanup. Do not conflate validated but operator-skipped cleanup with accepted publication failure. Inspect effectful Git calls and make sure only expected registered worktree removals occur.

**Disposition:** move TASK-063 from `review` to `todo`. Preserve all execution artifacts and the worker branch; no manual product integration. TASK-063 is not accepted until full CI and these material safety invariants are proven.

## Reviewer decision — 2026-10-17, execution c09d7653: REWORK REQUIRED

**Execution:** `c09d7653487745a1bbe65eca7f97f58a`. **Exact worker checkpoint:** `569810312055be210fa4ab24c227a33c47666b53`. **Product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. The worker reports `completed` with `remaining=[]`; preserve its checkpoint, branch, reports and all prior rework. This is a narrow continuation of TASK-063.

### Improvements verified

- `src/devlegate/cli.py` now sets IPC `mutable=apply` for `cleanup-workspaces`. A dry run no longer opts into mutable IPC replay semantics.
- The status round-trip fixture includes an actual nonempty `workspace_cleanup` value, and the reconciliation admission fixture supplies the protocol's `mutable=True`.
- The new real service-owner IPC dry-run test checks unchanged checkout, Git worktree registration and mutable receipts. These are genuine improvements, but they prove only a negative/nondeleting case.

### Blocker 1 — ordinary Git observation refusal can still escape after done publication

`_integrate_accepted()` catches selected cleanup failures after `_complete_accepted()`, but does **not** catch `subprocess.CalledProcessError`. During `_retire_integrated_workspace()`, `ExecutionWorkspaceManager._registrations()` invokes `git worktree list --porcelain` through `run_git(check=True)`. A nonzero Git exit thus escapes the optional cleanup guard **after** publication, potentially interrupting lifecycle finalization despite a durable `done` transition. Other checked Git observations inside the manager may exhibit the same behavior.

Translate expected nonzero Git observation/registration errors into the narrow cleanup refusal boundary (or explicitly contain the appropriate subprocess error there), without restoring `except Exception` or hiding programmer bugs and genuine state corruption. Persist an actionable blocked diagnostic, finalize accepted integration, and allow a subsequent unrelated ticket. Prove this with injected checked-Git observation failure as well as `git worktree remove` refusal; verify crash/restart behavior when `done` has already published.

### Blocker 2 — historical integration evidence is not bound to the same execution generation

`cleanup_workspaces()` independently obtains the latest accepted execution report and calls `_find_accepted_integration_commit(ticket_id)`. That lookup accepts **any** exact `accepted -> done` commit for the ticket ID in canonical `HEAD` history. It does not prove that the returned transition integrates the accepted revision/report/checkpoint currently used to validate the registered worktree, nor explicitly prove that checkpoint reached the product branch as part of that specific transition.

A prior legitimately integrated generation of a requeued/reused ticket ID may therefore satisfy the historical transition check for a later, not-integrated generation, including a later `done` state reached by a noncanonical/manual control move. Fail closed unless the canonical integration event, its accepted parent, report identity and product checkpoint form one exact verified lineage. Add a regression with an older accepted-to-done integration followed by a newer different execution/accepted generation without proven product integration; the newer worktree must be retained.

### Blocker 3 — deletion, owner-authorized apply and partial recovery still lack required real-Git proofs

The fourth checkpoint adds only a live-owner **dry-run of an unintegrated ticket** and fixes existing status/fixture tests. Across all checkpoints, targeted new tests do not yet establish the task's principal deletion and replay invariants. Add focused tests (reuse existing real Git and host harnesses):

- Proven clean `accepted -> done`: registered worktree really removed, Git registration absent, branch/ref, execution report and control/product history intact; dirty/wrong-HEAD/occupied/symlinked checkout and Git refusal retain data without blocking finalized publication or the next ticket.
- Actual owner-authorized `cleanup-workspaces --apply` of a historical proven `done` ticket; negative cases for review/accepted, mismatched provenance, foreign instance and unowned checkout. Verify operator dry-run causes no persistent receipts.
- Two eligible historical worktrees with a deliberately interrupted partial apply and re-observation/restart: same request ID replay, new explicit request, completed receipt replay and accurate durable `eligible/retired/skipped/blocked` status. No duplicate deletion, premature completed acknowledgement, or outside-root effect. Preserve submodule and drop regressions.

### Exact-checkpoint CI

[GitHub Actions #39014536885](https://github.com/ranmasao/devlegate/actions/runs/39014536885), for **`569810312055`**, completed **failure**: **1243 passed, 1 failed, 1 skipped**. Its sole failure is `tests/test_standalone_builder.py::test_standalone_assets_match_pinned_science_reference_mirror` because the external `astral-sh/python-build-standalone` release API returned HTTP 403 `rate limit exceeded` to `science`. This is an external infrastructure/rate-limit failure, **not evidence of a TASK-063 functional regression**. Ruff was skipped; do not claim the full CI gate passed. Rerun exact-final-SHA CI after the targeted rework; avoid unrelated changes to standalone packaging merely to mask rate limiting.

**Disposition:** return the SAME TASK-063 from `review` to `todo`. Do not integrate the checkpoint yet. Preserve all prior work and focus on the three blockers above; full CI success (pytest/coverage and Ruff) is still required on the final SHA.

## Reviewer decision — 2026-10-17, execution 0d446b19: REWORK REQUIRED

**Execution:** `0d446b19a673464a92f512d3b9d4ed07`. **Worker checkpoint:** `d48221206a8f8bc08f60b1c3557ce4366aa22b18`. **Product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. The worker reports `completed`, `remaining=[]`; retain all work, Git refs, execution reports, receipts and earlier review notes. Continue the SAME TASK-063 narrowly.

### Verified progress and CI

- Exact-checkpoint [GitHub Actions #39019074743](https://github.com/ranmasao/devlegate/actions/runs/39019074743) is **success**: **1246 passed, 1 skipped**, coverage **78%**, Ruff **All checks passed**. The prior standalone-download HTTP 403 no longer blocks CI.
- Post-publication `_integrate_accepted()` now catches `subprocess.SubprocessError` from checked Git invocations during optional workspace cleanup, preserving a blocked diagnostic rather than failing normal accepted integration. Keep this correction.
- The historical integration lookup now compares `Devlegate-Execution` and `Devlegate-Checkpoint` lines in the exact control integration commit with the selected report, plus control ancestry. This closes the prior mismatch of report ID versus an arbitrary earlier integration message. Keep this improvement.
- New real-Git `ExecutionWorkspaceManager.retire()` test verifies a clean checkout disappears from Git registration while its branch survives. The second new test checks exact message-generation mismatch. They are useful **unit/primitive proofs**, not the full task acceptance gates.

### Blocker 1 — historical sweep does not prove product publication of the checkpoint

In `cleanup_workspaces()`, eligibility now proves a canonical `accepted -> done` control commit whose message names the selected report's execution ID and checkpoint. **It never independently verifies that the named checkpoint is present in the actual published product history.** `_accepted_execution_report()` proves the execution branch/report and control transition, not product publication. A correctly shaped (but externally/manual-created) `accepted -> done` commit with matching trailers can therefore authorize deletion of an execution checkout whose changes were not actually integrated into the product branch.

Use an authoritative published-product ref plus exact ancestry/checkpoint observation, or equivalent durable proven publication transaction evidence; fail closed on ambiguous/unavailable evidence. Keep this read-only and respect the historical integration boundary. Add a real-Git test where a matching control done transition exists but the product branch is still at the pre-execution base: dry-run must report `skipped/blocked`, `--apply` must retain the registered checkout, and the true integrated case must succeed.

### Blocker 2 — partially completed apply can delete beyond the request's bounded target set

The mutable receipt is persisted before `cleanup_workspaces(apply=True)`, but it records no stable target set or progress. An incomplete-receipt replay invokes `cleanup_workspaces()` anew, which enumerates **only currently registered** worktrees with the original `limit`. Concrete example: eligible A, B, C, `--limit 2`; A is removed, the process crashes before the result receipt is saved; replay of the **same request ID** sees B, C and may remove both. Three distinct worktrees have then been removed by a request limited to two, and the eventual returned decisions omit A.

Bind the bounded cohort/progress to a durable admission before any removal (or establish an equivalent conservative replay rule that cannot enlarge the request's effect). On replay, re-observe registrations and report previously removed targets truthfully; never fabricate a completed result. Keep the existing single-owner protocol, avoid a generic jobs framework. Add a deterministic multi-target real-Git regression for partial apply, restart, same-ID replay, new-ID request and completed receipt replay, asserting exact deletion count and accurate results.

### Blocker 3 — actual integrated cleanup and owner-authorized apply remain unproven

The fifth checkpoint changes only `runtime.py` and two tests in `test_control_workspace.py`. The clean-workspace test calls `manager.retire()` directly on a merely prepared checkout **without** an accepted-to-done event. The integration-message test constructs synthetic control commits without exercising publication, report binding, worktree validation or cleanup. Previous IPC tests cover only a nonmutating dry-run of an unintegrated ticket.

Add narrow, realistic end-to-end/integration proofs for: (1) real `accepted -> done` publication and automatic checkout retirement with report/product/control/branch evidence retained; (2) checked Git observation failure and `worktree remove` refusal **after** published `done`, still finalizing integration and allowing unrelated scheduling; (3) historical owner-routed `cleanup-workspaces --apply` actually removing one proven eligible registered worktree, while preserving unowned/foreign, review/accepted, dirty/mismatched and active/uncertain workspaces. Verify status persistence/IPC projection for successful and blocked cases. Reuse existing fixture/service-owner tests; do not rerun the full suite locally.

### Disposition

**REWORK REQUIRED.** Move **TASK-063** from `review` back to `todo`. Preserve the fifth checkpoint and all historical artifacts; do **not** integrate it into `release/0.5.7` yet. Successful exact-final-SHA CI remains necessary but is not a replacement for the publication, bounded-replay and real-owner safety proofs above.

## Reviewer decision — 2026-10-17, execution f023121c: REWORK REQUIRED — revised scope not implemented

**Execution:** `f023121c7e7f49189d046e38bddd0677`; **exact checkpoint:** `bab5c5119359b7f745fdc4276ba05ca2de2bdd82`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `5d74ff521713cdf3a71c2ebe47f5408375cd4d93`. Worker claimed `completed` with `remaining=[]`, but the checkpoint implements **only two residual items from the obsolete narrower scope**, not the authoritative revised TASK-063 requirements at the top of this ticket. Preserve work branch/checkpoint and all prior reports; continue THIS ticket.

**Exact-checkpoint CI:** [#39021265752](https://github.com/ranmasao/devlegate/actions/runs/39021265752), **success**: 1250 passed, 1 skipped, coverage 78%, Ruff all checks passed. CI success is real; acceptance scope is not met.

### What was actually changed

One commit on top of `2961366529e1` changes only `src/devlegate/runtime.py` (+48/-3) and `tests/test_control_workspace.py` (+49). It checks that a historical accepted integration commit explicitly names the published checkpoint, constrains/validates saved cleanup cohorts, and adds two narrow regressions. Retain useful product-lineage and idempotence safety improvements.

### Blocking requirements absent from the checkpoint

1. **End-state violates the core contract.** After `_complete_accepted()`, `_integrate_accepted()` calls only `_retire_integrated_workspace()`, which calls `ExecutionWorkspaceManager.retire()`: this removes the worktree but explicitly **keeps** local `devlegate/work/<ticket>` and upstream branches. `cleanup_workspaces()` likewise enumerates registered worktrees only and never retires either branch. There is no per-resource safe cleanup for a **remote-branch-only** or local-branch-only remnant. Must prove exact execution/checkpoint and published product/control ancestry, retire clean checkout/registration and then both local and remote work refs (remote compare-and-delete lease), retaining durable history and recovery refs. On ambiguous identities, retain and report. Handle previously completed ticket generations and, where provable, legacy integration messages.

2. **No autonomous reconciliation on startup or while hosted.** The only historical sweep is `cleanup-workspaces` routed through operator IPC; no startup or periodic low-priority/background service-owned pass discovers residual refs after a crash right after published `done`, or recovers partial worktree/local/remote branch deletion. New product requirement is **automatic convergence without operator action or pre-recorded cleanup intent**. Reuse existing serialized owner and deterministic scopes; bounded per-pass fair scheduling; retry remote outages or blocked cases without preventing unrelated ticket execution.

3. **CLI/IPC expose the wrong surface.** `src/devlegate/cli.py` still only registers `cleanup-workspaces [--apply] [--limit]`, and `ipc_server.py` still accepts the same RPC. The revised public UX is `devlegate clean status` (pure read-only), `devlegate clean sweep [--limit]` (owner-routed one pass), with optional `clean ticket <ID>` if practical. Update help, IPC, status and tests; avoid splitting deletion proof among unsafe independent branch/worktree commands. The current mutable-receipt cohort may remain an internal RPC implementation detail, but must not constrain automatic backlog convergence to a one-time cleanup request.

4. **Acceptance tests are for the older feature.** New tests exercise a checkpoint string and cohort size, not real Git deletion of both work branches, owner restart after publication before intent recording, partial-stage restart, branch-only discovery, automatic background convergence, legacy safe/unsafe cases or new CLI. Add representative real-Git and live-owner tests for positive end-to-end cleanup; refusal/retry after remote/Git failures; proof that `review`, `accepted`, `todo`, active/unknown workers, foreign roots, modified refs, dirty trees are never deleted; and preserved reports/control/product history. Test that next runnable ticket is not blocked by cleanup refusal.

### Observed upstream backlog (2026-10-17)

Live GitHub inventory: 80 remote branches, 61 `devlegate/work/TASK-*` branches, **58** of those correspond to 59 current `done` tickets. Current checkpoint does not reduce this backlog. Historical local worktree count is not freshly observable from GitHub.

**Disposition:** return `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` for implementation of the **revised authoritative scope**; do not accept/integrate or create a substitute follow-on ticket. This review does NOT require reverting the useful narrow fixes and does NOT mandate any immediate deletion of current remote branches without the proof machinery.

## Reviewer decision — 2026-10-17, execution 8088cc2f: REWORK REQUIRED

**Execution:** `8088cc2fae604f82a9fb6bd795c08a09`; **exact worker checkpoint:** `bf2b5f2826766b9c5803e2abefbcf1f3ed44a67c`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `a84d00355ea97d4e891ed62cefd277aa6db18986`. Preserve all execution artifacts, previous checkpoints, and existing useful implementation; return the SAME TASK-063 to `todo`.

### Verified achievements

- The **public CLI command `cleanup-workspaces` is removed**, replaced with `devlegate clean status` (read-only) and `devlegate clean sweep --limit N` (mutable owner IPC). `ipc_server.py` no longer publicly dispatches the old method. Internal Python `cleanup_workspaces` / `submit_cleanup_workspaces` and receipt method strings are implementation details; they are **not** a reason to keep the ticket open by themselves. Do not reintroduce the old public command.
- Code now discovers execution worktree registrations **and** local/upstream branch refs, removes a validated worktree and then exact local and leased upstream worker refs. A best-effort automatic `_reconcile_workspace_backlog()` runs within idle scheduler iterations. Reuse this implementation and safety boundaries, rather than reverting to the earlier checkout-only task.
- Exact SHA [GitHub Actions #39023207453](https://github.com/ranmasao/devlegate/actions/runs/39023207453) succeeded: **1253 passed, 1 skipped, coverage 78%, Ruff all checks passed**. No CI blocker.

### Blocker 1 — deterministic background starvation on first blocked ticket

`_reconcile_workspace_backlog()` always calls `cleanup_workspaces(apply=True, limit=1)`. `_workspace_cleanup_cohort(1)` sorts all discovered resource IDs lexicographically and takes `[:1]` **before** proving that candidate eligible; there is no durable cursor, rotation, exclusion/backoff, or drain continuation. Any retained `TASK-001` resource with blocked provenance will be selected and skipped again on **every hosted poll**, so `TASK-002` through `TASK-078` never get cleaned autonomously even if individually safe. `_save_state` also persists the same skipped decision each iteration, and the full remote ref inventory is re-fetched each time.

This is not theoretical for the recorded backlog: the original TASK-001 accepted→done commit `b53a714aed2a99519ca9be23261f4cc58cfc7832` has parent `93d6c80b2c12d69d96cab296f39096658a91c43c` and **no modern trailers**, while its only durable report `14043d5cb75248ba9cc5b34f89f6859d` records `control_head=df88cdfd43fbd53e68a6c3a13a2057eb45cf501a`. The legacy branch of `_accepted_integration_commit_matches_report()` requires `parents[0] == report.control_head`; it therefore cannot accept *that exact report + integration commit*. Whether some stronger alternate historic proof is available should be evaluated, but a permanently blocked first candidate must NEVER starve other tickets.

**Required:** bounded fair scanning (stable cursor/rotation or equivalent, with lifecycle-safe re-observation), no repeated expensive mutations/writes for unchanged blocked candidates, retry blocked items later, and deterministic real-Git test: blocked earliest candidate + later proven eligible candidates; repeated autonomous passes must actually retire later candidates, including local/upstream refs; restart must preserve eventual fairness.

### Blocker 2 — upstream observation failures are silently turned into empty inventory

`_workspace_cleanup_cohort()` executes `git ls-remote` but on any nonzero status uses `observed_lines=[]`. For a **remote-branch-only** residual and temporarily unavailable upstream, `clean status` and automatic cleanup can return **zero decisions**, falsely indistinguishable from a fully clean repository. This directly conflicts with the requirement that an unobservable remote is *pending/blocked*, not proven absent. Preserve best-effort scheduling, but surface the observation failure as a retriable diagnostic in per-resource/overall status, and re-observe when connectivity returns. Add remote-branch-only offline/recovery test.

### Blocker 3 — scope acceptance is still not proven end-to-end

This checkpoint changes CLI/IPC/runtime and adds mostly primitive/dispatcher tests. The new real-Git deletion test calls `manager.retire(workspace, base)` and `engine._retire_execution_refs(...)` **directly on an unintegrated prepared checkout**; it does not test a published accepted→done event or the automatic scheduler. There is no demonstrated real-Git test of startup rediscovery after publication-before-cleanup, restart between worktree/local/remote deletions, historical legacy successful cleanup, owner-routed `clean sweep` actually retiring proven resources, or `clean status` reporting each residual resource accurately after partial cleanup. Add narrow integration tests rather than broad refactoring; preserve dirty, mismatched, foreign-instance, active-worker, evidence, submodule and refusal safeguards.

### Disposition

**REWORK REQUIRED**. Return TASK-063 from `review` to `todo` and preserve its exact worker checkpoint. **Do not integrate into `release/0.5.7` yet.** Treat the public CLI rename as COMPLETE; focus next pass on starvation-free autonomous convergence, truthful remote-unavailable status, and real owner/startup recovery tests. CI must pass on the final reworked SHA.

## Reviewer decision — 2026-10-17, execution b7245538: REWORK + CLI CONTRACT CORRECTION

**Execution:** `b724553841444ed5a26fd5d95325cf84`; **exact checkpoint:**
`3b0d961fd8e961fc36485e9b9a241d6156d00bf4`; **product base:**
`bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:**
`692c8fdc56938336b03b6cc694c6f81f3d41ca6d`.
Worker reported `completed`, `remaining=[]`. Preserve all checkpoint
work, evidence, reports and prior reviews. Do not publish product.

### Verified improvements worth retaining

- New `workspace_cleanup_cursor` persists the last processed candidate;
  `_workspace_cleanup_cohort` rotates the sorted registry/refs inventory
  and can advance beyond a blocked earliest candidate after restart.
- Failed `ls-remote` now produces an explicit
  `{"ticket_id":"__remote__", "status":"blocked"}` diagnostic, rather
  than silently claiming no remote branches exist.
- The earlier checkpoint already removed the **public**
  `cleanup-workspaces` CLI, introduced `clean status` and `clean sweep`,
  and implemented basic exact-ref retirement. Preserve the safe worktree/ref
  removal primitives and service-owner architecture.

### Blocking review findings

**1. Authoritative CLI contract revised by the user (above).**
`clean sweep --limit N` is unnecessary: an autonomous reconciler should
converge without operator-triggered mutation. Remove public `clean sweep`,
`--limit`, `--apply` and public mutating `clean-sweep` IPC; implement
`devlegate clean` as read-only status (optional `clean status` alias).
Internal bounded/fair reconciliation must remain. Do not bring back
`cleanup-workspaces` or expose low-level branch/worktree deletion commands.
The existing mutating RPC/receipt machinery may be retired or isolated if
not otherwise needed, without destroying historical receipts.

**2. Round-robin regression proves cursor movement, NOT backlog convergence.**
`test_automatic_workspace_cleanup_rotates_past_blocked_candidate` creates
two *unintegrated* worktrees, verifies both remain, and checks only that
cursor changes from T-1 to T-2 and survives `Devlegate` reconstruction.
There is no positive case in which T-1 is blocked while an **integrated,
proven, safe T-2** actually loses worktree + local/remote branches. Nor is
there an owner restart after publication-before-cleanup or after partial
ref deletion. Add focused real-Git/owner integration coverage required by
the updated acceptance criteria: cleanup of a real published done
generation, protected blocked neighbor, actual after-crash convergence
without prior cleanup intent, accurate inventory after partial cleanup,
and legacy provable/ambiguous history.

**3. Read-only IPC `clean status` currently runs repository inventory via
`dispatch_read_only -> engine.workspace_cleanup_status ->
cleanup_workspaces(apply=False)` on the IPC connection path.**
This reaches `_workspace_cleanup_cohort`, which writes the shared
`_workspace_cleanup_observation_error` field and observes mutable Git
state while the hosted scheduler also mutates/uses those facts. The
docstring "owner-consistent" is not sufficient synchronization.
Avoid status/owner races by publishing a coherent immutable owner snapshot
or proper read-only synchronization with revalidation; do not persist
receipts or mutate Git from status. Test concurrency, a remote outage,
and no false 'clean' for partial/unobservable inventory.

**4. Exact-checkpoint CI is RED.**
[GitHub Actions #39028536778](https://github.com/ranmasao/devlegate/actions/runs/39028536778):
**1254 passed, 1 failed, 1 skipped**, Ruff skipped after failed pytest.
The single failure is
`tests/test_worker_process.py::test_worker_process_group_isolated_and_interrupts_descendant[operator_abort]`:
the test sees an existing but as-yet-empty `processes.json` then
`json.loads` raises `JSONDecodeError`. The worker process-group module
was not modified in this checkpoint; this appears consistent with a
pre-existing test timing race, **not demonstrated cleanup regression**.
Avoid unrelated redesign to satisfy this review; rerun full authoritative
CI for the final revised SHA, report persistent failures transparently.

### Disposition

**REWORK REQUIRED.** Move this SAME TASK-063 `review -> todo`;
do not accept/integrate `3b0d961fd8e9`. The revised authoritative
functional spec above now explicitly supersedes historical suggestions
of `clean sweep` and user-visible limits. The automatic clean-up engine
still needs end-to-end safety, progress and recovery proofs; a green
checkpoint CI alone would not settle them.

## Reviewer decision — 2026-10-17, execution 7e768457: REWORK REQUIRED

**Exact execution:** `7e768457157043c187fcc8244e778196`; **checkpoint:** `fdfd664180dcf6e062265124a0ce40ca271ade55`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `93b61975cbc316becdaaeb2c6fe200ff3f430d0b`. Worker claimed `completed` / `remaining=[]`. Preserve every worker checkpoint and execution report. **Do not integrate** this checkpoint to the product branch yet.

### Verified progress and CI

- **The revised public CLI contract is implemented.** `devlegate clean` and `devlegate clean status` are read-only; public `cleanup-workspaces`, `clean sweep`, `--apply`, `--limit` and the `clean-sweep` IPC dispatcher are removed. Internal `cleanup_workspaces` and operator-receipt symbols are private implementation names, *not* themselves blockers. **Do not recreate a manual sweep.**
- `workspace_cleanup_status()` now reads the published `StatusSnapshot.workspace_cleanup` under `_snapshot_lock`, rather than running fresh Git observations in a concurrent IPC thread. Owner-side `_reconcile_workspace_backlog()` refreshes the published view after one pass. This addresses the previous shared-observation-field race directionally; keep this owner-published read model.
- Exact-checkpoint [GitHub Actions #39029700151](https://github.com/ranmasao/devlegate/actions/runs/39029700151) is **green**: **1255 passed, 1 skipped**, coverage **78%**, Ruff **All checks passed**. The previous unrelated worker-process JSON-file timing failure did not reproduce.

### Blocker 1 — `devlegate clean` presents a one-item recent pass as global inventory and can falsely claim `clean`

`workspace_cleanup_status()` returns `{"status": "clean", "decisions": []}` whenever `_published_status_snapshot.workspace_cleanup is None`. On service startup this state can precede **any** cleanup inventory; absence of a previous observation is *unknown / not yet scanned*, not proof of no residual branches or worktrees. More generally, `_reconcile_workspace_backlog()` processes `cleanup_workspaces(apply=True, limit=1)` then replaces `_state["workspace_cleanup"]` with `{"status":"reconciled","limit":1,"decisions":[last candidate]}`. After one `TASK-001` decision, 57 other outstanding work refs may exist, but `devlegate clean` will expose only that last candidate and no aggregate remainder, global completeness, per-resource status or inventory-progress indication. A pass with only `__remote__` blocked is also stored as outer `status:"reconciled"`. Conversely, an exception can leave a stale previously published projection if `_refresh_published_status` cannot complete.

**Required:** owner-maintained, read-only, coherent **aggregate** status/inventory model separate from the bounded *mutation* batch. It may be materialized from an owner-owned full/batched inventory with scan generation, totals by worktree/local/upstream refs, blocked reasons, explicit partial/unknown and observation freshness. A status query need not force expensive Git I/O but MUST indicate `initializing` / `unknown` / `partial` until coverage is established, and MUST NOT label unobserved resources `clean`. A successful per-item removal is not proof the backlog is drained; report prior blocked items and unscanned candidates accurately. Do not reintroduce public `--limit` to mask incomplete status. Test early startup with residual refs and no previous cleanup state, after 1 of >=3 candidates, blocked and remote-unavailable cases, completion and subsequent restart. Demonstrate read-only IPC never mutates Git, state or receipts.

### Blocker 2 — realistic automatic convergence / recovery remains unproven

The checkpoint adds CLI/IPC/owner snapshot tests; it does **not** add the demanded positive real-Git owner scenario: a **published accepted→done** ticket whose exact worktree registration, local work ref and upstream work ref are automatically removed by service scheduling, with durable product/control/report evidence retained. The earlier `test_proven_execution_resource_retirement_removes_local_and_remote_refs` calls `manager.retire` and `_retire_execution_refs` directly on a merely prepared, **not integrated** worktree. The prior round-robin test merely advances a persisted cursor between two **blocked** prepared worktrees; it never demonstrates that a later safely accepted T-2 is actually reclaimed while earlier T-1 remains blocked.

**Required targeted real-Git/service-owner tests** for (a) blocked old T-1 plus truly proven T-2 that actually loses checkout/registration/local/remote refs with round-robin and restarted owner; (b) crash immediately after published done **before cleanup intent**, restart automatic rediscovery without operator RPC; (c) failure between worktree, local and remote deletion steps, then restart to converge; (d) a legacy done with sufficient evidence versus an ambiguous historic generation; (e) refusal for dirty/foreign/currently-owned worktree or changed remote SHA and successful unrelated ticket scheduling. Keep these few integration tests narrow and deterministic; full CI on final SHA.

### Disposition

**REWORK REQUIRED.** Move `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` with this review appended. Retain all real improvements, CI result and original historical reviews. **Do not** reintroduce `clean sweep` or user-visible limits and **do not** split the task. The next checkpoint should correct truthful global status and prove autonomous, crash-recovering end-to-end cleanup, not keep rearranging the CLI.

## Reviewer decision — 2026-10-17, execution ad4d571c: REWORK REQUIRED

**Execution:** `ad4d571cc4534cad8df809768fdb290b`; **exact checkpoint:** `3560c105801aa11921b01a577345084798c9014d`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `161325e86f22bcdff6badeee60ce78d6d4df9f47`. Keep every existing checkpoint and report; no new ticket and no product integration.

### Verified progress

The implementation now separates an owner-published aggregate inventory from the owner-serialized bounded mutating pass. `devlegate clean` / `clean status` remain read-only, with no public `sweep`, `--limit`, `--apply` or `cleanup-workspaces` commands. The owner status shows `initializing` before any scan and multiple candidates after a pass. The new tests cover startup unknown and three-candidate inventory. Preserve these improvements.

### Blocker 1 — CI RED: post-integration state invariant breaks the published lifecycle

[Exact GitHub Actions #39031213993](https://github.com/ranmasao/devlegate/actions/runs/39031213993) finished **5 FAILED, 1252 passed, 1 skipped**; Ruff was skipped because pytest failed. Three `test_control_cross_layer` tests hit a **deterministic** mismatch: `_integrate_accepted()` writes `self._state["workspace_cleanup"] = {"ticket_id": ticket_id, "status": "retired", ...}` **after product/control publication**, but `_validate_state_invariant()` added in this checkpoint permits only `initializing/clean/partial/blocked/reconciled/applied`. The next `_save_state()` rejects `retired` as `DevlegateError: invalid workspace cleanup status`. The remaining two `test_cli_cross_layer` failures expose the same lifecycle regression: published `done` retains `accepted_integration`, and a dependent worker never starts or integration finalization stalls. GitHub test diagnostics actually show "retired workspace T-1", followed by "workflow blocked: invalid workspace cleanup status".

**Fix the contract, not just the assertion:** distinguish per-ticket integration/retirement **event** and aggregate cleanup **inventory** as separate fields or typed structures; ensure old persisted states can be loaded and upgraded/handled safely; ensure `accepted_integration` clears after durable publication even if cleanup or status publication fails. Add direct regression for post-done state persistence, restart, and continued dependent scheduling; check all callers that set `workspace_cleanup`, including zero-delta/legacy operator receipts.

### Blocker 2 — aggregate `blocked` classification is inconsistent with real decisions

`_publish_workspace_cleanup_inventory()` assigns top-level `blocked` only if an item has `status=="blocked"`. But the actual per-candidate proof/refusal paths of `cleanup_workspaces(apply=False)` emit `status:"skipped"` for unsafe/unproven `done`, dirty/foreign/mismatched refs and non-done tickets. With the 58 known historical worker-branch remnants, this distinction is actionable: the status can report `partial` with only `skipped` candidates and no explicit overall blocked classification. Separate `not yet eligible` (live review/todo) from `blocked unsafe proof`, and `pending/retry` from `eligible`; report by **worktree / local ref / upstream ref** as required by the authoritative scope. A complete scan must not be claimed merely because the arbitrary internal 10,000-candidate cap did not expose an omitted suffix; if capped, report partial coverage and continuation rather than `scan_complete:true`.

### Blocker 3 — core automatic end-to-end convergence still lacks proof

The only new tests in this checkpoint assert aggregate status before scan and with three **unintegrated** worktrees. Earlier deletion tests call `manager.retire` and `_retire_execution_refs` directly on prepared but unintegrated resources, while round-robin tests only verify cursor advancement between blocked resources. **No real-Git owner test** proves `accepted→done` publication followed by automatic worktree/registration/local/upstream ref removal, crash after durable `done` before any cleanup intent, restart after partial worktree/local/remote removal, and progress on safely eligible T-2 while T-1 is blocked. Add focused, deterministic owner/harness tests for these exact scenarios, preservation of product/control/reports, safe refusal on dirty/changed/foreign/reused refs, and no obstruction of unrelated scheduler work. This is the previously agreed primary TASK-063 acceptance requirement, not an optional enhancement.

### Disposition

**REWORK REQUIRED.** Move `kanban/review/TASK-063.md` → `kanban/todo/TASK-063.md` on canonical `devlegate/control`. Keep checkpoint `3560c105801a` and all progress. Fix the published-state invariant and red CI **first**, then finish truthful aggregate diagnostics and real crash-recovery/convergence tests. No manual cleanup CLI, new ticket, 0.5.6 changes, or integration to `release/0.5.7`.

## Reviewer decision — 2026-10-17, execution bf41e82f: REWORK REQUIRED

**Execution:** `bf41e82fc49c48b796b98da2a71f0021`; **exact worker checkpoint:** `d357eb80fa7f898a042772cde031b83d87c781f4`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `963ea785267accb164168d368e8880c0def83081`.

### Verified achievements

- The deterministic post-publication `workspace_cleanup.status="retired"` invariant failure from the preceding checkpoint has been addressed. [Exact SHA CI #39040517533](https://github.com/ranmasao/devlegate/actions/runs/39040517533) is **green**: **1259 passed, 1 skipped, coverage 78%, Ruff all checks passed**.
- The last patch recognizes unsafe `skipped` decisions as blocked, marks capped scans incomplete and exposes remote-observation failure. Preserve automatic owner-driven cleanup, exact SHA/CAS protection, cursor progress, owner-published status and the **read-only** `devlegate clean` / `devlegate clean status` interface. Do not restore public sweep/apply/limit.
- New direct test checks that a persisted per-ticket `retired` event survives restart, but this is **not** a crash-after-retirement transaction test.

### Blocker 1 — proven restart deadlock after successfully removing both execution branches

The requested crash boundary is still unsafe for a **non-zero-delta** completed ticket:

1. `_integrate_accepted()` durably publishes the product checkpoint and canonical `accepted -> done`, then `_retire_integrated_workspace()` removes the worktree and `_retire_execution_refs()` deletes **both** `refs/heads/devlegate/work/<ticket>` local and upstream refs.
2. Process dies **after those Git effects but before** `_run_iteration_body()` saves `accepted_integration=None`. The existing persisted transaction therefore remains pending, despite the product and control `done` being published and disposable refs correctly gone.
3. On restart, `recovery_workflow.recover_accepted_integration()` calls `owner.recovery_integrate_accepted()`, which invokes `_integrate_accepted()`. That calls `_accepted_execution_report()` **before** seeing the already-integrated status. After cleanup, `_execution_remote_head()` and the local-ref fallback are both `None`.
4. `execution_provenance.resolve_accepted_execution_report()` accepts the report only when `workspace_head == remote_head`, or, for `remote_head is None`, when `workspace_head == code_base_head`. An ordinary execution has `workspace_head != code_base_head`; report resolution therefore fails as ambiguous even though exact durable control integration trailers, product ancestry and immutable report still exist. Recovery cannot clear `accepted_integration`, and scheduling remains fenced.

**Required:** recovery of a *previously proven, already published* accepted→done generation must be possible with **both disposable work refs absent**, using independent exact canonical published control/product/report evidence without weakening checks for not-yet-published or mismatched/newer generations. Separate pre-publication proof from post-publication replay proof; do not make preservation of temporary branches a prerequisite for recovery. Make post-done cleanup best-effort and fenced only by necessary durable control facts, not a mutable transaction that requires a deleted disposable ref to replay.

**Mandatory deterministic real-Git owner test:** publish non-zero-delta product and control `done`, retire worktree + local + upstream work ref, simulate abrupt process loss **before** clearing `accepted_integration`, then construct a fresh owner. It must clear the pending integration, leave `done` and durable report/product unchanged, not recreate disposable refs, and schedule the next runnable ticket. Also test partial effects (worktree gone; local gone but remote present; both gone) and a mismatched/ref-reused generation.

### Blocker 2 — a per-ticket event is still masquerading as global `devlegate clean` inventory

The quick fix only adds `"retired"` to the allowed states. `_integrate_accepted()` still sets `self._state["workspace_cleanup"] = {"ticket_id": ticket_id, "status": "retired", ...}`, replacing the **aggregate** state published by `_publish_workspace_cleanup_inventory()`. Until a subsequent owner inventory pass, `workspace_cleanup_status()` exposes the last ticket's event as the whole `devlegate clean` response. It has no `scan_complete`, counts, blocked backlog or per-resource evidence. Thus one freshly integrated ticket can hide dozens of old remaining work refs, and the status schema changes unpredictably between a one-ticket event and a global inventory.

**Required:** retain retirement events in their own durable field/diagnostic, but publish one consistent global cleanup inventory schema from startup through after integration/restart, with explicit unknown/stale/partial when not fully observed. Add an integration test with an existing blocked/eligible backlog and a new successful `done`; assert `devlegate clean` does NOT become `{"status":"retired","ticket_id":...}` or falsely appear drained.

### Blocker 3 — critical autonomous convergence proofs are still missing

The **only new tests** in `d357eb80` validate a synthetic persisted `retired` dictionary and a mocked blocked-decision status. The prior test `test_proven_execution_resource_retirement_removes_local_and_remote_refs` deletes branches via direct helpers on a merely prepared, unintegrated worktree. The round-robin regression advances a cursor between *two unintegrated blocked* worktrees, but never verifies actual deletion of an eligible later ticket while an earlier ticket is blocked.

Add a small set of representative *positive* real-Git service-owner tests for canonical `accepted→done` publication, actual removal of registration/checkout/local/upstream refs, branch-only rediscovery, crash/restart after each stage, legacy provenance with and without enough evidence, and progress on safe T-2 despite blocked T-1. Preserve reports/product/control and existing safety protections. An exact-green CI is necessary but cannot substitute these behavior proofs.

### Disposition

**REWORK REQUIRED.** Move the **same TASK-063** from `review` back to `todo`; retain every checkpoint, execution report and review. Keep `release/0.5.7` unchanged. Prioritize crash-after-complete-ref-retirement recovery and state-schema separation, then add end-to-end tests rather than more narrow unit tests or CLI redesign.

## Reviewer decision — 2026-10-17, execution 55cbdfe2: REWORK REQUIRED (worker-reported incomplete)

**Execution:** `55cbdfe2f0ff451f9996924b91b387f6`; **exact checkpoint:** `fdd1e6f6d10736a3f2c5f73f4d275d896bf6079a`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `4987e9bc0524b8016a4c104694ce363b4f800f7f`.

**The worker explicitly returned `outcome: incomplete`**, with remaining: "Add deterministic real-Git owner tests for crash recovery after worktree/local/upstream ref retirement, partial-stage restart, and blocked-earliest versus eligible-later convergence." This is not an acceptance-ready worker claim even though CI passes.

### Confirmed code progress (retain)

- `_published_accepted_execution_report()` reconstructs the completed generation from canonical done commit, durable report and published product when both disposable work refs are absent; `_integrate_accepted()` selects it for pending post-done replay. This addresses the earlier branch-deletion/recovery dependency **in implementation**, but the crash boundary is not yet tested.
- Post-integration per-ticket retirement is written to `workspace_cleanup_event` instead of replacing the aggregate `workspace_cleanup` snapshot. The read-only `devlegate clean` projection is retained; do not reintroduce public `clean sweep`, `cleanup-workspaces`, `--limit`, `--apply` or a mutating cleanup IPC.
- [Exact GitHub Actions #39042740103](https://github.com/ranmasao/devlegate/actions/runs/39042740103) **passed**: 1259 tests passed, 1 skipped, coverage 78%, Ruff all checks passed.
- This checkpoint adds no new tests and itself acknowledges the missing cross-layer/real-Git owner verification. Do not regress existing accepted-to-done, safety and state-schema tests.

### Remaining acceptance blocker: prove autonomous end-to-end convergence and crash recovery

The current `test_proven_execution_resource_retirement_removes_local_and_remote_refs` drives `manager.retire()` + `_retire_execution_refs()` directly against a merely prepared checkout; `test_automatic_workspace_cleanup_rotates_past_blocked_candidate` asserts only cursor advancement with two **non-done** worktrees. These do **not** verify any real service-owned cleanup of a proven published `done` generation.

Implement a **small deterministic real-Git owner test matrix using existing control fixture / hosted owner / process-restart harness**:

1. **Normal full integration:** publish a non-zero-delta accepted→done ticket, verify the real `ExecutionWorkspaceManager` checkout and registration, the exact local `devlegate/work/<ticket>` ref and the upstream ref are all gone; control done commit, report and product checkpoint remain.
2. **Hard crash after all four cleanup effects but before `accepted_integration=None`:** preserve the durable pending state while Git has already removed both refs and the worktree; start a **fresh** owner, demonstrate recovery via `_published_accepted_execution_report` from independent canonical published evidence, clear `accepted_integration`, and let a subsequent runnable ticket start. Do not synthesize a `retired` state dictionary in lieu of proving the transaction.
3. **Crash between effects:** restart when checkout/registration are absent but both branches remain; restart again when local ref is absent but upstream ref remains; re-observe and retire only safely proven remnants. Include a **remote-only residual** and remote temporarily unobservable, then restored. Preserve wrong-SHA, reused generation, dirty/foreign worktree refusal.
4. **Fair historical backlog:** leave first registered candidate unsafe and later already integrated ticket proven safe; run bounded, owner-serialized automatic passes **without operator requests** and verify the safe later ticket really loses checkout/registration/local/upstream refs despite the blocked first. Verify restart retains fair progress.
5. **Historical provenance:** demonstrate actual automated removal for a legacy done generation with sufficiently strong trailerless control/report/product/ref evidence and refusal when identity is ambiguous. This is part of the original ticket's historic-backlog objective; do not substitute a synthetic helper-only test.

Keep these focused, reuse fixtures and existing tests, avoid unrelated runtime redesign, and run exact-SHA CI. If evidence is genuinely insufficient for a specific historical item, retain it with a precise blocked diagnostic rather than weakening identity checks.

### Disposition

**REWORK REQUIRED**: move `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` on `devlegate/control`. Preserve all previous worker checkpoints, evidence, reviews and the current implementation; **do not integrate to `release/0.5.7` yet**. Next pass is principally real-Git owner tests and any precise defects they uncover, not another broad CLI/status redesign.

## Reviewer decision — 2026-10-17, execution ec389cf0: REWORK REQUIRED

**Execution:** `ec389cf04f364d97995dae751568bd89`; **exact checkpoint:** `8063d1e2cdb5fb97998b7fcd4aa6353ab32f72dc`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `4b2facd92a9ba7a3d7e238557c8d640ce84b76c1`. Preserve checkpoint, all durable reports and review history. **Do not integrate** this SHA into `release/0.5.7`.

**Worker claim:** `incomplete`, with explicit remaining items for hosted-owner crash/restart + fair-convergence tests and local test/lint validation. [Exact GitHub Actions #39044330437](https://github.com/ranmasao/devlegate/actions/runs/39044330437) **FAILED: 11 failed, 1249 passed, 1 skipped**. Ruff was skipped due to pytest failure.

### Positive changes worth preserving

- New `_published_accepted_execution_report()` verifies that the accepted→done integration commit's **first parent equals persisted `accepted_integration.control_head`**, in addition to proving the published checkpoint and immutable report identity; strengthens post-publication recovery.
- Intention to use exact-SHA atomic deletion for the local ref is appropriate, but its current implementation is incorrect and drops a safety guarantee. Keep the required SHA-CAS and lifecycle ownership constraints, not the exact implementation.
- Existing separation between `workspace_cleanup_event` and aggregate `devlegate clean` inventory and the read-only public CLI is correct. Do not reintroduce public `sweep`, `--apply` or `--limit`.

### Blocker 1 — the local branch deletion command is invalid (CI regression)

In `src/devlegate/runtime.py::_retire_execution_refs()`, the new
`compare_and_swap_ref(self.repo, local_ref, "", expected_head, ...)`
passes an empty string as the **new object ID**. But `src/devlegate/git.py::compare_and_swap_ref()` directly invokes
`git update-ref <ref> <new-oid> <old-oid>`, not `git update-ref -d`.
Git therefore fails with **`fatal: : not a valid SHA1`**. The error is observed in all 11 failed tests, including zero-delta retry/recovery, control cross-layer and work-ref retirement. Merely changing test expectations would be incorrect. Implement a **real deletion operation** with exact expected-old SHA, correct argument shape, clear race handling and idempotence; confirm in local and CI tests on real Git.

The new `test_local_execution_ref_cas_failure_retains_ref` also expects a generic error text although its injected failed subprocess returns `stderr="ref changed"`; make assertions validate the actual preservation and diagnostic contract, not mismatched error wording.

### Blocker 2 — raw `git update-ref -d` bypasses Git's checked-out-branch safety

This was independently **reproduced with real Git** in an isolated temporary repo:
`git branch -d case` refuses to delete a branch used by another registered worktree; `git update-ref -d refs/heads/case <exact-SHA>` **succeeds**, leaving the worktree registration pointing at a missing branch. Therefore, simply replacing the empty-OID call with `git update-ref -d` fixes the syntax but can silently regress the previously provided `git branch -d` guard.

**Requirement:** no deletion of a branch attached to **any** live worktree, including worktrees not owned by this Devlegate instance, regardless of an exact matching ref SHA. Check full Git worktree registrations/branch occupancy and preserve the intended Git branch-delete safety; handle adversarial movement/reuse of refs and external worktree attachment conservatively. Do not accept a check only for `manager.path` or an unlocked stale snapshot if Git could change between observation and deletion. Maintain per-ref SHA CAS where safely possible. Add a real Git regression with a branch checked out in a foreign/external worktree: attempted cleanup must refuse and preserve ref, directory, registration and reported reason. Add a positive test of exact local and remote deletion after proven safe retirement.

### Blocker 3 — the core TASK-063 end-to-end acceptance is still absent

The worker explicitly reports the following as unfinished, confirming the prior reviewer disposition: real-Git **hosted owner** tests for (1) published non-zero-delta accepted→done resource cleanup; (2) hard crash after full/partial worktree/local/remote ref deletion but before `accepted_integration=None`, followed by **fresh-owner** recovery without recreating temporary refs or blocking the next task; (3) round-robin clearing a **proven eligible later** ticket with an earlier blocked candidate; (4) legacy trailerless done evidence when sufficient and safe refusal when ambiguous, remote-outage re-observation and branch-only leftovers. Do not substitute direct helper calls on unintegrated prepared worktrees, mock-only tests or a persisted synthetic `retired` dict.

### Disposition

**REWORK REQUIRED.** Move the same TASK-063 `review → todo` on `devlegate/control`. Fix **real Git deletion syntax and occupancy safety first**, obtain green exact-checkpoint CI, then add focused positive hosted-owner recovery/convergence tests. Retain existing improvements; do not redesign CLI, create a follow-on ticket or modify the release branch.

## Reviewer decision — 2026-10-17, execution 5f6b18cd: REWORK REQUIRED (incomplete claim)

**Exact execution:** `5f6b18cdfa6a4c76bd3cc4fff29b2b2b`; **worker checkpoint:** `551cd8926d009778450885b28215c7fcaa73e73f`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `e025a3ac0b52fd5d86b150ff5b6cf08c9ebbb996`. The prior execution `608b0343b8f84e7bbace8eb078823838` failed its egress protocol, but its checkpoint `c6c73db8a6ebd57eccd5a623bb96372bfd3c2cd7` was successfully retained and reused by this execution. Do not delete these checkpoints or reports.

### What was fixed

- This retry **produced a valid `devlegate_report`** (`egress_ok=true`, process return 0), unlike the prior failed attempt. It reported `outcome=incomplete`, not `completed`.
- The prior `git update-ref` empty new-OID failure was addressed in `c6c73db8` by translating empty new OID to `update-ref -d`. The current checkpoint replaces raw low-level deletion in `_retire_execution_refs()` with `delete_branch_if_unoccupied()` using `git branch -d`; this reinstates Git's checked-out-worktree safety. A real-Git test now creates a foreign registered checkout and confirms deletion is refused; keep it.
- The per-ticket `workspace_cleanup_event` remains separate from the aggregate read-only `devlegate clean` inventory. Do not revert or redesign the CLI.
- [Exact checkpoint CI #39051939287](https://github.com/ranmasao/devlegate/actions/runs/39051939287) was **in_progress** at reviewer inspection; do not report it as green before completion. Resolve all failures on this exact or subsequent checkpoint and run full Ruff.

### Remaining blockers

1. **Explicitly unfinished core acceptance.** The worker itself listed: `Add the broader hosted-owner end-to-end crash/restart, autonomous convergence, and legacy provenance tests required by TASK-063.` Existing helper tests, mock refusal and worktree occupancy alone do not prove owner-driven success. Implement focused **real Git + service owner** scenarios: a nonzero-delta published accepted→done ticket automatically loses worktree, registration, local and upstream refs while preserving product/control/report; crash after published done but before cleanup intent; restart after each partial deletion stage, including no disposable refs but persisted `accepted_integration`; safely eligible later T-2 actually cleaned while earlier T-1 blocked; remote-only leftover; legacy done without modern trailers when independently provable; refusal for ambiguous/dirty/foreign/reused refs and remote outages. Demonstrate next ticket remains schedulable. See the authoritative existing checklist; do not create a new ticket.
2. **SHA race not fully resolved.** The current implementation resolves `local_ref`, checks that it equals `expected_head`, re-resolves just before calling `git branch -d`, then deletes by *name*. Git's `branch -d` protects checked-out worktrees, but there is no atomic expected-old-SHA condition on that deletion: another actor could move the ref after the check. This is a narrower remaining race than the previous unsafe `update-ref -d` alternative. Do not simply return to raw `update-ref -d` (which ignores worktree occupancy). Work out a conservative deletion protocol that preserves **both** exact identity and registration/occupancy protection; regression-test a ref move in the recheck→delete window with actual Git where possible. A failed attempt must leave the new ref untouched and show a blocked diagnostic.
3. **Do not accept `incomplete`.** Even a green CI on the existing tests is not evidence that these explicitly missing acceptance paths work. A future `completed` claim should include reproducible end-to-end proof and full exact-SHA CI.

### Disposition

**REWORK REQUIRED:** move the **same TASK-063** from `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` on `devlegate/control`. Preserve all code progress and prior review history. Keep `release/0.5.7` unchanged, retain autonomous read-only CLI contract, and concentrate on safety + real hosted-owner verification rather than further CLI churn.

## Reviewer decision — 2026-10-17, execution a83c9556: REWORK REQUIRED

**Execution:** `a83c955643704d35a748021caaedd0e2`; **checkpoint:** `c030b8c5c4737d19b8b28a408500508ee64c27ab`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `eb6fd1299e6bcc2db708f3f840ca65018ee1cb2d`. Worker explicitly returned **`incomplete`**, with outstanding real-Git hosted-owner normal integration, staged crash/restart, remote-only, fairness and legacy provenance acceptance tests. Preserve all checkpoints, prior reports and reviews; do not integrate product yet.

### Valid progress

- Local deletion helper `delete_branch_if_unoccupied()` now accepts an `expected_head` and observes `refs/heads/<branch>^{commit}` before calling Git's occupancy-aware non-force `git branch -d`; `_retire_execution_refs()` forwards the expected SHA.
- Added a real Git test refusing an execution ref already moved **before** deletion starts, and a persisted aggregate snapshot test ensuring `workspace_cleanup_event` does not overwrite `workspace_cleanup`. Keep the existing foreign-worktree protection and clean read-only CLI contract.
- [Exact CI #39053013742](https://github.com/ranmasao/devlegate/actions/runs/39053013742): **1 failed, 1262 passed, 1 skipped**. The single failure is `test_standalone_builder.py::test_standalone_assets_match_pinned_science_reference_mirror` during an external Python Build Standalone asset download: **GitHub API 403 rate limit exceeded** from `api.github.com/repos/astral-sh/python-build-standalone/releases/tags/20260901`. This is **not evidence of a TASK-063 cleanup regression**; distinguish test infrastructure/network rate limiting from code failures, and rerun or establish equivalent independent validation rather than changing unrelated packaging code solely because of the external 403. Ruff was skipped following pytest failure.

### Blocking acceptance work

1. **Hosted-owner end-to-end behavior remains unproven.** Add deterministic real-Git, real owner/harness tests, not only direct helper calls or mock status tests: (a) published non-zero-delta accepted→done followed by automated removal of physical owned checkout, registration, exact local ref and exact upstream ref, while retaining published product/control/report; (b) hard restart after `done` but before cleanup intent, and after all refs are deleted but `accepted_integration` is still pending; (c) restart in each partial-deletion stage, including remote-only residue and transient remote observation failure, followed by successful autonomous convergence; (d) safely eligible later T-2 actually reclaimed while older T-1 is blocked without starving normal scheduling; (e) historical trailerless done where independently sufficient evidence exists, and a distinctly ambiguous case refused. Verify full automatic owner sequence with a small focused test matrix instead of expanding synthetic per-method tests.
2. **SHA-check race is narrowed but not eliminated.** The new helper performs `git rev-parse` then `git branch -d`, two separate Git commands. This cannot guarantee that the ref still names `expected_head` **at the effectful deletion point** if an external writer changes it between those commands. The new test moves the ref *before* starting deletion; it is not a test of the check→delete race. `git branch -d` protects worktree occupancy, but it is a name-based operation without the expected-old-SHA CAS. Either supply a defensible concurrency protocol for both occupancy and exact-SHA identity or accurately document and handle the residual risk according to the authoritative task requirement; do not regress to unchecked `update-ref -d`. Include an interleaving/race regression.
3. Worker has not claimed `completed`; do not accept merely because the focused suite passes or the unrelated 403 disappears. Full exact-checkpoint CI + lint should eventually pass or a demonstrably external failure should be documented and retested without touching unrelated packaging.

### Disposition

**REWORK REQUIRED.** Move the same `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` on canonical `devlegate/control`, preserving this entire ticket and history. Keep `release/0.5.7` unchanged. Focus next execution on **positive owner crash/restart/convergence tests and race safety**, not further CLI redesign or unrelated standalone packaging modifications.

## Reviewer decision — 2026-10-17, execution 73965b4a: REWORK REQUIRED

**Worker execution:** `73965b4a33494ba18dae39143e5b1cdb`; **exact checkpoint:** `d7a8298b7f7728d183d3689b1c81af03f2f44493`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `62483242b1720c12a7da41ac296fa0f622a2bfed`. The worker claimed `completed` and reported no remaining work. **That claim alone does not satisfy the ticket's independent acceptance requirements.** Preserve all prior evidence and implementation improvements.

### Verified progress — do not redo

- [Exact-checkpoint Actions #39054461887](https://github.com/ranmasao/devlegate/actions/runs/39054461887) is **GREEN**: **1264 passed, 1 skipped; 78% coverage; Ruff all checks passed**. The prior standalone-download 403 is not present in this run.
- `tests/test_control_cross_layer.py::test_accepted_ticket_is_fast_forward_integrated_and_completed` now genuinely exercises a **non-zero-delta accepted→done** integration and additionally asserts that the owned execution checkout, worktree registration, local `devlegate/work/T-1` ref and upstream ref all disappear. It retains its assertions for product and control publication. This is the first needed positive end-to-end integration proof: **credit it; do not ask for that same scenario again**.
- `_validate_execution_ref_heads()` now checks the actual local/remote ref SHA before declaring a done candidate `eligible` for cleanup; a real-Git test covers a **remote branch already rewritten before observation**. Preserve SHA validation, `git branch -d` checkout occupancy guard, remote compare-and-delete lease, owner-driven maintenance and read-only `devlegate clean` surface.

### Blocking gap 1 — no real crash/restart convergence proof

All newly added tests in `d7a8298b` cover **normal completed integration**, a direct ref validation helper on an already-changed remote, and earlier tests cover synthetic snapshots. Nothing exercises a fresh hosted owner after crash **(a)** immediately after durable `done` before cleanup intent; **(b)** after checkout/registration removal with both local and remote worker refs remaining; **(c)** after local deletion with only remote ref left; or **(d)** after all disposable refs are gone while durable `accepted_integration` still indicates an interrupted publication transaction. The separate post-done report-recovery path `_published_accepted_execution_report()` exists but is unproven at precisely the critical branchless replay boundary. A worker that cleaned a ticket normally is not evidence that recovery will not get stuck after one of those interruptions.

**Required:** one compact deterministic **real-Git, fresh-owner, actual execution state** test matrix for these transition points, asserting convergence **without operator cleanup RPC** and subsequent scheduling of another runnable ticket, while product/control/report evidence remains untouched. Exercise retry after remote outage and remote-only residual (not only a direct `_retire_execution_refs` call).

### Blocking gap 2 — backlog and historic safety still unproven

The existing `test_automatic_workspace_cleanup_rotates_past_blocked_candidate` only rotates a cursor between **two prepared non-done worktrees** and verifies neither deletion of a later eligible done ticket nor continued ordinary scheduling. The historical-control tests verify proof helpers, not **actual owner-driven deletion** on a trailerless legacy done generation. These are explicitly in the authoritative TASK-063 acceptance checklist.

**Required:** a real-Git owner setup with unsafe earlier T-1 and proven eligible integrated later T-2 that is actually reclaimed (checkout, registration, local and remote) by bounded periodic passes despite blocked T-1; verify restart fairness. Add an independently provable trailerless historic done generation that really is reclaimed and an ambiguous historic generation that is refused with a truthful diagnostic. Retain unrelated project instances and dirty/foreign refs.

### Remaining safety limitation — SHA check is not a delete CAS

The local `delete_branch_if_unoccupied()` helper still performs `rev-parse expected_head` and then separately `git branch -d <name>`. A ref can change **between** those two Git subprocesses. The earlier test only moves it before the helper starts, and the new `_validate_execution_ref_heads` check is another earlier observation: neither makes deletion conditional on the final expected SHA. Git's `branch -d` correctly protects worktree occupancy but is not a ref-SHA CAS. Do not revert to bare `update-ref -d`, which bypasses worktree occupancy protection. The next checkpoint must demonstrate a defensible way to preserve both properties or explicitly fail closed when the exact identity cannot be guaranteed; supply an interleaving test, not another pre-observation SHA mismatch test. In particular, ordinary `git branch -d` by name should not be described as atomic compare-and-delete.

### Disposition

**REWORK REQUIRED:** return **the same** `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` in canonical `devlegate/control`, preserving every prior review and execution report. **No product integration** to `release/0.5.7` yet. Retain all proven positive progress and green CI. Next execution should concentrate on the missing **crash/restart + fair backlog + legacy-owner tests**, and the narrow remaining ref-race safety boundary, **not** on CLI or status redesign.

## Reviewer decision — 2026-10-17, execution 33b2f6e2: REWORK REQUIRED

**Execution:** `33b2f6e2561f4cd8a2657c58c8e2fe7c`; **exact checkpoint:** `e1ca1b9ab2cf520d300b5b65ac8965cdba37873b`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `797bf90fd76acb518ac54644ddc5ab7238d28a84`. The worker claimed `completed`, with valid egress and no remaining items; reviewer acceptance still requires the independently specified behaviors below. Preserve all reports, checkpoints and prior reviews.

### Verified achievements — do not reimplement

- [Exact SHA GitHub Actions #39055845962](https://github.com/ranmasao/devlegate/actions/runs/39055845962) **GREEN: 1265 passed, 1 skipped, coverage 78%, Ruff all checks passed**.
- Prior checkpoint already proved **normal non-zero-delta accepted→done full disposal** (physical execution checkout, registered worktree, local work branch, upstream work branch, durable published product/control preserved). Credit it as covered.
- New `test_done_cleanup_recovery_after_process_loss_before_state_clear` in `tests/test_control_cross_layer.py` now exercises **real product/control publication**, mocks process death *at* `_save_state(... accepted_integration=None ...)` after cleanup, verifies work refs are absent, constructs a **fresh Devlegate(config)** and replays successfully from published product/control/report evidence. This addresses the most critical **both-refs-absent, pending-accepted-integration** crash window. Credit it as covered; do not ask for the same case again.
- Changed the safe `git branch -d` invocation to `git branch -d -- <branch>`, appropriately treating the branch as an operand and retaining Git's worktree-occupancy safety.

### Remaining acceptance gap 1 — interrupted partial stages and autonomous restart

The one added crash test starts with **all transient refs and checkout already removed**. It does not prove autonomous convergence after crash (1) immediately after published `done` **before any cleanup scheduling**; (2) after worktree/registration deletion but while both local and upstream refs exist; (3) after local ref deletion with a remote-only residual, or remote temporarily unavailable and later restored. Nor does it prove unrelated runnable tickets are not blocked by a stalled historical cleanup attempt.

**Implement focused real-Git fresh-owner tests** for these distinct boundaries using actual published done/report/product evidence and the service-owned startup/idle reconciliation, not direct helper calls on a merely prepared unintegrated checkout. Verify each safely eligible residual resource disappears without public cleanup invocation, preserve canonical history, and verify subsequent work can be scheduled. Reuse and parametrize the new successful crash test where possible.

### Remaining acceptance gap 2 — historic backlog/fairness

No new test demonstrates **an actually eligible later done ticket being retired by owner maintenance** while an earlier candidate is dirty, foreign, mismatched or otherwise blocked. The prior round-robin test only advances a cursor across **two unintegrated non-done worktrees**. Also not proven is actual owner-pass reclamation for an independently provable **legacy done transition with no modern `Devlegate-Execution` / `Devlegate-Checkpoint` trailers**, paired with safe refusal on an ambiguous legacy generation. These are explicit requirements, motivated by dozens of old disposable refs, not optional regression coverage. Build a compact, deterministic real-Git owner/historical fixture for both positive convergence and refusal.

### Remaining correctness defect — non-atomic checked-ref deletion

`src/devlegate/git.py::delete_branch_if_unoccupied()` still does `git rev-parse <branch>^{commit}` followed by independent `git branch -d -- <branch>`. The new `--` changes argument parsing **only**, not atomicity.

**Reproduced independently using real Git** in an isolated temporary repo: create branch `devlegate/work/T-1` at commit A; record `expected_head=A`; create merged/HEAD commit B and `git branch -f devlegate/work/T-1 B` **between** the identity check and branch-delete command; then `git branch -d -- devlegate/work/T-1` **succeeds and removes B**, even though the proven deletion target was A. Therefore a moved/reused ref can be deleted under an external interleaving, violating the authoritative exact-generation identity constraint. Tests that move a ref **before** `_retire_execution_refs()` / the helper only cover early mismatch, not this race. The remote ref correctly uses a fresh SHA lease; local deletion still does not.

Address **both** Git occupancy guard and exact expected-old-SHA deletion semantics. Do not merely substitute raw `git update-ref -d`, which bypasses Git's checked-out-worktree guard. Provide a deterministic interleaving test where a branch changes after the last identity read but before deletion; moved target must survive. If no Git primitive can guarantee both properties against unconstrained external processes, explicitly characterize that limit and adopt/document a conservative, narrowly scoped safety protocol rather than claiming `git branch -d` is SHA-CAS.

### Disposition

**REWORK REQUIRED.** Move the same `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` on `devlegate/control`. Preserve the **green CI, working normal-integration proof and working full-retirement crash-replay proof**; do not redo them. Keep `release/0.5.7` unchanged. Concentrate the next pass on **partial-stage restart, historical convergence/fairness and this demonstrable SHA race**, not further CLI/status redesign.

## Reviewer decision — 2026-10-17, execution fe630caf: REWORK REQUIRED

**Execution:** `fe630caf0a3f4622a5ed372571f85f6f`; **checkpoint:** `ea3940672a192086a84d164d2fe34b37d9632b98`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `e4798e1e31c9923cea8ba2f1f96a9d4978cb3ba8`. Worker correctly reported `incomplete` with missing partial-stage hosted-owner recovery, remote-only/outage, blocked-earliest/eligible-later convergence and historical provenance tests. Preserve current work, all immutable reports and prior reviewer decisions.

### Confirmed progress — retain and do not repeat

- [Exact checkpoint CI #39056912558](https://github.com/ranmasao/devlegate/actions/runs/39056912558) **passed: 1266 tests passed, 1 skipped, coverage 78%, Ruff all checks passed**.
- `delete_branch_if_unoccupied()` changed from name-based `git branch -d` to exact `git update-ref -d refs/heads/<branch> <expected-old-sha>` after observing worktree registrations. The new deterministic real-Git interleaving test changes the local ref immediately before CAS and verifies that the changed branch survives. **The prior ref-SHA check→delete race is addressed; do not re-request another moved-ref-only test.**
- Earlier real-Git owner tests prove **normal accepted→done full retirement** and **fresh-instance recovery after all transient resources were retired while `accepted_integration` remained pending**. These are already accepted as regression evidence.

### Remaining safety issue — worktree occupancy check is no longer at deletion

The new helper observes `git worktree list --porcelain`, checks the branch has no registration, and then invokes `git update-ref -d`. Git's `update-ref -d` enforces the old-SHA condition, **but does not enforce the occupied-worktree refusal performed by `git branch -d`**. A foreign worktree can be attached *after* the registration snapshot but *before* the effectful CAS. The service lock serializes Devlegate owners, but does not stop an unrelated Git process. The code comment/claim that fresh observation preserves foreign checkout protection is therefore conditional, not a full guarantee.

Independently verified with real Git 2.47.3: `git branch -d` refuses to delete the branch currently registered in a second worktree; `git update-ref -d <ref> <exact-SHA>` succeeds anyway and strands that worktree's symbolic HEAD. This is a **new narrow race**, not the old SHA race. Add a deterministic test that inserts foreign `git worktree add` after helper registration observation and before `update-ref -d`; explicitly specify realistic coordination guarantees / external-writer threat model and apply a conservative, justifiable mitigation. **Do not claim both properties are atomic if Git cannot provide them against unsynchronized external writers**, and do not regress the now-correct exact-SHA CAS.

### Remaining central acceptance — automatic partial recovery and fair convergence

The required proofs still absent at this checkpoint are positive, owner-hosted real-Git tests for:
- Restart after a canonical `done` publication **before any cleanup intent is recorded**; and restart after checkout/registration are gone but local/upstream refs remain, or after local deletion with **remote-only residue**. Demonstrate autonomous retry after remote observation outage clears, preserving report/product/control and allowing another runnable ticket.
- Earlier T-1 demonstrably blocked + later T-2 independently proven `done` and eligible: successive bounded owner maintenance passes **actually delete T-2 checkout, registration, local and upstream refs**; verify restart/fair scheduling. Cursor movement alone is insufficient.
- Same age-agnostic owner reconciler processing old/trailerless integration evidence (one independently provable positive and one ambiguous blocked negative). **This is not a request for a separate `legacy cleanup` route or migration.** See the new authoritative interpretation clarification above. Verify actual resource reclamation where exact evidence suffices, and explicit blocked diagnostics where it does not.

Keep tests small and parametrized using existing real-Git fixture/harness. Neither a green suite lacking these scenarios nor `completed` by the worker is enough to demonstrate their correct behavior.

### Disposition

**REWORK REQUIRED:** move the same ticket `kanban/review/TASK-063.md` → `kanban/todo/TASK-063.md` on canonical `devlegate/control`, preserving all previous reviews and run reports. **Do not integrate** `ea3940672a19` into `release/0.5.7`. Focus the next implementation on genuinely unproven automatic recovery/fairness and the foreign-worktree registration race, not broad refactoring, public cleanup commands or a separate historical cleanup service.

## Reviewer decision — 2026-10-17, execution a57cb341: REWORK REQUIRED

**Execution:** `a57cb3412fe945bcab2352d4411585fa`; **exact checkpoint:** `1988a67e07a2a2916ae08a27823397d585cc92a6`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`; **control admission:** `4d241527c162544091cb1a5e483cf85e279d836a`. The worker claimed `completed` with no remaining work. Independent review finds unproven core acceptance and a failing exact-SHA CI run. Preserve all checkpoints, tickets, reports and prior reviews.

### Verified change: compensating repair for concurrent foreign worktree attachment

- `src/devlegate/git.py::delete_branch_if_unoccupied()` keeps the exact expected-old-SHA `git update-ref -d` CAS. It now scans all registered worktrees **both before and after** the delete; if a foreign registration appears during deletion or the post-check cannot be performed, it attempts `git update-ref <ref> <expected_sha> ""`, conditional on the ref still being absent, and returns a blocked diagnostic. The new real-Git `test_local_execution_ref_foreign_worktree_added_during_delete_is_preserved` interleaves `git worktree add` immediately before delete and verifies that the worktree remains registered and the branch restored. **Credit this mitigation; do not re-request that exact interleaving test.**
- The conditional-create-with-empty-old-ref argument was separately checked to be valid on Git 2.47.3. It avoids overwriting a concurrently recreated ref, though restoration may legitimately fail if another process has already recreated it.
- This is **compensating mitigation, not an atomic Git occupancy+ref-SHA transaction**. The code explicitly acknowledges that external unsynchronized worktree registration **after the final occupancy observation** is outside its threat model. Do not claim an absolute safety guarantee against arbitrary concurrent external Git processes, and do not keep cycling between unsafe name-only deletion and unguarded `update-ref -d`. Preserve a clear, realistic concurrency boundary and the already covered SHA/occupancy regressions; prioritize the missing functional acceptance rather than demanding a nonexistent universal Git primitive.

### Exact-checkpoint CI is red; diagnose rather than assume a cleanup regression

[Actions #39059978711](https://github.com/ranmasao/devlegate/actions/runs/39059978711) **FAILED: 1 failed, 1266 passed, 1 skipped**; coverage/lint steps were skipped after pytest failure. The only failing case is `tests/test_cli_topology.py::test_real_service_transaction_stage_restart_preserves_execution[post-publication]`: after a simulated service restart, the test waited 15 s for the expected review state, but service status instead reported a retryable failure `worker identity persistence failed: [Errno 3] No such process`. The new diff only edits `src/devlegate/git.py` and `tests/test_control_workspace.py`; causal connection to this existing service-test failure is **not demonstrated**. Re-run this exact test under CI-like conditions and/or rerun the exact checkpoint workflow, investigate if reproducible, and achieve passing full CI plus Ruff before integration. Do not weaken the existing lifecycle test or rewrite unrelated worker handling on the basis of one unexplained failure.

### Core gaps — the main next pass

**No new owner-hosted partial-restart, backlog/fairness or old-provenance tests** were introduced by this checkpoint. Previous accepted proofs already cover (1) normal nonzero-delta `accepted→done` removal of owned checkout, registration and both refs, and (2) fresh-instance recovery after all disposable refs are removed while `accepted_integration` remains pending. Do **not** redo them.

Required remaining real-Git, actual owner-driven tests and any production defects they reveal:

1. Published canonical `done` before any cleanup intent; fresh-owner automatic discovery. Interrupted partial stage after worktree removal but before local/remote ref deletion, remote-only remnant, temporarily unreachable remote then restoration, and restart without manual cleanup RPC. Verify product, canonical control and durable reports unchanged and next runnable ticket can proceed.
2. Earliest blocked candidate plus genuinely eligible later integrated `done` candidate: bounded repeated owner passes actually reclaim **the later candidate's** physical checkout, registration, local and remote refs, including after restarting the owner; cursor rotation on non-done worktrees alone does not count.
3. **One universal age-agnostic owner reconciler** must evaluate old and current resources identically. Test an older `done` control transition without modern trailers for which independent exact evidence is sufficient and actual owner-pass reclamation succeeds; test missing/ambiguous evidence blocks the same path safely with specific diagnostics. These are coverage scenarios, **not authorization for a separate legacy cleanup service, migration, operator sweep or age filter**.

The core contract is *attempt safe proof of all discovered owned resources, and retire any provably eligible candidate regardless of age; never speculate on ambiguous identity*. No special eligibility standards for old data.

### Disposition

**REWORK REQUIRED.** Move the existing `kanban/review/TASK-063.md` → `kanban/todo/TASK-063.md` on `devlegate/control`, preserving full history and all worker implementations. **Do not modify** `release/0.5.7`. Focus on owner crash-stage/backlog convergence and exact-SHA CI diagnosis, retaining current race mitigation and previously proven positive scenarios.

## Reviewer decision — 2026-10-17, execution 4ffd5ce5: REWORK REQUIRED

**Execution:** `4ffd5ce514b84fe8a5373817e15f7168`; **exact workspace checkpoint:** `3fce120e0fb42830b4cf0bdde2b0884f046d23c6`; **control admission:** `9fab6dffa40a964915d476901b55d725be8a7dab`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. The worker explicitly reported `incomplete` and identified unfinished hosted-owner/legacy acceptance coverage. Preserve all earlier decisions, source changes and immutable execution reports.

### Newly verified progress

- [Exact SHA GitHub Actions #39061241699](https://github.com/ranmasao/devlegate/actions/runs/39061241699) is **GREEN: 1268 passed, 1 skipped, coverage 78%, Ruff all checks passed**. The preceding failing CI is superseded; do not ask for another run solely on the old failure.
- `src/devlegate/runtime.py::_workspace_cleanup_cohort` now discovers canonical direct-child directories under the configured `work` root even without a Git registration. Such candidates deliberately have `head=""` rather than a fabricated execution identity. New `test_workspace_cleanup_reports_unregistered_owned_checkout_without_deleting` proves an unregistered `T-1` directory is observed but its file is not removed. This is the correct fail-closed direction; **do not remove an unregistered directory by name alone**.
- Previously verified (retain, do not recreate): normal hosted real-Git accepted→done full worktree/registration/local/remote ref retirement; fresh-instance replay after all disposable refs are gone but `accepted_integration` remains pending; exact-SHA local ref CAS, foreign-worktree race compensation and their deterministic tests.

### New narrow defect in directory discovery — symlinked work root is followed

`_workspace_cleanup_cohort()` evaluates `work_root = (self.execution_worktree_root / "work").resolve()`, then checks `not work_root.is_symlink()` before `work_root.iterdir()`. **The check cannot detect an originally symlinked `work` root:** `Path.resolve()` has already followed the link, so the resolved target is normally not a symlink. A foreign directory tree reachable via `<managed-root>/work -> <external-dir>` is therefore enumerated, contrary to the claimed "never walk arbitrary directories" boundary. This discovery currently emits an invalid-head candidate and refuses mutation; no arbitrary deletion is demonstrated. Nevertheless, discovery must retain and validate the **lexical** managed root before any resolution or iteration, refuse symlink substitution and report an actionable blocked condition. Add a focused regression creating a symlinked `work` root with a canonical-looking external child, verifying no external traversal/candidate admission and no data loss. Preserve manager's existing lexical symlink protections.

For a legitimate unregistered leftover, the present `head=""` becomes `status="skipped", reason="workspace cleanup target identity is invalid"`. Since the new feature exists to diagnose lost registrations, improve the specific diagnostic to distinguish **missing Git registration / unproven execution generation** from a malformed request. Continue to retain the directory unless exact identity and safe ownership can genuinely be proven.

### Remaining principal acceptance: autonomous owner convergence

**Still not demonstrated by this checkpoint** (and explicitly listed incomplete by the worker):

1. Real-Git **fresh hosted-owner** recovery from published `done` **before cleanup scheduling**, from partial stage after worktree/registration removal with both refs remaining, from **remote-only** remainder after local removal, and from a temporary remote observation/push outage followed by recovery. Exercise automatic startup/idle reconciliation, not manual `cleanup_workspaces()` calls. Verify each eligible resource is removed, durable report/product/control unchanged and another runnable ticket proceeds.
2. **Fair backlog** with an earlier unsafe/blocked candidate and a later independently integrated, safely eligible `done` candidate: bounded subsequent owner passes must reclaim the later checkout, registration and both refs, including after owner restart. Cursor-only tests with two unintegrated worktrees are insufficient.
3. **Universal historical provenance**, not a separate migration/mode: positive real-Git owner-pass retirement of an old trailerless `accepted→done` transition when independent generation, report, product and control evidence is sufficient, and explicit safe blockage for ambiguous legacy evidence.

Do not rework already verified CAS, normal lifecycle or full-retirement crash case. Do not change public read-only `devlegate clean` into a mutating interface. Keep `release/0.5.7` unchanged pending reviewer acceptance.

**Disposition: REWORK REQUIRED.** Return the unchanged canonical ticket history, with this review appended, from `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` on `devlegate/control`.

## Reviewer decision — 2026-10-17, execution e479f3c5: REWORK REQUIRED

**Execution:** `e479f3c531b54043add9fc7918418114`; **workspace checkpoint:** `6dddf66d057d1228308c6cc81333e56f8c3e2d5d`; **control admission:** `8c7ea245476318d6352bc7d50cca7d75cafe1f52`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. Worker legitimately returned `incomplete`, with no transport/egress failure and the same unproven hosted-owner recovery/backlog/legacy acceptance scenarios.

### Confirmed improvements — accepted, do not redo

- [Exact-checkpoint GitHub Actions #39062777054](https://github.com/ranmasao/devlegate/actions/runs/39062777054): **GREEN, 1269 passed, 1 skipped, 78% coverage, Ruff all checks passed**.
- `_workspace_cleanup_cohort` now detects a directly symlinked configured `work` root **before resolving it**. It refuses external direct-child enumeration and emits a `__work_root__` blocked reason; the focused real-path regression `test_workspace_cleanup_rejects_symlinked_work_root_without_traversal` verifies external files retained and no external ticket candidate. The prior reviewer finding about `resolve()` masking the direct work-root symlink is **addressed**.
- An unregistered direct-child checkout is still retained without inventing a generation, now with a specific `workspace has no Git registration; execution generation is unproven` diagnostic. The existing focused regression is updated accordingly.
- Retain prior accepted evidence: normal owner-hosted non-zero-delta full retirement, full-retirement crash replay with fresh instance, exact-SHA local ref CAS, pre/post foreign-worktree observation and conditional ref restoration. No reason to rebuild or regress these.

### Remaining required acceptance — prioritize actual hosted-owner integration tests

1. **Autonomous partial-stage restart** with real Git and real published `done` control/product/report evidence: crash before cleanup intent; crash after worktree/registration removal while local and remote refs remain; local-ref-absent / remote-only remnant; temporary remote outage followed by restored observation. Fresh owner startup/idle passes must delete eligible residuals without manual cleanup commands; verify product/control/report preserved and next runnable ticket proceeds.
2. **Blocked-earliest / eligible-later fair convergence:** genuinely completed independent later ticket must have its checkout, worktree registration, local and remote refs removed by bounded owner passes, including across restart, despite an unsafe earlier candidate. Merely advancing the cleanup cursor among two non-done worktrees does not prove this.
3. **Historical trailerless positive and ambiguous negative** through the same age-agnostic owner reconciler: independently provable old `accepted→done` generations should actually be reclaimed; genuinely ambiguous generations should remain with actionable diagnostics. No special legacy mode or public sweep.

These are already specified in the authoritative ticket and worker's own `remaining`; concentrate on closing them. Do not introduce a new public mutating `devlegate clean` interface or modify `release/0.5.7` before acceptance.

**Disposition: REWORK REQUIRED.** Preserve complete ticket history and this new reviewer note; move `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` on canonical control.

## Reviewer decision — 2026-10-18, execution 92c832b8: REWORK REQUIRED

**Execution:** `92c832b884734f59bf83d817fc57103c`; **exact work checkpoint:** `bceca1fd52a7a38431300fdfcd824d059104256f`; **control admission:** `c779c669936ef6e46a94d7c3663bac06957a0471`; **product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`. Worker reported `incomplete` with valid transport/egress and expressly retained the real-Git hosted-owner partial-stage/fairness/legacy proof items.

### Verified progress — retain

- [Exact-checkpoint CI #39064452987](https://github.com/ranmasao/devlegate/actions/runs/39064452987) is **green: 1270 passed, 1 skipped, coverage 78%, Ruff all checks passed**.
- `src/devlegate/runtime.py` now catches `DevlegateError` in the bounded owner cleanup pass, status-inventory fallback, and per-candidate `cleanup_workspaces()` exception handler for a registered checkout. This makes an expected provenance/remote-observation failure reportable as a per-ticket `skipped`/blocked diagnostic rather than unhandled propagation.
- New `test_workspace_cleanup_contains_remote_provenance_observation_failure` exercises a *real registered checkout* and verifies it remains intact when `_accepted_execution_report` raises `DevlegateError("upstream execution refs could not be observed")`. The ticket is mocked `done` using `SimpleNamespace`, the provenance helper is mocked to fail, and `cleanup_workspaces(limit=1)` is called directly: useful negative/error-containment unit regression, **not a hosted-owner recovery or completed-integration test**.
- Preserve previously accepted work: exact-generation local CAS and foreign-worktree mitigation, symlink-safe direct-child discovery, explicit ambiguous/unregistered worktree diagnostics, normal accepted→done complete retirement and fresh-owner replay after all resources already disappeared.

### Unresolved main acceptance: real autonomous convergence

No tests in this checkpoint exercise fresh service-owner startup/idle scheduling with a genuinely published `done` candidate and partially remaining resources. The unchanged gaps remain the **top priority**, not further narrow exception-handler patches:

1. Real-Git **fresh hosted-owner** partial recovery after `done` **before cleanup scheduling**, after checkout/registration removal with **both refs still present**, **remote-only** residual after local ref removal, and temporary remote read/write outage followed by restored upstream. Verify autonomous retries, full eligible resource retirement, preserved immutable report and product/control history, and ability to execute another runnable ticket. Existing full-retirement crash replay is already proven; do not repeat it.
2. Real eligible-later `done` vs earlier unsafe/blocked candidate: subsequent bounded owner passes **actually retire eligible-later checkout, registration and local/upstream refs**, across restart, without manual cleanup RPC. The existing cursor-rotation test on two unintegrated worktrees does not satisfy this.
3. **One universal age-agnostic owner reconciler**: safely reclaim a genuinely provable older accepted→done generation *without current message trailers* using its independent report/product/control/ref evidence; retain a genuinely ambiguous older candidate with explicit diagnostics. Do not introduce a separate legacy mode or weaken identity proof.

Keep CLI `devlegate clean` read-only, previously verified safety guards intact, and `release/0.5.7` unchanged. **REWORK REQUIRED:** move the same complete ticket (no split or replacement) from `kanban/review/TASK-063.md` to `kanban/todo/TASK-063.md` on canonical `devlegate/control`, preserving all previous reviewer history and reports.
