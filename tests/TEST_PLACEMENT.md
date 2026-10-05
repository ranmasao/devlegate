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

The original large modules are fixture/helper sources. Their semantic tests are
collected through the domain modules so Git, socket, worker, and subprocess
ownership is not duplicated. Cross-layer modules retain tests whose proof role
does not fit an artificial narrow bucket.

## Focused Commands

Run a semantic domain with normal pytest selection, for example:

```text
python3 -m pytest -q tests/test_control_scheduler.py
python3 -m pytest -q tests/test_control_workspace.py tests/test_control_recovery.py
python3 -m pytest -q tests/test_cli_parser.py
python3 -m pytest -q tests/test_ipc_transport.py
python3 -m pytest -q tests/test_ipc_owner.py
python3 -m pytest -q tests/test_cli_topology.py
```

These are edit-loop selections, not validation profiles. Run the stronger IPC
owner and independent production-topology companions when a change crosses
those boundaries, and run full CI for repository-wide proof.
