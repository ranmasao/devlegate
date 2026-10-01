# Focused Test Audit

This is an operational decomposition audit for Devlegate 0.5.6. It answers
which collected tests provide useful edit-loop feedback for a change, without
weakening the proof boundaries in `TEST_BOUNDARIES.md`. It is not a validation
policy and does not replace full CI.

## Method And Evidence

Collection and timing were observed on 2026-10-01 with Python 3.12.3, pytest
9.0.3, and Ruff 0.15.14 from the repository root. The repository `.venv` was
not provisioned, so the system Python environment was used directly; the
commands and observations are therefore environment-specific evidence.

Collection command:

```text
python3 -m pytest --collect-only -q
```

It collected 1,082 tests in 4.92 seconds. The current supported `./dev test`
command invokes the same pytest entry point after `./dev setup`.

Representative timings:

| Selection | Tests | Observed time | Main cost signal |
| --- | ---: | ---: | --- |
| protocol, agent, result, prompt, snapshot files | 91 | 5.82 s | mostly temporary files; snapshot Git observation is the outlier |
| worker protocol and supervisor | 59 | 8.43 s | child processes and waits; inline config 5.03 s |
| IPC server file | 76 | 42.80 s | repeated engine/Git/socket setup; recovery and owner tests are slowest |
| daemon file | 83 | 148.53 s | worker groups, sleeps, lifecycle drains, signals, and Git state |
| CLI focused keyword selection | 30 | 3.89 s | parser/render/bootstrap and small IPC doubles; setup is visible |
| control-plane focused keyword selection | 7 | 17.00 s | Git-prefix ambiguity alone took 11.50 s |
| IPC transport focused keyword selection | 7 | 7.31 s | each real socket fixture has setup/teardown |
| full control-plane file | 196 | over 300 s observed | run was still progressing at timeout |
| full CLI file | 203 | over 300 s observed | run was still progressing at timeout |

The last two observations are lower bounds, not complete runtimes. They are
still sufficient to show that whole-file execution is not a routine edit-loop
unit. No tests were changed, skipped, or deleted to obtain these measurements.
The slowest-test output also identified Git/worktree and subprocess costs; it
did not show a universal fixed timeout, so timing must not be treated as a
performance guarantee.

## Current Inventory

The collected suite is physically spread across 39 test modules. The largest
modules are:

| Module | Collected tests | Primary domains present |
| --- | ---: | --- |
| `test_cli.py` | 203 | parser/help, bootstrap/read-only, client IPC, lifecycle, production topology, presentation |
| `test_control_plane.py` | 196 | scheduler, control reconciliation, execution workspace/publication, recovery, retry, worker identity |
| `test_daemon.py` | 83 | host signals, readiness/diagnostics, owner loop, lifecycle drain, process-loss recovery |
| `test_ipc_server.py` | 76 | fake dispatch, Unix transport, endpoint ownership, owner handoff, mutation receipts |
| `test_worker_protocol.py` | 52 | egress parser, typed claims, process-group supervision seams, worker boundary |
| `test_standalone_package.py` | 31 | archive identity, extraction safety, standalone distribution |
| `test_distribution_graph.py` | 28 | packaging graph, progress, adapter execution |
| `test_systemd_supervisor.py` | 28 | host unit rendering, ownership, lifecycle/systemd operations |
| `test_execution_result.py` | 26 | report schema, persistence, provenance |
| `test_output.py` | 26 | status/plan projection and rendering |
| `test_agent_protocol.py` | 25 | bootstrap manifest and prompt/agent protocol |
| `test_project_registry.py` | 25 | project identity, aliases, registry and systemd selection |
| `test_log_reader.py` | 24 | execution log selection and journal command construction |
| `test_tickets.py` | 22 | ticket/frontmatter/dependency parsing |
| `test_ipc_protocol.py` | 21 | framing and request/response codecs |
| `test_service_snapshot.py` | 19 | immutable published service projections |
| `test_host_installation.py` | 17 | host installation records and systemd policy seams |
| `test_ipc_client.py` | 17 | client decoding, replay identity, view round trips |
| `test_execution_workspace_submodules.py` | 16 | submodule workspace materialization and retirement |
| `test_standalone_builder.py` | 16 | standalone build metadata and artifact verification |
| `test_full_source.py` | 14 | source archive and submodule materialization |
| `test_project_context.py` | 14 | project context routing and safe initialization |
| remaining 16 modules | 194 | Git state, licensing, runtime store/identity, platform, launcher, output, helpers, and small component families |

The inventory is a domain map, not a suggestion to rename files. A test can
belong to more than one operational set while retaining one authoritative
location.

## Semantic And Proof-Domain Map

The broad proof layers remain exactly those in `TEST_BOUNDARIES.md`:

```text
pure/component -> engine semantic -> IPC/owner handoff -> production topology
```

The useful operational domains below are finer-grained:

| Domain | Current evidence | Layer | Typical shared setup |
| --- | --- | --- | --- |
| CLI syntax/help/version | parser, help, version, concise errors in `test_cli.py` | component | `main()`/subprocess helper; usually no Git world |
| Bootstrap and read-only CLI | `init`, `render`, `check`, offline status/plan and project context tests in `test_cli.py`, `test_project_context.py`, `test_project_registry.py` | component/engine | temporary repository, config, registry; some Git fixtures |
| Status/snapshot presentation | `test_output.py`, `test_service_snapshot.py`, selected `test_cli.py` and `test_ipc_client.py` | component/engine/IPC | constructed snapshots or fake IPC; live-worker observers are stronger |
| Scheduler/control semantics | early control-plane admission, dependency, barrier, validation, and serial-planning tests; `test_git_state.py`, `test_snapshot.py` | engine semantic | `git_fixture`, control worktree, SQLite |
| Execution workspace/publication | workspace, branch, checkpoint, publication, submodule, and provenance tests in `test_control_plane.py` and `test_execution_workspace_submodules.py` | engine semantic | real Git worktrees and temporary state; often expensive even without service |
| Retry/recovery/reconciliation | `test_control_plane.py` reconciliation/retry/recovery groups, `test_git_state.py`, `test_daemon.py` recovery, `test_ipc_server.py` owner recovery | engine plus IPC | Git topology, durable state, worker identity; receipt tests add sockets |
| Lifecycle/stop/restart | `test_daemon.py` drain/signal/stop groups and CLI lifecycle tests | engine/host plus topology | threads, polling, sleeps, worker subprocesses; real CLI/service tests are strongest |
| Worker egress/protocol | parser, report, malformed event, typed outcome, and log handoff tests in `test_worker_protocol.py`, `test_execution_result.py` | component/worker boundary | child process only for selected cases |
| Worker supervision | process-group, identity, environment, launch and interruption tests in `test_worker_protocol.py`, `test_worker_supervisor.py`, selected control/daemon tests | worker/process component | real child process, process group, waits |
| IPC framing/client | `test_ipc_protocol.py`, `test_ipc_client.py` | component | sockets are mostly fake peers or in-memory streams |
| IPC transport/ownership | `running_server` transport, endpoint permission, cleanup, malformed/disconnect tests in `test_ipc_server.py` | IPC | real Unix socket, temporary state, engine setup |
| IPC mutable owner handoff | owner-thread tests near the end of `test_ipc_server.py`, plus client receipt tests | IPC/owner | real socket plus `ServiceEngine.serve()` thread and Git fixture |
| Host/systemd integration | `test_systemd_supervisor.py`, `test_host_installation.py`, host portions of `test_daemon.py`, `test_project_registry.py` | component/host | mocked `systemctl` seams for most; readiness tests add process/FD behavior |
| Packaging/distribution/licensing | `test_distribution_graph.py`, `test_standalone_builder.py`, `test_standalone_package.py`, `test_deb_package.py`, `test_licensing.py`, `test_full_source.py`, `test_runtime_identity.py`, `test_launcher.py` | component/build and release topology | archive/build subprocesses, source tree, optional external tools |

Cross-domain evidence is intentional. For example, a receipt is tested in the
engine, over owner IPC, and after a real service restart. Those tests are not
duplicates: they exercise different failure boundaries.

## Large-Module Decomposition

### `test_control_plane.py` (196 tests)

Current node groups are identifiable by stable names rather than source line
ranges:

- Scheduler/control: `test_control_init_*`, `test_*diagnostic*`, dependency/frontier tests, `test_once_*`, control-head and serial-planning tests.
- Workspace/publication: `test_execution_*`, `test_checkpoint_*`, `test_*publication*`, `test_*worktree*`, `test_*branch*`, and the workspace materialization/retirement block.
- Recovery/reconciliation: `test_reconcile_*`, `test_*recovery*`, `test_*resume*`, `test_*zero_delta*`, and durable stage replay tests.
- Retry/admission and worker identity: `test_*retry*`, `test_*worker_identity*`, `test_*agent_running*`, and retry projection/selection tests.
- Cross-layer/service probes: the final live-service authority/recovery tests and any node named `test_live_service_*`.

The module imports `clone_world`, `LiveService`, Git helpers, execution
workspace code, reports, host installation, registry, and worker identity
types. Many nodes construct a complete Git/control fixture even when the
assertion is one admission rule. Therefore a physical split is organizational
until fixture construction is extracted. In the current tree, explicit node
IDs or a narrow `-k` expression are the smallest useful unit. A future split
into scheduler, workspace, recovery, and retry modules would improve source
navigation, but only a fixture refactor would reliably reduce runtime.

Do not omit workspace/publication tests from a scheduler change when the change
touches binding or checkpoint authority. Do not treat direct recovery tests as
a replacement for the corresponding `test_real_service_*` evidence in
`test_cli.py`.

### `test_cli.py` (203 tests)

This is the most operationally valuable split:

- Syntax/help/render: `test_help*`, `test_*help*`, `test_version*`, concise argument/error tests, and fake-engine rendering.
- Bootstrap/read-only: `test_init_*`, `test_render_*`, `test_check_*`, offline status/plan, project selection, and read-only no-mutation tests.
- Client IPC: `test_*uses_daemon*`, retry/reconcile/recover/drop forwarding, fail-closed authority/socket tests, and candidate selection.
- Host/lifecycle: direct stop/restart, startup identity, readiness, attached host, and service hosting tests.
- Production topology: every `test_real_service_*`, plus `test_foreground_hosts_real_ipc_status_and_plan_until_stopped` and real CLI force-stop/drop cases.

Parser/help selection is cheap enough for an edit loop. Bootstrap and client
IPC selections are usually moderate because repository fixtures are shared.
Lifecycle and production selections are expensive because they start services,
workers, or multiple CLI subprocesses. A physical split would materially help
if parser/bootstrap tests were moved away from the file-level imports and
fixture setup, but moving `test_real_service_*` without a clear topology index
risks accidental omission. Use explicit node groups first.

Every production topology node retains its current classification. In
particular, no direct engine or fake IPC test replaces a real service test.

### `test_daemon.py` (83 tests)

The natural groups are host construction/readiness/diagnostics, owner-loop
iteration and error policy, graceful lifecycle/drain, and worker/process-loss
supervision. The last two groups dominate runtime: the full file took 148.53
seconds, with process-loss and drain cases taking 4-6 seconds each. Host
construction and diagnostic tests are suitable node selections, but lifecycle
groups share `make_engine`, `control_fixture`, `invoke`, polling, and worker
helpers. Physical movement alone will not remove that cost. Extracting a small
engine-host fixture and deterministic clock/barrier would be a prerequisite to
a useful physical split.

### `test_ipc_server.py` (76 tests)

The file has four distinct groups:

- Fake dispatch and validation: `dispatch_*`, strict payload, and view/privacy tests.
- Socket transport and ownership: malformed/incomplete/disconnected clients, framing, endpoint paths/permissions, collision, cleanup, and active-peer protection.
- Owner handoff: `test_*owner_thread*`, retry/reconcile admission, read-only overlap, shutdown admission, and receipt tests.
- Recovery/topology bridge: full live owner path and interrupted/recovery tests.

`running_server` creates a real engine, initializes a Git fixture, starts a Unix
socket, and tears both down. Thus even a seven-node transport probe took 7.31
seconds. Fake dispatch nodes can be separated physically and would become very
cheap; transport and owner groups would mostly be reorganized unless the
fixture is split into a no-Git fake-engine server fixture and a minimal real
engine fixture. Owner handoff remains IPC/owner proof and cannot be collapsed
into fake dispatch.

## Focused Proof Sets

These are human-readable sets, not new pytest markers or policy. Until named
profiles exist, select by explicit node IDs or carefully reviewed `-k` terms.
Each set is the minimum useful lower-bound proof; changes crossing a stronger
boundary must add the stated stronger set.

| Change area | Minimum focused set | Must also run |
| --- | --- | --- |
| CLI parsing/help/rendering | `test_cli.py` parser/help/version/concise-error nodes; `test_output.py` for changed rendering | bootstrap set if routing or config changed; production CLI set if service command behavior changed |
| Project/bootstrap/read-only commands | `test_cli.py` init/render/check/offline status/plan nodes; `test_project_context.py`, `test_project_registry.py` relevant nodes | `test_control_plane.py` init/read-only semantic nodes when control state or Git observation changes; live service status only for process/socket claims |
| Scheduler/control-plane semantics | relevant scheduler/dependency/admission nodes in `test_control_plane.py`; `test_git_state.py`, `test_snapshot.py` for observation changes | workspace/publication when binding/checkpoint authority changes; IPC owner plus production CLI for mutable command path changes |
| Execution workspace/publication | workspace/checkpoint/publication nodes in `test_control_plane.py`; `test_execution_workspace_submodules.py` if submodules or retirement are involved | recovery/reconciliation nodes; real service restart/publication tests for persisted process/restart claims |
| Retry/reconciliation/recovery | relevant `test_control_plane.py` retry/reconcile/recovery nodes | matching `test_ipc_server.py` owner/receipt nodes; matching `test_cli.py::test_real_service_*` restart/recovery nodes |
| Lifecycle/stop/restart | targeted `test_daemon.py` drain/signal/host nodes and direct CLI lifecycle nodes | `test_ipc_server.py` shutdown/admission nodes; real service graceful lifecycle/restart tests for process lifetime or readiness |
| Worker egress/protocol | parser/report/typed claim nodes in `test_worker_protocol.py`; `test_execution_result.py` report nodes | worker supervision if launch/exit status is touched; control semantic tests if durable result handling changes |
| Worker process supervision | process-group and identity nodes in `test_worker_protocol.py` and `test_worker_supervisor.py` | daemon/control worker-loss nodes; real service worker identity/restart tests for persisted process identity |
| IPC framing/socket ownership | all relevant `test_ipc_protocol.py`, `test_ipc_client.py`, and transport/ownership nodes in `test_ipc_server.py` | owner-handoff nodes for mutation; real CLI/service nodes for independent process/socket lifecycle |
| IPC mutable owner handoff | owner-thread, admission, receipt, and read-only-overlap nodes in `test_ipc_server.py` | direct engine admission/recovery nodes and real CLI retry/reconcile/concurrency tests |
| systemd/host integration | relevant `test_systemd_supervisor.py`, `test_host_installation.py`, `test_project_registry.py`, and daemon host/readiness nodes | production topology only when actual service startup, readiness, or authority handoff is changed |
| Status/snapshot presentation | `test_output.py`, `test_service_snapshot.py`, selected `test_ipc_client.py` view round trips | IPC transport for encoding/access changes; live observers for service-owned live evidence |
| Packaging/distribution/licensing | smallest affected module among distribution graph, standalone builder/package, deb, licensing, launcher, runtime identity | `test_full_source.py` and built-artifact tests for source/archive/release claims |
| Full-source/release tooling | `test_full_source.py`, affected standalone/deb/licensing/build tests | full CI; release tooling is not an ordinary edit-loop substitute for the authoritative matrix |

The sets deliberately preserve lower-layer evidence. A cheap component test is
useful feedback, but it does not authorize skipping a stronger test when the
implementation changes that boundary.

## Cross-Layer Invariants That Must Not Collapse

The following relationships are explicit protections against a misleading fast
profile:

- Mutable service authority: direct engine admission proves semantics, IPC
  owner tests prove serialization, and real CLI/service tests prove process
  lifetime and authority. Run all applicable layers.
- Read-only status and plan: immutable projection tests, IPC codec/transport
  tests, and live observer tests answer different questions.
- Receipts and uncertain delivery: engine persistence, IPC replay identity, and
  real restart/crash timing must remain separate.
- Worker loss and identity: worker component tests do not prove service restart
  recovery or persisted PID identity; use the real-service recovery nodes.
- Graceful lifecycle: engine drain rules, IPC admission closure, and real
  service readiness/stop/restart are intentionally cumulative.
- Workspace/publication: direct Git/worktree invariants do not prove a service
  process survived, restarted, or avoided stale publication.

All `test_real_service_*` production-topology tests remain stronger-boundary
evidence under this audit. The controller's direct SQLite/Git inspection is
observation of the real service state, not a replacement for the service
process boundary.

## Full-CI-Only Or Normally Deferred

These are poor routine edit-loop targets unless the change directly affects
them:

- SIGKILL/restart, crash-point recovery, worker identity, and multi-process
  concurrency in `test_cli.py`.
- Long lifecycle drain, process-group loss, and signal race tests in
  `test_daemon.py`.
- Full IPC owner recovery and receipt-restart groups in `test_ipc_server.py`.
- Source/archive, standalone, Debian, licensing, and distribution graph tests
  when the change is unrelated to packaging or release files.
- Full-source reproducibility and release artifact tests, especially when they
  invoke build or archive tooling.

These tests remain authoritative in full CI. Deferral is an economics
recommendation, not a claim that they are redundant.

## Selection Unit Recommendation

The current best unit is a combination:

1. Explicit pytest node IDs for a small change and a stable semantic group.
2. Existing test files for cohesive small families such as protocol, output,
   runtime store, ticket, and worker prompt tests.
3. Human-readable named profiles implemented later as reviewed aliases over
   node groups, not as opaque filename globs.
4. Physical module splits only after fixture extraction demonstrates a runtime
   benefit.

`-k` is useful for exploration but is too broad to be the durable contract by
itself: names overlap (`retry`, `recovery`, `status`) and parametrized nodes can
silently expand. A future profile should print the selected nodes and include
an explicit stronger-layer companion list.

## Follow-Up Work In Dependency Order

1. Add a collection inventory script or pytest-supported report that emits the
   current node list and domain ownership without changing test behavior.
2. Extract fixture layers from `test_cli.py` and `test_control_plane.py`:
   pure invocation, temporary repository, Git/control worktree, real engine,
   and `LiveService`. Measure each layer before moving tests.
3. Split `test_ipc_server.py` fake dispatch from real socket/owner tests after
   the minimal fake-engine fixture is independent and provenance is documented.
4. Split daemon host/diagnostic tests from lifecycle/process-loss tests after
   deterministic barriers replace avoidable sleeps where safe.
5. Introduce reviewed named focused profiles over explicit node groups, with
   cross-layer companion tests and collection output.
6. Revisit physical module organization only after the measured fixture costs
   show that it reduces execution time rather than just file size.

These are implementation/refactoring candidates, not changes made by this
audit. They should preserve the matrices and cross-layer rules in
`TEST_BOUNDARIES.md`, `RECOVERY_MATRIX.md`, and `CONCURRENCY_MATRIX.md`.

## Unresolved Questions

- The full CLI and control-plane runtimes were not completed within the
  five-minute observation window; CI measurements are needed before assigning
  a reliable wall-clock budget.
- It is not yet established whether fixture extraction can reduce Git setup
  enough to justify physical movement; that requires before/after measurements.
- The suite has no maintained semantic markers or profile aliases today, so
  node-group names in this document are descriptive until a later design
  chooses their stable representation.
- Packaging tests may depend on optional host tools in CI; their timing and
  availability need a release-environment measurement rather than local
  extrapolation.

## Conclusion

Focused feedback is already practical for component, parser, presentation,
protocol, and carefully selected semantic nodes. It is not practical to use
the four largest files as edit-loop units. The immediate safe approach is
explicit node/group selection with mandatory stronger-layer companions; the
medium-term opportunity is fixture extraction, followed by physical splits and
named profiles only when measurements show a real cost reduction.
