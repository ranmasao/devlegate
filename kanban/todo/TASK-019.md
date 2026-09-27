---
"type": "devlegate.ticket"
"title": "Fail closed on invalid bound workspace recovery"
---

## Milestone

Devlegate 0.5.5 self-hosting recovery hardening.

## Goal

Make persisted bound-execution planning and recovery fail closed when an
`agent_pending` execution workspace can no longer be proven to descend from its
planned product revision, while automatically repairing pre-worker workspace
corruption whenever Devlegate can prove that the repair is lossless and
identity-safe.

A read-only plan must never claim that a bound execution can be resumed when the
same persisted execution state will fail workspace validation at service startup.
Conversely, an invalid pre-worker workspace binding must not turn an otherwise
valid ticket into an operator-managed dead end when Devlegate has enough evidence
to reconstruct the admitted workspace safely.

## Context

Dogfooding in rslab2 exposed a contradictory state for ticket `LAB-153`.

Observed product/control state was clean and synchronized:

```text
Code: master @ 95215689f5db
Control: devlegate/control @ 073b5ecaa0ad
```

Persisted execution state was:

```text
phase=agent_pending
stage=lifecycle
execution=a97c6db5f41e4c349171d756b67ad26a
bound_ticket=LAB-153
```

The service failed during runtime recovery with:

```text
execution branch devlegate/work/LAB-153 is unrelated to planned code revision
95215689f5db9e8e7e3e1189bfc2c7b2435bed1f
```

`devlegate status` correctly surfaced:

```text
Current: LAB-153  •  recovery-required
```

but `devlegate plan` simultaneously reported:

```text
Plan: run-worker · resume persisted bound execution
Bound: yes
```

and YAML status contained:

```yaml
plan:
  action: run-worker
  reason: resume persisted bound execution
  bound: true
```

There was also no ordinary recovery candidate:

```yaml
failed_executions: []
reconciliation: null
```

Current implementation explains the mismatch:

- execution-workspace reuse validates that the planned base is an ancestor of the
  execution branch and fails closed when it is not;
- bound `agent_pending` planning checks ticket identity/state, but does not prove
  the persisted workspace/branch lineage before returning `run-worker`;
- ordinary interrupted retry candidates require `agent_running`;
- current drop candidates require `agent_running` at lifecycle with a durable
  pending ExecutionReport.

Thus an invalid pre-worker binding can produce an impossible resume plan and an
operator recovery dead-end.

### Clarified recovery semantics

`LAB-153` itself is still expected to execute. The observed failure happened
before worker ownership, so the invalid execution workspace is infrastructure
state to recover, not a reason to abandon the ticket or require an operator to
re-authorize ordinary execution.

For bound `agent_pending` recovery, distinguish three material states:

- **REUSABLE** — the exact persisted workspace binding already satisfies all
  resume invariants and the worker may be launched from that proven workspace;
- **RECOVERABLE** — the binding is invalid, but Devlegate can prove the exact
  persisted execution identity, prove that no worker result can be lost, preserve
  the conflicting Git state losslessly, reconstruct the workspace from the exact
  admitted product revision, and re-prove all resume invariants;
- **UNSAFE** — ownership, preservation, or material identity cannot be proven
  strongly enough for automatic mutation.

A `RECOVERABLE` pre-worker failure is expected to heal automatically. Operator
involvement is a fallback for `UNSAFE` ambiguity, not the normal path for an
unrelated or stale execution branch discovered before worker start.

### Concrete stale-generation reproduction

The rslab2 state has now been traced to an older, fully identified execution
generation rather than an unknown unrelated branch.

The previous LAB-153 execution was:

```text
execution=be7ce77dab48436db7b92f4164506538
base=96083c32b71ac671dec59a347908d980e818f7de
checkpoint=bf0de8dbd477ee2e25b726d50195a16253637190
branch=devlegate/work/LAB-153
```

Its durable control-plane receipt still exists at
`executions/LAB-153/be7ce77dab48436db7b92f4164506538.json` and binds that
execution to checkpoint `bf0de8db...`.

That execution completed successfully, but review explicitly rejected it as a
completion result because its participant-binding authority depended on work later
assigned to LAB-154. The canonical LAB-153 ticket records that the old checkpoint
is diagnostic only and must not be accepted or reused as the completion result.

LAB-154 later completed at product revision:

```text
95215689f5db9e8e7e3e1189bfc2c7b2435bed1f
```

A fresh LAB-153 execution was then admitted:

```text
execution=a97c6db5f41e4c349171d756b67ad26a
phase=agent_pending
stage=lifecycle
execution_base_head=95215689f5db9e8e7e3e1189bfc2c7b2435bed1f
```

Before that new worker ever ran, the conventional work branch still pointed to the
old execution checkpoint:

```text
devlegate/work/LAB-153 -> bf0de8dbd477ee2e25b726d50195a16253637190
```

Git proves the generations diverged:

```text
merge-base = 96083c32b71ac671dec59a347908d980e818f7de
old branch = one commit ahead from the old base
new admitted base = three commits ahead from the same merge-base
```

After updating/restarting Devlegate, startup still failed with the same unrelated
branch error and the systemd service exited with status 1. This confirms that the
current failure is an unhandled stale-generation workspace condition at startup,
not a worker failure.

This concrete case should classify as RECOVERABLE when the following provenance can
be proven:

- the current bound execution is the new pre-worker generation
  `a97c6db5...`;
- the conflicting branch HEAD `bf0de8db...` is exactly attributable to the prior
  durable LAB-153 execution `be7ce77d...`;
- the current execution has no worker ownership or result that could be lost;
- the old checkpoint can be preserved under a real product-repository ref before
  the conventional work branch is moved.

A SHA recorded only in control-plane JSON is not by itself a Git reachability root
for the product repository. If the conventional work branch is the last product
ref keeping an old checkpoint reachable, automatic recovery must create or verify a
durable product-repository evidence/checkpoint ref before repointing or deleting
that branch. Do not rely on the textual SHA in the control receipt as object
retention.

The recovery operation must preserve the distinction between the old rejected
execution and the new admitted execution. It must not reinterpret the old checkpoint
as work belonging to the new generation merely because both use the same
`devlegate/work/LAB-153` branch name.

## Required behavior

- A bound `agent_pending` plan may return `run-worker` only when the exact
  persisted execution workspace binding required for resume is provably reusable.
- The proof must include the same material lineage/identity invariants used by actual
  workspace recovery, including the exact planned product base and execution branch
  ancestry.
- Planning and execution must not maintain divergent definitions of "resumable".
  Prefer one shared read-only inspection/validation boundary or equivalent common
  semantics.
- Read-only planning remains non-mutating. When it observes an invalid but
  automatically recoverable binding, it may report a recovery action/state, but it
  must not report executable `run-worker` until the repaired workspace has been
  re-proven.
- Service/runtime recovery classifies the persisted binding as REUSABLE,
  RECOVERABLE, or UNSAFE before launching a worker.
- A RECOVERABLE `agent_pending` binding is repaired automatically without requiring
  an operator command.
- Automatic repair is permitted only when Devlegate can prove all of the following:
  - the persisted execution/ticket/base/control identity is exact;
  - no worker currently owns the execution and no durable worker result is being
    discarded;
  - the conflicting branch/worktree material can be attributed strongly enough to
    the persisted execution to authorize repair;
  - any conflicting commit state can be preserved losslessly before destructive or
    ref-moving mutation;
  - the observed branch/worktree identity has not changed between inspection and
    mutation.
- When a conflicting conventional work branch is proven to belong to an older
  execution generation of the same ticket, preserve that older generation under a
  durable product-repository ref bound to its execution/checkpoint identity before
  reusing the conventional branch name for the current generation.
- Before moving, replacing, or deleting a conflicting execution branch containing
  commits, preserve the exact observed HEAD under an immutable recovery/evidence
  reference bound to the ticket and execution identity, or an equivalent durable
  provenance mechanism. Evidence preservation must succeed before mutation.
- After evidence preservation, re-observe the branch/worktree state and use
  compare-and-swap / exact expected-head semantics so concurrent drift causes the
  repair to fail closed rather than overwrite newer material.
- Automatic repair reconstructs the execution branch/worktree from the exact
  persisted `execution_base_head`, not from a guessed/current product HEAD.
- The reconstructed workspace is fully revalidated before worker launch: repository
  identity, expected branch, expected path/registration, exact admitted base
  lineage, checkout HEAD, required cleanliness, and relevant submodule invariants
  must all satisfy the normal resume contract.
- If no worker ever owned the execution and the exact admitted workspace can be
  reconstructed and re-proven, the same execution identity may continue. Do not
  manufacture a replacement execution generation merely because infrastructure
  state was repaired.
- Automatic recovery must not change the canonical ticket workflow state merely
  because the workspace was invalid. In the reproduced case, `LAB-153` remains the
  same pending ticket and proceeds to normal worker execution after repair.
- If the persisted workspace/branch is missing, unrelated, registered to the wrong
  branch/path, dirty in a way recovery forbids, or otherwise cannot be proven safe,
  read-only plan/status must not advertise `run-worker`. The runtime may repair it
  only when the condition satisfies the RECOVERABLE proof above.
- If ownership or lossless preservation cannot be proven, classify the condition as
  UNSAFE, keep the worker stopped, surface a concrete recovery-required reason, and
  provide a supported explicit operator recovery path without requiring manual
  SQLite/state-file edits.
- The UNSAFE operator path must itself be identity-bound and fail closed. It must not
  guess that an unrelated branch/worktree belongs to the current execution,
  silently delete unproven work, or rewrite product/control history.
- That fallback recovery path must remain usable when normal service startup cannot
  complete ordinary execution. Prefer keeping the service alive in a blocked state
  when practical; otherwise provide a safe offline operator path.
- Existing valid `agent_pending` restart/resume behavior remains unchanged.

## Acceptance criteria

- Reproducing an `agent_pending` execution whose
  `devlegate/work/<ticket>` branch is not descended from its persisted
  `execution_base_head` never launches a worker against that invalid workspace.
- Before repair, read-only plan/status do not claim an executable bound resume.
- For a provably RECOVERABLE pre-worker mismatch, service/runtime recovery completes
  without operator action, preserves conflicting Git evidence before mutation,
  reconstructs the workspace from the exact persisted base, revalidates it, and
  then launches the worker at most once.
- The automatic RECOVERABLE path keeps the same ticket eligible for execution and
  does not route it through drop/abandon semantics.
- When no worker ever owned the execution and the workspace is reconstructed to the
  same admitted generation, continuing with the same execution identity is allowed
  and is covered by tests.
- Automatic repair refuses mutation if the observed branch/worktree identity changes
  after inspection or if exact expected-head/evidence conditions no longer hold.
- An unrelated branch containing commits is never destroyed merely because its
  conventional name matches the ticket. Automatic replacement is allowed only after
  exact ownership/identity proof plus durable preservation of the observed commit
  state.
- If the invalid binding is UNSAFE rather than RECOVERABLE, no worker is launched,
  no unproven Git material is destroyed, and one documented supported CLI recovery
  route remains available.
- A valid persisted `agent_pending` workspace still resumes exactly once and keeps
  the same execution identity.
- Status text, status YAML/JSON, and plan text/YAML/JSON agree on whether the bound
  execution is reusable, being recovered, or recovery-required.
- Full tests, lint, and relevant recovery/subprocess tests remain green.

## Required regressions

- Given persisted `agent_pending` state with a valid execution branch descended
  from the exact planned base and matching registered worktree, classify it as
  REUSABLE, report the existing bound resume, and launch at most one worker.
- Given the same state but an execution branch unrelated to the exact planned base,
  never launch the worker before recovery.
- Given that unrelated branch is exactly attributable to the persisted execution,
  no worker has owned it, its observed HEAD can be pinned durably, and branch/worktree
  mutation can be guarded by exact identity/CAS checks, classify it as RECOVERABLE,
  preserve the old HEAD, rebuild from the exact admitted base, re-prove the workspace,
  and execute the same ticket without operator intervention.
- Given a stale `devlegate/work/<ticket>` branch that exactly matches a durable
  checkpoint from a previous reviewed/rejected execution of the same ticket, while a
  newer execution is still `agent_pending` on a later admitted base, classify the
  state as RECOVERABLE, preserve the old checkpoint under a durable product ref,
  rebuild the conventional branch/worktree for the new base, and launch only the
  new execution.
- Given only a textual old checkpoint SHA in control evidence and no durable product
  ref retaining that object, recovery must establish the product-repository
  retention ref before moving the last known conventional branch reference.
- Given branch/worktree drift between RECOVERABLE inspection and effect, automatic
  recovery refuses the mutation and does not launch a worker.
- Given a missing or wrong registered execution worktree/branch, use the same shared
  inspection semantics as actual recovery; automatically reconstruct only when
  ownership and lossless preservation are provable, otherwise classify UNSAFE.
- Given an unrelated branch containing commits whose ownership cannot be proven,
  do not automatically clean, rewrite, or delete it merely because its conventional
  branch name matches the ticket.
- Given an UNSAFE invalid bound workspace and a stopped/blocked service, the supported
  operator recovery command remains available and performs only its explicitly
  authorized identity-bound effect.
- Given successful automatic reconstruction before any worker ran, the canonical
  ticket remains pending/eligible, the execution may retain its existing execution
  id, and the worker subsequently performs the ticket normally.
- Given status and plan produced from one stable persisted generation, they cannot
  disagree as `recovery-required` versus executable bound resume.


## Review feedback after execution 872b0c1f06174351b978fe16e72ee77f

The implementation has useful pieces that should be retained: shared read-only
workspace inspection, durable recovery refs for prior-generation checkpoints,
re-observation before mutation, exact expected-head CAS branch replacement, rebuild
from the persisted admitted base, and final REUSABLE revalidation.

However, this result does not yet satisfy TASK-019.

### 1. RECOVERABLE is classified too early

`ExecutionWorkspaceManager.inspect()` currently returns `RECOVERABLE` whenever
the conventional execution branch is missing or fails the admitted-base ancestry
check. That inspection does not prove the material conditions that define
RECOVERABLE in this ticket: exact current execution identity, absence of worker
ownership/result, attribution of the conflicting HEAD to one prior durable
execution generation, and lossless evidence preservation capability.

As a result, read-only plan/status can advertise an unrelated or unowned branch as
"recoverable" even though `_repair_bound_execution_workspace()` will later reject it
because prior-generation ownership cannot be proven. Such a state is UNSAFE by the
ticket definition. Keep one shared classification boundary: topology alone may
identify a repair candidate, but the externally reported REUSABLE / RECOVERABLE /
UNSAFE classification must incorporate the same provenance/ownership proof used by
the mutation path.

### 2. UNSAFE agent_pending still has no supported operator recovery route

The ticket explicitly requires an identity-bound supported CLI fallback that remains
usable when automatic repair is unsafe. The current existing paths still do not
cover this state:

- interrupted retry candidates require `phase == agent_running`;
- drop candidates require `phase == agent_running` and lifecycle/report evidence;
- this checkpoint adds no CLI/operator command or documented offline/service-blocked
  recovery path.

Therefore an unproven unrelated pre-worker branch can still become the same
operator dead-end this ticket is intended to remove. Add a narrow explicit recovery
operation whose admission is bound to the exact ticket/execution/base and observed
workspace/branch identity, preserves any explicitly authorized conflicting material
before mutation, and fails closed on drift. Do not broaden drop semantics or guess
ownership.

### 3. Required regressions are missing

Checkpoint `97ac62b44850617ea522151b8f2a49705b1688a4` changes only
`execution_workspace.py` and `runtime.py`; no tests were added. TASK-019 requires
regressions for, at minimum:

- valid bound workspace -> REUSABLE and exactly one resumed worker;
- stale prior-generation checkpoint -> RECOVERABLE only after exact durable
  attribution;
- evidence ref established before moving the last conventional product ref;
- branch/worktree drift between inspection and mutation -> fail closed;
- unowned unrelated branch with commits -> UNSAFE and untouched;
- missing/wrong worktree classification through the shared inspection semantics;
- successful repair retaining the same current execution id;
- status/plan agreement;
- supported UNSAFE operator recovery with exact identity binding.

Please keep the existing evidence-pin/CAS/rebuild work, move the full
REUSABLE/RECOVERABLE/UNSAFE proof into a shared read-only classification boundary,
add the explicit UNSAFE recovery path, and cover the required scenarios with tests.


## Review continuation after execution 3a14247446e2476994db7391eb411699

This incomplete handoff moves the implementation materially in the right direction
and should be continued, not redesigned.

The prior review's two semantic blockers are now substantially addressed:

- externally visible bound-workspace classification is provenance-aware rather than
  treating topology alone as sufficient for RECOVERABLE; an unrelated branch with
  no unique durable prior-generation attribution is reported UNSAFE;
- an explicit `devlegate recover <ticket> <execution> --observed-head <sha>`
  service/IPC path now exists for UNSAFE pre-worker recovery, with exact active
  execution identity checks, evidence pinning, re-observation, CAS branch movement,
  exact-base reconstruction, and final REUSABLE validation.

Do not discard these changes.

The execution correctly reported itself incomplete. Finish the remaining acceptance
work:

1. Add the required focused regressions for REUSABLE, provenance-proven
   RECOVERABLE, unattributable UNSAFE, evidence retention before ref movement,
   inspection-to-mutation drift/CAS refusal, same execution-id continuation,
   status/plan agreement, and the explicit operator recovery path. Include the
   missing/wrong worktree cases called out by the ticket.
2. Fix the current lint failures in `runtime.py`. Exact-head CI for
   `0a1f0970fed7134455f3044b0aed3c5086e301f0` ran the full suite successfully
   (`974 passed, 1 skipped`) but then failed Ruff with 11 E501 line-length errors.
3. Re-run the full repository validation and report the exact result.

The absence of a long-form report is not a blocker for this incomplete handoff; the
durable JSON receipt already preserves its summary and remaining work. Acceptance
still requires the finished regressions and green exact-head CI.


## Review continuation after execution 5caccee44ceb4e3ba286913ca7ce7135

This incomplete continuation is healthy and should be resumed from its current
checkpoint rather than redesigned.

Progress confirmed at checkpoint
`5577db1a6d6b87957b83164d3c0ca16bef7ce5a4`:

- the Ruff E501 failures from the previous checkpoint are fixed;
- exact-head CI is green: `978 passed, 1 skipped`, coverage generated, and
  `All checks passed!`;
- focused workspace-inspection regressions now cover a clean exact binding as
  REUSABLE and a missing worktree as a RECOVERABLE rebuild candidate;
- the provenance-aware RECOVERABLE/UNSAFE classification and identity-bound
  operator `recover` path remain intact.

The worker correctly reported the task incomplete. Finish only the remaining
required runtime/integration coverage from TASK-019:

- provenance-proven stale prior generation -> RECOVERABLE;
- unattributable unrelated branch with commits -> UNSAFE and untouched;
- evidence ref established before the last conventional ref is moved;
- inspection-to-mutation branch/worktree drift -> CAS/fail-closed refusal;
- successful automatic repair keeps the same current execution id and launches
  at most once;
- status and plan agree on REUSABLE / RECOVERABLE / UNSAFE;
- explicit operator recovery succeeds only for the exact authorized
  ticket/execution/observed-head identity and fails closed on drift;
- missing/wrong registered worktree cases exercise the same shared semantics.

The previous receipt's local note that pytest was unavailable is superseded by the
authoritative exact-head GitHub run above, so no environment issue remains as a
release blocker. Add the missing tests, run the full validation again, and report
the exact result.


## Review continuation after execution d50ded12d6544b41832dd8f41ee4c12c

The implementation is now semantically close to acceptance and exact-head CI is
green at checkpoint `994d3bfbc8e83dbd240f0bd0baef64585bd6cdb3`:

- `982 passed, 1 skipped`;
- coverage completed at 79%;
- Ruff completed with `All checks passed!`.

The previous major semantic blockers remain resolved. The current code has
provenance-aware REUSABLE / RECOVERABLE / UNSAFE classification, durable evidence
pinning before ref movement, exact expected-head CAS, same-execution reconstruction,
and an identity-bound operator recovery implementation.

Do not redesign these mechanisms. Finish the remaining required proof cases only.

### 1. Prove inspection-to-mutation drift fails closed

The implementation re-observes the conventional branch before/after worktree
retirement and uses `update-ref <ref> <new> <old>` CAS, but no focused regression
currently mutates the branch/worktree after RECOVERABLE inspection and proves that
automatic repair refuses the stale effect.

Add a deterministic race regression that changes the observed branch/worktree
identity between inspection and mutation and proves:

- the newer/unexpected material is not overwritten;
- the repair does not launch a worker;
- preserved recovery evidence does not authorize mutation of the drifted state.

### 2. Prove automatic repair continues the same execution exactly once

`test_prior_generation_is_repaired_with_evidence_and_same_execution_id` proves the
same execution ID survives manual invocation of the repair helper and that the plan
becomes runnable, but it never exercises the normal runtime path through repair into
worker launch.

Add one focused runtime/integration regression proving a provenance-attributed stale
generation is automatically repaired and then launches exactly one worker under the
same current execution ID.

### 3. Exercise the supported operator recovery boundary

The new test covers private engine admission/recovery helpers and observed-head
mismatch, but TASK-019 requires a supported CLI recovery route. The new
`devlegate recover <ticket> <execution> --observed-head <sha>` and IPC dispatch
currently have no regression in `test_cli.py` / `test_ipc_server.py`.

Add focused public-boundary coverage proving:

- CLI/IPC carries the exact ticket/execution/observed-head identity;
- wrong ticket, wrong execution ID, or stale observed HEAD is rejected before the
  recovery effect;
- the exact authorized identity reaches the owner-side recovery path.

### 4. Cover wrong registered-worktree classification through the shared boundary

The existing historical low-level workspace test proves `prepare()` rejects a work
branch attached to an unexpected worktree, and the new tests cover a missing
worktree candidate. Add the TASK-019-level assertion that the shared bound-workspace
inspection/status/plan path classifies a wrong registered worktree as non-runnable
(UNSAFE unless the exact recovery proof permits otherwise) rather than advertising
bound resume.

The worker's local note about unavailable development dependencies is not a blocker:
the authoritative exact-head GitHub run above supersedes it.

Once these focused regressions are present and the new exact-head CI remains green,
TASK-019 should be ready for review -> accepted.
