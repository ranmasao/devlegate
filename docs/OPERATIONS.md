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

## Runtime Upgrades

A package install does not replace code already loaded by a running service.
Upgrade the wheel or standalone executable, stop or checkpoint the service, run
`devlegate upgrade-templates`, review the project-side diff, and restart the
service. The next worker launch then uses the upgraded packaged worker prompt and
the corrected materialized Architect and Reviewer contracts. Use
`devlegate upgrade-templates --check` and `devlegate render --check` for read-only
drift diagnostics. Customized project templates fail with manual merge guidance
instead of being overwritten.

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

The explicit start command is the normal entry point:

```sh
devlegate start
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
normal `devlegate start` chooses systemd automatically when it is usable.

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

For each admitted worker, Devlegate writes an access-controlled diagnostic at
`STATE_DIR/prompts/<execution-id>.json`. It contains the exact UTF-8 prompt text,
its SHA-256, directive, ticket and execution identity, and bound review identities.
The service log records only prompt construction and successful stdin delivery by
execution ID and digest; it does not log prompt content. A transport failure is
reported separately and never logged as successful delivery. Treat prompt
diagnostics as confidential runtime state because ticket and review text can be
private.

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
devlegate reconcile <ticket-id>
```

`retry` requests a new attempt after a failed execution. `drop` retires a
blocked execution only when its exact execution record, checkpoint, report, and
absent worker ownership are proven; it preserves the record and does not rewrite the
ticket. `reconcile <ticket-id>` observes the pending state and selects resume, an
eligible update-base transplant, or the proven lease-guarded published-lineage
rewrite. Unknown or ambiguous state is not automatically recovered. The legacy
`reconcile resume` and `reconcile update-base` forms remain available for explicit
diagnostics and compatibility. `--onto` asserts the exact current product HEAD;
it does not select the algorithm. Supply it as a full SHA or a unique hexadecimal
commit prefix of at least 4 characters; Devlegate records the resolved full ID.
`--rewrite-published` is a compatibility option, not required for the canonical
service-proven published-lineage path.

`devlegate stop --force` is the only lifecycle command that persists an
execution-scoped manual-retry barrier. It records explicit operator intent for
the active execution before interrupting its worker. Ordinary `stop`,
`restart`, service shutdown, external systemd or WSL shutdown, and process loss
do not create that barrier. Safe automatic continuation remains conditional on
proven ownership, provenance, generation, and workspace identity; a normal
worker failure is not automatically retried.

Tickets normally move through:

```text
backlog -> todo -> review -> accepted -> done
```

Only eligible `todo` work is selected. Completed worker results enter review;
accepted work is integrated only after the acceptance boundary is satisfied.
For rework, the Reviewer publishes immutable feedback at
`reviews/<ticket-id>/<execution-id>.md` with the `devlegate-review-v1` attribution
header and moves the unchanged ticket from review to todo, optionally through
backlog. Devlegate binds only that reviewed execution's exact artifact and injects
it over worker stdin. Historical projects without artifacts are accepted only when
Git proves an exact content-preserving review disposition; malformed, conflicting,
stale, or mismatched artifacts block worker launch.

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
