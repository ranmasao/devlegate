---
"type": "devlegate.ticket"
"title": "Complete pre-worker operator recovery continuation across execution generations"
---

## Milestone

Devlegate 0.5.5 recovery hardening.

## Goal

Make an explicitly authorized UNSAFE pre-worker recovery a complete, reachable,
generation-safe transition rather than merely a successful operator-command
admission or local-workspace repair.

After recovery, the exact current `agent_pending` execution must have coherent
persisted identity, coherent local/remote execution-branch lineage, and a reusable
workspace. The service must then continue through the normal owner path to at most
one worker launch under the same current execution ID.

A recovery-required state must also remain recoverable without a timing race: the
service must not destroy the only supported IPC recovery path merely because the
scheduler cannot proceed.

## Context

TASK-019 introduced the correct core mechanisms for bound pre-worker recovery:

- shared REUSABLE / RECOVERABLE / UNSAFE inspection semantics;
- durable product-repository evidence refs;
- exact observed-head re-observation and CAS;
- automatic repair for provenance-proven stale generations;
- an identity-bound public
  `devlegate recover <ticket> <execution> --observed-head <sha>` path for UNSAFE
  ambiguity.

Dogfooding the original rslab2 LAB-153 case after upgrading to 0.5.5.dev0 exposed
that these mechanisms are individually correct but the end-to-end operator recovery
continuation is incomplete.

The concrete current execution was:

```text
ticket=LAB-153
execution=a97c6db5f41e4c349171d756b67ad26a
phase=agent_pending
stage=lifecycle
execution_base_head=95215689f5db9e8e7e3e1189bfc2c7b2435bed1f
```

The conventional work branch still represented the previous LAB-153 execution:

```text
previous execution=be7ce77dab48436db7b92f4164506538
previous base=96083c32b71ac671dec59a347908d980e818f7de
previous checkpoint=bf0de8dbd477ee2e25b726d50195a16253637190
branch=devlegate/work/LAB-153
```

The old checkpoint remains durably attributable through the previous execution
receipt and must remain preserved as old-generation evidence rather than being
reinterpreted as work belonging to the new execution.

### Failure 1: supported recovery was reachable only through a startup race

The normal service stopped after reporting:

```text
bound execution workspace is unsafe: conflicting execution branch ownership
cannot be proven; operator recovery required
```

The public `recover` command requires the live daemon IPC endpoint. A startup
admission window made it possible to race a recovery request against startup, but
that is not a supported recovery guarantee.

A state that explicitly requires operator recovery must keep a supported recovery
authority reachable. Prefer a live blocked service that accepts the exact recovery
command. An offline one-shot path is acceptable only as a deliberately designed
fallback, not as a timing workaround.

### Failure 2: accepted operator recovery repaired Git but left stale execution state

The public command returned:

```text
recovery accepted: LAB-153
```

and the recovery effect reconstructed the local conventional work branch/worktree
at the new admitted base.

However, the persisted `agent_pending` state retained fields inherited from the
previous execution lifecycle, including:

```text
execution_stage=lifecycle
execution_start_head=<old generation>
```

Normal fresh `agent_pending` admission does not establish
`execution_start_head`; that identity belongs to the later
`agent_running` transition. Retaining it caused the next read-only inspection to
compare the reconstructed workspace against stale generation metadata and classify
the same state UNSAFE again.

The root mechanism is that `_save_state("agent_pending", ...)` merges fields into
the previous state without necessarily removing execution-stage fields that are not
valid for the new pre-worker generation.

### Failure 3: the conventional remote work branch still belonged to the old generation

The local work branch was reconstructed to the new admitted base while
`origin/devlegate/work/LAB-153` still pointed to the old rejected checkpoint.

The current execution must not silently inherit that old remote checkpoint as its
`execution_remote_head` merely because the conventional branch name is reused.

Before retiring, moving, or otherwise reusing a conventional remote execution
branch that represents an older generation:

- preserve the exact old checkpoint under durable product-repository evidence
  already bound to the old execution identity;
- freshly re-observe the remote branch;
- use an exact lease/CAS-equivalent publication mutation;
- then establish the current execution's remote-lineage state truthfully.

For a fresh current generation with no published predecessor, `None` is a valid
remote predecessor. Do not fabricate ancestry from the previous generation.

### Failure 4: status projection contradicted the executable plan

After one-shot state normalization, status reported simultaneously:

```text
execution:
  phase: agent_pending
  stage: null
  state: recovery-required

plan:
  action: run-worker
  bound: true
  reason: resume persisted bound execution
```

The material workspace proof had already become REUSABLE, and the service was
running and ready. The `recovery-required` label came from the presentation layer
falling back to that label whenever a non-idle persisted execution lacked current
process-local live-ownership evidence.

Lack of current process ownership immediately after restart is not itself proof that
operator recovery is required.

## Required behavior

### Keep recovery authority reachable

- An UNSAFE bound `agent_pending` execution must not launch a worker.
- The persistent service should remain alive in a blocked/recovery-required state
  when the repository/runtime authority itself is still healthy.
- While blocked for this condition, read-only status remains available and the exact
  public operator `recover` command remains admissible.
- The service must not depend on a one-second startup race or repeated start attempts
  to receive the recovery command.
- If there is a class of state where keeping the service alive is genuinely unsafe,
  provide an explicit bounded offline recovery path guarded by the same runtime lock
  and identity proofs; do not require manual SQLite edits.

### Normalize pre-worker identity after successful explicit recovery

A successful explicit UNSAFE recovery must leave one coherent current
`agent_pending` generation.

At minimum:

- keep the exact current ticket and execution ID;
- keep the exact admitted `execution_base_head` and control binding;
- keep the canonical execution branch/path identity;
- ensure no worker identity or durable worker result is discarded;
- clear or replace stale execution-stage metadata that belongs only to a previous
  `agent_running` generation;
- `execution_start_head` must not survive merely because it was present in stale
  state; it is established again only when the current execution actually enters
  `agent_running`;
- stale interruption/lifecycle metadata must not make the repaired pre-worker
  generation look post-worker;
- after the mutation, the same shared inspection used by planning must classify the
  workspace REUSABLE before scheduler continuation.

Do not solve this by globally discarding unknown state fields. Normalize only fields
whose lifecycle ownership is understood and test the state invariant explicitly.

### Reconcile conventional remote execution-branch lineage

When the conventional local or remote work branch represents an older execution
generation of the same ticket:

- preserve the old checkpoint under an immutable/durable product evidence ref bound
  to the old execution/checkpoint identity before changing the conventional ref;
- distinguish old-generation remote lineage from the current execution's
  `execution_remote_head`;
- freshly observe the remote conventional ref before mutation;
- retire/move/recreate it only with exact expected-head lease/CAS semantics;
- if the remote ref drifts, fail closed and preserve both current state and old
  evidence;
- after reconciliation, the current execution records the actual predecessor for its
  own generation: exact current-generation predecessor if one exists, otherwise
  `None`;
- later checkpoint publication must therefore use the normal lease/ancestry rules
  against the current generation, not against an old rejected checkpoint.

Do not use unconditional force push/delete and do not infer ownership from branch
name alone.

### Continue exactly once after recovery

Once explicit recovery has completed and the reconstructed workspace is REUSABLE:

- the normal owner/scheduler path resumes the persisted bound execution;
- the current execution retains its existing execution ID;
- transition to `agent_running/worker-launch` establishes the new
  `execution_start_head` from the actual reusable workspace;
- at most one worker is launched;
- restart/crash between recovery effect and worker launch remains replay-safe;
- a repeated identical operator request/receipt must not perform a second destructive
  recovery effect or create a second execution generation.

### Make status and plan semantically coherent

Status projection must distinguish material recovery requirement from temporary lack
of process-local execution ownership.

For one stable snapshot:

- REUSABLE bound `agent_pending` plus executable bound plan must not be labeled
  `recovery-required` solely because a restarted service has not yet claimed the
  execution;
- RECOVERABLE may be presented as recovering/recovery-pending according to actual
  owner behavior;
- UNSAFE requiring operator authorization is `recovery-required`;
- once the live owner claims the bound execution, normal preparing/starting/running/
  finalizing projections continue as today.

The exact user-facing state name for a REUSABLE but not-yet-claimed persisted
execution may be `preparing`, `pending`, or another coherent term, but it must not
claim that operator recovery is required when `plan.action == run-worker`.

Status text and JSON/YAML must derive from the same semantics.

## Acceptance criteria

- The real LAB-153-shaped stale-generation scenario can be constructed from durable
  state without manual SQLite edits.
- The service starts and remains alive while that scenario is UNSAFE and exposes the
  supported exact operator recovery route.
- No worker launches before exact operator authorization.
- Exact recovery preserves the prior rejected checkpoint under durable product
  evidence before conventional local/remote ref reuse.
- Wrong ticket, wrong execution, stale observed local HEAD, or drifted remote
  conventional ref fail closed without destroying material.
- Successful recovery leaves a coherent `agent_pending` state with no stale
  lifecycle/start-head metadata from the previous generation.
- The current execution's remote predecessor is truthful and does not silently point
  at an old execution generation.
- Shared inspection returns REUSABLE after recovery.
- The service then launches exactly one worker under the original current execution
  ID.
- Worker admission establishes `execution_start_head` from the reconstructed
  current-generation workspace.
- Crash/restart after recovery but before worker launch neither replays destructive
  ref mutation nor creates a second execution generation.
- A stable REUSABLE bound execution cannot simultaneously report
  `plan.action=run-worker` and `execution.state=recovery-required` merely due to
  missing process-local live ownership.
- No manual runtime-database edit is required for the supported product path.
- Full tests, Ruff, coverage, and relevant real-service/subprocess recovery tests are
  green.

## Required regressions

### Reachable blocked-service recovery

- Persist an exact `agent_pending` UNSAFE workspace with no worker/result and start
  the real service.
- Prove the service remains running/blocked rather than exiting.
- Submit the public CLI/IPC `recover` command after the ordinary startup window has
  elapsed and prove it is still accepted.
- Prove scheduler/worker admission remains closed until the recovery effect succeeds.

### LAB-153 stale-generation continuation

Construct one previous execution and one current execution of the same ticket:

- previous execution has a durable receipt/checkpoint on the conventional work
  branch;
- current execution is admitted at a later unrelated/descendant product generation;
- persisted current state contains the historically observed stale
  `agent_pending + lifecycle/start-head` metadata;
- local and remote conventional execution refs initially represent the previous
  generation.

Then prove end to end:

1. status/plan classify the current workspace as non-runnable before recovery;
2. exact operator recovery preserves previous-generation evidence first;
3. local and remote conventional refs are reconciled only under exact observed
   identities;
4. current persisted pre-worker state is normalized;
5. plan becomes an executable bound resume;
6. exactly one worker launches with the same current execution ID.

### State-normalization replay

- Crash after evidence preservation, after local ref reconstruction, after remote ref
  reconciliation, and after persisted-state normalization.
- On restart, either complete the remaining exact effect or report a precise blocked
  state; never repeat an unsafe mutation or lose evidence.
- Re-submitting an already admitted/replayed recovery identity is idempotent with
  respect to material Git state.

### Remote ref drift

- Change the remote conventional work branch between observation and effect.
- Exact lease/CAS must reject the mutation.
- The unexpected remote material remains untouched.
- Old-generation evidence already preserved remains valid but does not authorize the
  drifted mutation.

### Status/plan agreement

- REUSABLE bound `agent_pending`, running service, no current live execution owner:
  plan remains `run-worker` and execution projection is not
  `recovery-required`.
- UNSAFE bound `agent_pending`: plan blocked and execution projection
  `recovery-required`.
- Once the same execution is claimed by the service, normal preparing/starting/
  running projection is preserved.


## Review continuation after execution 828f0f5b3b4e429e9794bb930703903f

Checkpoint `59e3d223e7210ac6f1418db4ea4b3d13b6b7b0e5` moves the
implementation in the intended direction, but TASK-031 is not ready for acceptance.

Confirmed good work:

- a persistent UNSAFE `agent_pending` state can now keep the service in a blocked
  owner loop rather than forcing the IPC recovery route to depend on startup timing;
- explicit pre-worker recovery clears stale `execution_stage`,
  `execution_start_head`, interruption, and resume metadata without changing the
  current ticket/execution binding;
- status projection no longer reports a reusable bound pending execution as
  `recovery-required` merely because the restarted process has not yet claimed it;
- remote conventional-branch reconciliation now freshly observes the remote,
  preserves prior evidence, and uses `--force-with-lease` rather than an
  unconditional mutation;
- exact-head CI is green: `1021 passed, 1 skipped`, Ruff clean.

Two blocking issues remain.

### 1. Remote reconciliation records the wrong current-generation predecessor

The new explicit-recovery path moves the conventional remote branch to the current
admitted base:

```python
git push   --force-with-lease=refs/heads/<branch>:<old-remote-head>   <remote>   <base_head>:refs/heads/<branch>
```

but after doing that it persists:

```python
execution_remote_head=None
```

Those two facts are inconsistent.

Normal checkpoint publication later calls:

```python
_publish_execution_branch(
    workspace,
    checkpoint.after_head,
    self._state.get("execution_remote_head"),
)
```

and that function first requires:

```python
observed_remote_head == expected_remote_head
```

After this recovery, however:

```text
observed remote conventional branch = base_head
expected execution_remote_head      = None
```

so the recovered execution will fail at checkpoint publication with:

```text
execution branch remote changed during worker execution
```

before it can complete the very continuation TASK-031 exists to guarantee.

Choose one coherent current-generation model and test it end to end:

- either preserve old evidence, lease-move the conventional remote branch to the
  current `base_head`, and persist `execution_remote_head=base_head`; then normal
  publication uses that exact current-generation predecessor;
- or preserve old evidence and lease-delete/retire the conventional remote branch,
  keeping `execution_remote_head=None`; then the first current-generation checkpoint
  creates the branch from no predecessor.

Do not leave a real remote predecessor at `base_head` while persisting `None`.

Add a regression that performs explicit recovery, launches the same execution ID,
creates a worker checkpoint, and successfully publishes it through the normal
`_publish_execution_branch()` path.

### 2. The TASK-031 required regressions were not added

This execution changes only:

- `src/devlegate/runtime.py`
- `src/devlegate/cli.py`

No maintained tests were added or modified.

The existing TASK-019-era tests cover local workspace classification/repair and
identity-bound recover admission, but they do not prove the new TASK-031 continuation
contract. In particular they do not cover the remote-predecessor bug above.

Add focused regressions for the explicit requirements in this ticket, at minimum:

- **reachable blocked-service recovery:** real service remains alive beyond the
  startup admission window, no worker launches, public CLI/IPC recover is still
  accepted;
- **LAB-153 stale-generation continuation:** prior receipt/checkpoint plus stale local
  and remote conventional refs, stale pending lifecycle/start-head metadata, exact
  recovery, normalized state, REUSABLE plan, same execution ID, exactly one worker,
  successful checkpoint publication;
- **remote drift:** mutate the conventional remote after observation and prove the
  lease/CAS fails without touching unexpected material while preserved evidence
  remains valid;
- **replay/crash boundaries:** recovery repeated after evidence preservation, local
  reconstruction, remote reconciliation, and persisted-state normalization is
  idempotent/fail-closed rather than duplicating destructive effects;
- **status/plan agreement:** REUSABLE bound pending + `run-worker` is not
  `recovery-required`; UNSAFE remains `recovery-required`; claimed execution
  keeps normal preparing/running projection.

The full LAB-153-shaped test must pass through the normal worker/checkpoint/publication
path, not stop after `manager.inspect(...)=REUSABLE`.

Do not redesign the recovery model. Keep the service-liveness, targeted
pre-worker-state normalization, lease/CAS, evidence preservation, and status changes.
Fix the current-generation remote predecessor invariant, add the missing end-to-end
regressions, and rerun exact-head tests, coverage, and Ruff.


## Review continuation after execution 45dfb5e86de343be9372951070b8aa71

Checkpoint `36ca734dda3e8e4e44aae42c8e41c17fb5ec698a` fixes the
previous current-generation remote predecessor bug and adds a useful end-to-end
happy-path regression, but TASK-031 is still incomplete.

Confirmed:

- after explicit recovery, the persisted `execution_remote_head` now matches the
  actually observed reconciled conventional remote branch;
- the new regression carries the same current execution ID through recovery, worker
  admission, checkpointing, and successful normal remote publication;
- stale `execution_stage` / `execution_start_head` metadata is cleared on the
  successful path;
- exact-head CI is green: `1022 passed, 1 skipped`, Ruff clean.

A crash/replay blocker remains.

### 1. Recovery is not replay-safe after local branch reconstruction

The explicit recovery effect currently performs these durable mutations in order:

1. pin operator evidence for the authorized old local branch HEAD;
2. preserve/reconcile the old remote generation;
3. remove the old registered worktree;
4. CAS the conventional local branch from `observed_head` to `base_head`;
5. recreate/inspect the worktree at `base_head`;
6. only then normalize persisted runtime state with
   `clear_pre_worker_generation=True`.

The operator evidence ref remains bound to the original `observed_head`:

```text
refs/devlegate/recovery/operator/<ticket>/<execution> = old_head
```

If the process crashes after step 4 or 5 but before step 6, durable state still says
the execution is the old UNSAFE pre-worker generation, while the conventional local
branch now points at `base_head`.

On restart, the exact public recover path cannot resume:

- retrying with `observed_head=old_head` fails because the conventional local branch
  is already `base_head`;
- retrying with `observed_head=base_head` reaches
  `_recover_unsafe_bound_execution_workspace()`, where the existing operator
  evidence is still `old_head`, and fails with
  `operator recovery evidence has conflicting identity`.

That leaves the execution requiring recovery but with no admissible recovery request.
This violates the required crash/restart replay semantics.

Make the recovery transaction restartable across the durable effect boundaries.
Do not weaken the original authorization proof or overwrite old evidence. A replay
must be able to recognize that the exact previously authorized recovery has already
completed some prefix of its effects and continue the remaining exact suffix.

Add deterministic fault-injection regressions at least after:

- old-generation evidence preservation;
- remote conventional-branch reconciliation;
- local branch CAS/reconstruction;
- persisted-state normalization.

For each boundary, restart from durable state and prove the exact same execution can
either finish the remaining recovery suffix or return a precise safe terminal state.
No duplicate destructive ref mutation, evidence loss, new execution ID, or manual
SQLite edit is acceptable.

### 2. Remaining TASK-031 regressions are still missing

The new test closes the LAB-153 happy-path continuation through checkpoint
publication. Keep it.

Still add the explicit required proofs that are not covered by this checkpoint:

- **reachable blocked-service recovery:** start the real service in an UNSAFE bound
  `agent_pending` state, wait beyond the former startup race window, prove the
  daemon remains alive and no worker launches, then submit the public CLI/IPC
  `recover` command successfully;
- **remote ref drift:** change the conventional remote branch after observation but
  before the leased mutation and prove recovery fails closed, unexpected remote
  material remains untouched, and already-preserved old-generation evidence remains
  valid;
- **recovery replay/idempotence:** repeat the same authorized recovery across the
  crash points above and prove material Git state is not destructively replayed;
- **status/plan agreement:** retain explicit maintained coverage that REUSABLE bound
  pending + `run-worker` is not `recovery-required`, while UNSAFE remains
  `recovery-required`.

Do not redesign the successful remote-predecessor fix or the normal publication
path. The remaining work is to make the explicit recovery effect transactional/
replay-safe and prove the required operator-facing paths.


## Review continuation after execution 10a75b35bdac40c89c7921da21ff3ab4

Checkpoint `2684ef3b132af0228ec1306a331144b163a1f7cb` closes the
previous replay-dead-end and appears semantically correct, but TASK-031 is still
missing required maintained proof.

Confirmed:

- explicit recovery now persists the exact authorized operator-recovery identity;
- replay recognizes an already-completed durable prefix without weakening the
  original observed-head authorization;
- already-reconciled remote and already-moved local conventional refs are not
  destructively repeated;
- a crash after local reconstruction can restart, preserve the same execution ID,
  finish normalization, clear the recovery intent, and return to a bound
  `run-worker` plan;
- the prior current-generation remote predecessor fix remains intact;
- exact-head CI is green: `1023 passed, 1 skipped`, Ruff clean.

No new recovery-model redesign is requested.

The remaining blocker is the explicit regression contract in TASK-031.

### 1. Cover all durable recovery prefixes, not only local reconstruction

The ticket requires deterministic crash/restart coverage after:

- old-generation evidence preservation;
- remote conventional-branch reconciliation;
- local branch CAS/reconstruction;
- persisted-state normalization.

This checkpoint adds only the local-reconstruction case.

Add fault-injection tests for the remaining boundaries and prove on restart that the
same authorized recovery either completes the exact remaining suffix or is already
coherently complete. In every case assert:

- the original operator evidence remains immutable and attributable;
- old-generation evidence is not lost or rebound;
- remote/local ref mutations are not repeated destructively;
- the execution ID is unchanged;
- no second execution generation is created;
- no manual runtime-store edit is required.

A parameterized recovery-prefix test is appropriate if it makes the invariant
clearer.

### 2. Add the required real-service blocked recovery regression

Construct an UNSAFE bound `agent_pending` execution and start the real persistent
service.

Prove end to end that:

- the service remains alive in blocked/recovery-required state beyond the former
  startup admission window;
- no worker is launched before operator authorization;
- status remains reachable;
- the public CLI/IPC `recover <ticket> <execution> --observed-head <sha>` command is
  accepted after that delay;
- the admitted recovery continues through the normal owner path.

This is the user-visible failure mode that motivated TASK-031 and must not remain
covered only by internal method tests.

### 3. Add the required remote-drift lease regression

Inject a change to the conventional remote execution branch after the recovery path
observes its old-generation head but before the leased reconciliation mutation.

Prove:

- the exact lease/CAS rejects the recovery mutation;
- the unexpected remote material is untouched;
- old-generation evidence already preserved remains valid;
- the current execution does not record the drifted remote as its predecessor;
- a later operator retry still requires a fresh, exact observation rather than
  silently accepting the drift.

### 4. Keep explicit status/plan agreement coverage

Ensure maintained tests directly prove both sides of the TASK-031 projection
invariant:

- REUSABLE bound `agent_pending` + `plan.action=run-worker` is not rendered as
  `recovery-required`;
- UNSAFE bound `agent_pending` remains `recovery-required`.

Existing projection tests may be extended/reused if they already exercise the exact
runtime semantics; do not add redundant presentation-only tests merely for count.

After adding these proofs, rerun the exact-head full suite, coverage, and Ruff.


## Review continuation after execution 4d6961a36b8e4c0380d0a57194dba4ea

Checkpoint `5118ee3316fc0a57c6c7ca72b5423e9df0717e22` adds the intended
proof-oriented hardening without redesigning the recovery implementation, but
TASK-031 is not ready for acceptance yet.

Confirmed:

- production recovery code was not redesigned in this execution;
- maintained regressions were added for evidence-prefix replay, remote drift, and
  real-service recovery reachability;
- prior happy-path coverage still carries the same execution ID through explicit
  recovery, worker admission, checkpointing, and normal remote publication;
- the previous local-reconstruction replay regression remains in place.

The remaining work is limited to proof corrections and completion of the explicit
TASK-031 regression contract.

### 1. The new remote-prefix replay test crashes before remote reconciliation

The parameterized
`test_operator_recovery_replays_each_durable_prefix(..., boundary="remote")`
installs two fault paths.

One raises after a successful `push --force-with-lease`, which would model the
required crash after the remote reconciliation effect.

However, the test also wraps `_execution_remote_head()` and raises on its second
observation. In the recovery sequence that second observation occurs before the
leased `--force-with-lease` mutation. The test therefore exits before the durable
remote-ref reconciliation effect happens, so it does not prove restartability after
that boundary.

Adjust the fault injection so the remote case crashes only after the successful
leased remote mutation has returned. On restart prove that the already-reconciled
remote ref is recognized as an already-completed exact prefix, is not destructively
mutated again, and the remaining recovery suffix completes under the same execution
identity.

Do not change the recovery model unless this corrected regression exposes a real
defect.

### 2. Complete the real-service blocked-recovery proof

The new real-service regression correctly proves that:

- a persistent UNSAFE `agent_pending` service remains alive beyond the former
  startup race window;
- status remains reachable;
- the public CLI/IPC `recover` request is accepted after that delay.

But the public recovery acknowledgement proves owner-side admission, not completion
of the recovery effect. Extend the maintained real-service regression far enough to
observe the admitted recovery being processed by the service owner and the same
execution returning to the normal continuation path.

Reuse existing happy-path worker/checkpoint/publication coverage rather than building
a redundant second full scenario if possible. The maintained service-level proof
must nevertheless show that the admitted public request does not merely receive an
ACK while leaving the service permanently blocked.

### 3. Complete the remote-drift assertions

The new remote-drift regression correctly injects remote movement after observation
and before the leased mutation, and proves the unexpected remote material and the
original operator evidence remain intact.

Also assert the two remaining required invariants explicitly:

- the drifted remote HEAD is not recorded as the current execution's
  `execution_remote_head` / predecessor;
- a later recovery attempt cannot silently reuse the stale authorization to accept
  the drifted generation; it requires a fresh exact observation/authorization.

Do not weaken the lease/CAS or observed-head identity checks.

### 4. Make status/plan agreement an exact maintained assertion

Keep explicit coverage for both sides of the projection invariant, preferably
against the machine-readable status semantics:

- REUSABLE bound `agent_pending` with `plan.action == "run-worker"` must not
  report `execution.state == "recovery-required"` merely because no process-local
  live owner has claimed it yet;
- UNSAFE bound `agent_pending` must have a blocked plan and
  `execution.state == "recovery-required"`.

Avoid assertions such as
`"recovery-required" in output or "unsafe" in output`, because a plan reason can
contain `unsafe` while the execution projection itself is wrong.

After these proof corrections, rerun the exact-head full suite, coverage, and Ruff.
If they remain green and no corrected regression exposes a new functional defect,
no further recovery redesign is requested.
