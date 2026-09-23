# Test Boundaries

This document is the maintained test-proof map for the current Devlegate 0.5
architecture. It describes what a test proves, not merely the behavioral
scenario in its name.

Test resources created outside pytest's normal temporary tree must have an
explicit owner with deterministic teardown. Resources retained during a crash
or recovery test are still owned by that test and must be removed when it
finishes.

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
| Production topology | `LiveService` starts `python -m devlegate foreground`, real CLI subprocesses use the real socket, or `h1_driver.py` starts the real service engine | Service authority, independent CLI/service lifetime, real socket lifecycle, process identity, crash/restart and persisted recovery | Nothing beyond the scenario and assertions actually made |

## Suite Map

This audit classifies 17 maintained test families, 8 major production
invariant families, 8 recovery evidence families, and 7 concurrency families.
The counts are grouping counts, not test-case counts.

| Test family/file | Layer | What it proves | Boundary caution |
| --- | --- | --- | --- |
| `test_agent_protocol.py` | Pure/component | Init, render, manifest safety, deterministic bootstrap file handling | CLI bootstrap is not production service coverage |
| `test_ipc_protocol.py` | Pure/component | Frame limits, chunking, codecs, request/response shape validation | No socket server or owner loop |
| `test_ipc_client.py` | Pure/component plus small IPC transport doubles | Client response decoding, request IDs, uncertain mutable delivery and replay identity, view round trips | `serve_once` is a fake socket peer; it is not `ServiceEngine` owner proof |
| `test_runtime_store.py` | Pure/component | SQLite schema, revisions, malformed data, atomic failed commit behavior | Direct database tests do not prove client access policy |
| `test_tickets.py`, `test_project_context.py`, `test_worker_prompt.py`, `test_execution_result.py`, `test_execution_workspace_submodules.py`, `test_helpers.py`, `test_terminal.py` | Pure/component | Ticket/context parsing, worker protocol data, report/workspace/terminal/helper behavior | Real files/Git are semantic realism, not process topology |
| `test_git_state.py`, `test_snapshot.py` | Engine semantic/component | Git observation, synchronization/recovery decisions and stable snapshot retries | No service or independent CLI path |
| `test_control_plane.py` direct `Devlegate` tests | Engine semantic | Control-plane validation, workspace binding, checkpoints, lifecycle/publication rules, accepted integration state, reconciliation semantics | `invoke()` setup is a subprocess bootstrap helper; most assertions exercise direct engine methods |
| `test_service_snapshot.py` | Engine semantic | Immutable published projections, read-only snapshot behavior, worker lifecycle projection | The long-worker test uses a fake `WorkerSupervisor.run`; it proves engine publication ordering, not OS worker behavior |
| `test_daemon.py` | Engine/host component | Signal intent, checkpoint-aware drain, graceful stop/restart behavior, host delegation, selected engine error policy | Fake engines and monkeypatched iterations do not prove production service startup |
| `test_worker_supervisor.py` | Worker/process component | `WorkerSupervisor` live ownership, worker launch configuration, and process supervision seams | Component tests do not prove production service/CLI topology |
| `test_worker_protocol.py` | Pure/component plus worker protocol | `WorkerEgressParser`, typed claims, malformed/duplicate reports, transport-vs-claim status, reserved tool semantics, and worker process regression coverage | Some legacy process tests use real child processes, but this is the worker boundary, not production service/CLI topology |
| `test_ipc_server.py` fake dispatch tests | IPC/component | Request validation and read-only dispatch against a minimal fake engine | `dispatch_*` with `FakeEngine` cannot prove mutation serialization or real engine semantics |
| `test_ipc_server.py` `running_server` tests | IPC transport | Real Unix socket framing, malformed/disconnected/idle client isolation, endpoint permissions, cleanup, multiple endpoints | Most use a server without an owner loop; mutable authority claims require the owner-thread families below |
| `test_ipc_server.py` owner-thread tests | IPC/owner handoff | `test_retry_submission_runs_on_service_owner_thread`, read-only availability during owner retry, shutdown admission race, retry/reconcile receipt semantics | In-process owner thread proves handoff and serialization, not independent process lifetime |
| `test_cli.py` parser/render/bootstrap tests | Pure/component/bootstrap | Argument parsing, formatting, command routing, `init`, `render`, `check`, repository-root and readiness rules | In-process `main()` and `invoke()` do not prove CLI/service separation |
| `test_cli.py` IPC boundary tests | IPC/owner boundary | CLI refuses mutable fallback, uses service IPC, authority/socket fail-closed behavior, read-only projection selection | `cli_daemon` is an in-process socket server; it is not production topology |
| `test_cli.py` `test_real_service_*` families | Production topology | Real service subprocess, real CLI subprocess, socket authority, worker process identity, recovery and concurrency behavior | These names are truthful; assertions still define the exact claim, and pytest disk inspection remains controller-side observation |
| `test_daemon.py` fake-host tests | Host component | Signal installation and return-code policy for `run_service` | Fake `ServiceEngine` is intentionally not service topology |

## Production Invariants

| Invariant | Semantic proof | IPC/owner proof | Subprocess proof | Status |
| --- | --- | --- | --- | --- |
| Service engine is the sole mutable runtime owner | Direct state/admission and `serve()` tests in `test_control_plane.py`, `test_daemon.py`, and `test_cli.py` | `test_retry_submission_runs_on_service_owner_thread`; reconcile owner-thread test | Real retry/reconcile CLI tests and recovery receipt tests | KEEP; intentional defense in depth |
| CLI mutable commands do not construct a mutable engine or write state | CLI monkeypatch tests around `retry`, `reconcile`, and no-service failure | `test_ipc_server.py` owner handoff tests | `test_real_service_process_executes_retry_from_real_cli`, `test_real_service_process_executes_reconciliation_from_real_cli` | KEEP across layers |
| Read-only views are stable and do not mutate canonical state | `test_service_snapshot.py`, `test_snapshot.py`, service view tests | IPC view/decoder and many-client tests | `test_run_hosts_real_ipc_status_and_plan_until_stopped`, live-worker observer tests | KEEP; read-only representation and topology are different claims |
| Matching/stale state is not mutation authority | Direct admission and validation tests in `test_control_plane.py` | Same-ID/distinct-ID/stale request owner tests | `test_real_service_stale_status_cannot_authorize_second_retry` | KEEP cross-layer |
| Mutable request IDs are idempotent and collision-safe | Direct receipt/state tests | `test_retry_request_receipt_coalesces_duplicates_and_survives_restart`, reconcile equivalent | Real same-ID and receipt-restart tests | KEEP; failure modes differ by boundary |
| Concurrent mutation is serialized and cannot duplicate work | Direct admission/retry semantics | Owner-thread and concurrent IPC tests | Real concurrent retry tests and observer-during-retry tests | KEEP cross-layer |
| Acknowledged CLI lifetime does not own service lifetime | Owner admission semantics | Owner-thread admission tests | `test_real_service_admitted_retry_outlives_cli_process` | KEEP; normal client exit after ACK is the release invariant |
| Lost mutable response after durable admission is uncertain | Client replay semantics and receipt persistence | Fake-peer uncertain delivery and receipt tests | `test_real_service_crash_during_mutable_response_reports_uncertain_delivery` | KEEP; real crash timing is proven |
| Worker result/state transitions are durable and fail closed | `test_control_plane.py`, `test_service_snapshot.py`, `test_execution_result.py` | Owner dispatch tests do not replace this | Real worker/retry and recovery process-loss tests | KEEP |
| Clients do not need SQLite fallback when authority exists | Runtime locator/CLI fail-closed tests | Socket/authority tests in `test_ipc_server.py` | Real CLI/service socket tests | KEEP; controller disk inspection is not a fallback |
| Socket ownership and cleanup are safe | Locator and server component tests | Real Unix socket tests, endpoint replacement and shutdown families | `LiveService` readiness/stop/restart | KEEP; topology adds process lifetime |
| Graceful lifecycle commands close admission before exit | `test_lifecycle_drain_rejects_submitted_command_before_owner_admission`, checkpoint-barrier tests | Lifecycle IPC dispatch and service identity tests | `test_real_service_graceful_lifecycle_waits_for_active_worker` | KEEP; SIGINT remains abort-only |
| Explicit drop retires only a proven execution and preserves provenance | Drop admission, evidence-ref, and workspace-retirement tests | IPC/owner handoff and shared selector tests | `test_real_service_drop_retire_old_lineage_and_runs_fresh` | KEEP; same-ID and distinct-ID generations are fresh |

No important mutation-authority claim is supported solely by a mocked lower
layer. The production claims have corresponding `LiveService` evidence. The
in-process tests remain useful because they isolate semantic and handoff
failure modes and fail faster.

## Recovery Evidence

The recovery process boundary is real only in `test_cli.py` tests using `LiveService`.
`LiveService` launches an independent `python -m devlegate foreground` process, waits
through the actual authority socket with `ping`, invokes the normal CLI path in
separate subprocesses, captures output, and can SIGKILL/restart the service.
`h1_driver.py` constructs the real `ServiceEngine` in that service process and
adds deterministic crash points around durable save/effect boundaries. It is a
test driver, not a substitute engine.

| Recovery claim | Evidence location | Layer(s) | Evidence kind |
| --- | --- | --- | --- |
| Idle restart is inert; merge pending observes before effect | `test_real_service_sigkill_restarts_without_mutation`, `test_real_service_merge_pending_restart_is_observe_first` | Subprocess | SIGKILL, same SQLite/Git/worktrees, durable state and HEAD assertions |
| Graceful stop/restart drains active workers and proves readiness | `test_checkpoint_failure_during_lifecycle_drain_has_no_completion_boundary` | Subprocess | `test_real_service_graceful_lifecycle_waits_for_active_worker`, `test_bare_cli_restart_waits_for_ready_replacement` |
| `worker-launch` remains fail closed | `test_real_service_worker_launch_restart_stays_fail_closed` | Subprocess | Crash after stage persistence; status/plan ambiguity and no rerun |
| Matching live worker is not signaled or duplicated | `test_real_service_matching_live_worker_restart_does_not_duplicate` | Subprocess | Worker PID marker matches persisted identity; restart preserves one attempt |
| Absent worker uses explicit RESUME | `test_real_service_absent_worker_uses_process_loss_resume` | Subprocess | Kill service and worker; new identity and second attempt are proven |
| Post-worker lost result requires RESUME and never fabricates a report | `test_real_service_post_worker_loss_requires_resume` | Subprocess | Crash after worker loss; new execution/report and review state |
| Checkpoint/publication/lifecycle recovery is idempotent | `test_real_service_transaction_stage_restart_preserves_execution`, `test_real_service_product_movement_during_downtime_blocks_stale_publish` | Subprocess | Crash points, durable execution identity, Git refs, and stale publish blocking |
| Accepted integration R/H/C recovery is exact | `test_real_service_accepted_integration_restart_is_idempotent` and the foreign, exact-local, remote, interleaving, and divergent-history tests | Subprocess | `h1_driver.py` crash points plus exact parent/remote SHA and fail-closed assertions |
| Mutable receipt is admission evidence, not a queue | `test_real_service_receipt_restart_is_not_a_persistent_command_queue`, `test_real_service_reconcile_receipt_restart_is_not_a_queue` | Subprocess | Crash after receipt save; restart preserves receipt without dispatch, new request required |

The direct/private tests in `test_control_plane.py` establish deterministic
recovery semantics such as checkpoint rejection, lifecycle lineage, and exact R/H/C
rules. They do not replace the corresponding real restart tests. The recovery
matrix (`RECOVERY_MATRIX.md`) is the durable-state/recovery contract; this map adds
the execution layer carrying each row.

## Concurrency Evidence

The concurrency matrix is split deliberately:

| Concurrency claim | IPC/component evidence | Production-topology evidence |
| --- | --- | --- |
| Idle/incomplete/malformed clients do not block others | `test_idle_connection_does_not_block_unrelated_client`, `test_incomplete_frame_does_not_block_unrelated_client`, malformed/disconnect tests in `test_ipc_server.py` | Real socket is exercised, but no independent service process is required for this transport claim |
| Many observers and read-only state remain available | `test_many_read_only_clients_overlap_without_mutating_runtime`, client round-trip tests | `test_real_service_many_observers_succeed_while_worker_runs`, `test_real_service_observers_succeed_during_owner_retry` |
| Mutable submission crosses handler to owner thread | `test_retry_submission_runs_on_service_owner_thread`, reconcile owner-thread test | Real CLI retry/reconcile tests prove the same handoff through separate processes |
| Same request ID executes once; collision is rejected | `test_retry_request_receipt_coalesces_duplicates_and_survives_restart`, reconcile equivalent | `test_real_service_same_request_id_retries_concurrently_once` |
| Distinct concurrent requests cannot duplicate a worker | Owner/admission tests and concurrency engine tests | `test_real_service_distinct_concurrent_retries_do_not_duplicate_worker` |
| Stale observation is not authority | Direct admission tests and IPC owner revalidation | `test_real_service_stale_status_cannot_authorize_second_retry` |
| Shutdown rejects pending mutation and cleans handlers | `test_shutdown_rejects_operator_command_waiting_for_owner_admission`, handler cleanup/shutdown tests | Service host lifecycle is additionally exercised by `LiveService` restart/stop tests |

The concurrency matrix's in-process owner tests prove transport plus handoff and are not
weaker copies of the LiveService tests: they identify handler-thread versus
owner-thread behavior directly, while LiveService adds independent process and
CLI lifetime.

## Bootstrap Versus Runtime Service

Bootstrap commands intentionally work before a service exists. The following
families are bootstrap/component evidence, not service-topology evidence:

- `test_help_and_parser_expose_phase1_commands`
- `test_init_*`, `test_render_*`, and `test_check_*` in `test_cli.py`
- control initialization and preflight tests in `test_control_plane.py`

`invoke()` starts a short-lived CLI subprocess, which is useful for command
boundary and filesystem effects. Unless it starts a persistent service and uses a
separate client against a live owner, it does not prove service topology.
`test_foreground_hosts_real_ipc_status_and_plan_until_stopped` is the explicit runtime
exception. `test_real_service_*` is the convention for full production service
topology and is currently truthful.

## Compatibility And Direct Seams

The architecture reachability audit traced these seams through production code.
The classifications below describe the current supported paths; historical
compatibility seams are listed only where their removal explains surviving
coverage.

| Symbol/path | Reachability | Classification | Evidence |
| --- | --- | --- | --- |
| `ServiceEngine` | `cli.main()` constructs it for persistent service modes through `_service_engine`; `run_service()` hosts it; IPC dispatch receives it | KEEP - canonical current path | `src/devlegate/cli.py`, `src/devlegate/daemon.py`, `src/devlegate/service.py` |
| `Devlegate` | `cli.main()` constructs it for `init`, `render`, `check`, and `control`; it supplies bootstrap/presentation behavior | KEEP - supported bootstrap/internal semantic surface | `src/devlegate/cli.py:359`, `src/devlegate/cli.py:493-500` |
| `Application` | No production references remain; the former `_service_engine()` substitution branch was retired | REMOVE - unsupported runtime injection seam | Replaced by direct `ServiceEngine` construction in `src/devlegate/cli.py` |
| `DevlegateApplication` | No production, test, package export, or documentation contract was found | REMOVE - compatibility residue | `src/devlegate/application.py` deleted; no supported 0.5 import contract |
| `devlegate`, `devlegate foreground`, `devlegate once` | Construct the canonical `_service_engine()` and call `run_service()` with the selected hosting mode | KEEP - canonical service commands | `src/devlegate/cli.py`, `src/devlegate/daemon.py` |
| `devlegate stop` | Uses the authenticated IPC stop request and does not construct an engine | KEEP - canonical service control command | `src/devlegate/cli.py`, `src/devlegate/ipc_server.py` |
| `devlegate status` / `plan` | Tries IPC first and uses a read-only guarded `ServiceEngine` canonical `status_view()` / `plan_view()` fallback only when authority is absent | KEEP - read-only bootstrap/offline path | `src/devlegate/cli.py:58-94` |
| `devlegate retry` / `reconcile update-base` | Validate CLI input and submit IPC intentions; do not construct a mutable CLI engine | KEEP - canonical client path | `src/devlegate/cli.py:103-179`, `src/devlegate/ipc_server.py:47-78` |
| `ServiceEngine.retry()` / `reconcile_update_base()` / `reconcile_resume()` | Direct internal engine methods, called by semantic tests and owner-side code; not called by CLI client dispatch | KEEP - intentional internal semantic seam | `src/devlegate/runtime.py`; owner command handling routes through the corresponding owned methods |
| `runtime.Devlegate` alias | No supported callers or documented contract | REMOVED in 0.5.2 - obsolete alias | `src/devlegate/runtime.py` no longer defines the alias |
| `ServiceEngine.status()` / `plan()` | No supported callers; canonical views are `status_view()` and `plan_view()` | REMOVED in 0.5.2 - obsolete spellings | `src/devlegate/runtime.py`, `src/devlegate/cli.py` |
| Historical helper reexports from `devlegate.cli` | Tests now import runtime-owned helpers directly; `_git` remains a current CLI dependency | REMOVED in 0.5.2 - private test compatibility | `tests/test_cli.py`, `tests/test_terminal.py`, `tests/test_worker_protocol.py` |

### Construction Map

```text
python -m devlegate / console script
  -> cli.main()
     -> init/render/check/control -> Devlegate(read_only=True)
     -> status/plan -> IPC, or guarded read-only ServiceEngine fallback
     -> retry/reconcile -> IPC client only
      -> devlegate -> background launcher -> devlegate foreground
      -> foreground/once -> ServiceEngine -> run_service() -> UnixIPCServer + ServiceEngine.serve()
      -> stop -> authenticated Unix IPC -> service shutdown intent
```

`h1_driver.py` is test-only and constructs `ServiceEngine` so it can inject
deterministic crash points before calling the real `run_service()` host. It is
not a production construction path. Test fixtures construct engines directly
for semantic or IPC proof and are likewise not production paths.

### Mutation Authority Audit

The production mutable path is:

```text
CLI retry/reconcile -> Unix IPC -> dispatch_mutation
  -> ServiceEngine.submit_* -> owner command admission
  -> ServiceEngine owner loop -> _retry_owned/_reconcile_update_base_owned
  -> _save_state and other runtime mutations
```

`RuntimeStore` writes occur inside `ServiceEngine` methods. No source path was
found where the CLI client writes runtime state directly or where IPC handlers
execute mutable work instead of submitting it. `ServiceEngine.retry()` and
`reconcile_update_base()` remain direct engine semantics and are not obsolete:
they are owner-side algorithms and useful lower-layer regression seams. No
production mutable bypass was found.

The following tests exercise current bootstrap, semantic, or IPC seams and
remain valid after the architecture cleanup:

- `test_operational_cli_constructs_service_engine_directly` checks the canonical
  runtime construction contract.
- `test_foreground_service_command_constructs_one_service_engine` checks the
  foreground host construction contract.
- `test_service_engine_status_and_plan_return_immutable_views` checks the
  canonical engine view contract.
- `test_cli_status_and_plan_render_fake_engine_without_runtime` checks cheap
  rendering without claiming production topology.
- Runtime-owned private helpers and methods in `test_control_plane.py`,
  `test_daemon.py`, and `test_worker_protocol.py` are direct semantic seams.

These tests are not production topology proof. The production reachability
audit above now resolves `Devlegate`, `ServiceEngine`, and the persistent
service host.
Application injection is retired; direct tests still do not become topology
proof.

## Cleanup Candidates

The compatibility cleanup removed four compatibility-only tests and retargeted
three tests to the canonical engine seam. The uncalled direct loop and signal
helper seams were also removed; remaining direct engine tests use
`tests/runtime_helpers.py`.

### KEEP - Unique Proof

- All `test_real_service_*` tests covering SIGKILL, restart, real socket
  authority, worker PID identity, CLI/service separation, or persisted process
  loss. These claims cannot be established in-process.
- `test_ipc_server.py` incomplete-client, handler-cleanup, endpoint ownership,
  and owner-thread tests. Their transport and handoff failure modes are
  distinct from subprocess failures.
- Recovery and concurrency matrix documents plus their exact assertions.

### KEEP - Intentional Cross-Layer Duplication

- Direct engine admission/recovery plus IPC owner-thread plus LiveService retry
  and reconciliation families.
- Direct snapshot/view tests plus IPC codec/view round trips plus live-worker
  observer tests.
- Direct receipt/idempotency tests plus IPC receipt tests plus real concurrent
  and restart receipt tests.

### Obsolete Architecture/Path

None carried forward. The reachability audit retired the unsupported
Application injection seam, uncalled direct loop, and signal helper seams.

### Redundant Or Compatibility-Only

None remaining from the I2 candidate groups. The fake-engine rendering and
`cli_daemon` routing tests were retained because they protect distinct cheap
formatting, error-translation, and IPC-boundary behavior.

### Removed Tests And Replacements

| Removed test | Removed contract | Surviving proof/reason |
| --- | --- | --- |
| `test_operational_cli_dispatches_run_through_application` | Historical Application substitution during service startup | `test_operational_cli_constructs_service_engine_directly` proves the canonical construction; real-service tests prove runtime behavior |
| `test_all_operational_cli_commands_use_service_engine` | Broad fake-engine construction table that overlapped dedicated command tests | Dedicated foreground construction, canonical once construction, bootstrap tests, and IPC-only CLI tests cover the actual contracts |
| `test_service_engine_is_the_only_runtime_authority` | Application/Devlegate/ServiceEngine alias identity assertion | Direct `ServiceEngine` construction and owner-thread/real-service authority tests prove behavior rather than retired alias identity |
| `test_application_import_does_not_import_cli` | Import-direction check for the deleted `devlegate.application` module | No supported module remains; package entrypoint/import tests and canonical source imports cover current import direction |

Tests retargeted rather than removed:

- `test_application_status_and_plan_return_immutable_views` is now
  `test_service_engine_status_and_plan_return_immutable_views` and uses the
  canonical engine.
- `test_cli_status_and_plan_render_fake_application_without_runtime` is now
  `test_cli_status_and_plan_render_fake_engine_without_runtime`; it retains
  unique rendering coverage without the retired Application seam.
- `test_retry_refuses_without_daemon_without_constructing_application` is now
  `test_retry_refuses_without_daemon_without_constructing_engine`; it still
  proves retry refuses before any engine construction.

### GAP - Missing Proof

None remaining for the audited 0.5 service-boundary claims.

The former GAP 1 is resolved by
`test_real_service_admitted_retry_outlives_cli_process`: a real CLI receives
the admission ACK and exits, then the same live service and the same genuinely
started long-running worker remain active. This proves normal acknowledged
client lifetime separation, not arbitrary client crash timing.

The former GAP 2 is resolved by
`test_real_service_crash_during_mutable_response_reports_uncertain_delivery`:
The test-only `H1_CRASH_POINT=receipt_after_save` setting proves the owner saved
an accepted retry
receipt before SIGKILL, and the real CLI's same-ID replay reports uncertain
delivery and directs the user to inspect status before retrying.

### UNCLEAR

None remaining for the audited 0.5 service-boundary claims.

Decision: an abnormal CLI SIGKILL after admission is not a separate release
invariant. The required invariant is that acknowledged client lifetime does
not own service lifetime, proven by the normal CLI exit test. Loss before a
mutable acknowledgement is the uncertain-delivery boundary, proven by the
real service crash test. A third exact-instruction CLI kill would not establish
a distinct 0.5 semantic contract.

## Current Audit Rules

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
