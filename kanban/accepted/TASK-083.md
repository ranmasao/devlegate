---
"type": "devlegate.ticket"
"title": "Make reviewed-ticket rework a normal, provenance-safe execution transition"
---

## Milestone / priority

Devlegate 0.5.7 — high-priority lifecycle correctness fix, before broader
revision-advancement semantics in TASK-076. Queue in `todo` independently of
TASK-079 (which may currently have a bound execution); do not preempt, drop,
reset, or mutate that active execution merely to start this ticket.

## Problem: rework is not workspace recovery

A completed worker execution may end in `review`; the curator can request
rework directly (`review -> todo`) or defer it (`review -> backlog -> todo`).
In both cases, the next selection from `todo` is **a new, ordinary rework
generation** of the same ticket, with its own execution identity and effective
code base. It is **not** resumed execution of the already published worker
report, and it is **not** an interrupted Git transaction, lost worker, or
operator-authorized unsafe-workspace recovery.

The ticket's rework instructions, preceding worker checkpoint, and previous
execution report are inputs to the new generation. The prior implementation
must be reused or retained as attributable work; do not silently discard it
and start a fresh implementation from an empty checkout.

Avoid coupling generation rotation to the exact intermediate Kanban lane:
`backlog` is a scheduling deferral, not a change of execution provenance.
A control-file move alone (possibly made while the daemon is offline) must
not mutate the product worktree, rewrite refs, or create an execution.
Materialize rework **only upon scheduler admission** of the runnable `todo`
ticket under normal owner authority.

## Reproduced incident (2026-10-19)

- Old TASK-079 execution `5619a16244ec4c00bc0851b3d138175d` ended
  `incomplete`, with report in
  `executions/TASK-079/5619a16244ec4c00bc0851b3d138175d.json`.
  Its checkpoint is `e1dff2e043911766c9955aed4e64474dc9f7deff`,
  originally based on `bf393bc5193a3505cc96143dd51b5a5a0c1d88db`.
- Curator returned the same ticket from `review` to `todo`, later deferred
  it to `backlog`, then requeued it to `todo`. Previous review guidance
  was kept; existing implementation must also survive.
- After integration of TASK-082, product `release/0.5.7` was at
  `412fca11d553b5537cb973ef8fa94b6ac6a041b1`.
  New execution `e2e3545cbe64426490f2cfb7776faa19` reached
  `agent_pending`, but incorrectly retained `execution_stage=lifecycle`
  and a stale `execution_start_head` from the earlier generation.
- A partial automatic workspace repair had set local
  `refs/heads/devlegate/work/TASK-079` and its clean worktree to
  `412fca11`, but remote `refs/heads/devlegate/work/TASK-079`
  still held `e1dff2e`. The local evidence ref
  `refs/devlegate/recovery/workspace/TASK-079/5619a16244ec4c00bc0851b3d138175d`
  pointed at `e1dff2e` but initially did not exist on origin.
- The planner reported `recovery-required` /
  `conflicting execution branch ownership cannot be proven`: workspace
  inspection compared a clean **new-generation** checkout against an old
  `execution_start_head` and then attempted to attribute its current HEAD to
  the previous execution report.
- An explicit operator `recover` cleared the stale stage/start-head and
  yielded `phase=agent_pending`, `stage=null`, `plan=run-worker`,
  `reason=resume persisted bound execution`. Operator intervention was a
  workaround, **not** the desired protocol for ordinary rework.

Use this sequence as a deterministic regression, not as authorization to
rewrite those historical commits, reports, or the currently running daemon.

## Required semantics: generation identity and admission

1. Distinguish at least:
   - **Fresh**: first admission of a ticket with no preceding completed
     execution;
   - **Rework**: a prior worker has a durable, completed lifecycle/report and
     the same ticket has become runnable in `todo` again;
   - **Resume**: the *same* admitted execution after an interruption before
     durable completion, using its unchanged execution ID and binding;
   - **Recovery**: lost/ambiguous authority, inconsistent transaction or
     unproven workspace requiring explicit recovery rules.
   Do not conflate these in `WorkDirective.RESUME`, preparation refusal,
   control ticket movement, status display, or recovery interfaces.
2. On **rework admission**, mint a fresh execution ID exactly once. Preserve
   an explicit, durable reference to the exact previous execution
   ID/report/checkpoint/control identity, rework request/ticket generation,
   and admitted new product/control revisions. No dependency on an
   in-memory-only flag or current ticket lane history remaining in `review`.
   The new report must remain attributable to the new execution; the previous
   report is immutable.
3. Treat `execution_stage`, `execution_start_head`, worker identity,
   pending report, interruption markers, stale remote predecessor and
   recovery/reconciliation intents as **generation-scoped**. At the boundary
   between completed execution and newly admitted rework, clear or rebuild
   these atomically as appropriate. In particular do not emit
   `agent_pending + lifecycle` inherited from the preceding generation.
   Preserve previous generation evidence rather than clearing global
   historical reports.
4. Once an execution reaches `agent_pending` with a durable admission
   binding, daemon restart and repeated polls **resume that same pending
   generation**, not mint a second ID or repeat already completed Git
   mutations. If a phase is genuinely interrupted/unsafe, fail closed;
   restarting must not turn a recovery incident into ordinary rework.

## Required semantics: preserve implementation, prepare new base

5. A prior checkpoint is real code, not just a prompt reference. For
   revisions already integrated into the product, prove they are already
   present and do not duplicate them. For still-unintegrated prior worker
   commits, prepare a new generation from the current product HEAD while
   **carrying forward attributable implementation changes** using a safe,
   explicit Git transition. A minimal implementation may transplant a
   proven single-worker linear delta (with exact ancestry, patch identity
   and clean worktree checks) onto the new product base, if it can prove
   correctness; do not silently reimplement from scratch or drop changes.
   If the existing mechanism cannot safely preserve the delta, report a
   **rework preparation conflict** with the old checkpoint and target HEAD,
   without launching the worker. A conflicting transplant requires a
   deliberate resolution path, not a hidden `reset --hard`, blind cherry-pick,
   or unbounded general rebase engine. Keep the prior implementation
   inspectable even when automatic carry-forward is refused.
6. Recheck Git worktree registrations, branch head, cleanliness, symlink/path
   safety, submodules, and exact prior report provenance before mutation.
   A branch name of the form `devlegate/work/TASK-XXX` is NOT sufficient
   proof of who owns the old generation. Distinguish absent refs from Git
   observation failures. No mutation while a worker owns the old checkout,
   or if prior outcome/publication has not durably completed.
7. Treat local execution refs, remote execution refs and historical evidence
   as separate observed resources. Before changing a reusable ticket-scoped
   execution ref, preserve its old exact head in a generation-scoped evidence
   ref with guarded create-if-absent/CAS and validate it after races. Where
   a remote ref will be displaced, require durable *remote* evidence
   publication/verification before its replacement; **local evidence alone
   does not prove remote durability**. Use an exact expected-remote-head
   lease for remote updates and refuse ambiguous/conflicting results.
   Reconcile the current new-generation predecessor consistently in durable
   state so a successful worker can publish its checkpoint normally.
   Never overwrite a conflicting historical evidence ref or rewrite prior
   execution reports.
8. Make each observable mutation stage crash-replayable: admission state,
   prior evidence pin, remote evidence publication, worktree detachment,
   local ref transition, remote ref transition, generation worktree creation,
   and publication predecessor binding. Re-observe exact identities after a
   crash; a partially completed safe transition must complete idempotently
   or stop with precise conflict diagnosis. Do not use the exceptional
   operator `recover` as a routine intermediate stage. Keep existing
   ServiceEngine owner lock, WAL/SQLite rules, worker ownership proofs and
   force-with-lease/CAS authority intact.
9. The new worker receives **focused rework context**: current ticket
   requirements plus curator's outstanding review findings, explicit
   continuation/rework directive, and a safe way to inspect the attributable
   previous code/checkpoint against its new base. Do not expose private
   internal execution IDs, local state paths, or hidden provenance fields
   in the agent-visible prompt unless already part of the declared worker
   boundary. State what previous implementation is preserved/ported, what
   requires attention, and instruct the worker to inspect before editing.
   Avoid LLM-generated retrospective summaries or silently dropping earlier
   work. Do not call an ordinary rework an interrupted worker resume.
10. Explain rework/fresh/resume/recovery outcomes in status and logs without
    claiming an incident when rework reuses a completed checkpoint. True
    ambiguous or dirty/unowned workspace remains a fail-closed conflict,
    never quietly downgraded to reusable.

## Scope / implementation guidance

- Start by tracing these specific seams and documenting the ownership of
  each state field and Git effect:
  `runtime.py::_finalize_execution_lifecycle`, new admission in
  `_run_iteration_body`, `_save_state`,
  `_prepare_execution_workspace`, `_make_execution_plan`,
  `recovery_workspace.inspect_bound_workspace/repair_bound_workspace`,
  `execution_workspace.ExecutionWorkspaceManager`, checkpoint publication,
  and worker prompt rendering. Prefer a small explicit rework/generation
  transition seam over accumulating special cases inside `runtime.py`.
- Do **not** solve this by broadening the existing unsafe-workspace recovery
  conditions, implicitly authorizing operator recovery, suppressing
  provenance checks, or increasing generic retry attempts.
- Retain the existing deterministic `devlegate/work/TASK-XXX` convention
  unless a narrowly justified generation-scoped ref is required for durable
  proofs. A wholesale multi-worker scheduler or branch naming redesign is
  out of scope. TASK-076 still owns *live* safe descendant advancement of
  control/product branches during an execution.
- Do not change TASK-079's already-running execution, its previous report,
  reviewer findings, or the manual 2026-10-19 recovery evidence. Do not
  modify historical product/control commits for this task.

## Required deterministic regressions

1. `review -> todo` after a completed `incomplete` worker: one new
   execution ID, prior report/checkpoint retained, rework context used,
   previous source changes preserved on new base.
2. `review -> backlog -> todo` while product/control advance: same semantics
   as direct rework; no generation is created in `backlog`.
3. Exact TASK-079 pattern: old `bf393bc5 -> e1dff2e`, new product
   `412fca11`, old `execution_stage=lifecycle`; new pending execution
   must not inherit lifecycle/start-head, and a clean new-base workspace is
   not misdiagnosed as unproven prior ownership.
4. Old local/remote execution branch points at previous checkpoint;
   rework preserves preceding code and evidence, installs a coherent
   expected remote predecessor, then publishes a new checkpoint without
   forced or unrelated ref rewrite.
5. Local old-evidence ref exists but remote evidence is absent: cannot
   displace remote execution history until exact remote evidence is
   created and proven. Existing same remote evidence = idempotent; different
   remote evidence = fail closed.
6. Git failure, CAS/lease race, detached/dirty/foreign-registered worktree,
   missing/unreadable report, changed old ref, conflicting code transplant,
   malformed observation: no loss of prior code/evidence and no worker launch.
7. Crash after each distinct mutation boundary; restart resumes the same
   new pending execution ID, does not double-admit, redo an unsafe mutation,
   or publish a checkpoint against the wrong remote predecessor.
8. Normal first execution, genuinely interrupted same-execution resume,
   operator unsafe-workspace recovery, accepted integration/cleanup, and
   ticket rework are still distinct. No regression in existing accepted
   ticket and worker ownership checks.
9. Curator feedback remains available to the rework worker even after
   deferral in backlog. Do not rely on stale file content from prior
   `review` state when selecting the current todo body.
10. Tests prove the exact Git refs/worktree/durable state transitions with
    real Git and stateful engine; add a *selected* independent-service
    restart/IPC test when the touched boundary requires process proof.
    Mocks alone and a green status snapshot do not prove the carry-forward
    and evidence-before-ref-mutation requirements.

## Verification budget and curator acceptance

Follow `tests/TEST_BOUNDARIES.md` and `tests/TEST_PLACEMENT.md`:
focused test selection in `tests/test_control_scheduler.py`,
`tests/test_control_workspace.py`, `tests/test_control_recovery.py`,
`tests/test_execution_workspace_submodules.py`,
`tests/test_worker_prompt.py`, and `tests/test_git.py` where relevant.
Select named `tests/test_cli_topology.py` restart scenarios if production
restart/owner semantics change. Run Ruff locally. Avoid repeated local full
pytest/coverage; full authoritative pytest-with-coverage and Ruff belong in
exact-checkpoint GitHub CI.

Worker report must enumerate: old and new execution IDs/base/checkpoints,
actual preserved/transplanted code provenance, per-stage Git command and
effect counts (local reads, local mutations, remote operations), new/refused
transitions and their crash replay behavior, focused commands/results and
exact checkpoint. The worker must not claim that checkpoint preservation
alone equals completed rework if previous code was discarded.

**Curator acceptance requires demonstrable ordinary rework, not recovery:**
a previously reviewed and deferred ticket can be re-admitted against a
newer product base, carry forward its implementation and reviewer notes,
publish a new execution without manual `recover`, and preserve immutable
prior evidence. Real conflicts must remain explicitly blocked.

## Next-generation review handoff — execution 96d01f9b

This ticket is returned for **rework**, not operator workspace recovery.
Read the independent curator review artifact before editing. In the current
runtime, the worker receives this reference through the ticket assignment:

```sh
git show devlegate/control:reviews/TASK-083/96d01f9bf2ce4945937b7cb228cd69db.md
```

The separate artifact contains the blocking CI failures, specific unsafe
Git transitions, required deterministic crash/replay and lineage tests,
and focused acceptance requirements. Preserve the previous implementation
at `66955a81347a252489f6e1881d4dd6f4f7cbf859` and its original
execution report. The earlier specification above remains unchanged; this
handoff pointer is temporary until first-class review-artifact injection
exists. Do not use the legacy annotation marker.

## Next-generation review handoff — execution 0f5106b1

The newer **second-pass curator review** supersedes the previous
review's current-action instructions while preserving its evidence:

```sh
git show devlegate/control:reviews/TASK-083/0f5106b14f664846ab0fb64b6f423e0c.md
```

That separate review identifies exact-SHA CI failure (15 tests),
the inverted Git ancestry conditions, incorrect `git cherry` replay
arguments, remaining retry/rework classification errors, remote drift
across preparation stages, and the necessary concrete regression tests.
Retain source work from `66955a81347a252489f6e1881d4dd6f4f7cbf859`
through `82dc6ddf4f63f1aab360258f1dddf49ff185c01c`.
This is **ordinary rework**, not an invitation to operator recovery or
to override the active product branch. Original specification is above;
review evidence remains in separate files, with no legacy marker.

## Next-generation review handoff — execution bf79591c

This is an ordinary review-to-todo rework, **not** operator workspace recovery. The third execution declared `incomplete`. Read the independent third-pass review artifact before editing:

```sh
git show devlegate/control:reviews/TASK-083/bf79591c56a04229a95f7d36c3c81402.md
```

This review supersedes earlier artifacts only for current action items; preserve them as immutable evidence. Preserve prior implementations including checkpoint `39913d77d396c5d8082f12232828d009d931c803`. The outstanding crash/replay, lineage, remote-drift and exact-SHA CI obligations remain mandatory. Do not fold a separate broad Git API refactor into TASK-083.

## Next-generation review handoff — execution ff56eddb

Fourth execution `ff56eddb922e413cb2d27fba125f09e5` ended `incomplete` with checkpoint `50fab54e71102488b562a0945cc9c2513d5ffd3b`. For the precise remaining resume/rework regression failures, crash/replay and multi-generation tests, red CI, and missing worker report artifact, inspect:

```sh
git show devlegate/control:reviews/TASK-083/ff56eddb922e413cb2d27fba125f09e5.md
```

This is ordinary review -> todo **rework**, not operator recovery. Preserve previously implemented code and reports. Broad Git API refactoring, autonomous recovery redesign, and TASK-076 are separate work.

## Next-generation review handoff — execution 08a91b94

The fifth execution `08a91b94afb14160ae3045218d6060a3` published checkpoint `cccce554082df95db3c97dfbe521524431b67472` and claimed `completed`, but **exact-checkpoint CI was red: 1300 passed, 2 failed, 1 skipped**. Read the independent curator review for both exact failure names and the required lifecycle-contract diagnosis:

```sh
git show devlegate/control:reviews/TASK-083/08a91b94afb14160ae3045218d6060a3.md
```

This is ordinary rework, not unsafe-workspace operator recovery. Keep prior implementation, checkpoint, and review history. Do not broaden this task into Git API, autonomous recovery, God Module, skill audits, or TASK-076.
