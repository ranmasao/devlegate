---
"type": "devlegate.ticket"
"title": "Isolate worker environment from service host state"
---

## Milestone

Devlegate 0.5.5 self-hosting runtime hardening.

## Goal

Prevent service-hosting and systemd notification state from crossing the worker
process boundary.

## Context

During the first systemd-hosted self-execution, the journal repeatedly reported:

```text
Got notification message from PID ..., but reception only permitted for main PID
```

The systemd unit intentionally uses `Type=notify` and `NotifyAccess=main`.
However, `WorkerSupervisor` currently starts OpenCode from an
`os.environ.copy()`, so the worker process tree inherits service-owned values such
as `NOTIFY_SOCKET`, `DEVLEGATE_HOST_MODE=external`, and
`DEVLEGATE_REQUIRE_NOTIFY=1`.

Nested Devlegate processes started by worker tests can therefore misidentify
themselves as the externally hosted service and attempt readiness notification.

Do not solve this by widening `NotifyAccess`; child processes must not gain service
readiness authority.

## Required behavior

- The environment passed across the worker boundary must not expose service-host
  ownership or transient lifecycle authority.
- At minimum, worker processes must not inherit `NOTIFY_SOCKET`,
  `DEVLEGATE_HOST_MODE`, `DEVLEGATE_REQUIRE_NOTIFY`,
  `DEVLEGATE_STARTUP_FD`, or Devlegate restart-authority/request variables.
- systemd watchdog/notify state must likewise not leak into workers if present.
- Project/user environment required for normal worker execution remains available;
  sanitization must be explicit and scoped to host-control state rather than
  replacing the environment arbitrarily.
- `NotifyAccess=main` remains the containment policy for the managed systemd unit.
- A Devlegate process launched inside a worker/test environment must not mistake
  inherited host state for its own hosting authority.

## Acceptance criteria

- A worker launched from a systemd-hosted service does not receive the service's
  notify socket or Devlegate external-host ownership variables.
- Nested Devlegate invocations from the worker environment do not attempt to notify
  the parent service solely because the parent is systemd-hosted.
- The existing externally hosted service still performs its own readiness
  notification successfully.
- Existing worker execution, stop/restart, and lifecycle behavior remains intact.
- Full tests and lint remain green.

## Required regressions

- Given a parent environment containing systemd notify and Devlegate host-control
  variables, when the worker environment is constructed, then those values are
  absent while unrelated environment values remain available.
- Given a nested Devlegate invocation from that sanitized worker environment, when
  it determines hosting mode/readiness behavior, then it does not inherit external
  service authority.
- Given the real service main process, when systemd readiness is required, then the
  main process still has the required notify state and can report READY.
