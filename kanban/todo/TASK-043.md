---
"type": "devlegate.ticket"
"title": "Add forced restart for stuck active workers"
"depends_on": ["TASK-037"]
---

## Milestone

Devlegate 0.5.6 lifecycle and operator UX.

## Goal

Add:

```text
devlegate restart --force
```

as the explicit bounded-recovery path for restarting a service when an active
worker cannot reach the normal checkpoint boundary.

The command must compose the already-proven forced-stop semantics with the existing
restart/recovery model. It must not introduce a second worker-kill mechanism or
silently discard execution lineage.

## Context

Dogfooding exposed the missing operator path after a network loss left an active
worker unable to make progress.

Plain:

```text
devlegate restart
```

correctly entered checkpoint-aware draining. Under managed systemd,
`TimeoutStopSec=infinity` then left the restart job waiting indefinitely for the
worker to reach its safe boundary.

The installed CLI already supported:

```text
devlegate stop --force
```

but the still-running older service instance rejected the force payload. The
operator therefore had to retire the exact managed unit externally with:

```text
systemctl --user kill --kill-who=all --signal=SIGKILL <unit>
```

Because a systemd restart job was already pending, killing the old service completed
the stop phase and systemd immediately started the replacement service. Recovery
then resumed the retained execution workspace and created a new execution attempt.

This demonstrated the desired high-level behavior, but only through an external
systemd intervention. Devlegate needs a first-class operator command for it.

## Required behavior

### CLI surface

Add:

```text
devlegate restart --force
```

- Plain `devlegate restart` remains checkpoint-aware and unchanged.
- `restart --force` explicitly means that the operator does not want to wait for
  the active worker to reach the graceful checkpoint boundary.
- Keep restart output concise and human-readable.
- Do not reintroduce `--json` or `--yaml` to the top-level restart action.

### Forced restart semantics

`restart --force` must be a composition of existing proven mechanisms:

```text
force-retire current owned worker
-> persist interruption/lifecycle evidence
-> preserve execution/workspace/branch lineage
-> release old service authority
-> establish replacement service authority
-> recover through the existing interrupted-execution model
```

In particular:

- close new worker admission before retirement;
- interrupt only the exact owned worker/process group using the existing
  WorkerSupervisor bounded termination discipline;
- preserve the same interruption provenance used by `stop --force`;
- preserve the execution workspace, execution branch, ticket identity, checkpoint
  lineage, and recovery evidence;
- do not treat forced restart as `drop`, purge, acceptance, integration, or a clean
  execution completion;
- do not fabricate a fresh execution lineage merely because the service process was
  replaced;
- after replacement startup, recover the retained interrupted execution through the
  normal recovery path.

### Lifecycle identity and escalation

A graceful restart already in progress must be safely promotable to forced restart.

For:

```text
restart request A -> draining
restart --force request B
```

the system must preserve one coherent authoritative lifecycle operation rather than
inventing a conflicting second restart.

If the original lifecycle request identity remains authoritative, force escalation
and completion proof must bind to that identity, following the same invariant
already established for graceful-stop -> `stop --force`.

### Managed systemd hosting

For an externally/systemd-hosted service:

- operate only on the exact proven managed unit;
- do not use blind kill-by-name or process-name matching;
- do not rely on the operator manually invoking `systemctl kill`;
- the replacement start must be part of the same proven restart operation;
- `Restart=no` remains valid: this feature is an explicit lifecycle restart, not a
  crash restart policy;
- a force restart must not leave an indefinitely pending systemd stop/restart job.

### Version-skew behavior

A new CLI talking to an older running service may encounter a daemon that does not
understand the forced-restart payload.

- Fail closed with a clear version/protocol incompatibility error.
- Do not silently fall back to an untracked systemd kill.
- Preserve existing protocol/version diagnostics so the operator can determine that
  the installed CLI and running daemon differ.

## Acceptance criteria

- `devlegate restart --force` is exposed in CLI help.
- Plain restart remains graceful/checkpoint-aware.
- Active-worker forced restart retires the exact owned process group through the
  existing force-stop machinery.
- Interruption evidence is durable before the old authority is considered retired.
- Execution/workspace/branch/checkpoint lineage survives the restart.
- Replacement service authority is established and the interrupted execution is
  recovered through the existing recovery model.
- Graceful restart followed by force escalation preserves one coherent lifecycle
  identity and completes without waiting for a phantom request receipt.
- Managed-systemd force restart does not require an external `systemctl kill`.
- Foreign or unproven systemd authority cannot be killed or restarted through this
  path.
- Older-daemon protocol mismatch fails closed and does not trigger a fallback kill.
- Full tests, coverage, and Ruff remain green.

## Required regressions

1. CLI parser:
   - `restart --force` is accepted;
   - `restart --json` and `restart --yaml` remain rejected.

2. Active worker, live service:
   - start a real service with a deliberately long-running owned worker;
   - invoke the real `devlegate restart --force` surface;
   - prove exact worker-group retirement, old authority release, replacement
     authority startup, and successful command completion.

3. Recovery:
   - prove durable state records the forced interruption;
   - prove the same ticket/execution/workspace/branch lineage is retained;
   - prove replacement startup enters the existing interrupted/recoverable path
     rather than treating the execution as clean, dropped, or unrelated.

4. Escalation:
   - graceful restart A enters draining;
   - `restart --force` request B promotes the same lifecycle;
   - completion is proved through the authoritative lifecycle identity rather than
     a phantom second receipt.

5. Uncooperative worker:
   - reuse the existing WorkerSupervisor process-group harness to prove bounded
     termination escalation is reachable from the public forced-restart path.

6. Managed systemd:
   - exact managed unit only;
   - no blind process-name fallback;
   - explicit restart succeeds with `Restart=no`;
   - no external `systemctl kill` is necessary.

7. Version skew:
   - newer CLI + older daemon that rejects the force payload fails closed with a
     clear incompatibility diagnostic and performs no external kill fallback.
