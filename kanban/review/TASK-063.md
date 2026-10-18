---
"type": "devlegate.ticket"
"title": "Automatically retire integrated execution Git resources"
"depends_on": ["TASK-061"]
---

## Milestone and authoritative scope — revised 2026-10-17

Devlegate 0.5.7. TASK-061 is already integrated. **The current contract below supersedes obsolete historical review instructions**,
including old prohibitions on deleting work branches, operator-only historical
cleanup, and the mandatory public `cleanup-workspaces` command. Keep the existing worker code, execution
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

## Current reviewer decision — 2026-10-18: REWORK REQUIRED

**Reviewed execution:** `bcb99ad130ee4995b453b228287a84cf`.
**Checkpoint:** `0b205c9d326ef8c297e2fe7ea56e3a1d11e0bcba`.
**Admission control:** `37881f9d9aee0a824a8aa2468de8d81784e9fd50`.
**Product base:** `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`.
The worker claimed `completed`; acceptance remains blocked by missing
mandatory end-to-end recovery and historical provenance evidence. Preserve
all existing execution work and checkpoints; do not integrate `release/0.5.7`.

### Established — retain existing implementation and evidence

- Previously tested: normal post-publication retirement; fully absent
  disposable refs with pending accepted integration; exact-ref CAS,
  remote deletion lease, foreign/unknown checkout safeguards and read-only
  `devlegate clean`.
- The previous checkpoint `e66ca1fff210` introduced bounded eligibility
  preflight for owner-hosted cleanup fairness.
- **New checkpoint `0b205c9d326e` adds a 103-line real-Git owner-hosted
  integration test** `test_hosted_cleanup_retires_later_proven_execution_after_blocked_first`
  in `tests/test_control_workspace.py`. It creates two durable report/control
  done histories and remote work refs; `T-1` has a dirty checkout; the
  `LiveService` owner must fully remove `T-2` checkout/local/upstream refs
  while retaining `T-1`. It then restarts the service, removes the blocker,
  and requires automatic eventual retirement of `T-1`, while preserving
  canonical done tickets and durable report evidence. This materially
  addresses the blocked-earliest/eligible-later requirement. No production
  code was changed in this checkpoint.
- The preceding unregistered-checkout test exercises real ref deletion and
  safe directory retention, but mocks integrated publication/provenance.
- **CI caveat:** no exact-checkpoint GitHub Actions run or status was
  confirmed for `0b205c9d326e`; the new test's intended assertions have
  been inspected, but its actual pass/fail result was not independently
  observed. Do not mistake the worker's `completed` claim for acceptance.

### Outstanding — remaining required proofs

1. **Partial-stage restart and remote outage:** owner-hosted real-Git
   published `done` proof covering crash before cleanup intent,
   worktree/registration absent while local+upstream refs remain, remote-only
   leftover, remote outage followed by restoration and automatic retry.
   Preserve durable publication and allow unrelated runnable tickets to
   progress. Reuse the existing fully-retired pending-state recovery test.
2. **Trailerless historical reconciliation:** through the *same* autonomous
   age-agnostic owner reconciler, actually remove exact, safely provable old
   `done` resources without modern trailers; separately demonstrate an
   ambiguous historical identity is retained with an actionable explanation.
   Exercise live owner passes, not only direct helper invocations.
3. **Cross-layer unregistered checkout proof:** if not already included in
   one of the preceding scenarios, use real published-done evidence to show
   exact disposable refs retire while an unknown/unregistered directory
   remains safely untouched and visibly blocked.
4. **Validation:** run focused owner-hosted tests and lint, and obtain
   exact-final-checkpoint full GitHub Actions CI (tests/coverage/Ruff)
   before acceptance. Investigate any failures before claiming completion.

### Next execution

Prioritize the positive **owner-hosted partial-stage recovery** and
**trailerless historical** acceptance proofs. Do not reimplement the now
covered fairness scenario or spend another iteration on isolated mocks,
diagnostic polish or unrelated CLI changes. Preserve no-force-delete,
remote CAS, instance ownership, read-only `clean` and original ticket
contract. Older reviews are recoverable from control Git history.
