# Devlegate Architecture

This document describes the current unreleased 0.5 architecture. It is a
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

## Runtime Service

The production runtime is one persistent service hosting one `ServiceEngine`.
The service host owns the project runtime lock and local Unix IPC endpoint.
`ServiceEngine` is the single mutable workflow engine: it makes workflow
decisions, runs workers, and performs service operations.

Multiple CLI processes may observe the service or submit supported operations.
Mutable operations are admitted and serialized by the service owner. The
service owns ticket movement, worker execution, checkpoint commits, publication,
reports, and accepted product integration.

Bare `devlegate` starts the persistent service in the background. `devlegate
--foreground` hosts the same service in the current terminal, and `devlegate
--once` performs one synchronization and execution pass. `devlegate stop`
requests orderly shutdown through the service IPC endpoint. The service is
managed directly by Devlegate and does not require an external service manager.

## Runtime State

SQLite stores local operational runtime state. This state records active
bookkeeping such as execution progress and request handling; it is not the
canonical product or workflow history.

By default the state is under `$XDG_STATE_HOME/devlegate`, or
`~/.local/state/devlegate` when `XDG_STATE_HOME` is unset. `STATE_DIR` can
override the location. State and Git observations are used together when the
service must establish whether a previous operation took effect.

## IPC And CLI Clients

The service exposes a project-specific local Unix IPC endpoint derived from the
runtime identity. `status` and `plan` use the service-owned view when the
service is active. When no service authority exists, those commands can take a
guarded read-only observation directly.

If service authority exists but IPC cannot safely be used, the client fails
closed rather than reading or writing around the service. `retry` and
`reconcile update-base` are mutable operations and go through the service;
they do not construct a separate mutable CLI runtime.

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
