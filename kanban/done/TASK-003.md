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

The first TASK-003 implementation correctly sanitized worker host-control state, but
review exposed a separate readiness regression already present in its product base.
`_run_attached_target` currently passes the externally required
`readiness_report` to `run_service` only when `startup_fd is not None`.
That condition is wrong: `DEVLEGATE_STARTUP_FD` belongs to the internally hosted
background launcher, while an externally systemd-hosted service normally has no
startup fd and must still send `READY=1` through `NOTIFY_SOCKET`.

The regression is observable in dogfooding: the project service remained in
systemd `activating` state while its main process continued running, then systemd
eventually failed the start with `Result: timeout`. The systemd readiness path must
therefore be proved independently of the internal startup-fd path before this ticket
can be accepted.

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
- External systemd readiness must not depend on `DEVLEGATE_STARTUP_FD` or any
  internal-host startup pipe. When external hosting requires notification,
  `readiness_report` must reach the service host and send `READY=1`.
- Internal startup-fd reporting and external systemd readiness remain separate
  mechanisms with separate ownership and tests.

## Acceptance criteria

- A worker launched from a systemd-hosted service does not receive the service's
  notify socket or Devlegate external-host ownership variables.
- Nested Devlegate invocations from the worker environment do not attempt to notify
  the parent service solely because the parent is systemd-hosted.
- The existing externally hosted service still performs its own readiness
  notification successfully when no startup fd exists.
- `Type=notify` with `NotifyAccess=main` can reach active/running state from the
  main Devlegate process rather than remaining activating until systemd timeout.
- Existing worker execution, stop/restart, and lifecycle behavior remains intact.
- Full tests and lint remain green.

## Required regressions

- Given a parent environment containing systemd notify and Devlegate host-control
  variables, when the worker environment is constructed, then those values are
  absent while unrelated environment values remain available.
- Given a nested Devlegate invocation from that sanitized worker environment, when
  it determines hosting mode/readiness behavior, then it does not inherit external
  service authority.
- Given an external host with `DEVLEGATE_REQUIRE_NOTIFY=1`,
  `DEVLEGATE_HOST_MODE=external`, a valid `NOTIFY_SOCKET`, and no
  `DEVLEGATE_STARTUP_FD`, when the attached service becomes ready, then the main
  process invokes the systemd readiness reporter exactly once.
- Given an internal background host with a startup fd, startup-pipe readiness
  continues to work without granting systemd notify authority to worker children.
- Given the managed real-service path, systemd readiness is covered strongly enough
  that gating it on `startup_fd` would fail the test suite.
