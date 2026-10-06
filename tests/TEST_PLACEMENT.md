# Test Placement

Choose the narrowest semantic domain that proves the behavior. Do not choose a
file merely because it imports the implementation being changed. Keep the
cheapest useful proof, but a cheaper layer never replaces a required stronger
layer; use `TEST_BOUNDARIES.md` for that decision and
`FOCUSED_TEST_AUDIT.md` for the rationale.

## Domains

- Control scheduler and admission: `test_control_scheduler.py`
- Control workspace and publication: `test_control_workspace.py`
- Control retry, reconciliation, and recovery: `test_control_recovery.py`
- Control cross-layer probes: `test_control_cross_layer.py`
- CLI parser and bootstrap: `test_cli_parser.py`
- CLI IPC client routing: `test_cli_ipc.py`
- CLI production topology: `test_cli_topology.py`
- Daemon host, lifecycle, and recovery: `test_daemon_host.py`, `test_daemon_lifecycle.py`, `test_daemon_recovery.py`
- IPC dispatch, transport, and owner handoff: `test_ipc_dispatch.py`, `test_ipc_transport.py`, `test_ipc_owner.py`
- Worker egress and claim validation: `test_worker_egress.py`
- Worker process groups, stdin delivery, and interruption: `test_worker_process.py`
- Worker execution-log ownership and handoff: `test_worker_logging.py`
- Remaining worker boundary and inline configuration: `test_worker_protocol.py`
- Project registration and address resolution: `test_project_registry.py`
- Project rename/remove/list and systemd lifecycle: `test_project_lifecycle.py`
- Host signal/install policy: `test_daemon_host.py`, `test_host_installation.py`, `test_systemd_supervisor.py`
- Distribution and licensing: `test_distribution_graph.py`, `test_deb_package.py`, `test_licensing.py`

## Remaining Maintained Domains

- Agent/bootstrap protocol: `test_agent_protocol.py`
- CLI common, cross-layer, and topology: `test_cli_common.py`, `test_cli_cross_layer.py`, `test_cli_topology.py`
- Control scheduler, workspace, recovery, and cross-layer: `test_control_scheduler.py`, `test_control_workspace.py`, `test_control_recovery.py`, `test_control_cross_layer.py`
- Daemon cross-layer and recovery: `test_daemon_cross_layer.py`, `test_daemon_recovery.py`
- Execution result/workspace and project context: `test_execution_result.py`, `test_execution_workspace_submodules.py`, `test_project_context.py`
- Force retry and worker prompt: `test_force_retry.py`, `test_worker_prompt.py`
- Git, snapshots, runtime identity/store, and helpers: `test_git_state.py`, `test_snapshot.py`, `test_runtime_identity.py`, `test_runtime_store.py`, `test_helpers.py`
- IPC protocol/client/dispatch/transport/owner/cross-layer: `test_ipc_protocol.py`, `test_ipc_client.py`, `test_ipc_dispatch.py`, `test_ipc_transport.py`, `test_ipc_owner.py`, `test_ipc_cross_layer.py`
- Launcher, namespace/platform boundaries, terminal, and log reader: `test_launcher.py`, `test_namespace_isolation.py`, `test_platform_boundary.py`, `test_terminal.py`, `test_log_reader.py`
- Output, standalone packaging, source, diagnostics, tickets, and coverage: `test_output.py`, `test_standalone_builder.py`, `test_standalone_package.py`, `test_full_source.py`, `test_service_diagnostics.py`, `test_tickets.py`, `test_coverage.py`

The former mixed modules are now `_cli_support.py`, `_control_support.py`,
`_daemon_support.py`, and `_ipc_support.py`. Worker and project semantic modules
also use `_worker_support.py` and `_project_support.py` for reusable setup. These
support modules own fixtures/helpers only; semantic test functions physically
live in their narrowest domain module. Worker protocol and project registry
retain only their cohesive boundary tests. There are no collection facades or
hidden `_test_*` inventories.
Cross-layer modules retain tests whose proof role does not fit an artificial
narrow bucket.

## Focused Commands

Run a semantic domain with normal pytest selection, for example:

```text
python3 -m pytest -q tests/test_control_scheduler.py
python3 -m pytest -q tests/test_control_workspace.py tests/test_control_recovery.py
python3 -m pytest -q tests/test_cli_parser.py
python3 -m pytest -q tests/test_ipc_transport.py
python3 -m pytest -q tests/test_ipc_owner.py
python3 -m pytest -q tests/test_cli_topology.py
python3 -m pytest -q tests/test_worker_egress.py tests/test_worker_process.py tests/test_worker_logging.py
python3 -m pytest -q tests/test_project_registry.py tests/test_project_lifecycle.py
```

These are edit-loop selections, not validation profiles. Run the stronger IPC
owner and independent production-topology companions when a change crosses
those boundaries, and run full CI for repository-wide proof.
