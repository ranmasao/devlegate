# Devlegate Operations

This is the operator reference for using the Devlegate service.
The [README](../README.md) is the project overview; [Architecture](ARCHITECTURE.md)
covers ownership and implementation boundaries.

## Historical Workspace Cleanup Inventory

Use `devlegate workspace ls --json` for a fresh owner-serialized inventory,
`devlegate workspace prune --dry-run --json` for a bounded preview, and
`devlegate workspace prune --apply --limit 20 --request-id <id> --json` to apply.
See [workspace inventory and prune](workspace.md) for eligibility and receipts.

The owner attempts historical cleanup on its first idle iteration after startup
or restart, then no more often than every 300 monotonic seconds. A pass runs on
the next idle scheduler iteration after it is due; scheduler activity can delay
it. Accepted-integration cleanup runs separately and immediately. Control changes
are reconsidered on the next historical pass, and restart starts a new pass.

The owner refreshes the read-only inventory after each historical pass, including
an interrupted pass when observation is possible. `observed_at_unix` is the wall
clock time of that inventory attempt; its age can be computed against the current
wall clock and is subject to clock adjustments. It does not guarantee current
repository state. `scan_complete` is false on observation failure or when the
10,000 candidate limit is reached. Non-done tickets remain visible as blocked,
but do not count as actionable blockers and do not receive per-candidate
retirement proofs. Mutation always uses fresh proof.

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

First control bootstrap uses `git worktree add --orphan` when available. With
older Git (including stock Debian 12 Git 2.39), only a failed native attempt
followed by usage-option proof of missing support can select the isolated
fallback: `worktree add --detach --no-checkout`, `symbolic-ref HEAD` to the exact
control ref, then `read-tree --empty`. Both paths create the same root workflow
skeleton and publish with an absent-ref lease. The declared Git >=2.31 dependency
is unchanged; this is not acceptance evidence for every distribution.

The fallback never checks out product files. After interruption, a registered
unborn control worktree with an empty or exact partial skeleton index can resume;
an exact committed skeleton can retry publication. After publication, normal
remote attach applies. A detached intermediate, foreign files/index, uncertain
registration or failed observation stops initialization and preserves evidence.
Inspect the reported path, Git worktree registration, HEAD and index before
manual recovery; do not blindly remove or prune them. Capability probing is
limited to failed first bootstrap, never existing attach or execution polls.

## Runtime Upgrades

A package install does not replace code already loaded by a running service.
Upgrade the wheel or standalone executable, stop or checkpoint the service, run
`devlegate upgrade-templates`, review the project-side diff, and restart the
service. The next worker launch then uses the upgraded packaged worker prompt and
the corrected materialized Architect and Reviewer contracts. Use
`devlegate upgrade-templates --check` and `devlegate render --check` for read-only
drift diagnostics. Customized project templates fail with manual merge guidance
instead of being overwritten.

For the Devlegate and rslab2 projects, perform the same local update on the host
that runs each service after the integrated 0.5.7 build is available:

```sh
# In each project's checkout, after installing the integrated 0.5.7 build.
# Stop or checkpoint that project's service using its normal operator procedure.
devlegate upgrade-templates --check
devlegate upgrade-templates
devlegate render --check
# Restart that project's service using its normal operator procedure.
```

If `upgrade-templates --check` reports customized templates or a manifest,
preserve the project-owned changes, merge the packaged update manually, then run
`devlegate render --check`; do not overwrite the conflict. A restart reloads the
Python-packaged worker prompt only for newly admitted executions. Existing
executions retain their bound prompt. Compare
`STATE_DIR/prompts/<execution-id>.json` files: `prompt_sha256` identifies the
persisted exact prompt, and the newly admitted execution records the new digest.
The operator must install, restart, and verify each host; this repository cannot
update either host or the separate rslab2 source tree.

Review-protocol migrations are external one-shot operations, not runtime or CLI
features. Existing `reviews/.devlegate-review-v1` files are inert historical data:
their presence, content, modification, or ancestry does not alter runtime behavior.

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

Bare `devlegate` displays help. Package installation does not provision host
policy or project service units. Use `devlegate host install` to install
per-user host policy separately when needed.

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

Ticket IDs start with an ASCII letter or digit and may also contain ASCII
letters, digits, underscores, and hyphens. Thus `reconcile update` selects the
ticket `update`, not an abbreviation of `update-base`. Exact command names take
precedence; use `reconcile auto resume` (or another command name) to select a
ticket with that name. Invalid syntax is rejected before sending an IPC request;
the service remains responsible for admission and ownership decisions.

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
it over worker stdin. Missing, headerless, mutable, conflicting, stale, or
mismatched artifacts block worker launch regardless of marker history. If an old
pending REWORK has no valid current-protocol artifact, Devlegate does not infer
feedback or start it as FRESH. Stop the service, preserve and inspect its workspace
and evidence, then explicitly dispose of that pending generation outside the
runtime before publishing a new, fully attributed review disposition.

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
