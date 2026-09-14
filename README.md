# Devlegate

[![CI](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml/badge.svg?branch=feature%2Fv0.5.0-state-control-separation)](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ranmasao/devlegate/badges/coverage.json)](https://github.com/ranmasao/devlegate/actions/workflows/ci.yaml)

Devlegate is a local deterministic orchestrator for agent-driven software
development workflows. It keeps product history, control-plane workflow, and
per-ticket execution work separate while one foreground service owns mutable
runtime activity.

## Architecture

Devlegate uses three Git surfaces:

- The operator checkout contains the product branch.
- A Devlegate-owned control worktree contains canonical workflow tickets and
  execution reports.
- A Devlegate-owned execution worktree contains the selected ticket's worker
  changes and durable execution branch.

Git remains the canonical history for product and control changes. Local
operational runtime state is separate: Devlegate stores one opaque state record
in a SQLite database under `STATE_DIR` (by default
`$XDG_STATE_HOME/devlegate`, or `~/.local/state/devlegate`). The database name
is derived from the repository identity and is not workflow history.

### One Service Authority

The production runtime is one foreground service hosting one `ServiceEngine`.
The service host owns the project runtime lock and Unix IPC endpoint;
`ServiceEngine` is the single mutable workflow engine. Multiple CLI processes
may act as clients, but they do not independently become runtime writers.

The service exposes a project-specific local Unix IPC endpoint derived from the
runtime identity. Read-only clients request observations through that endpoint
when the service is active. `status` and `plan` can also perform a guarded,
read-only local observation when no service authority exists. If authority is
present but IPC is unavailable, the client fails closed instead of reading or
writing around the service.

Mutable client commands are submitted to the service and admitted by its owner
loop:

- `retry` submits an explicit retry request.
- `reconcile update-base` submits a pending product-base reconciliation.
- `status` and `plan` request service-owned views when the service is active.

The service serializes mutable operations. Concurrent observation clients are
supported, and an authority or ownership condition that cannot be proved is
treated as unsafe.

## Usage

Install this unreleased checkout with:

```sh
python -m pip install -e /path/to/devlegate
```

Run the commands from the target repository root. Configure at least
`OPENCODE_MODEL` and the managed workflow paths in `.env` when they differ
from their defaults. An existing `.env` is never rewritten. The target project
must have its normal Git remote and authentication configured.

### Bootstrap and Preflight

```sh
devlegate init [--conflicts abort|backup|replace]
devlegate render
devlegate render --check
devlegate control init
devlegate check
```

`init` seeds project-local protocol templates, `.env` when needed, and the
incomplete `.devlegate/project.md` project-context adapter. It does not start
the runtime service. `render` produces the declared project-owned artifacts;
`render --check` verifies them without writing. `control init` attaches or
creates the configured control branch and worktree. `check` validates
configuration, checkout state, control topology, and workflow readiness without
running a worker.

The project owns `.env`, `.devlegate/project.md`, its templates, and rendered
artifacts. Devlegate preserves existing project files. Project context names
documents by reference using restricted NanoYAML; it does not copy their
contents into the adapter. NanoYAML supports one-line JSON-compatible flow
sequences, but is not a general YAML implementation.

### Start the Service

```sh
devlegate run
devlegate daemon
devlegate run --once
```

`run` hosts the foreground workflow service until it is stopped. `daemon` is
the alternate foreground service command name and does not detach or
daemonize the process. `run --once` hosts the service for one synchronization
and execution pass, then exits. None of these commands starts a hidden
background process or a persistent warm-worker pool.

### Observe and Mutate

```sh
devlegate status
devlegate status --json
devlegate plan
devlegate plan --json
devlegate retry
devlegate retry <ticket-id>
devlegate reconcile update-base <ticket-id> --onto <branch>
```

`status` reports the current service-owned observation when a service is
running, or performs a guarded read-only observation when no authority exists.
It returns a nonzero status when the observed plan is blocked. `plan` reports
what Devlegate would decide from one consistent snapshot and does not mutate
runtime state.

`retry` requires the foreground service. With no ticket ID it presents the
service-provided retry candidates in an interactive terminal; with a ticket ID
it submits that request directly. `reconcile update-base` also requires the
service and submits the requested pending product-base reconciliation. A
request is not treated as accepted unless the service acknowledges its exact
identity.

All commands accept `--env FILE` where supported to select configuration other
than `$PWD/.env`. `--version` prints the installed version.

## Workflow

```text
Architect or Reviewer
    -> publishes tickets to the control branch
    -> foreground service observes and validates workflow state
    -> service selects one runnable todo ticket
    -> service runs one worker in an execution worktree
    -> service checkpoints and publishes the execution branch
    -> service records the report and moves completed work to review
    -> Reviewer evaluates the result and moves accepted work to accepted
    -> service fast-forwards accepted product history and moves it to done
```

Only `todo` tickets whose dependencies are in `done` are runnable. Completed
work goes to `review`; incomplete, blocked, or failed work remains in `todo`
with its execution evidence. Review and product integration remain separate
workflow stages.

## Shutdown and Recovery

The service runs in the foreground and owns signal handling. Operator aborts,
orderly service shutdown, and process loss are recorded as different runtime
facts. Pre-worker synchronization transactions may resume when their result is
proven from persisted state and Git observations. A safely normalized
`service_shutdown` or `process_loss` execution may resume in its existing
execution workspace with a fresh worker. An `operator_abort` and ordinary
execution failure require explicit retry where supported; ambiguous worker
ownership and later or otherwise ambiguous execution states fail closed rather
than receiving unconditional recovery.

When ownership, Git identity, execution identity, or the result of a mutable
operation is ambiguous, Devlegate fails closed and keeps the evidence for
inspection. Use `retry` or the appropriate `reconcile update-base` operation
only after the current status establishes that the request is safe.

## Safety Model

- The product checkout and control worktree must be clean before synchronization
  or scheduling. Devlegate does not destroy local changes or reconcile
  divergent history automatically.
- The configured control and workflow paths are validated before use. Only
  canonical ticket filenames are managed; editor artifacts and sentinels are
  not tickets.
- `CONTROL_BRANCH` defaults to `devlegate/control`. Its worktree is under
  `STATE_DIR/worktrees/<repository-key>/control`; status, plan, check, and run
  do not create it as a side effect of observation.
- Each selected ticket uses a durable `devlegate/work/<ticket-id>` execution
  branch for lineage and history, plus a disposable Devlegate-owned execution
  worktree for the active workspace. Execution starts at the exact planned
  product HEAD and retains its bound control revision.
- The control revision binds and authorizes execution; control history is never
  merged or copied into execution or product history. Accepted product
  integration is based on proven fast-forward ancestry only.
- Worktrees are recreated only when their branch and identity can be proven.
  Dirty, conflicting, detached, unsafe, or unexpectedly occupied worktrees
  fail closed without reset, force removal, branch stealing, rebasing, merge
  conflict resolution, or destructive reconciliation.
- Ticket frontmatter uses restricted NanoYAML. Devlegate validates metadata,
  dependency references, duplicate IDs, dependency cycles, and dependency
  state before dispatch.
- Tickets move through `backlog -> todo -> review -> accepted -> done`.
  Devlegate selects one runnable ticket at a time and does not treat review or
  accepted as satisfying dependencies.
- The service owns ticket movement, worker execution, checkpoint commits,
  publication, reports, and product integration. Workers never commit, push,
  merge, rebase, move tickets, write reports, or integrate product history.
- Worker output is untrusted. Only one strictly validated `devlegate_report`
  event can provide a semantic claim; malformed, missing, duplicate, or
  technically failed output does not mutate workflow state as completed work.
- Execution reports are structured records in the control worktree. Tickets
  remain specifications, not execution logs.
- The service lock and IPC endpoint establish one mutable authority per project
  identity. Read-only fallback uses a shared absence guard and is refused when
  service authority is present but cannot be reached.
- SQLite is operational service state. Git remains canonical for product and
  control history; neither storage layer is silently substituted for the other.
- Pre-worker synchronization may resume only when its result is proven from
  persisted state and Git observations. Ambiguous execution ownership and
  mutable delivery remain fail-closed.

## Development Setup

For contributors working on this repository:

```sh
./dev setup
```

This creates `.venv`, installs Devlegate in editable mode, and installs the
pytest, Ruff, and coverage development tools.

## Development Commands

```text
./dev setup             Create the environment and install dependencies
./dev test              Run pytest
./dev lint              Run Ruff check
./dev check             Run normal tests and Ruff
./dev coverage          Run tests once with statement/branch/subprocess coverage
./dev run [args...]     Run the installed Devlegate CLI
./dev clean             Remove named caches and development artifacts
./dev purge             Also remove .venv and named development artifacts
```

`./dev check` is the normal local correctness command: pytest followed by
Ruff. `./dev coverage` is an explicit diagnostic command. It measures product
code with coverage.py, captures normally terminating Python subprocesses,
combines parallel data, and prints per-module missing lines and branches. It
has no percentage threshold and is not a release gate.

GitHub Actions runs the full test suite once under coverage and runs Ruff
separately. Successful trusted branch runs generate the disposable Shields
coverage payload; failed runs do not replace the last published measurement.

Devlegate 0.5 remains an unreleased development architecture. Background
daemonization, system service integration, warm workers, general parallel
workers, and full YAML compatibility are outside this scope.
