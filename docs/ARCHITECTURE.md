# Devlegate Architecture

This document describes the current 0.5 architecture. It is a
technical reference for contributors and operators. The product overview is in
the [README](../README.md); development direction is in the
[roadmap](../ROADMAP.md).

## System Boundaries

Devlegate keeps product work, workflow records, and active ticket work in
separate places.

- The operator checkout contains the product branch and product code.
- The control branch and its Devlegate-owned worktree contain workflow tickets,
  workflow directories, and execution reports.
- Each selected ticket has a durable execution branch and a Devlegate-owned
  execution worktree for active worker changes.

Git is the source of truth for product history and control history. Control
history is never merged or copied into product history or an execution branch.
The control revision can bind and authorize an execution attempt, but it does
not become part of the product or execution history.

The execution branch is durable history for a ticket. Its worktree is the
disposable active workspace. A worktree may be recreated only when its branch
and workspace identity can be proven; a dirty, conflicting, detached, unsafe,
or unexpectedly occupied worktree is refused.

## Python Import Boundary

The installed `devlegate` console entry point loads Devlegate-owned code and
private bundled dependencies from the installed package tree. Bundled
NanoYAML therefore lives under `devlegate._vendor` rather than under the public
top-level `nanoyaml` name; a managed checkout cannot replace it merely by
containing a same-named package.

`python -m devlegate` retains the interpreter's normal module lookup behavior.
Python resolves the initial `devlegate` package before its code can establish
any policy, so a checkout-local `devlegate/` can shadow the installed package
for that invocation form. Devlegate's background launcher is different: a
trusted parent starts its child with Python safe-path mode (`-P`), so the
managed checkout cannot win merely because it is the child's working directory.
This does not sanitize an explicitly supplied `PYTHONPATH`, `sitecustomize`,
modified interpreters or installations, or an arbitrary same-UID compromise.
Use the installed console entry point when the managed checkout is not trusted
to own Python namespaces.

## Runtime Service

Devlegate has one service execution model. Background, foreground, and once
differ only in hosting and lifetime policy. The canonical service host owns the
project runtime lock, local Unix IPC endpoint, signal handling, readiness, and
orderly teardown. It invokes one `ServiceEngine` scheduler iteration boundary.

Devlegate 0.5 hosted execution requires Linux. The boundary is required by
strong worker process identity and recovery, process-group lifecycle handling,
and Unix IPC peer-authentication assumptions. Unsupported platforms fail before
mutable service authority or scheduler operation begins. Low-level identity and
peer-authentication checks still fail closed independently; broader portability
is not promised by this release.

The hosting and lifetime policies are:

```text
background = detached + continuous
foreground = attached + continuous
once       = attached + one scheduler iteration
```

Hosting ownership is a separate policy dimension:

```text
direct    = caller owns the attached service process
internal  = Devlegate owns detached process hosting and self-restart
external  = another supervisor owns process lifetime
```

The current forms map to these policies as follows:

| Form | Ownership | Attachment | Lifetime |
| --- | --- | --- | --- |
| bare `devlegate` | internal | detached | continuous |
| `devlegate foreground` | direct | attached | continuous |
| `devlegate once` | direct | attached | one iteration |
| systemd user service | external | inherited streams | continuous |

The external policy is implemented by the explicit systemd user-service backend.
`devlegate @ALIAS service install --supervisor systemd` writes an atomic,
project-specific unit under the XDG user-unit directory and reloads the user
manager. `start`, `stop`, `restart`, and `status` route through `systemctl --user`;
the backend never falls back to Devlegate's internal detached host. Removal
refuses units that do not contain the Devlegate identity marker, then stops,
disables, removes, and reloads the exact unit.

The generated unit uses `Type=notify`, `KillMode=mixed`,
`TimeoutStopSec=infinity`, and `Restart=no`. The external canonical host sends
`READY=1` through the inherited `NOTIFY_SOCKET`; a missing required socket fails
startup. systemd owns inherited service output and journal routing, while
Devlegate owns execution-log persistence. Hosting ownership is not a project
`.env` setting.

Per-user host policy is separate from software distribution. After a runnable
Devlegate artifact is provided by a wheel, self-contained executable, or future
distro package, `devlegate host install --supervisor internal|systemd` records
the default detached supervisor at
`$XDG_CONFIG_HOME/devlegate/installation.json`. It does not invoke a package
manager, create a Python environment, or install a global unit. Systemd host
policy provisions project units lazily at detached start.

Project selection is resolved before runtime construction. `@ALIAS` is the
preferred local handle; `--env FILE` is the explicit path form; and an
unqualified command uses exactly `$PWD/.env`. All forms must identify a
registered project, and explicit env paths resolve their Git repository from
the env file's location rather than the caller's current directory. The
registry is versioned user configuration under the XDG configuration hierarchy.
There is at most one registered project per canonical Git working-tree root,
and its canonical project configuration is `<repository-root>/.env`. Directory
adoption resolves to that repository root; alternate env filenames and env
files below the root are rejected. Aliases do not enter runtime or systemd
identity. Explicit addressing is independent of caller CWD, while relative
`STATE_DIR` is resolved from the canonical repository root. `init <alias>`
must be run from that root. Fleet-wide orchestration is outside this release.

`project remove @ALIAS` is the reversible project decommissioning boundary. It
resolves and retains the canonical target, gracefully stops the owning runtime,
removes only a verified managed systemd registration, verifies runtime
authority is gone, and removes the alias last with a compare-and-remove check.
It preserves the repository, project configuration and workflow history,
project documents and settings, and all retained `STATE_DIR` evidence. A later
`project alias <name> /path/to/repository` restores addressing and the same
runtime identity because `state_key` is derived from the canonical working-tree
root. `devlegate host uninstall` requires an empty registry and no residual
managed project units, then removes only the host policy record. It preserves
projects and runtime evidence; removal of the software artifact remains the
responsibility of its distribution mechanism.

The production runtime is one persistent service hosting one `ServiceEngine`.
`ServiceEngine` is the single mutable workflow engine: it makes workflow
decisions, runs workers, and performs service operations.

Multiple CLI processes may observe the service or submit supported operations.
Mutable operations are admitted and serialized by the service owner. The
service owns ticket movement, worker execution, checkpoint commits, publication,
reports, and accepted product integration.

Hosted repository access is restricted to the service owner thread. IPC handlers
do not inspect Git, SQLite, tickets, plans, or retry eligibility; they return
published immutable views and enqueue mutable commands. The owner thread
validates and dispatches commands at scheduler boundaries, while duplicate
request identities are resolved through durable receipts.

Bare `devlegate` starts the detached form selected by the installed host policy:
internal policy uses Devlegate's detached subprocess, while systemd policy uses
the exact managed project unit. `devlegate foreground` runs that host attached
to the current terminal, and `devlegate once` runs it for one complete scheduler
iteration. All three modes use the same authority and IPC boundary.
`devlegate stop` requests orderly shutdown through the actual runtime owner.

### Operational Logging

Operational output has an explicit service or execution scope. Service-scoped
records describe the control plane and remain in the main service stream.
Execution-scoped records are keyed by the durable `execution_id`; they contain
sanitized worker output and execution-local diagnostics without becoming
workflow evidence. The internally supervised layout is:

```text
STATE_DIR/logs/<state_key>/service.log
STATE_DIR/logs/<state_key>/executions/<execution-id>.log
```

The service host owns persistence of the service stream when internally hosted;
direct and external hosting inherit that stream from the caller or supervisor.
Devlegate owns execution-log persistence in all modes. Reports, checkpoints,
disposition, runtime state, and Git provenance remain authoritative if an
execution log is unavailable.
Foreground operation may show and persist worker output; internal and external
operation routes detailed worker output to the execution log instead of the
service stream.
When a worker sink is opened, the service stream records start and finish
handoff markers with the ticket ID, execution ID, and absolute execution-log
path. These records are operator navigation only; runtime state, execution
reports, checkpoints, dispositions, and Git provenance remain authoritative.

Retry and automatic-resume authorization are scheduler-iteration inputs or
local iteration state, not persistent service state.

## Runtime State

SQLite stores local operational runtime state. This state records active
bookkeeping such as execution progress and request handling; it is not the
canonical product or workflow history.

By default the state is under `$XDG_STATE_HOME/devlegate`, or
`~/.local/state/devlegate` when `XDG_STATE_HOME` is unset. `STATE_DIR` can
override the location. State and Git observations are used together when the
service must establish whether a previous operation took effect.

Terminal service failures have a separate lifetime from workflow runtime state.
When a hosted service escapes its canonical host boundary unexpectedly,
Devlegate records the latest small versioned diagnostic under the project state
directory. A successful READY clears that diagnostic; orderly shutdown does not
create one. Hard process loss, power loss, SIGKILL, and similar external events
may leave no service diagnostic and are not assigned a cause by absence.

## IPC And CLI Clients

The service exposes a project-specific local Unix IPC endpoint derived from the
runtime identity. `status` and `plan` use the service-owned view when the
service is active. When no service authority exists, those commands can take a
guarded read-only observation directly.

If service authority exists but IPC cannot safely be used, the client fails
closed rather than reading or writing around the service. `retry`, `drop`,
`reconcile resume`, and `reconcile update-base` are mutable operations and go
through the service; they do not construct a separate mutable CLI runtime.

## External Control Writers

Managed agents should prefer deterministic ticket and control operations with an
expected-old-head or CAS-style publication when that interface is available.
Direct Git writers remain supported external actors, including humans, GitHub
or API clients, and third-party automation. They should use ordinary non-force
updates and treat a non-fast-forward rejection as a signal to re-observe the
published history. Force updates are strongly discouraged; an external rewrite
requires explicit operator-authorized control-lineage reconciliation afterward.

`devlegate reconcile control --from <local-head> --to <remote-head>` is not
ordinary synchronization. It adopts only the exact, freshly verified divergent
pair named by the operator, preserves the displaced local head under a local
evidence ref, and never chooses remote history automatically.

Mutable requests carry an identity and are acknowledged only when the service
admits that request. An unavailable endpoint during a mutable request is not
treated as proof that the operation did not happen.

## Ticket And Execution Lifecycle

Tickets move through:

```text
backlog -> todo -> review -> accepted -> done
```

Only `todo` tickets whose dependencies are in `done` are runnable. `review` and
`accepted` do not satisfy dependencies. Devlegate selects one runnable ticket
for a possible execution.

Status exposes scheduler admission rather than duplicating internal dependency
readiness:

- `eligible` means immediately admissible by the scheduler;
- `blocked` means a `todo` ticket that is neither current nor eligible and has
  exactly one effective tagged blocking reason.

For valid enumerable workflow state, the admission projection is partitioned as
`todo = current union eligible union blocked`. These sets are pairwise
disjoint. Dependency readiness remains an internal DAG predicate used by the
scheduler; it is not serialized as a second status category.

Machine status serializes `tickets.eligible` and `tickets.blocked`. Each
blocked entry has one tagged `reason`: `dependencies` carries only immediate
unfinished dependency IDs and states, while scheduler-wide barriers carry the
single applicable barrier kind and ticket ID when one exists.

The worker starts in the ticket's execution worktree at the planned product
revision. A completed result is submitted to `review`. An incomplete, blocked,
or failed result remains in `todo` with its execution evidence. Reviewer
acceptance moves the ticket to `accepted`; Devlegate then integrates the
accepted product change and moves the ticket to `done`.

Review and product integration are separate boundaries. Accepted integration
uses proven fast-forward ancestry only. Devlegate does not automatically rebase,
merge conflicting histories, resolve conflicts, or perform destructive
reconciliation.

## Worker Boundary

Workers edit implementation files and return a result or claim. Devlegate owns:

- worker prompt construction and execution workspace preparation;
- checkpoint commits and execution-branch publication;
- workflow movement and structured execution reports;
- retry and recovery decisions;
- accepted integration into product history.

Worker output is untrusted. The supported result is one strictly validated
`devlegate_report` event. Missing, malformed, duplicate, or technically failed
output does not mark an execution complete. Worker process status is kept
separate from the worker's claim.

Workers do not commit, push, merge, rebase, switch branches, move tickets,
write reports, or integrate into the product branch. Reviewers decide whether
work is accepted; Devlegate performs accepted product integration.

An explicit `devlegate drop <ticket-id>` retires one blocked execution lineage
when its exact execution ID, checkpoint, report, and absent worker ownership
are proven. Drop preserves the worker conclusion as observed and records a
separate `dropped` orchestration disposition. It pins the checkpoint under an
execution-specific evidence ref, retires the validated execution worktree and
per-ticket branch, and clears the active execution binding. It never deletes
or rewrites the current ticket, retargets a report to a later ticket generation,
or purges evidence. A later ticket with the same ID is therefore fresh work.

## Safety Rules

- The product checkout and control worktree must be clean before synchronization
  or scheduling.
- Configured control and workflow paths, ticket metadata, dependency references,
  duplicate IDs, and dependency cycles are validated before dispatch.
- Only canonical ticket filenames are managed; editor artifacts and sentinels are
  not tickets.
- Worktrees are recreated only after branch and workspace identity is proven.
  Devlegate does not reset, force-remove, or steal an unsafe worktree.
- Product and control Git observations are kept separate. A selected execution
  retains its planned product revision and bound control revision.
- Ambiguous Git state, worker ownership, execution identity, or mutable delivery
  fails closed and leaves evidence for inspection.
- Drop does not kill workers, drop live or ambiguous executions, or advance
  handled control-generation markers for state it has not synchronized.

## Shutdown And Recovery

Pre-worker synchronization transactions may resume when their result is proven
from persisted state and Git observations.

A safely normalized `service_shutdown` or `process_loss` execution may resume in
its existing execution workspace with a fresh worker. An `operator_abort` and
ordinary execution failure require explicit retry where supported. Ambiguous
worker ownership, unknown later execution stages, and other unproven states do
not receive unconditional recovery.

The service records shutdown, process loss, and operator abort as different
runtime facts. Restart and retry logic must prove the prior result before
continuing; it must not invent a result or launch a duplicate worker.

## Bootstrap Boundary

Project-side `.env`, `.devlegate/project.md`, templates, rendered role
artifacts, and existing documentation remain owned by the target project.
`devlegate init`, `render`, and `control init` prepare or validate those
boundaries, but project changes are adopted through the project's own
change-management process. See the [pre-install guide](../preinst_readme.md)
for setup and adoption details.
