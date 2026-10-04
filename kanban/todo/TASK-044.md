---
"type": "devlegate.ticket"
"title": "Add forced retry across changed descendant ticket generations"
---

## Milestone

Devlegate 0.5.6 lifecycle and recovery UX.

## Goal

Add an explicit:

```text
devlegate retry <ticket-id> --force
```

path for a stranded execution whose worker is proven absent but whose TODO ticket
changed on a descendant control generation after the original execution was
admitted.

The operation must preserve recoverable worker progress and provenance, retire the
stale admission, and start a fresh execution against the current ticket generation.

## Context

Dogfooding exposed this exact sequence on TASK-018:

1. TASK-018 was admitted under control generation `2c78961...`.
2. The worker became unreachable/lost after a network failure.
3. The runtime remained at:

   ```text
   phase=agent_running
   stage=worker-running
   state=recovery-required
   ```

4. The control branch advanced normally as a descendant to `116de834...`.
5. The TASK-018 body changed in that descendant generation.
6. Plain `retry TASK-018` was accepted at the IPC surface, but owner-side recovery
   correctly rejected lifecycle replay with:

   ```text
   control descendant changed the active ticket; lifecycle replay refused
   ```

This fail-closed behavior is correct for ordinary retry: the old execution was bound
to an immutable admitted ticket body and must not be silently replayed as though the
ticket were unchanged.

A one-shot recovery demonstrated the desired forced semantics safely:

- prove the persisted worker is absent;
- prove the product generation is unchanged;
- prove current control is a descendant of admitted control;
- prove the same TODO ticket still exists but its body changed;
- checkpoint any retained workspace changes;
- pin old-execution recovery evidence;
- release the stale runtime execution binding without deleting the execution
  worktree/branch;
- fast-forward control to the current descendant;
- require an explicit retry;
- admit a fresh execution ID against the current ticket generation;
- reuse the preserved TASK-018 worktree with a RESUME directive.

The recovered execution successfully resumed the preserved workspace at checkpoint
`6717af1c4d284352cf55f8c1b67e4b3538f7278e` under a new execution ID.

## Required CLI behavior

Add:

```text
devlegate retry <ticket-id> --force
```

Plain:

```text
devlegate retry <ticket-id>
```

must keep its current conservative semantics and continue to reject changed ticket
identity/generation.

`--force` means:

> Retire a stranded old admission of this same ticket and retry the current ticket
> generation, preserving proven recoverable workspace progress.

It does NOT mean:

- kill an arbitrary live worker;
- ignore ambiguous worker ownership;
- bypass divergent control history;
- discard the old workspace;
- reuse stale ticket text;
- force product-base reconciliation;
- drop/purge an execution;
- bypass ticket dependency or scheduler policy.

## Admission requirements

`retry --force` is allowed only when all of the following can be proven:

1. Runtime phase is an execution state eligible for stranded-worker recovery.
2. The requested ticket exactly matches the persisted active execution ticket.
3. The old execution ID is known and valid.
4. Persisted worker ownership is proven `absent`.
   - `matching-live` MUST reject.
   - `indeterminate` MUST reject.
5. The product checkout and remote product generation are still compatible with the
   stranded execution.
   - For the initial implementation, require exact unchanged product generation.
   - Do not silently combine this operation with product-base reconciliation.
6. Current control history is a descendant of the execution admission control head.
   - Divergence/rewrite MUST reject.
   - `reconcile control` remains the separate mechanism for rewritten control
     history.
7. The same ticket ID exists in current `todo`.
8. The current ticket is runnable under normal dependency/scheduler rules.
9. The current ticket body differs from the admitted body.
   - If it is unchanged, ordinary retry/recovery should be used instead.
10. The retained execution branch/worktree has the exact canonical topology expected
    for that ticket and is safe to preserve.
11. Submodule/dependency integrity can be proven before checkpointing or reuse.

The operation must fail closed before mutation if any proof is missing.

## Forced retry semantics

The owner-side operation should perform one coherent transition:

```text
old execution E1 / old ticket generation T1
        |
        | prove worker absent
        | prove product compatible
        | prove control T1 -> T2 is descendant
        | prove same current TODO ticket T2
        |
        | preserve retained workspace changes
        | checkpoint/pin recovery evidence for E1
        | mark E1 superseded/interrupted
        | release stale execution runtime binding
        | adopt current control generation
        |
        +--> fresh execution E2
             current ticket body T2
             preserved workspace/checkpoint
             RESUME directive
```

The new execution MUST have a fresh execution ID. Do not mutate the old execution
record into the new one.

## Provenance

Before releasing the old runtime binding:

- checkpoint retained worker filesystem changes using the normal Devlegate checkpoint
  discipline;
- create durable evidence identifying at least:
  - old execution ID;
  - ticket ID;
  - old admitted control head;
  - current descendant control head;
  - product/base identity;
  - preserved checkpoint HEAD;
  - reason: forced retry after lost worker and changed descendant ticket generation;
- preserve an immutable Git evidence ref for the old execution/checkpoint.

A suitable namespace is:

```text
refs/devlegate/recovery/force-retry/<ticket>/<old-execution>
```

or an equivalent versioned provenance model.

Do not delete the execution worktree or branch merely to clear runtime state.

## Workspace reuse

The fresh execution should reuse the existing canonical per-ticket worktree when it
is provably safe.

The resumed worker must receive:

```text
WorkDirective.RESUME
```

and the assignment must come from the CURRENT ticket body, not the persisted old
body.

The new execution's `execution_start_head` must be the preserved checkpoint HEAD,
and publication leases must remain coherent with the actual execution branch remote
predecessor.

If safe workspace reuse cannot be proven, fail closed and preserve evidence for
manual handling.

## Synchronous operator semantics

The current IPC behavior can print:

```text
retry accepted: TASK-018
```

before owner-side deterministic admission later rejects the request.

For `retry --force`, success must not be reported merely because the IPC request was
queued.

Before the CLI reports successful admission, the mutation owner must have completed
all deterministic precondition checks required to authorize the forced retry.

A successful user-visible acknowledgement should therefore mean at least:

```text
forced retry admitted
old execution proven stranded
current ticket generation proven eligible
recovery transition durably accepted
```

Execution of the new worker may still happen asynchronously after admission, but a
known-invalid request must not first print a success acknowledgement and only later
appear as rejected in the daemon log.

Consider applying the same admission/acknowledgement clarity to ordinary `retry`
where practical, but do not broaden this ticket into a redesign of all IPC command
completion semantics unless required.

## Safety constraints

- Never signal or kill a worker through `retry --force` unless a separate explicit
  lifecycle operation has already proven and retired it. For this ticket,
  `retry --force` requires worker ownership to be `absent`.
- Never act on process name matching.
- Never treat `indeterminate` ownership as absent.
- Never accept divergent/re-written control history.
- Never overwrite or silently discard old execution provenance.
- Never replace the current ticket body with the old admitted body.
- Never turn this into `drop`.
- Never automatically integrate preserved worker changes into product history.
- Never weaken existing product-generation or workspace integrity checks.

## Acceptance criteria

- CLI help exposes `retry [ticket-id] --force`.
- Plain retry remains unchanged and fail-closed for changed active ticket identity.
- A stranded worker-running execution with proven-absent worker and changed same
  ticket on a descendant control generation can be force-retried.
- The old execution receives durable interruption/supersession evidence.
- Retained workspace changes are checkpointed before stale runtime binding is
  released.
- The old execution ID is never reused as the fresh execution ID.
- Current control/ticket generation is used for the fresh admission.
- The retained canonical worktree is reused safely and the worker gets RESUME.
- matching-live or indeterminate worker ownership rejects with no mutation.
- divergent control history rejects with no mutation.
- changed product generation rejects and directs the operator to the normal
  reconciliation path.
- unsafe/dirty/ambiguous workspace topology rejects while preserving evidence.
- a successful CLI acknowledgement is emitted only after deterministic forced-retry
  admission succeeds.
- full tests, coverage, and Ruff remain green.

## Required regressions

1. **Dogfood reproduction**
   - admit ticket T at control C1;
   - enter `agent_running/worker-running`;
   - make the exact worker disappear;
   - advance control C1 -> C2 normally;
   - modify the same TODO ticket body at C2;
   - ordinary retry rejects;
   - `retry T --force` succeeds;
   - E1 is preserved as old provenance;
   - E2 is fresh and bound to C2/current body;
   - preserved workspace changes are present for E2;
   - worker prompt uses RESUME and the current ticket body.

2. **Live worker**
   - exact persisted worker is still matching-live;
   - force retry rejects;
   - no signal is sent;
   - no state/control/workspace mutation occurs.

3. **Indeterminate ownership**
   - worker observation is indeterminate;
   - force retry rejects with no mutation.

4. **Control divergence**
   - current control is not a descendant of admitted control;
   - force retry rejects and points to explicit control reconciliation.

5. **Unchanged ticket**
   - control advances but the active ticket body is unchanged;
   - force retry rejects or delegates to ordinary retry without creating a special
     supersession generation.

6. **Product movement**
   - product generation changed since admission;
   - force retry rejects and does not implicitly perform update-base reconciliation.

7. **Workspace preservation**
   - retained worker filesystem changes exist;
   - force retry checkpoints them before clearing stale runtime binding;
   - evidence ref points to that exact checkpoint;
   - fresh execution starts from that checkpoint.

8. **Workspace integrity failure**
   - unexpected branch/path/submodule/HEAD state;
   - force retry fails closed;
   - old runtime/provenance evidence remains inspectable.

9. **Fresh identity**
   - E2 != E1;
   - current ticket/control binding belongs to E2;
   - E1 cannot later be replayed as the active execution.

10. **Acknowledgement semantics**
    - a deterministic forced-retry precondition failure is returned synchronously to
      the CLI;
    - CLI does not print `retry accepted` for a request already known to be invalid.

## Non-goals

- Forced restart; tracked separately by TASK-043.
- Product-base reconciliation.
- Rewritten control-history adoption.
- Dropping/purging execution artifacts.
- Generic arbitrary state repair.
- Killing a still-live worker.


### First review: forced-retry transition is internally inconsistent, has no required regressions, and CI is red

Execution `a6ccf0569a5049c69de2dfd38ee3e2c4` / checkpoint
`be713661c9b81b512f20868a574a2a97f30c3ba8` establishes useful CLI/IPC/runtime
plumbing, but it cannot be accepted.

The checkpoint changes only:

- `src/devlegate/cli.py`;
- `src/devlegate/ipc_server.py`;
- `src/devlegate/runtime.py`.

It adds **no tests at all**, despite this ticket defining ten mandatory regression
classes.

More importantly, the current forced transition is semantically inconsistent and
cannot complete the target dogfood path as written.

#### Blocker 1: forced admission creates a state that ordinary retry immediately classifies as non-retryable

`_record_operator_admission()` performs:

```python
if command.method == "retry" and command.force:
    self._force_retry_transition(command.ticket_id)
```

before the request receipt/admission is returned.

`_force_retry_transition()` then stores:

```python
failed_executions[ticket_id] = {
    ...
    "control_head": old_control,
    "current_control_head": current_control,
    "todo_fingerprint": current_todo_fingerprint,
    "interrupted": True,
    ...
}
```

and clears the active execution to `idle`.

After admission, owner dispatch calls:

```python
_retry_owned(ticket_id, ..., force=True)
    -> _retry_locked(ticket_id, ..., force=True)
    -> _retry_candidates()
```

But the ordinary failed-execution evaluator explicitly requires:

```python
metadata["control_head"] == control.local_head
```

otherwise it marks the candidate non-retryable with:

```text
control generation changed since the failure
```

For the exact forced-retry case this ticket exists to handle:

```text
old_control C1 != current descendant C2
```

Therefore the failure record created by `_force_retry_transition()` is, by
construction, not an ordinary retry candidate at C2.

The result is an invalid split boundary:

```text
force preconditions succeed
-> old execution is checkpointed/retired
-> durable state becomes idle
-> CLI admission can be acknowledged
-> dispatch tries ordinary retry candidate selection
-> candidate is rejected because control_head=C1 != current=C2
```

This violates the ticket's central requirement that a successful
`forced retry admitted` acknowledgement means the deterministic transition required
for the fresh retry is actually authorized.

Do not solve this by weakening ordinary failed-execution validation globally.

Forced retry needs its own coherent post-transition authorization identity, or the
transition must persist a current-generation retry authorization that the next step
can prove without pretending the old failure occurred at C2.

At minimum preserve both facts distinctly:

- old execution E1 was admitted at C1;
- fresh retry E2 is authorized against current C2/current ticket fingerprint.

The old provenance must continue to say C1. The new authorization must explicitly
say C2. Do not overwrite provenance to make ordinary retry validation pass.

#### Blocker 2: admission/receipt crash boundary is not durable or replayable

The forced transition mutates durable runtime state *before* the mutable request
receipt is recorded:

```text
_force_retry_transition()
  -> checkpoint workspace
  -> create evidence ref
  -> clear active execution / save idle state
then
_record_operator_admission()
  -> record mutable receipt
```

A crash after the first save but before receipt persistence leaves:

- old execution already retired;
- phase `idle`;
- force-retry provenance/evidence present;
- no durable accepted request receipt proving that the command was admitted.

A repeated `retry --force` cannot simply restart admission because force validation
requires the old `agent_running/worker-running` state, which has already been
cleared.

Define a write-ahead/idempotent forced-retry disposition so every crash boundary is
recoverable and a duplicate request ID/fingerprint can deterministically observe or
complete the same transition.

The required proof should cover crashes:

1. before checkpoint;
2. after checkpoint but before evidence ref;
3. after evidence ref but before state retirement;
4. after retirement but before receipt/admission acknowledgement;
5. after acknowledgement but before E2 launch;
6. after E2 durable binding but before worker start.

No boundary may strand the operator with an admitted-looking command that cannot be
continued, or require rewriting/removing old provenance manually.

#### Blocker 3: no fresh remote control observation is proven before force admission

`_validate_force_retry_admission()` compares the admitted control generation to:

```python
git rev-parse HEAD
```

of the existing control worktree.

It does not freshly fetch/re-observe the control remote in this path.

The ticket's target case is specifically an execution stranded while the canonical
control branch advances externally. Forced admission must prove the CURRENT canonical
ticket generation, not merely whichever descendant happens to be checked out
locally.

Reuse the established fresh-control observation/synchronization discipline. Do not
silently mutate to an unseen remote descendant, but ensure the force admission
decision is based on a fresh canonical remote observation and a proven safe local
relationship.

Add a regression where local control is still C1 while remote has advanced C1 -> C2
with the changed ticket; `retry --force` must not incorrectly conclude that the
ticket is unchanged merely because local control was stale.

#### Blocker 4: CI is red and existing retry compatibility is broken

GitHub Actions run `37118118876` fails:

```text
7 failed, 1096 passed, 1 skipped, 7 warnings
```

The failures/warnings are direct consequences of changing the existing retry
interfaces without updating their contracts/test doubles. Examples include:

```text
submit_retry(..., force=...)
TypeError: submit() got an unexpected keyword argument 'force'
```

and:

```text
_retry_owned(ticket, stop_event, force)
TypeError: existing retry test double takes 1-2 positional arguments
```

This also breaks ordinary public retry tests such as:

- `test_retry_submission_runs_on_service_owner_thread`;
- `test_read_only_ipc_remains_available_while_owner_retry_is_active`;
- `test_retry_request_receipt_coalesces_duplicates_and_survives_restart`;
- both interrupted-retry end-to-end cases;
- `test_retry_uses_daemon_authority_and_never_constructs_cli_engine`;
- `test_retry_interactive_candidates_are_rendered_and_selected_locally`.

Plain retry compatibility is an explicit acceptance requirement. Restore the full
existing suite and keep ordinary retry semantics unchanged.

#### Blocker 5: all TASK-044 regressions are absent

Add the ticket's required proof matrix rather than relying on production-code review.

At minimum provide:

1. **Exact dogfood end-to-end**
   - E1 worker-running at C1;
   - worker proven absent;
   - remote/current control advances C1 -> C2;
   - same TODO ticket body changes;
   - plain retry rejects;
   - public `retry T --force` synchronously admits;
   - E1 evidence is preserved at C1;
   - E2 is fresh, bound to C2/current body;
   - retained workspace starts E2 with RESUME.

2. **Live and indeterminate worker**
   - both reject before mutation;
   - no signal/kill;
   - no checkpoint/evidence/state/control mutation.

3. **Control topology**
   - descendant changed ticket succeeds;
   - unchanged ticket routes to/requires ordinary retry;
   - divergent/re-written control rejects;
   - stale-local/fresh-remote descendant is handled deterministically.

4. **Product movement**
   - reject; do not implicitly reconcile product.

5. **Workspace safety**
   - dirty retained filesystem progress is checkpointed;
   - evidence ref points exactly to preserved checkpoint;
   - bad branch/path/registration/HEAD/submodule state fails closed.

6. **Fresh identity and prompt**
   - E2 != E1;
   - E2 `execution_start_head` is the preserved checkpoint;
   - prompt/directive is RESUME;
   - prompt contains CURRENT ticket body, not C1 body.

7. **Synchronous acknowledgement**
   - deterministic rejection reaches CLI as failure, never
     `forced retry admitted`;
   - successful acknowledgement occurs only after durable forced-retry admission is
     replayable.

8. **Crash/idempotency boundaries**
   - inject crashes around every durable transition listed above;
   - restart/reissue converges to one E2 and one preserved E1 provenance chain.

9. **Ordinary retry compatibility**
   - all pre-existing retry/receipt/interactive/IPC tests remain green.

10. **Full repository validation**
    - standard coverage+xdist CI green;
    - Ruff green.

### Additional implementation notes

The current use of a distinct `forced_retry` authorization kind and unconditional
RESUME directive is directionally reasonable.

The evidence namespace:

```text
refs/devlegate/recovery/force-retry/<ticket>/<old-execution>
```

is also appropriate.

Keep those ideas if useful, but separate these concepts explicitly:

```text
old execution provenance (E1/C1)
fresh current-generation authorization (T/C2)
durable transition/disposition identity
fresh execution identity E2
```

Do not represent all four by overloading an ordinary `failed_executions` record.

Return to review only when the actual public forced-retry dogfood scenario runs
end-to-end, crash recovery is deterministic, ordinary retry remains compatible, and
full CI/Ruff are green.
