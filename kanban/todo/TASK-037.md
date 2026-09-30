---
"type": "devlegate.ticket"
"title": "Fix stop lifecycle semantics and add forced shutdown"
"depends_on": ["TASK-036"]
---

## Milestone

Devlegate 0.5.6 lifecycle and operator UX.

## Goal

Make `devlegate stop` correctly represent checkpoint-aware graceful shutdown,
remove meaningless structured-output switches from this operator action, and add an
explicit `--force` path for bounded interruption when the operator does not want to
wait for the current execution checkpoint.

## Context

The current top-level `stop` and `restart` parsers mechanically receive
`--json` and `--yaml` through the shared output helper.

For these lifecycle actions, the structured success payload currently carries no
new machine-useful result beyond the command exit status and a fixed acknowledgement
such as `stopped` or `restarted`. Machine-readable runtime state is already
available through the read/query surface (`status --json/--yaml`).

Do not generalize this rule to every mutation command: some mutations return
meaningful identities, idempotence outcomes, resolved paths, or other data that a
caller did not already supply. This ticket cleans only the two top-level lifecycle
actions whose structured output is redundant.

More importantly, systemd-hosted graceful stop currently combines incompatible
timeouts:

- the generated unit uses `TimeoutStopSec=infinity` because a checkpoint-aware
  shutdown may legitimately take as long as the active worker needs;
- `SystemdSupervisor._run()` applies a generic five-second subprocess timeout;
- `SystemdSupervisor.stop()` calls synchronous `systemctl --user stop ...`.

Stopping while a ticket is executing can therefore successfully put systemd into
`deactivating` and still make the CLI fail with:

```text
devlegate: systemd user manager did not respond
```

A repeated stop can then fail while merely observing the already valid transitional
state:

```text
devlegate: systemd command failed (... is-active ...): deactivating
```

The service already has checkpoint-aware lifecycle draining, worker ownership,
bounded worker process-group interruption, interruption provenance, and recoverable
execution lineage. Forced stop must compose with those mechanisms rather than act as
an untracked kill or implicit `drop`.

## Required behavior

### Graceful `stop`

- Plain `devlegate stop` remains checkpoint-aware.
- Once accepted, stop closes new worker admission and lets already owned work reach
  the existing safe lifecycle/checkpoint boundary.
- The CLI must not report a systemd timeout merely because graceful draining takes
  longer than a generic command timeout.
- Do not make synchronous `systemctl stop` plus an arbitrary short subprocess
  timeout the authority for whether a Devlegate lifecycle request succeeded.
- A systemd unit in `deactivating` after an accepted stop is a valid transitional
  state, not an operational failure.
- A repeated stop while the exact service is already draining/deactivating is
  idempotent and reports that stop is already in progress rather than failing.
- When active work means shutdown will continue asynchronously, return success once
  the stop intent is durably accepted and report concise state such as:
  `stop requested; will stop at checkpoint (1 worker active)`.
- If no active work remains and shutdown completes promptly, `service stopped`
  remains appropriate.
- `status` must continue to expose the accepted stop intent and active-worker count
  while the service drains.
- A genuinely unavailable systemd user manager, foreign/unproven unit identity, or
  failure to durably accept the lifecycle request remains an error.

### `stop --force`

Add:

```text
devlegate stop --force
```

with explicit immediate-shutdown semantics.

- Force closes worker admission immediately.
- If an owned worker/process group is active, interrupt and retire that exact owned
  process group using the existing bounded termination discipline, including a
  bounded escalation to SIGKILL when normal termination does not complete.
- Persist interruption/lifecycle evidence before treating the worker as retired.
- Preserve the execution workspace, execution branch, ticket identity, and durable
  provenance needed for safe recovery/resume.
- Forced stop is not `drop`, purge, ticket deletion, integration, or acceptance.
- Re-starting after a safely proven forced service shutdown must use the existing
  interrupted-execution recovery model rather than fabricate a clean execution.
- If force is requested while a graceful stop is already draining, it may
  deterministically escalate that same lifecycle intent; it must not create a
  conflicting second stop identity.
- Systemd hosting must operate only on the exact proven managed unit. `--force`
  must not become a blind kill-by-name fallback when ownership is ambiguous.
- The command should wait only for the bounded force-retirement/authority-release
  proof needed to know that forced shutdown actually completed.

### CLI surface

- Remove `--json` and `--yaml` from both top-level lifecycle actions:
  - `devlegate stop`;
  - `devlegate restart`.
- The structured payloads for these commands currently add no information beyond
  successful completion/acceptance already represented by exit status and concise
  operator text.
- `devlegate stop --json`, `devlegate stop --yaml`,
  `devlegate restart --json`, and `devlegate restart --yaml` must fail at
  argument parsing before any lifecycle side effect.
- Keep stop/restart output concise and human-readable.
- Preserve machine-readable lifecycle observation through `status --json/--yaml`;
  do not weaken or remove those query formats.
- Do not change structured output policy for unrelated commands in this ticket.

## Acceptance criteria

- Stopping a systemd-hosted service during a long-running worker no longer fails
  after five seconds while systemd is legitimately deactivating.
- Graceful stop returns a successful accepted/draining result without requiring the
  worker to finish inside an arbitrary CLI timeout.
- Repeating stop during draining is idempotent.
- `stop --force` retires the exact owned worker process group and service
  authority within bounded termination semantics.
- Forced interruption remains recoverable and does not discard the execution
  workspace or ticket lineage.
- No foreign/unproven process or systemd unit can be killed through either path.
- `stop` help exposes `--force` and no longer exposes `--json`/`--yaml`.
- `restart` help no longer exposes `--json`/`--yaml`.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Active worker + systemd graceful stop -> accepted draining, no five-second false
  timeout.
- `is-active` reporting `deactivating` for an already accepted stop -> valid
  stopping state, not `SystemdSupervisorError`.
- Repeated graceful stop -> same lifecycle, no duplicate intent.
- Idle graceful stop -> complete stop and authority release.
- `stop --force` with active worker -> owned group terminated, interruption
  persisted, service authority released, workspace retained.
- Force escalation while graceful drain is pending -> one coherent lifecycle.
- Worker ignores termination -> bounded escalation retires the owned group.
- Foreign/mismatched systemd authority -> force refuses to operate.
- `stop --json` and `stop --yaml` -> parser failure with no stop request sent.
- `restart --json` and `restart --yaml` -> parser failure with no restart
  request sent.


## Review findings

Execution `3ce09e1dbbf04cc2bdaa1e1f9a8bc5f6` / checkpoint
`3e482048cd26cfbdd28f1e384a9d4218cc59d4b8` requires a focused
lifecycle hardening pass.

The implementation direction is appropriate:

- top-level `stop` / `restart` no longer expose redundant structured-output
  options;
- `stop --force` is present and propagates force intent through IPC;
- force reuses the existing owned worker-group interruption path, including bounded
  escalation;
- systemd stop is submitted with `--no-block`;
- `deactivating` is treated as a valid systemd transitional state;
- graceful stop with an active worker can return an accepted/draining message
  instead of waiting for checkpoint completion.

Do not redesign WorkerSupervisor termination or invent a second kill mechanism.

### Production blocker: repeated/escalated stop waits on the wrong request ID

`ServiceEngine.request_lifecycle()` intentionally keeps the identity of the first
accepted lifecycle request when a same-intent stop is repeated or escalated:

```text
graceful request A accepted
stop --force request B arrives
-> lifecycle remains the same stop, request_id == A
```

That is the correct one-lifecycle model.

However, `_stop_runtime()` currently generates request B locally and later calls:

```text
_wait_for_service_stop(locator, B, instance_id)
```

even when the service acknowledgement reports the already-authoritative request A.
The completed lifecycle receipt is written for A, so direct/internal repeated stop
or graceful -> force escalation can shut down successfully and then fail waiting
for a receipt that can never match B.

Use the **acknowledged authoritative lifecycle request ID** for completion proof.
Validate its type/identity appropriately; do not silently invent a second lifecycle.
A repeated same-intent stop and a force escalation must converge on the first
accepted lifecycle identity.

Add regressions for at least:

- repeated graceful stop while draining -> same lifecycle ID, no failure;
- graceful stop A followed by `stop --force` request B -> force escalation succeeds,
  completion is proved through lifecycle A, no phantom receipt B is required;
- initial force stop still binds/waits on its own admitted lifecycle identity.

### Mutable IPC callback must not retry on TypeError

`dispatch_mutation()` currently catches `TypeError` from:

```python
lifecycle(method, request_id, force)
```

and, for non-force requests, invokes the callback again with the old two-argument
shape.

A `TypeError` may originate **inside** a valid three-argument callback after it has
already performed a side effect. Retrying the mutable callback can therefore
duplicate lifecycle effects.

Remove this runtime TypeError-based compatibility retry. Keep one explicit callback
contract and update internal tests/doubles to that signature. Programming errors
must propagate; they are not evidence of an old callback shape.

Add a regression proving an internal callback `TypeError` causes exactly one
callback invocation.

### Required lifecycle proofs are largely missing

The checkpoint changes five production modules but only adjusts six lines in
`tests/test_systemd_supervisor.py`. No new force-path, CLI-surface, IPC-force, or
recovery regressions were added.

Add/adjust focused tests to cover the ticket contract without duplicating existing
WorkerSupervisor primitives:

1. **Active worker + graceful stop**
   CLI returns success with accepted/draining text while the worker continues to its
   safe checkpoint; the service itself remains alive until that boundary.

2. **Systemd non-blocking stop**
   Prove the exact managed unit is submitted with
   `systemctl --user --no-block stop ...` and no five-second synchronous-stop
   timeout governs lifecycle success.

3. **Systemd `deactivating`**
   `is-active` returning `deactivating` is accepted as stopping state.

4. **Repeated graceful stop**
   Idempotent, same lifecycle intent/identity, no duplicate conflicting request.

5. **Force with active worker**
   Through the real host/engine boundary, force produces
   `operator_abort`, persists interruption state, retires the owned group, releases
   service authority, and leaves workspace/ticket/execution lineage recoverable.

6. **Force escalation**
   Graceful drain followed by force is one coherent lifecycle and exercises the
   authoritative-request-ID rule above.

7. **Uncooperative worker**
   Reuse the existing worker-process test harness to prove the force path reaches the
   bounded SIGKILL escalation and exact group retirement proof.

8. **Restart after forced stop**
   The retained execution is seen through existing interrupted-execution recovery;
   it is not silently treated as a clean/new execution and is not dropped.

9. **Foreign/unproven systemd authority**
   `stop --force` cannot become a kill-by-name fallback.

10. **CLI parser boundary**
    `stop --json`, `stop --yaml`, `restart --json`, and
    `restart --yaml` fail in argument parsing before any lifecycle request;
    `stop --force` appears in help.

Existing lower-level tests for SIGINT/SIGKILL and interruption metadata may be reused
where they already prove the primitive, but at least one end-to-end/live-service
test must prove that `stop --force` actually reaches that primitive and preserves
recovery state.

### Current CI is red

GitHub CI run `36683890730` reports:

```text
5 failed, 1046 passed, 1 skipped
```

Failures include stale/incorrect lifecycle expectations in:

- `tests/test_project_registry.py`;
- `tests/test_cli.py::test_real_service_graceful_lifecycle_waits_for_active_worker[stop]`;
- `tests/test_systemd_supervisor.py::test_two_project_units_and_operations_are_independent`.

Some failures are expected test-contract updates (for example the CLI should now
return after accepted draining); others expose incomplete test adaptation around the
new requirement that a managed stop first obtains durable service IPC acceptance.

Fix the regressions deliberately rather than weakening the new lifecycle contract.
Return to review with the full suite, coverage, and Ruff green.
