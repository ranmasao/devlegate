# Test Boundaries

This document is the test-proof map for Devlegate 0.5.0. It is the source of
truth for later I3/I4 cleanup. It describes what a test proves, not merely the
behavioral scenario in its name.

## Proof Model

The suite uses three runtime proof layers:

```text
engine tests       -> semantic state transitions and durable invariants
IPC tests          -> transport, dispatch, and owner handoff
subprocess tests   -> production service/CLI/process topology
```

A test proves only the strongest boundary it actually crosses. Real Git,
SQLite, worktrees, and subprocesses used as a test driver do not by themselves
prove service topology. Tests may inspect durable files directly from pytest;
that is observation, not a production client bypass.

### Taxonomy

| Layer | Actual execution path | Proves | Does not prove |
| --- | --- | --- | --- |
| Pure/component | codecs, parsers, stores, helpers, private deterministic functions | Local input/output, validation, serialization, persistence mechanics | Service ownership, CLI/service process separation |
| Engine semantic | `ServiceEngine`/`Devlegate` constructed directly; direct methods or `serve()` in a test thread | State-machine decisions, admission rules, recovery algorithms, Git/state invariants, worker-result handling | Independent CLI process lifetime or OS crash topology |
| IPC/owner handoff | Real Unix socket and `UnixIPCServer`; for mutable claims, a real `ServiceEngine.serve()` owner thread | Framing, connection isolation, dispatch, owner-thread serialization, receipt behavior | Independent service process, SIGKILL/restart, CLI survival |
| Production topology | `LiveService` starts `python -m devlegate run`, real CLI subprocesses use the real socket, or `h1_driver.py` starts the real service engine | Daemon authority, independent CLI/service lifetime, real socket lifecycle, process identity, crash/restart and persisted recovery | Nothing beyond the scenario and assertions actually made |

## Suite Map

This audit classifies 17 maintained test families, 8 major production
invariant families, 8 H1 evidence families, and 7 H2 concurrency families.
The counts are grouping counts, not test-case counts.

| Test family/file | Layer | What it proves | Boundary caution |
| --- | --- | --- | --- |
| `test_agent_protocol.py` | Pure/component | Init, render, manifest safety, deterministic bootstrap file handling | CLI bootstrap is not daemon coverage |
| `test_ipc_protocol.py` | Pure/component | Frame limits, chunking, codecs, request/response shape validation | No socket server or owner loop |
| `test_ipc_client.py` | Pure/component plus small IPC transport doubles | Client response decoding, request IDs, uncertain mutable delivery and replay identity, view round trips | `serve_once` is a fake socket peer; it is not `ServiceEngine` owner proof |
| `test_runtime_store.py` | Pure/component | SQLite schema, revisions, malformed data, atomic failed commit behavior | Direct database tests do not prove client access policy |
| `test_tickets.py`, `test_project_context.py`, `test_worker_prompt.py`, `test_execution_result.py`, `test_execution_workspace_submodules.py`, `test_helpers.py`, `test_terminal.py` | Pure/component | Ticket/context parsing, worker protocol data, report/workspace/terminal/helper behavior | Real files/Git are semantic realism, not process topology |
| `test_git_state.py`, `test_snapshot.py` | Engine semantic/component | Git observation, synchronization/recovery decisions and stable snapshot retries | No daemon or independent CLI path |
| `test_control_plane.py` direct `Devlegate` tests | Engine semantic | Control-plane validation, workspace binding, checkpoints, lifecycle/publication rules, accepted integration state, reconciliation semantics | `invoke()` setup is a subprocess bootstrap helper; most assertions exercise direct engine methods |
| `test_service_snapshot.py` | Engine semantic | Immutable published projections, read-only snapshot behavior, worker lifecycle projection | The long-worker test uses a fake `_run_worker`; it proves engine publication ordering, not OS worker behavior |
| `test_daemon.py` | Engine/host component | Signal intent, foreground polling, shutdown behavior, host delegation, selected engine error policy | Fake engines and monkeypatched iterations do not prove production daemon startup |
| `test_worker_protocol.py` | Worker/process component | Worker subprocess command boundary, process groups, prompt/egress protocol, typed results, identity cleanup | Some tests use real child processes, but this is the worker boundary, not the production service/CLI topology; patched `Popen` cases are component tests |
| `test_ipc_server.py` fake dispatch tests | IPC/component | Request validation and read-only dispatch against a minimal fake engine | `dispatch_*` with `FakeEngine` cannot prove mutation serialization or real engine semantics |
| `test_ipc_server.py` `running_server` tests | IPC transport | Real Unix socket framing, malformed/disconnected/idle client isolation, endpoint permissions, cleanup, multiple endpoints | Most use a server without an owner loop; mutable authority claims require the owner-thread families below |
| `test_ipc_server.py` owner-thread tests | IPC/owner handoff | `test_retry_submission_runs_on_service_owner_thread`, read-only availability during owner retry, shutdown admission race, retry/reconcile receipt semantics | In-process owner thread proves handoff and serialization, not independent process lifetime |
| `test_cli.py` parser/render/bootstrap tests | Pure/component/bootstrap | Argument parsing, formatting, command routing, `init`, `render`, `check`, repository-root and readiness rules | In-process `main()` and `invoke()` do not prove CLI/service separation |
| `test_cli.py` IPC boundary tests | IPC/owner boundary | CLI refuses mutable fallback, uses daemon IPC, authority/socket fail-closed behavior, read-only projection selection | `cli_daemon` is an in-process socket server; it is not production topology |
| `test_cli.py` `test_real_service_*` families | Production topology | Real service subprocess, real CLI subprocess, socket authority, worker process identity, H1/H2 behavior | These names are truthful; assertions still define the exact claim, and pytest disk inspection remains controller-side observation |
| `test_daemon.py` fake-host tests | Host component | Signal installation and return-code policy for `run_daemon`/`run_service` | Fake `ServiceEngine` is intentionally not service topology |

## Production Invariants

| Invariant | Semantic proof | IPC/owner proof | Subprocess proof | Status |
| --- | --- | --- | --- | --- |
| Service engine is the sole mutable runtime owner | Direct state/admission and `serve()` tests in `test_control_plane.py`, `test_daemon.py`, and `test_cli.py` | `test_retry_submission_runs_on_service_owner_thread`; reconcile owner-thread test | Real retry/reconcile CLI tests and H1 receipt tests | KEEP; intentional defense in depth |
| CLI mutable commands do not construct a mutable engine or write state | CLI monkeypatch tests around `retry`, `reconcile`, and no-daemon failure | `test_ipc_server.py` owner handoff tests | `test_real_service_process_executes_retry_from_real_cli`, `test_real_service_process_executes_reconciliation_from_real_cli` | KEEP across layers |
| Read-only views are stable and do not mutate canonical state | `test_service_snapshot.py`, `test_snapshot.py`, application view tests | IPC view/decoder and many-client tests | `test_run_hosts_real_ipc_status_and_plan_until_stopped`, live-worker observer tests | KEEP; read-only representation and topology are different claims |
| Matching/stale state is not mutation authority | Direct admission and validation tests in `test_control_plane.py` | Same-ID/distinct-ID/stale request owner tests | `test_real_service_stale_status_cannot_authorize_second_retry` | KEEP cross-layer |
| Mutable request IDs are idempotent and collision-safe | Direct receipt/state tests | `test_retry_request_receipt_coalesces_duplicates_and_survives_restart`, reconcile equivalent | Real same-ID and receipt-restart tests | KEEP; failure modes differ by boundary |
| Concurrent mutation is serialized and cannot duplicate work | Direct admission/retry semantics | Owner-thread and concurrent IPC tests | Real concurrent retry tests and observer-during-retry tests | KEEP cross-layer |
| Worker result/state transitions are durable and fail closed | `test_control_plane.py`, `test_service_snapshot.py`, `test_execution_result.py` | Owner dispatch tests do not replace this | Real worker/retry and H1 process-loss tests | KEEP |
| Clients do not need SQLite fallback when authority exists | Runtime locator/CLI fail-closed tests | Socket/authority tests in `test_ipc_server.py` | Real CLI/service socket tests | KEEP; controller disk inspection is not a fallback |
| Socket ownership and cleanup are safe | Locator and server component tests | Real Unix socket tests, endpoint replacement and shutdown families | `LiveService` readiness/stop/restart | KEEP; topology adds process lifetime |

No important mutation-authority claim is supported solely by a mocked lower
layer. The production claims have corresponding `LiveService` evidence. The
in-process tests remain useful because they isolate semantic and handoff
failure modes and fail faster.

## H1 Recovery Evidence

H1's process boundary is real only in `test_cli.py` tests using `LiveService`.
`LiveService` launches an independent `python -m devlegate run` process, waits
through the actual authority socket with `ping`, invokes the normal CLI path in
separate subprocesses, captures output, and can SIGKILL/restart the service.
`h1_driver.py` constructs the real `ServiceEngine` in that service process and
adds deterministic crash points around durable save/effect boundaries. It is a
test driver, not a substitute engine.

| H1 claim | Evidence location | Layer(s) | Evidence kind |
| --- | --- | --- | --- |
| Idle restart is inert; merge pending observes before effect | `test_real_service_sigkill_restarts_without_mutation`, `test_real_service_merge_pending_restart_is_observe_first` | Subprocess | SIGKILL, same SQLite/Git/worktrees, durable state and HEAD assertions |
| `worker-launch` remains fail closed | `test_real_service_worker_launch_restart_stays_fail_closed` | Subprocess | Crash after stage persistence; status/plan ambiguity and no rerun |
| Matching live worker is not signaled or duplicated | `test_real_service_matching_live_worker_restart_does_not_duplicate` | Subprocess | Worker PID marker matches persisted identity; restart preserves one attempt |
| Absent worker uses explicit RESUME | `test_real_service_absent_worker_uses_process_loss_resume` | Subprocess | Kill service and worker; new identity and second attempt are proven |
| Post-worker lost result requires RESUME and never fabricates a report | `test_real_service_post_worker_loss_requires_resume` | Subprocess | Crash after worker loss; new execution/report and review state |
| Checkpoint/publication/lifecycle recovery is idempotent | `test_real_service_transaction_stage_restart_preserves_execution`, `test_real_service_product_movement_during_downtime_blocks_stale_publish` | Subprocess | Crash points, durable execution identity, Git refs, and stale publish blocking |
| Accepted integration R/H/C recovery is exact | `test_real_service_accepted_integration_restart_is_idempotent` and the foreign, exact-local, remote, interleaving, and divergent-history tests | Subprocess | `h1_driver.py` crash points plus exact parent/remote SHA and fail-closed assertions |
| Mutable receipt is admission evidence, not a queue | `test_real_service_receipt_restart_is_not_a_persistent_command_queue`, `test_real_service_reconcile_receipt_restart_is_not_a_queue` | Subprocess | Crash after receipt save; restart preserves receipt without dispatch, new request required |

The direct/private tests in `test_control_plane.py` establish deterministic
H1 semantics such as checkpoint rejection, lifecycle lineage, and exact R/H/C
rules. They do not replace the corresponding real restart tests. The H1 matrix
(`H1_RESTART_MATRIX.md`) is the durable-state/recovery contract; this map adds
the execution layer carrying each row.

## H2 Concurrency Evidence

The H2 matrix is split deliberately:

| H2 claim | IPC/component evidence | Production-topology evidence |
| --- | --- | --- |
| Idle/incomplete/malformed clients do not block others | `test_idle_connection_does_not_block_unrelated_client`, `test_incomplete_frame_does_not_block_unrelated_client`, malformed/disconnect tests in `test_ipc_server.py` | Real socket is exercised, but no independent service process is required for this transport claim |
| Many observers and read-only state remain available | `test_many_read_only_clients_overlap_without_mutating_runtime`, client round-trip tests | `test_real_service_many_observers_succeed_while_worker_runs`, `test_real_service_observers_succeed_during_owner_retry` |
| Mutable submission crosses handler to owner thread | `test_retry_submission_runs_on_service_owner_thread`, reconcile owner-thread test | Real CLI retry/reconcile tests prove the same handoff through separate processes |
| Same request ID executes once; collision is rejected | `test_retry_request_receipt_coalesces_duplicates_and_survives_restart`, reconcile equivalent | `test_real_service_same_request_id_retries_concurrently_once` |
| Distinct concurrent requests cannot duplicate a worker | Owner/admission tests and H2 engine tests | `test_real_service_distinct_concurrent_retries_do_not_duplicate_worker` |
| Stale observation is not authority | Direct admission tests and IPC owner revalidation | `test_real_service_stale_status_cannot_authorize_second_retry` |
| Shutdown rejects pending mutation and cleans handlers | `test_shutdown_rejects_operator_command_waiting_for_owner_admission`, handler cleanup/shutdown tests | Service host lifecycle is additionally exercised by `LiveService` restart/stop tests |

The H2 matrix's in-process owner tests prove transport plus handoff and are not
weaker copies of the LiveService tests: they identify handler-thread versus
owner-thread behavior directly, while LiveService adds independent process and
CLI lifetime.

## Bootstrap Versus Runtime Service

Bootstrap commands intentionally work before a daemon exists. The following
families are bootstrap/component evidence, not daemon-service evidence:

- `test_help_and_parser_expose_phase1_commands`
- `test_init_*`, `test_render_*`, and `test_check_*` in `test_cli.py`
- control initialization and preflight tests in `test_control_plane.py`
- `test_application_import_does_not_import_cli`

`invoke()` starts a short-lived CLI subprocess, which is useful for command
boundary and filesystem effects. Unless it starts `run`/`daemon` and uses a
separate client against a live owner, it does not prove service topology.
`test_run_hosts_real_ipc_status_and_plan_until_stopped` is the explicit runtime
exception. `test_real_service_*` is the convention for full production service
topology and is currently truthful.

## Compatibility And Direct Seams

The following tests exercise intentional or historical seams and must not be
removed during I2:

- `test_service_engine_is_the_only_runtime_authority` checks the current
  `Application`/`Devlegate` identity aliases.
- `test_operational_cli_dispatches_run_through_application` and
  `test_operational_cli_constructs_service_engine_directly` check two supported
  construction seams.
- `test_all_operational_cli_commands_use_service_engine` checks the current
  CLI construction contract with a fake engine.
- `test_application_import_does_not_import_cli` checks import direction, not
  runtime ownership.
- Historical helper imports and private methods in `test_control_plane.py`,
  `test_daemon.py`, and `test_worker_protocol.py` are direct semantic seams.

These tests are not production topology proof. Whether the aliases and seams
remain production-reachable is a separate production-code audit.

## Cleanup Candidates

No tests were removed. Candidates are recorded only for later decisions.

### KEEP - Unique Proof

- All `test_real_service_*` tests covering SIGKILL, restart, real socket
  authority, worker PID identity, CLI/service separation, or persisted process
  loss. These claims cannot be established in-process.
- `test_ipc_server.py` incomplete-client, handler-cleanup, endpoint ownership,
  and owner-thread tests. Their transport and handoff failure modes are
  distinct from subprocess failures.
- H1 and H2 matrix documents plus their exact recovery/concurrency assertions.

### KEEP - Intentional Cross-Layer Duplication

- Direct engine admission/recovery plus IPC owner-thread plus LiveService retry
  and reconciliation families.
- Direct snapshot/view tests plus IPC codec/view round trips plus live-worker
  observer tests.
- Direct receipt/idempotency tests plus IPC receipt tests plus real concurrent
  and restart receipt tests.

### I3 CANDIDATE - Obsolete Architecture/Path

None proven by this audit. The direct aliases and private seams may be old, but
their production reachability was not established or disproved by test-layer
inspection. Removal belongs to an explicit I3 reachability decision.

### I4 CANDIDATE - Redundant Or Compatibility-Only

- `test_operational_cli_dispatches_run_through_application` and
  `test_operational_cli_constructs_service_engine_directly`: apparent duplicate
  CLI construction contracts; retain until the supported compatibility seam is
  chosen.
- `test_all_operational_cli_commands_use_service_engine`: overlaps the two
  construction tests but covers command-family dispatch in one table; possible
  consolidation only after deciding the seam contract.
- `test_service_engine_is_the_only_runtime_authority`: compatibility identity
  assertion; retain while `Application` and `Devlegate` aliases are reachable.
- `test_cli_status_and_plan_render_fake_application_without_runtime` and the
  `cli_daemon` CLI routing tests: lower-layer rendering/routing coverage that
  must not be counted as production topology; possible I4 review only if the
  same formatting and fail-closed cases remain covered.

### GAP - Missing Proof

- No separate full-production test proves a CLI process exits while a long,
  actively mutating service remains alive after the CLI exits. Existing real
  CLI tests prove request completion and service survival during work, while
  `LiveService` itself proves service lifetime; the exact CLI-abnormal-exit
  claim is not isolated. This is a nontrivial follow-up, not an I2 addition.
- No full-production test independently proves a service process crash while a
  client is blocked on a mutable response and the client reports uncertain
  delivery. `test_ipc_client.py` proves the client transport rule with a fake
  peer, and H1 receipt tests prove persisted admission recovery, but not this
  combined failure timing.

### UNCLEAR

- Whether `Application` and `Devlegate` are supported public compatibility
  names or only transitional aliases. This design decision controls I3 versus
  I4 treatment of the alias tests.
- Whether the exact abnormal CLI-exit scenario is a release-blocking invariant
  or adequately implied by the existing normal CLI subprocess tests and
  service-host lifecycle tests.

## Audit Rules For I3/I4

Before removing a test, identify its invariant, actual boundary, and failure
mode. A same-scenario test is not a replacement unless it crosses a materially
equivalent boundary and leaves the same evidence. In particular:

- Do not replace `LiveService` tests with in-process engine or IPC tests for
  process lifetime, SIGKILL, worker identity, or persisted restart claims.
- Do not count a direct `ServiceEngine` test as mutable CLI coverage.
- Do not count a `FakeEngine` IPC dispatch test as owner serialization proof.
- Do not treat real Git/SQLite effects as service topology.
- Keep lower-layer tests when they isolate a deterministic semantic or
  transport failure that a slower subprocess test would obscure.
