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


## Review findings — cgroup-v2 implementation pass

Checkpoint:

```text
43e656c1de7a45c708f324d9cb23cab09672dba7
```

The architectural direction is now correct: per-execution cgroup v2 ownership,
`cgroup.events/populated` retirement proof, systemd delegation only as host
provisioning, and removal of sampled /proc ancestry from the intended proof
boundary.

The implementation is not acceptable yet.

### 1. Do not use Python preexec_fn as the join-before-exec boundary

`ExecutionContainment.spawn()` currently uses:

```python
subprocess.Popen(..., preexec_fn=join)
```

where `join()` executes Python code and `Path.write_text()` after fork and
before exec.

Devlegate is a multi-threaded daemon. Python's preexec_fn path is unsafe in a
multi-threaded parent because the child may inherit locks held by threads that
do not exist after fork and deadlock before exec. The join-before-exec invariant
must not be implemented by running arbitrary Python runtime/file-object code in
that window.

Use a small trusted launcher executable/process with a deliberately minimal
contract, or another race-free spawn primitive whose safety can be proved for the
supported runtime. The worker must still enter the execution cgroup before any
untrusted worker instruction executes.

Add a production-topology regression proving repeated concurrent worker starts
cannot wedge in the containment join path.

### 2. Forced-termination fallback is not recursive

When `cgroup.kill` is unavailable, `force_terminate()` loops over:

```text
<execution>/cgroup.procs
```

That file lists only processes directly attached to that cgroup, not processes
inside descendant cgroups.

At the same time, `cgroup.events populated` is recursive. Therefore an
execution can remain populated through a child cgroup while the fallback sees an
empty parent `cgroup.procs`, returns, and leaves the execution unretired.

Either:

- make `cgroup.kill` a deliberate minimum capability requirement; or
- implement and prove a correct recursive fallback across the execution cgroup
  subtree, including concurrent forks/migrations to the extent permitted by the
  ownership model.

Do not claim recursive forced termination from the current parent-only
`cgroup.procs` loop.

Add a regression with a process in a nested child cgroup.

### 3. Capability probe must prove join capability, not merely open cgroup.procs

The current probe creates a child cgroup and opens `cgroup.procs` for append,
but does not prove that an execution process can actually be moved into that
cgroup.

The admitted capability is specifically "Devlegate can create an execution
boundary and join a child before exec". Probe that capability with a disposable
trusted child/helper without moving the daemon itself. Fail closed if the join
cannot be completed and observed.

### 4. Remove stale ownership documentation/tests from the previous /proc design

`docs/EXECUTION_OWNERSHIP.md` in this checkpoint still describes the sampled
descendant tracker as best-effort cleanup and says a future cgroup/scope
follow-up is required. That directly contradicts the current TASK-055
architecture and implementation.

The diff also carries regressions written for the discarded tracker semantics,
including assertions about "known execution-owned descendant". Reconcile or
replace them so the test/documentation surface has one ownership model:
execution cgroup v2.

### Required next pass

- Replace `preexec_fn` containment join with a safe trusted join-before-exec
  mechanism.
- Make forced termination genuinely recursive, or explicitly require
  `cgroup.kill` as a minimum capability and justify the resulting kernel
  requirement.
- Strengthen the capability probe to demonstrate actual child cgroup membership.
- Remove stale /proc-tracker documentation and obsolete regressions.
- Preserve `cgroup.events/populated` as the authoritative retirement proof.
- Run full authoritative CI and Ruff after the corrected implementation.


## Review findings — checkpoint 6d24616d81e0

The previous four architectural findings are substantially addressed:

- Python `preexec_fn` was replaced by a separate trusted launcher;
- `cgroup.kill` is now an explicit minimum capability instead of pretending
  parent `cgroup.procs` is recursive;
- the capability probe performs an actual disposable child join;
- ownership documentation was rewritten around cgroup v2.

The implementation is still not acceptable.

Authoritative CI run `37739867403` failed:

```text
73 failed, 1065 passed, 1 skipped
```

Ruff did not run because the test step failed.

### 1. Production cgroup requirements must not make unrelated semantic tests depend on CI-host delegation

The implementation now unconditionally calls:

```python
ExecutionContainment.create(execution_id)
```

for every worker run.

GitHub Actions does not provide a writable delegated execution subtree, so large
parts of the suite now fail at worker admission with:

```text
execution containment unavailable: no-delegated-subtree
```

This invalidates semantic, egress, recovery, integration, and lifecycle tests
whose subject is not host cgroup provisioning.

Do not weaken production fail-closed behavior. Instead make execution
containment an explicit injectable runtime dependency/capability boundary.

Tests below production topology should be able to use a deterministic fake/test
containment provider with the same create/spawn/retire contract.

Keep a smaller set of Linux production-topology tests for the real cgroup v2
provider, and skip them explicitly when the host cannot supply the required
delegation. The authoritative suite must still prove the production provider on
a suitable environment; ordinary semantic tests must not silently fall back to
PGID ownership.

### 2. Recovery semantics were accidentally coupled to removed PGID helpers

CI also fails recovery tests because `_worker_group_exists` was removed while
tests and recovery behavior still depend on the old observer contract.

Examples include:

- `test_startup_reconciles_absent_worker_as_process_loss`;
- PID reuse observer regressions;
- leader-gone/group-survives regressions.

Do not simply restore PGID as the ownership authority.

Define the post-TASK-055 durable recovery contract explicitly:

- live current execution with a known cgroup boundary;
- retired/absent execution boundary;
- stale durable worker identity after daemon/process loss;
- containment state unavailable/indeterminate.

The recovery path must use cgroup ownership evidence where that evidence exists,
while retaining PID/start-time/boot-id only for leader identity/provenance.
Update obsolete PGID-specific tests to the new contract instead of deleting
coverage mechanically.

### 3. The trusted Python launcher must be immune to worker-controlled Python startup hooks

The launcher currently starts as:

```text
sys.executable execution_launcher.py ...
```

with the same environment passed toward the worker.

Before `execution_launcher.main()` writes itself into `cgroup.procs`, Python
startup can process environment/user-site mechanisms such as `PYTHONPATH`,
`sitecustomize`, or `usercustomize`. If execution-controlled input can affect
those mechanisms, arbitrary code could run before the cgroup join, violating the
join-before-untrusted-exec invariant.

Make the launcher bootstrap itself independently trustworthy. For example,
invoke a suitably isolated Python startup mode and prove that worker-controlled
Python path/site hooks cannot execute before membership is established, or use a
smaller non-Python helper.

Add a regression that supplies hostile startup-hook environment/input and proves
the hook cannot execute before the launcher observes itself inside the execution
cgroup.

Also prove the launcher mechanism works from the standalone/scie runtime, not
only from a source checkout where `execution_launcher.py` is an ordinary file.

### 4. Add explicit containment-provider test boundaries

The 73 failures show that containment is now a fundamental execution dependency,
but the code/tests still treat it as an implicit global host property.

Introduce the smallest provider seam necessary so that:

```text
worker supervisor
    -> ExecutionContainment provider contract
        -> Linux cgroup-v2 production provider
        -> deterministic test provider
```

This is not a plugin system and not optional production containment. It is
dependency injection for a mandatory capability.

Do not allow the test provider into normal production selection.

### Required next pass

- Keep production worker admission fail-closed without usable cgroup v2
  containment.
- Introduce an explicit containment-provider seam for tests/runtime composition.
- Restore the full non-host-specific suite using deterministic containment
  doubles rather than requiring GitHub runner delegation.
- Recast process-loss/recovery tests around cgroup ownership semantics.
- Harden launcher startup so no worker-controlled Python startup hook can run
  before cgroup join.
- Prove launcher operation in the standalone/scie execution environment.
- Rerun full authoritative CI and Ruff.


## Review findings — checkpoint a50dcf82b4b1

The implementation moved in the intended direction, but it is not acceptable
yet.

Authoritative CI run `37741714643` fails during test collection before any
tests execute:

```text
ImportError: cannot import name 'default_containment_provider'
from 'devlegate.execution_containment'
```

### 1. Complete the provider composition API

`worker_supervisor.py` imports and calls `default_containment_provider()`,
and `tests/conftest.py` monkeypatches that symbol, but
`execution_containment.py` does not define it.

Define the production composition point explicitly and keep its semantics
unambiguous:

- normal production composition selects `LinuxCgroupContainmentProvider`;
- failure to create/use that provider remains fail closed;
- tests may replace the composition point with
  `DeterministicContainmentProvider`;
- there must be no environment-driven production fallback to the deterministic
  provider.

Then run the full suite so the provider seam is exercised rather than merely
importable.

### 2. Prove the hardened launcher in the standalone/scie runtime

The source implementation now invokes the launcher interpreter with `-I -S`,
which is the correct direction for suppressing worker-controlled Python startup
hooks before the cgroup join.

However, this checkpoint does not add the previously required proof that the
launcher mechanism works from the standalone/scie runtime.

`_launcher_interpreter()` assumes that when running under PEX/scie,
`sys._base_executable` identifies a usable embedded interpreter and that
`Path(__file__).with_name("execution_launcher.py")` is available as an
ordinary executable source path. That must be demonstrated against the actual
standalone artifact rather than inferred from a source checkout.

Add a focused standalone proof that:

- executes the real packaged Devlegate/scie runtime;
- resolves the trusted launcher mechanism exactly as production does;
- joins a disposable execution cgroup before worker code;
- successfully execs the worker;
- does not depend on source-tree files outside the packaged artifact.

If the scie packaging model does not materialize `execution_launcher.py` as a
normal path, redesign the launcher packaging/entry mechanism rather than
special-casing source checkout behavior.

### 3. Add the hostile Python-startup regression requested in the previous pass

The use of `-I -S` should be backed by a regression.

Supply hostile `PYTHONPATH` / `sitecustomize` / user-site input that would
create an observable marker if Python startup processed it before launcher
`main()`. Prove that the marker is not created before cgroup membership is
established.

The test should exercise the production launcher command construction, not only
assert that the strings `-I` and `-S` are present.

### 4. Re-run recovery semantics after collection is fixed

This checkpoint adds durable `containment_path` and makes
`observe_worker_identity()` consult `cgroup.events` when that path is
available, which is the intended direction.

Because CI did not get past import collection, none of the recovery/process-loss
regressions actually ran. Do not consider that finding closed until the full
suite proves:

- populated persisted execution cgroup => matching/live ownership;
- missing retired cgroup => absent/process-loss as appropriate;
- unreadable/malformed cgroup state => indeterminate/fail closed;
- legacy PID/PGID identity remains diagnostic/provenance only and does not
  override cgroup ownership evidence.

### Required next pass

- Define and test `default_containment_provider()`.
- Get the full semantic suite collecting and running with the deterministic test
  provider.
- Add the hostile Python startup-hook regression.
- Add real standalone/scie launcher proof.
- Run the full authoritative CI and Ruff.


## Review findings — checkpoint 0cb598030477

The implementation is closer, but is not acceptable yet.

Authoritative CI run `37745287044` completed with:

```text
49 failed, 1091 passed, 1 skipped
```

Ruff did not run because the test step failed.

### 1. The test containment provider is injected only in the pytest process, not into spawned service processes

`tests/conftest.py` monkeypatches
`default_containment_provider`, which works only inside that Python process.

A substantial part of the suite starts a real Devlegate service in a subprocess.
Those subprocesses import fresh production modules and therefore select
`LinuxCgroupContainmentProvider` again. On GitHub Actions they have no delegated
cgroup subtree, so service/topology tests time out before reaching their intended
semantics.

Do not add an environment variable that can silently select the fake provider in
normal production.

Instead provide an explicit test-only composition path at the service harness /
test entry boundary. The subprocess must be constructed with a deterministic
containment provider by test code itself, while the normal CLI/service entrypoint
must continue to select production cgroup containment unconditionally.

The separation should remain:

```text
production entrypoint -> LinuxCgroupContainmentProvider
test service harness  -> DeterministicContainmentProvider
```

not:

```text
environment variable -> maybe fake production containment
```

### 2. DeterministicContainmentProvider must satisfy the supervisor contract used by unit doubles

Many unit tests fail with:

```text
TypeError: FakeProcess.wait() got an unexpected keyword argument 'timeout'
```

The containment test double has accidentally imposed additional `Popen`
behavior on existing fake processes.

The provider contract should abstract containment semantics, not force every
worker-process double to emulate more of `subprocess.Popen`.

Make the deterministic boundary independently track the lifecycle it needs, or
adapt the test provider so existing process doubles can be used without changing
unrelated worker protocol/egress tests merely to satisfy containment plumbing.

### 3. The deterministic provider currently does not model recursive ownership

CI exposes:

```text
test_natural_leader_exit_with_live_descendant_blocks_post_worker
WorkerRunResult(... worker_group_retired=True)
```

The deterministic boundary equates leader completion with boundary emptiness.
That is exactly the semantic mistake TASK-055 is intended to remove.

For tests whose subject includes descendant ownership/retirement, use a
deterministic boundary that can explicitly represent "leader exited, boundary
still populated", or move those tests to the real cgroup-v2 provider where a
usable delegated test hierarchy exists.

Do not use PGID behavior as a fake proof of cgroup semantics.

It is acceptable to have two test levels:

- a lightweight fake for tests unrelated to containment;
- focused containment contract tests with an explicit synthetic population
  model or real cgroup v2.

But the fake must never cause a test asserting recursive ownership to pass for
the wrong reason.

### 4. The claimed standalone/scie proof is still not an actual standalone-artifact proof

The new launcher no longer depends on `execution_launcher.py`, which is an
improvement. `_launcher_command()` uses isolated Python `-I -S -c`, so source
file materialization is no longer required.

However, `test_isolated_joiner_has_no_source_launcher_dependency` only inspects
the generated argv in the source-tree test environment. It does not execute the
real standalone/scie artifact.

The previous review explicitly required a focused proof against the packaged
artifact. Add one to the standalone build/validation path. It should run the
actual generated executable and exercise the same launcher-interpreter
resolution used in production.

If a real cgroup delegation is unavailable in generic CI, split the proof:

- artifact-level proof that the standalone runtime resolves and starts the
  isolated joiner interpreter correctly without source-tree dependencies;
- host-level cgroup proof on an environment with delegated cgroup v2.

Do not label a source-level argv assertion as standalone proof.

### 5. Keep the startup-hook regression, but distinguish what it proves

`test_isolated_joiner_blocks_startup_hook_before_membership` is useful: it
demonstrates that `-I -S` prevents a hostile `PYTHONPATH/sitecustomize` hook
from running before the joiner code.

Its fake `cgroup.procs` regular file does not prove kernel cgroup membership,
and does not need to. Document/test it specifically as a Python-bootstrap
isolation regression; real membership remains the production cgroup-provider
responsibility.

### Required next pass

- Inject the deterministic provider into subprocess-based service tests through
  an explicit test composition path, not a production environment fallback.
- Decouple the test provider from extra `Popen.wait(timeout=...)` assumptions.
- Provide correct synthetic recursive-population semantics for containment
  contract tests, or run those tests against real delegated cgroup v2.
- Add actual standalone/scie artifact-level launcher validation.
- Preserve the `-I -S` hostile-startup regression.
- Re-run the full authoritative suite and Ruff.


## Review findings — checkpoint e6c04101a98a

The test-composition work is materially improved, but the implementation is not
acceptable yet.

Authoritative CI run `37752218253` completed with:

```text
14 failed, 1126 passed, 1 skipped
```

Ruff did not run because the test step failed.

The remaining failures are concentrated in service restart/process-loss
topology rather than ordinary worker semantics.

### 1. In-memory deterministic containment cannot model durable ownership across daemon restart

The new subprocess test entrypoint successfully injects
`DeterministicContainmentProvider` into live service tests. This removes the
accidental dependency on GitHub-host cgroup delegation.

However, the provider's boundary state exists only inside the service process.
When a topology test kills/restarts Devlegate, the new service process cannot
re-observe the previous synthetic execution boundary.

The failing set is dominated by exactly those scenarios:

- transaction-stage restart preservation;
- post-worker/process-loss recovery;
- duplicate-worker refusal after parent SIGKILL;
- stale publication/recovery paths.

TASK-055 changes the durable recovery authority from transient PID/PGID state to
an execution containment boundary. Therefore tests that exercise daemon death
and restart need a containment test double whose state is independently
observable by the restarted process, just as a real cgroup remains observable
after the daemon dies.

Do not solve this by teaching recovery to trust PGID again when the test
provider is used.

Provide a deterministic durable containment backend for restart/recovery tests,
for example a test-owned filesystem-backed boundary state with explicit
population transitions and stable execution identity. The normal lightweight
in-process provider may remain for tests that never cross a daemon lifetime.

The semantic requirement is:

```text
daemon A dies
    ↓
execution boundary remains independently observable
    ↓
daemon B can classify populated / retired / indeterminate
```

This mirrors the real cgroup contract without requiring cgroup delegation in
generic CI.

### 2. Recovery identity should persist an execution-boundary identity, not leak a provider-specific host path as the abstraction

Production currently persists `containment_path` and
`observe_worker_identity()` directly reads `<path>/cgroup.events`.

That works for the first cgroup-v2 implementation, but it couples durable
recovery state to the physical host representation and makes the provider seam
asymmetric: an alternate provider cannot participate in recovery unless it
pretends to be a filesystem path.

Refine the contract so the persisted worker/execution state carries a
containment identity understood by the containment provider, and recovery asks
the provider to observe/classify that identity.

For the current Linux provider the identity may internally encode the cgroup
path, but `worker_supervisor` / durable state should not hard-code
`cgroup.events` access as the universal containment representation.

Keep this small; do not build a plugin framework or schema migration layer.

### 3. The standalone/scie artifact proof is still missing

The prior review required an actual packaged-artifact proof.

This execution changes only:

- deterministic containment behavior;
- process waiting adaptation;
- test service composition;
- one recovery regression.

There is still no change to the standalone build/validation path and no test
that executes the generated standalone/scie Devlegate artifact through the
trusted launcher-interpreter resolution.

The source-level `_launcher_command()` tests are useful but do not satisfy the
artifact requirement.

Add focused standalone validation that executes the generated artifact and
proves the packaged runtime can obtain/start the trusted isolated Python joiner
without depending on the source tree. If generic CI lacks cgroup delegation,
the artifact-level test may stop after proving joiner interpreter/bootstrap
availability; real cgroup membership remains covered by the host-capability
test.

### 4. Do not weaken the deterministic provider by blocking indefinitely on old FakeProcess APIs

The `TypeError` compatibility shim improved the ordinary suite, but:

```python
except TypeError:
    process.wait()
```

changes a bounded wait into an unbounded wait for a Popen-like test double.

For deterministic tests this should not create a new possibility of hanging the
test process. Prefer a provider/test-double contract that reports process state
without converting a requested bounded observation into an unbounded wait.

This is secondary to the restart/recovery issue, but should be cleaned up in the
same pass.

### Required next pass

- Add a restart-safe/durable deterministic containment model for service
  recovery topology tests.
- Move recovery observation behind the containment-provider contract rather than
  hard-coding `containment_path/cgroup.events` in worker supervision.
- Preserve production cgroup-v2 fail-closed behavior.
- Add actual standalone/scie artifact-level launcher validation.
- Keep lightweight semantic tests independent of host cgroup delegation.
- Preserve bounded waits in the test provider.
- Re-run full authoritative CI and Ruff.


## Review findings — checkpoint f972cf334347

The architectural changes requested in the previous pass are present:

- durable recovery observation is now behind `ContainmentProvider.observe()`;
- persisted worker state carries an opaque `containment_id` rather than having
  worker supervision read `cgroup.events` directly;
- a filesystem-backed deterministic provider exists for daemon-restart tests;
- bounded polling replaced the previous unbounded `wait()` compatibility path;
- the standalone proof is now integrated into `tools/build_standalone.py` and
  executes the generated standalone artifact, rather than only inspecting a
  source-tree launcher argv.

The implementation is still not acceptable because authoritative CI is red.

Authoritative CI run `37767067501` reports:

```text
54 failed, 1086 passed, 1 skipped
```

Ruff did not run because the test step failed.

### 1. Fix the durable test-provider construction before evaluating recovery semantics

The dominant failure is a direct test-composition bug.

`tests/service_harness.py` injects:

```python
DurableDeterministicContainmentProvider("<path-as-string>")
```

while the provider constructor stores the argument as-is and immediately calls:

```python
self.root.mkdir(...)
```

The spawned service therefore exits during construction with:

```text
AttributeError: 'str' object has no attribute 'mkdir'
```

This causes a large set of IPC/topology/restart tests to fail before exercising
their intended semantics.

Normalize the provider root at its API boundary (for example with `Path(root)`)
or pass a real `Path` from the explicit test composition path. Prefer making
the provider constructor robust to the path-like contract if that is the
intended public/internal type.

After this is fixed, rerun the full suite before making further recovery
changes. The current CI result cannot establish whether the new durable model
passes or fails restart/process-loss semantics because most affected services
never start.

### 2. Add focused provider-observation regressions independent of the full service topology

The new provider abstraction is the right direction, but it should have direct
tests proving the durable contract without relying only on large service tests.

At minimum, prove for `DurableDeterministicContainmentProvider`:

- created/spawned boundary persists a stable opaque identity;
- a second provider instance using the same root can observe that identity;
- live/populated state is observed as `matching-live`;
- retired/destroyed state is observed as `absent`;
- malformed/foreign identity is `indeterminate`;
- observation still works after discarding the original provider/boundary
  objects, modeling daemon restart.

For `LinuxCgroupContainmentProvider.observe()`, retain focused tests for
`populated 1`, `populated 0`, missing boundary, malformed identity, and
unreadable state.

### 3. Keep the standalone artifact proof

The new proof in `tools/build_standalone.py` materially closes the prior
artifact-level finding: it invokes the generated executable with
`PEX_INTERPRETER=1` and executes the same packaged
`_launcher_command()` bootstrap without depending on
`execution_launcher.py` from the source tree.

Generic CI does not need real cgroup delegation for this particular artifact
bootstrap proof; real cgroup membership remains a host-capability concern.

Do not regress this back to a source-only assertion.

### Required next pass

- Fix the Path/string construction bug in the durable test provider.
- Add direct durable-provider observation/restart contract tests.
- Rerun the full authoritative suite.
- Run Ruff after tests pass.
- If restart/recovery tests still fail after the service can actually start,
  review those failures against the provider-level ownership contract rather
  than restoring PGID authority.


## Review findings — checkpoint aad797e013bb

The previous construction bug is fixed and the focused provider-observation
coverage is present. The implementation is still not acceptable because
authoritative CI remains red.

Authoritative CI run `37816863778` reports:

```text
28 failed, 1116 passed, 1 skipped
```

Ruff did not run because the test step failed.

### 1. Lightweight deterministic containment still extends the fake-process contract

A large group of ordinary worker protocol/egress/logging tests now fail with:

```text
AttributeError: 'FakeProcess' object has no attribute 'poll'
```

`DeterministicContainmentProvider._Boundary.wait_empty()` currently polls the
returned process object directly.

This repeats the earlier problem in another form: containment plumbing must not
force unrelated worker-process doubles to implement additional `Popen`
surface merely so semantic tests can run.

Keep bounded semantics, but decouple the lightweight containment double from
`process.poll()`. It may track synthetic population explicitly, use a small
adapter owned by the provider, or have the worker-supervisor test harness update
boundary state. Do not broaden every existing `FakeProcess` just to satisfy
the containment test provider.

### 2. The durable test provider is durable as a file, but not autonomous as an ownership boundary

The remaining service/restart failures are concentrated in process-loss and
transaction-stage restart topology.

The current durable provider writes:

```text
matching-live
```

during `spawn()`, and changes it to:

```text
absent
```

only from `wait_empty()` in the original daemon process.

If that daemon is SIGKILLed, nothing independently updates the state when the
worker later exits. A restarted daemon can therefore keep observing stale
`matching-live` forever.

That does not model the real cgroup contract. A real cgroup's populated state is
kernel-maintained independently of the Devlegate daemon lifetime.

The deterministic restart backend must have the same essential property:

```text
daemon A dies
worker/boundary changes state
daemon B observes the new state
```

without daemon A having to call `wait_empty()`.

Implement an independently maintained synthetic boundary. Possible designs
include a small test-only wrapper/monitor process that owns the durable state and
updates it when the worker subtree retires, or another deterministic mechanism
whose state transition does not require the original Devlegate process to stay
alive.

Do not fix these tests by teaching production recovery to fall back to PGID
ownership.

### 3. Preserve provider-level recovery observation

The new `containment_id` plus `ContainmentProvider.observe()` abstraction is
the correct direction and should remain.

For the Linux provider:

- `cgroup.events populated=1` => matching/live;
- `populated=0` or missing retired boundary => absent;
- malformed/unreadable state => indeterminate.

For the durable test provider, make its independently maintained state obey the
same observable contract across daemon lifetime boundaries.

### 4. Standalone artifact proof is now materially present

The proof added to `tools/build_standalone.py` executes the generated
standalone executable under `PEX_INTERPRETER=1`, imports the packaged
containment module, constructs the real trusted joiner command, and executes a
worker through it without relying on the source-tree launcher file.

Keep this proof. It closes the previous source-vs-packaged launcher finding.

### Required next pass

- Remove the `.poll()` requirement from the lightweight deterministic
  provider without introducing unbounded waits.
- Make durable synthetic containment state evolve independently of the daemon
  that launched the worker.
- Re-run the restart/process-loss topology tests and verify they exercise the
  provider observation contract rather than PID/PGID fallback.
- Preserve `containment_id` / provider-level observation and the standalone
  artifact proof.
- Run the full authoritative suite and Ruff.


## Review findings — checkpoint 68242d2753c7

The previous two implementation directions are substantially present:

- the lightweight deterministic provider no longer requires `.poll()` from old
  FakeProcess doubles;
- durable test containment now uses an independent monitor process, so state can
  continue changing after the Devlegate daemon dies.

The implementation is still not acceptable because authoritative CI is red.

Authoritative CI run `37824162221` reports:

```text
18 failed, 1126 passed, 1 skipped
```

Ruff did not run because the test step failed.

### 1. Fix the PID-file publication race

The durable monitor writes its worker PID with:

```python
open(pf, 'w', encoding='ascii').write(str(p.pid))
```

while the parent waits only for:

```python
pid_file.is_file()
```

and then immediately executes:

```python
int(pid_file.read_text())
```

CI hit the race directly:

```text
ValueError: invalid literal for int() with base 10: ''
```

File existence is not publication completion.

Publish the monitor handshake atomically or use an IPC primitive with an
unambiguous readiness boundary. Examples include write-to-temp + fsync/close +
rename, a pipe whose complete line is read by the parent, or another explicit
ready protocol.

The parent must not consider the worker identity available until a complete,
validated PID record has been received.

Add a stress regression that repeatedly starts the durable boundary and proves
the handshake cannot observe partial/empty identity state.

### 2. The monitor currently models leader lifetime, not recursive execution lifetime

The monitor does:

```python
p = subprocess.Popen(a, start_new_session=True)
r = p.wait()
state = absent
```

This means it marks the synthetic boundary `absent` when the direct worker
leader exits.

That is not equivalent to the cgroup contract TASK-055 is introducing. A worker
can exit while a descendant remains alive; the real cgroup remains populated.

The durable restart backend is specifically used to model ownership across
daemon death, so it must not reintroduce:

```text
leader exited == execution retired
```

Either make the monitor own/observe a synthetic recursive execution boundary
whose population includes descendants, or explicitly separate:

- ordinary restart tests that need only durable leader state; and
- ownership/retirement tests that use a boundary capable of representing live
  descendants.

The durable provider's `observe(...)=absent` must not become authoritative
while a modeled execution descendant is still alive.

Add a regression:

```text
worker spawns descendant
worker exits
daemon/launching provider disappears
second provider observes matching-live
descendant exits
second provider eventually observes absent
```

Do not implement this by making production recovery trust PGID.

### 3. Monitor signal forwarding must be bounded and robust

The current monitor handler is:

```python
h = lambda n, f: os.kill(p.pid, n)
```

It signals only the direct worker PID. If the synthetic boundary is meant to
represent execution ownership, forced/graceful termination semantics must match
that model and not leave modeled descendants behind.

For the test backend, process-group signaling is acceptable as a simulation
mechanism if its limitations are explicit and the worker is guaranteed to own a
dedicated group. But the monitor must then signal the group, handle an already
exited leader safely, and still update durable state only after the modeled
boundary is retired.

### 4. Keep the production architecture unchanged

Do not weaken or redesign the production cgroup-v2 provider to accommodate these
test-backend defects.

The following parts should remain:

- production fail-closed cgroup-v2 containment;
- trusted join-before-exec;
- `cgroup.kill` recursive termination requirement;
- `cgroup.events/populated` retirement proof;
- opaque `containment_id` plus provider-level `observe()`;
- standalone/scie launcher proof.

### Required next pass

- Replace the racy pid-file publication with an atomic/readiness handshake.
- Make the durable synthetic boundary model execution population beyond direct
  leader lifetime.
- Make monitor signaling correspond to the modeled boundary, not only the
  leader PID.
- Add focused leader-exits/descendant-lives/restart observation coverage.
- Re-run full authoritative CI and Ruff.


## Review findings — checkpoint a3f6389c235b

The latest implementation addresses the two previous design findings in code:

- PID readiness publication now uses temp-file + atomic replace and validates a
  complete positive PID before returning from spawn;
- the durable test monitor tracks execution process-group lifetime beyond direct
  worker-leader exit and forwards signals to that group;
- focused regressions were added for repeated handshake startup and
  leader-exits/descendant-lives observation.

The ticket is still not acceptable because authoritative CI is not green.

Authoritative CI run `37829846818` was canceled by a runner shutdown signal at
about 87% completion. Before cancellation, the pytest progress already contained
multiple failures (at least seven visible `F` markers), so this cannot be
treated as an otherwise-green run interrupted only by infrastructure.

Ruff did not run.

### Required next pass

- Re-run authoritative CI for the same or corrected checkpoint until the suite
  completes.
- Inspect and fix the actual remaining failing tests; do not infer success from
  focused/local tests.
- Preserve the atomic monitor handshake, process-group lifetime tracking,
  provider-level recovery observation, production cgroup-v2 behavior, and
  standalone/scie proof.
- Require full authoritative pytest and Ruff green before returning the ticket
  to review.

If the next complete CI run shows failures caused solely by the synthetic test
backend, fix that backend without weakening production cgroup ownership
semantics.


## Review findings — checkpoint 0dcf56814dc8

The previous monitor regressions are improved, but the ticket is still not
acceptable.

Authoritative CI run `37837065870` completed normally and reports:

```text
14 failed, 1132 passed, 1 skipped
```

Ruff did not run because pytest failed.

The remaining failures are concentrated in restart/recovery service topology,
including:

- accepted integration after product publication;
- receipt restart semantics;
- transaction-stage restart preservation across multiple stages;
- SIGKILL parent / duplicate-worker recovery;
- post-worker-loss resume;
- stale-publication recovery.

### 1. Diagnose the surviving synthetic boundary population in the failing topology tests

The new durable monitor now keeps the boundary live after worker-leader exit by
scanning `/proc` for non-zombie members of the worker process group.

In the failing service tests, recovery waits until timeout, which strongly
suggests that the synthetic boundary remains `matching-live` longer than the
test expects.

Do not change recovery policy blindly.

Instrument the deterministic test backend/failing topology tests so a timeout
reports the exact surviving modeled members:

```text
pid
ppid
pgrp
session
state
argv/comm where safely available
```

Then establish whether each survivor is:

- a legitimate execution-owned descendant that the old tests failed to retire;
- a monitor/test-harness artifact incorrectly included in the model;
- a race in state publication/observation;
- or stale process-group identity reuse.

Update either the test backend or the affected topology fixtures according to
that evidence.

### 2. Do not treat process-group membership as a general substitute for cgroup recursive ownership

The durable deterministic backend currently models population with a process
group. This is acceptable as a constrained test mechanism only where the worker
and all modeled descendants are guaranteed to remain in that group.

A descendant that calls `setsid()` or `setpgid()` can escape this synthetic
model even though it would remain in the production cgroup.

Therefore:

- production ownership tests for setsid/double-fork escape resistance must stay
  tied to the real cgroup-v2 provider/contract;
- synthetic restart tests must not claim to prove arbitrary recursive process
  ownership merely from PGID scanning;
- if the durable backend is used for a descendant-lifetime contract test, make
  that limitation explicit or use a stronger synthetic registry/monitor model.

Do not reintroduce PGID as production recovery authority.

### 3. Make synthetic state publication atomic as well

PID and leader-exit publication now use atomic temp-file + replace, but boundary
state transitions still use direct truncate/write:

```python
open(state, 'w').write('absent\n')
```

A restarted daemon can observe the file between truncate and complete write and
classify the boundary as `indeterminate`. Because recovery fails closed on
indeterminate ownership, even a brief publication race can strand an execution.

Publish `matching-live` / `absent` state with the same atomic replacement
discipline used for the PID/exit handshake.

Add a stress observation regression that repeatedly transitions and observes the
durable state from a second provider and never sees a partial/invalid value.

### 4. Preserve the already-correct production architecture

Keep:

- cgroup-v2 production containment and fail-closed admission;
- join-before-exec trusted bootstrap;
- recursive `cgroup.kill` termination;
- `cgroup.events/populated` retirement proof;
- opaque `containment_id` + provider-level observation;
- standalone/scie artifact launcher proof.

### Required next pass

- Make durable state transitions atomic.
- Instrument the 14 failing topology cases to identify the exact surviving
  synthetic boundary members.
- Fix the synthetic backend or affected test fixtures based on evidence.
- Keep PGID/process-group modeling explicitly test-only and constrained.
- Re-run the full authoritative suite to completion.
- Require full pytest + Ruff green before review.


## Review findings — checkpoint 39bd20f6f45c

The implementation improves the synthetic backend:

- durable state publication is now atomic;
- the test monitor uses Linux subreaper semantics;
- reparented descendants are tracked through ancestry rather than only PGID;
- an atomic-state stress regression was added.

The ticket is still not acceptable.

Authoritative CI run `37859215126` completed with:

```text
14 failed, 1133 passed, 1 skipped
```

Ruff did not run because pytest failed.

The failing set is essentially the same restart/recovery topology cluster as the
previous pass.

### 1. The required survivor diagnostics were not implemented

The previous review explicitly required that timeout/failure diagnostics report
the exact synthetic boundary members that still keep ownership live:

```text
pid
ppid
pgrp
session
state
argv/comm
```

This pass changes the monitor algorithm but does not add those diagnostics.

As a result, the CI still tells us only that recovery timed out; it does not tell
us which concrete process(es) the subreaper model still considers live.

Do not make another containment-model change without that evidence.

Add deterministic diagnostics to the test backend and surface them in the
failing service/topology assertions. For every timeout, we should be able to
answer exactly which process keeps the boundary `matching-live`.

### 2. Verify whether the subreaper monitor itself retains descendants/zombies

The monitor now calls `PR_SET_CHILD_SUBREAPER` and scans ancestry from
`/proc`.

That is directionally stronger than PGID-only tracking, but a subreaper also
becomes responsible for adopted descendants. If adopted children become zombies
and are not reaped, the monitor may retain process state longer than expected.

The scanner ignores `Z` state, but the monitor itself still needs a coherent
reaping strategy so adopted descendants do not accumulate or distort lifecycle
behavior.

Instrument first, then prove:

- direct worker exit;
- reparented descendant still alive;
- reparented descendant exits;
- adopted child/zombie is reaped;
- boundary transitions to `absent`;
- monitor exits.

Do not rely on `/proc` filtering alone as proof that monitor lifecycle is
correct.

### 3. The 14 remaining CI failures are not "unrelated"

They are concentrated in exactly the restart/recovery behavior modified by
TASK-055:

- accepted integration after publication;
- receipt restart;
- transaction-stage restart preservation;
- SIGKILL parent / duplicate-worker handling;
- post-worker-loss resume;
- stale-publication recovery.

Therefore these cannot be dismissed as environmental/flaky failures until the
new containment test substrate is proven not to be the cause.

### 4. Preserve the production architecture

Keep unchanged:

- cgroup-v2 production containment;
- fail-closed admission;
- join-before-exec trusted bootstrap;
- recursive `cgroup.kill`;
- `cgroup.events/populated` retirement proof;
- opaque `containment_id` / provider-level observation;
- standalone/scie proof.

### Required next pass

- Add survivor diagnostics to the durable synthetic provider and topology test
  failures.
- Use those diagnostics to identify the exact processes holding the boundary
  live in the 14 failing cases.
- Add explicit adopted-child reaping in the monitor if the evidence shows it is
  required.
- Fix the synthetic backend/test fixtures based on observed evidence, not
  further guesswork.
- Re-run full authoritative pytest and Ruff.
- Return to review only with a complete green run.


## Review findings — checkpoint 591f8f554449

The requested diagnostics and subreaper reaping support are now present in the
synthetic containment backend, but the ticket is still not acceptable.

Authoritative CI run `37861915985` completed with:

```text
14 failed, 1133 passed, 1 skipped
```

Ruff did not run because pytest failed.

The remaining failures are still the same restart/recovery topology cluster.

### 1. Survivor diagnostics exist, but are not surfaced by the failing topology path

The backend now records concrete synthetic members with:

```text
pid
ppid
pgrp
session
state
comm
```

and `worker_supervisor.finish_interrupted()` can append them to:

```text
execution cgroup remains populated: ...
```

However, the authoritative CI failure output contains neither that message nor
any `pid=...` diagnostics.

Most failures terminate in:

```text
LiveService.wait_for(...)
service condition did not become true before timeout
```

So the diagnostic data is not reaching the assertion path that actually fails.

The previous review's purpose was not merely to create a diagnostic file; it
was to make the surviving ownership set visible in the failing topology tests.

Expose the durable containment diagnostics to the service test harness timeout
path, or persist them in a test-visible runtime/service diagnostic snapshot that
`LiveService.wait_for()` includes when timing out.

The next CI failure, if any, must tell us exactly which modeled process(es) keep
the boundary live.

### 2. Do not make another ownership-model change before reading those diagnostics

The latest backend now has:

- atomic state publication;
- subreaper adoption;
- adopted-child reaping;
- ancestry-based descendant tracking;
- concrete member diagnostics.

That is sufficient instrumentation to stop guessing.

Do not add another containment algorithm variation until the full topology run
shows the actual surviving member set.

### 3. Focused green tests do not supersede authoritative topology failures

The worker report says focused containment/recovery/topology tests pass, but the
authoritative parallel suite still fails 14 cases.

Because the failures are concentrated exactly in the lifecycle/recovery surface
changed by TASK-055, they remain blockers until either:

- the diagnostics show a real test-harness concurrency defect and it is fixed;
  or
- the containment/recovery implementation is corrected.

### 4. Preserve the current production architecture

Keep unchanged:

- production cgroup-v2 ownership;
- fail-closed admission;
- trusted join-before-exec;
- recursive `cgroup.kill`;
- `cgroup.events/populated` retirement proof;
- opaque `containment_id` plus provider observation;
- standalone/scie proof.

### Required next pass

- Surface synthetic survivor diagnostics in `LiveService.wait_for()` timeout
  failures (or an equivalent authoritative topology failure path).
- Re-run the full authoritative suite.
- If failures remain, use the reported PID/PPID/PGRP/session/state/comm evidence
  to identify the concrete lifecycle bug.
- Fix based on that evidence.
- Require full pytest and Ruff green before review.


## Review status — checkpoint b3abda29720d

This execution is explicitly incomplete:

```text
remaining:
Complete the full authoritative pytest run; the local run exceeded the execution
timeout before completion despite focused topology, IPC, containment, and
recovery tests passing.
```

The code change in this pass does exactly what the previous review requested:
`LiveService.wait_for()` now appends durable synthetic `.members` survivor
records to early-exit and timeout assertions.

However, authoritative GitHub CI run `37889774918` is still in progress in
`Run tests with coverage`; Ruff has not run yet.

Do not make further containment changes without new evidence.

### Required next pass

- Use the completed authoritative result for checkpoint
  `b3abda29720dfe4eb5dcffe6f2a4c50118302a9a`.
- If pytest fails, inspect the now-surfaced survivor records and fix only the
  concrete lifecycle/test-substrate defect they demonstrate.
- If pytest passes, require Ruff green as well.
- Return to review only with a completed authoritative pytest + Ruff result.

The production cgroup-v2 architecture and the diagnostic plumbing added in this
pass should otherwise remain unchanged.
