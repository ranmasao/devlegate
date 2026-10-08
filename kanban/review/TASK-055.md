---
"type": "devlegate.ticket"
"title": "Establish cgroup v2 execution ownership boundary"
---

## Milestone

Execution ownership and lifecycle hardening.

## Goal

Replace process-group and /proc-descendant inference as the execution ownership
proof with the smallest kernel-enforced cgroup v2 containment layer needed by
Devlegate today.

This ticket is deliberately preparatory: it establishes exact recursive process
ownership and retirement semantics only. It does not implement the future
general sandbox, filesystem isolation, network isolation, warm executor pool, or
resource-governance architecture.

## Incident

A real Devlegate service restart on 2026-10-06 reported orderly shutdown while
systemd subsequently found and SIGKILLed Python descendants still alive in the
service cgroup.

The existing worker process-group model is insufficient because a descendant can
leave the worker PGID/session with setsid()/setpgid()/double-fork while remaining
owned by the execution.

A later TASK-055 implementation attempted to compensate by sampling /proc
ancestry. Review established that polling cannot prove recursive ownership:
a descendant may fork, detach, and be reparented between samples. That
implementation must not be used as the retirement proof.

## Architectural decision

For Linux execution ownership:

    worker PID / PGID
        != execution ownership boundary

    sampled /proc ancestry
        != execution ownership boundary

    cgroup v2 subtree
        = execution ownership boundary

Devlegate may retain PID/PGID/start-time/boot-id information for diagnostics,
leader signaling, provenance, and stale-identity protection, but successful
execution retirement must be proven from the cgroup boundary.

The /proc descendant tracker introduced by the earlier TASK-055 attempt is not
required as an ownership mechanism and should be removed unless a narrowly
justified diagnostic use remains.

## Platform requirement

The Linux execution runtime may require:

- a unified cgroup v2 hierarchy;
- a writable/delegated subtree in which Devlegate is permitted to create and
  manage execution child cgroups.

Do not make systemd itself part of the execution ownership contract.

Systemd may be the current host-supervisor mechanism that grants delegation on
systemd hosts, but the containment implementation must be expressed against
cgroup v2 core semantics so that OpenRC/other supervisors or containers can
provide an equivalent delegated subtree later.

If the required cgroup v2 delegation is unavailable, worker execution must fail
closed with an explicit capability diagnostic rather than silently falling back
to PGID-only ownership.

## Minimal containment model

Establish an abstraction equivalent to:

    ExecutionContainment
        create(execution identity)
        spawn/join before untrusted exec
        request graceful termination
        force terminate boundary
        prove empty/retired
        destroy boundary

The public/internal names are implementation-defined.

This abstraction must not expose systemd-specific semantics to the worker
supervisor.

## Join-before-exec invariant

Do not use:

    Popen(worker)
    move worker PID into cgroup

as the correctness boundary, because the worker could fork before migration.

No untrusted worker code may execute before the process belongs to its execution
cgroup.

Use a race-free mechanism appropriate to the supported runtime. A small trusted
launcher that joins the execution cgroup and then execve()s the worker is an
acceptable design. A kernel primitive with equivalent guarantees is also
acceptable.

The proof must cover that all descendants inherit the execution cgroup even if
they later call setsid(), change process groups, double-fork, or are reparented.

## Retirement proof

Use cgroup v2 core state as the authoritative retirement proof.

At minimum, successful retirement requires the execution cgroup subtree to be
proven unpopulated, using the kernel-maintained cgroup.events/populated state or
an equivalent cgroup v2 primitive.

Do not infer emptiness from:

- worker leader exit;
- process-group disappearance;
- sampled /proc ancestry;
- absence of currently known descendant PIDs.

Orderly service stop must not report completion while an execution cgroup remains
populated.

## Forced termination

Preserve the existing lifecycle semantics:

1. request cooperative/graceful stop where applicable;
2. terminate the exact execution ownership boundary;
3. wait boundedly;
4. escalate if required;
5. prove the execution cgroup is empty;
6. only then report retirement.

If cgroup.kill is available and appropriate, it may be used for recursive forced
termination.

Do not make cgroup.kill itself a mandatory platform requirement in this ticket
unless investigation proves there is no comparably correct fallback for the
supported minimum kernel. The required invariant is recursive containment and
provable retirement, not one particular convenience file.

Do not kill unrelated processes outside the execution cgroup.

## Current systemd integration

For the currently supported systemd service path:

- arrange for the Devlegate service to receive the cgroup v2 delegation needed
  to create execution child cgroups;
- do not create one transient systemd scope per execution merely to avoid
  defining cgroup semantics in Devlegate;
- systemd remains the outer service supervisor and final service-level safety
  boundary;
- Devlegate owns the per-execution cgroup lifecycle inside its delegated subtree.

The exact unit property changes must be tested and documented.

## Non-systemd preparation

This ticket does not need to implement full OpenRC integration.

However, the implementation must make clear that non-systemd support requires
only an equivalent delegated cgroup v2 root, not a different execution ownership
model.

Keep host-supervisor provisioning separate from containment semantics so a later
OpenRC/container/attached provider can supply the same capability.

## Capability inspection

Introduce a deterministic capability probe usable by host/runtime code.

It should distinguish at least:

- cgroup v2 unified hierarchy unavailable;
- cgroup v2 present but no usable delegated subtree;
- usable execution containment available;
- optional recursive-kill primitive availability if tracked separately.

Do not detect support from distro name or /etc/os-release.

## Relationship to future isolation

This cgroup layer is the first reusable execution-substrate primitive for future
work, but this ticket intentionally stops at ownership.

Future work may build on it for:

- pids.max / memory.max / cpu.max resource policy;
- filesystem/mount isolation;
- restricted process/tool execution;
- network policy;
- low-trust agents;
- warm executor generations and fast reset.

Do not implement those features here.

The design principle for future work is:

    cgroup ownership != full sandbox

## Acceptance criteria

- Every Linux worker execution runs inside its own execution-specific cgroup v2
  subtree.
- No untrusted worker instruction executes before joining that boundary.
- A child that calls setsid() remains inside the same execution cgroup.
- A child that double-forks/reparents remains inside the same execution cgroup.
- Worker leader/PGID exit cannot produce a successful retirement result while the
  execution cgroup remains populated.
- Forced interruption retires all processes in the execution boundary and does
  not touch unrelated service processes.
- Successful retirement is proved by cgroup state, not /proc ancestry.
- The earlier sampling descendant tracker is removed from correctness-critical
  ownership logic.
- Missing/unusable cgroup v2 delegation fails closed before worker admission.
- Current systemd-host installation/runtime provides the required delegation.
- Containment core is not coupled to systemd APIs and has a documented path for
  future non-systemd provisioning.
- Existing stop/restart/retry/process-loss behavior remains green.
- Full authoritative CI and Ruff are green.

## Required regressions

At minimum:

1. execution cgroup is created before worker exec;
2. worker observes itself inside the expected execution cgroup before running
   workload code;
3. setsid() descendant remains in the execution cgroup;
4. immediate parent exit plus reparented child remains in the execution cgroup;
5. double-fork descendant remains in the execution cgroup;
6. worker leader and original PGID can disappear while cgroup populated remains
   true;
7. such a state is not reported as retired;
8. graceful completion reaches populated=0 before retirement succeeds;
9. forced interruption removes all execution-owned descendants;
10. unrelated sibling execution/service processes survive;
11. cgroup delegation unavailable => worker admission fails closed;
12. cgroup v1-only/hybrid-without-usable-v2 => explicit unsupported capability
    result;
13. PID reuse does not affect cgroup ownership proof;
14. systemd service installation exposes the required delegated subtree;
15. containment tests do not require distro-specific package-manager behavior.

## Cleanup of previous attempt

The current TASK-055 work branch contains /proc descendant tracking introduced
before the cgroup-v2 decision.

When updating/rebasing this ticket:

- do not preserve that tracker as the retirement authority;
- retain only genuinely useful tests/documentation that still apply;
- avoid layering cgroup containment on top of a second competing descendant
  ownership model.

## Non-goals

This ticket does not:

- implement a general container runtime;
- add mount/user/pid/network namespaces;
- implement filesystem sandboxing;
- introduce seccomp/Landlock policy;
- add CPU/memory/pid resource limits;
- implement warm executor pooling;
- implement complete OpenRC service installation;
- require Docker/Podman/runc/containerd;
- add compatibility/migration layers.
