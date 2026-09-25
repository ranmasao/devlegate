# Devlegate Operations

This is the operator reference for using the Devlegate service.
The [README](../README.md) is the project overview; [Architecture](ARCHITECTURE.md)
covers ownership and implementation boundaries.

## Adopt A Project

Start with an existing Git repository, its intended product branch, a configured
remote, and working Git authentication. From the repository root:

```sh
devlegate init my-project
```

Inspect and complete the generated `.env`, `.devlegate/project.md`, templates,
and role artifacts using the project's own change-management process. Then
return to a clean valid checkout and initialize the separate workflow history:

```sh
devlegate control init
devlegate check
```

`init` prepares project-owned setup; it does not start execution. The project
owns its documentation and decides how generated changes are accepted.

## Select A Project

The preferred selector is a registered alias:

```sh
devlegate @my-project status
```

An explicit environment file is equivalent and works from any directory:

```sh
devlegate --env /path/to/repository/.env status
```

Without either selector, commands use `./.env` from the current directory.
`@ALIAS` and `--env` belong before the command and are mutually exclusive.
Aliases are local names; repository identity, `state_key`, runtime
paths, and systemd unit names do not depend on the alias.

Register or inspect projects with:

```sh
devlegate project alias my-project /path/to/repository
devlegate project list
devlegate project resolve @my-project
devlegate project identify /path/to/repository
devlegate project rename my-project new-name
```

Each repository root has at most one registered project and uses its
root `.env`. Directory adoption resolves to that root. Fleet-wide orchestration
is not implemented.

## Service Hosting

The bare command is the normal entry point:

```sh
devlegate
```

It chooses systemd user supervision when the current user manager is usable and
the exact project unit can be safely managed. Otherwise it runs attached to the
current terminal. This fallback is not an internal detached service.

Explicit attached forms are:

```sh
devlegate foreground
devlegate once
```

`foreground` remains attached and continuous; `once` performs one scheduler
pass. All modes use the same service authority and local Unix IPC boundary.
The systemd backend uses `systemctl --user`, project-specific units, and the
active user's XDG configuration tree. It never silently adopts an unmanaged or
cross-project unit.

Inspect or control a service with these commands:

```sh
devlegate status
devlegate plan
devlegate stop
devlegate restart
devlegate @my-project service status --supervisor systemd
```

The `service` command is an advanced explicit control for a systemd user unit;
normal `devlegate` startup chooses systemd automatically when it is usable.

```sh
devlegate @my-project service install --supervisor systemd
devlegate @my-project service start --supervisor systemd
devlegate @my-project service stop --supervisor systemd
devlegate @my-project service remove --supervisor systemd
```

`status` separates service state from execution phase and operator state. A
healthy running service proves ownership through its IPC endpoint; persisted
`agent_running` state alone is not proof. If authority exists but safe IPC is
unavailable, clients fail closed.

## Logs And State

SQLite runtime state is operational bookkeeping, not product or
workflow history. By default state is under `$XDG_STATE_HOME/devlegate`, or
`~/.local/state/devlegate`; `STATE_DIR` may override it.

For internal hosting, service output is stored at:

```text
STATE_DIR/logs/<state-key>/service.log
STATE_DIR/logs/<state-key>/executions/<execution-id>.log
```

Direct and external hosting inherit the service stream from the caller or
supervisor, while Devlegate still owns execution-log persistence. Logs are
diagnostic navigation, not substitutes for reports, checkpoints, runtime state,
or Git history.

Machine-readable output is available where supported:

```sh
devlegate status --json
devlegate plan --yaml
```

`--json` and `--yaml` are mutually exclusive. Service and worker streams remain
operational output rather than machine-readable documents.

## Recovery And Lifecycle

Use explicit commands when an operation needs operator intent:

```sh
devlegate retry <ticket-id>
devlegate drop <ticket-id>
devlegate reconcile resume <ticket-id>
devlegate reconcile update-base <ticket-id> --onto <product-branch>
```

`retry` requests a new attempt after a failed execution. `drop` retires a
blocked execution only when its exact execution record, checkpoint, report, and
absent worker ownership are proven; it preserves the record and does not rewrite the
ticket. `reconcile resume` continues retained progress when the admitted product
base is unchanged. `update-base` is a separate, explicit product-base
transplant operation. Unknown or ambiguous state is not automatically recovered.

Tickets normally move through:

```text
backlog -> todo -> review -> accepted -> done
```

Only eligible `todo` work is selected. Completed worker results enter review;
accepted work is integrated only after the acceptance boundary is satisfied.

## Decommissioning

Remove one project registration without deleting its repository or evidence:

```sh
devlegate project remove @my-project
```

This stops the owning runtime, removes only a verified managed systemd unit when
present, verifies authority is gone, and removes the alias last. Re-register it
later with `project alias`. Host policy removal is separate and requires an
empty registry:

```sh
devlegate host uninstall
```

Neither command removes the software artifact, invokes a package manager, or
deletes retained project runtime evidence.
