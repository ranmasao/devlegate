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

For scheduled automatic passes, `--limit N` is a **per-pass work budget**,
not a lifetime cap on the number of candidates ever reclaimed. Continue
later passes. For an operator RPC, keep request deduplication and truthful
partial/replay reporting as applicable; a replay must not invent completed
results or inadvertently touch an unauthorized new execution generation.

## CLI / owner IPC surface: `devlegate clean`

Replace the provisional public `cleanup-workspaces` command with a small
`clean` **command group**. The first-class UX is automatic service cleanup;
CLI commands are optional for inspection and immediate maintenance:

- `devlegate clean status`: read-only inventory of eligible, removed,
  blocked and remaining **worktrees + local and remote branches**, with
  reasons and JSON/machine output using existing CLI conventions.
- `devlegate clean sweep [--limit N]`: request one bounded reconciliation
  pass **through the live service owner**; it must not create a second
  mutable engine or require prior operator opt-in for background cleanup.
- `devlegate clean ticket <TICKET-ID>`: optional narrow per-ticket repair/
  diagnosis if straightforward; reuse the exact same engine, proofs,
  authority and output rather than a second deletion path.

Do not create separate unsafe `clean worktrees` and `clean branches`
commands that bypass stage ordering or integration proof. Remove the
unreleased `cleanup-workspaces` public command and update CLI/IPC/status,
help, tests and docs consistently. A private internal reconciliation
method may retain an implementation-specific name. Preserve read-only
status purity and existing owner admission/replay semantics.

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
7. `clean status` does not mutate SQLite receipts, Git refs or filesystem.
   `clean sweep` is owner-routed, bounded per pass and safely repeatable;
   status/IPC reporting is accurate after partial effects and restart.
   Preserve drop, zero-delta, recovery, submodules, and previous targeted
   regressions.

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
operator-only historical cleanup, expose `cleanup-workspaces`, or bind
the aggregate lifetime deletion count to a manually admitted cleanup
request are **obsolete under the 2026-10-17 user decision above**.
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
