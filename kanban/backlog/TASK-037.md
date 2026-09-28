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

The current top-level `stop` parser mechanically receives `--json` and
`--yaml` through the shared output helper. They only affect the success payload;
lifecycle errors are still emitted as ordinary stderr text, so the switches do not
provide a coherent machine protocol for this command.

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

- Remove `--json` and `--yaml` from the top-level `stop` command.
- `devlegate stop --json` and `devlegate stop --yaml` must fail at argument
  parsing before any lifecycle side effect.
- Keep stop output concise human-readable operator output.
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
