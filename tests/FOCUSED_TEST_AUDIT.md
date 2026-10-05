# Focused Test Audit

This audit describes the focused test layout in the current working tree. It
does not replace full CI or the proof boundaries in `TEST_BOUNDARIES.md`.

## Evidence

Collection was run from the repository root with Python 3.12 and the installed
pytest version:

```text
python3 -m pytest --collect-only -q
```

The current result was 1,131 tests collected in 1.77 seconds across 56 test
modules. Collection is the supported way to verify the inventory because
parametrized cases are included in the count.

The focused worker and project command was also run:

```text
python3 -m pytest -q tests/test_worker_egress.py tests/test_worker_process.py tests/test_worker_logging.py tests/test_project_registry.py tests/test_project_lifecycle.py
```

It collected and passed 75 tests in 2.78 seconds. The retained source modules
were checked separately:

```text
python3 -m pytest -q tests/test_worker_protocol.py tests/test_project_registry.py
```

It passed 18 tests in 1.50 seconds.

## Before And After

The direct comparison is the parent tree at `HEAD` versus this working tree.
The parent had 52 `tests/test_*.py` modules. The current tree has 56. The four
new focused modules are `test_worker_egress.py`, `test_worker_process.py`,
`test_worker_logging.py`, and `test_project_lifecycle.py`.

The split keeps shared implementations in the existing modules. Tests moved
behind facades are renamed with a leading underscore in the source module and
re-exported by the focused module. Therefore they are collected once, under
the facade path, while their existing test bodies and fixture globals remain
usable.

| Area | Before in parent tree | After in current tree |
| --- | --- | --- |
| Worker egress, logging, and process tests | `test_worker_protocol.py` | `test_worker_egress.py` (30), `test_worker_logging.py` (7), `test_worker_process.py` (9); `test_worker_protocol.py` retains 6 |
| Project registry and lifecycle tests | `test_project_registry.py` | `test_project_registry.py` (12) and `test_project_lifecycle.py` (17) |
| Entire pytest inventory | 52 test modules | 56 test modules, 1,131 collected tests |

No test module was removed by this change. The old 39-module inventory and
the old monolith names are not descriptions of the current tree.

## Current Inventory

The complete current collection inventory is below. Counts include
parametrized cases.

| Module | Tests | Module | Tests |
| --- | ---: | --- | ---: |
| `test_agent_protocol.py` | 25 | `test_cli_common.py` | 4 |
| `test_cli_cross_layer.py` | 47 | `test_cli_ipc.py` | 59 |
| `test_cli_parser.py` | 42 | `test_cli_topology.py` | 62 |
| `test_commit_messages.py` | 2 | `test_control_cross_layer.py` | 29 |
| `test_control_recovery.py` | 99 | `test_control_scheduler.py` | 27 |
| `test_control_workspace.py` | 46 | `test_coverage.py` | 1 |
| `test_daemon_cross_layer.py` | 18 | `test_daemon_host.py` | 20 |
| `test_daemon_lifecycle.py` | 36 | `test_daemon_recovery.py` | 9 |
| `test_deb_package.py` | 13 | `test_distribution_graph.py` | 28 |
| `test_execution_result.py` | 26 | `test_execution_workspace_submodules.py` | 16 |
| `test_force_retry.py` | 14 | `test_full_source.py` | 14 |
| `test_git_state.py` | 8 | `test_helpers.py` | 2 |
| `test_host_installation.py` | 17 | `test_ipc_client.py` | 17 |
| `test_ipc_cross_layer.py` | 11 | `test_ipc_dispatch.py` | 6 |
| `test_ipc_owner.py` | 36 | `test_ipc_protocol.py` | 21 |
| `test_ipc_transport.py` | 25 | `test_launcher.py` | 7 |
| `test_licensing.py` | 8 | `test_log_reader.py` | 30 |
| `test_namespace_isolation.py` | 2 | `test_output.py` | 26 |
| `test_platform_boundary.py` | 8 | `test_project_context.py` | 14 |
| `test_project_lifecycle.py` | 17 | `test_project_registry.py` | 12 |
| `test_runtime_identity.py` | 10 | `test_runtime_store.py` | 9 |
| `test_service_diagnostics.py` | 2 | `test_service_snapshot.py` | 19 |
| `test_snapshot.py` | 7 | `test_standalone_builder.py` | 27 |
| `test_standalone_package.py` | 31 | `test_systemd_supervisor.py` | 28 |
| `test_terminal.py` | 1 | `test_tickets.py` | 22 |
| `test_worker_egress.py` | 30 | `test_worker_logging.py` | 7 |
| `test_worker_process.py` | 9 | `test_worker_prompt.py` | 12 |
| `test_worker_protocol.py` | 6 | `test_worker_supervisor.py` | 7 |

## Focused Commands

Use these current-file selections for edit-loop feedback:

```text
python3 -m pytest -q tests/test_worker_egress.py
python3 -m pytest -q tests/test_worker_process.py
python3 -m pytest -q tests/test_worker_logging.py
python3 -m pytest -q tests/test_worker_egress.py tests/test_worker_process.py tests/test_worker_logging.py
python3 -m pytest -q tests/test_project_registry.py
python3 -m pytest -q tests/test_project_lifecycle.py
python3 -m pytest -q tests/test_project_registry.py tests/test_project_lifecycle.py
```

These are selections, not named pytest profiles. Add the stronger companion
tests required by `TEST_BOUNDARIES.md` when a change crosses an IPC, service,
host, recovery, or production-topology boundary.

## Cohesive Remaining Modules

The following modules remain intentionally cohesive in the current tree:

- Execution result and workspace: `test_execution_result.py`, `test_execution_workspace_submodules.py`.
- Project context: `test_project_context.py`.
- Runtime store and identity: `test_runtime_store.py`, `test_runtime_identity.py`.
- Snapshot and output: `test_service_snapshot.py`, `test_snapshot.py`, `test_output.py`.
- systemd: `test_systemd_supervisor.py`, with related host policy in `test_host_installation.py` and `test_daemon_host.py`.
- Standalone packaging: `test_standalone_builder.py`, `test_standalone_package.py`.
- Full-source packaging: `test_full_source.py`.
- Debian packaging: `test_deb_package.py`.
- Licensing: `test_licensing.py`.

The control, CLI, daemon, and IPC domains are already represented by their
current scheduler/workspace/recovery, parser/common/IPC/topology, host/
lifecycle/recovery, and protocol/client/dispatch/transport/owner modules.
Their cross-layer files remain separate where the proof boundary requires it.

## Facade Check

The facade modules import successfully during collection. The original worker
and project modules retain only their remaining directly named tests, while
the moved tests appear once under the new facade paths. The focused collection
count is 75 and the focused run passed, so no import or collection change is
needed.

The facade pattern is organizational: it does not claim that fixture setup or
runtime cost has changed. Stronger service, IPC, host, packaging, and release
claims still require their existing cross-layer tests and full CI coverage.
