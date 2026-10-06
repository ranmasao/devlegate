---
"type": "devlegate.ticket"
"title": "Prevent execution descendants from escaping cleanup"
---

## Milestone

Execution ownership and lifecycle hardening.

## Goal

Audit and harden execution process ownership so subprocesses created by worker
workloads cannot survive execution completion merely by creating a new process
group/session while remaining inside the Devlegate service cgroup.

The immediate incident occurred during a real service restart: Devlegate logged
`orderly stop complete`, but systemd then found and SIGKILLed three Python
processes that were still members of the service cgroup, and one remained long
enough to be reported as a left-over process during the next start.

## Incident evidence

Observed on 2026-10-06 for
`devlegate-2f443efe.service`:

```text
lifecycle stop accepted through devlegate stop
orderly stop complete
Killing process 81215 (python3) with signal SIGKILL.
Killing process 81471 (python3) with signal SIGKILL.
Killing process 233021 (python) with signal SIGKILL.
Unit process 81471 (python3) remains running after unit stopped.
Unit process 81215 (python3) remains running after unit stopped.
Found left-over process 81471 (python3) in control group while starting unit.
```

Relevant unit properties:

```text
KillMode=mixed
KillSignal=15
FinalKillSignal=9
SendSIGKILL=yes
TimeoutStopUSec=infinity
```

A second Devlegate project service stopped cleanly moments later without any
left-over processes, so this is not merely the generic systemd stop path.

Current worker execution already uses `start_new_session=True` and tracks the
worker process group. Tests and production-topology helpers can themselves
launch subprocesses with `start_new_session=True`, which may create descendants
outside the worker PGID while still remaining inside the service cgroup.

## Required behavior

- Audit every production/test subprocess launch reachable from worker execution
  that can create a new session/process group or otherwise escape the tracked
  worker PGID.
- Distinguish:
  - the worker leader/process group;
  - descendants still owned by the execution;
  - unrelated processes that happen to exist in the service.
- Reproduce the escape condition with a deterministic regression: a worker
  descendant creates a new session/process group and remains alive after the
  worker leader/group retires.
- Prove whether current `worker_group_retired` can become true while such an
  execution-owned descendant still exists.
- Define the smallest correct ownership boundary for execution retirement.
- Do not rely solely on process-group membership if that cannot recursively
  contain descendants.
- If the current architecture can safely enumerate/prove descendant ownership,
  implement that bounded fix and corresponding teardown/proof.
- If reliable recursive ownership requires an execution-specific cgroup/scope,
  document the finding and split implementation into a focused follow-up ticket
  instead of introducing a broad systemd/cgroup redesign here.
- Preserve exact-worker forced-stop semantics from existing lifecycle work.
- Do not weaken fail-closed behavior: Devlegate must not declare execution
  retirement proven while known execution-owned processes may still be alive.
- Avoid killing unrelated host processes based only on stale PID/PGID identity.

## Orderly service stop semantics

Audit the meaning of `orderly stop complete`.

If that message currently means only that the main daemon is ready to exit,
make the lifecycle contract and tests explicit. If Devlegate claims all owned
execution processes are retired before orderly completion, enforce and prove
that stronger invariant.

The service may continue to use systemd as a final containment/safety boundary,
but normal orderly stop should not silently depend on systemd SIGKILL to clean
up processes Devlegate still owns.

## Acceptance criteria

- A deterministic regression reproduces a descendant escaping the worker PGID.
- The audit identifies concrete launch paths capable of the escape.
- The ticket establishes whether the 2026-10-06 incident is explained by the
  current ownership model.
- Execution completion/retirement cannot be reported as proven while a known
  execution-owned escaped descendant remains alive, or a follow-up cgroup
  implementation ticket is created with exact evidence showing why current
  primitives cannot provide that guarantee safely.
- Existing worker interruption, forced-stop, retry/recovery, service lifecycle,
  and process-loss tests remain green.
- No solution broadens process killing to unrelated processes.
- Full authoritative CI and Ruff are green.

## Required regressions / evidence

- worker descendant calls `setsid()` / equivalent and leaves the original
  worker process group;
- worker leader and original PGID exit while descendant remains alive;
- current/fixed retirement behavior is asserted explicitly;
- forced interruption still terminates the exact execution ownership boundary;
- stale PID/PGID reuse remains fail closed;
- orderly service stop does not report a stronger cleanup guarantee than the
  implementation actually proves;
- process cleanup leaves no execution-owned descendants in the chosen ownership
  boundary after successful retirement.

## Scope

This ticket is an ownership/reproduction/hardening audit first.

It does not redesign the whole service manager, introduce containers, or add a
general sandbox. An execution-specific systemd scope/cgroup is a valid
follow-up direction if the audit demonstrates that PGID-based ownership is
fundamentally insufficient.
