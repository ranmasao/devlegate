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
