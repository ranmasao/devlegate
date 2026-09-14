# Temporary 0.5 documentation notes

This file is a non-canonical working draft created during J1.1.
It preserves technical material removed from README for later J3/J4
architecture and documentation cleanup.

Do not treat this file as current public documentation.
J3/J4 should decide what belongs in canonical architecture, operational,
historical, or other documentation.

## Git Surfaces And Local State

Devlegate keeps three physically separate Git surfaces:

- The operator checkout contains the product branch.
- A Devlegate-owned control worktree contains canonical workflow tickets and
  execution reports.
- A Devlegate-owned execution worktree contains the selected ticket's changes
  and durable execution branch.

Git is the canonical history for product and control changes. Local operational
runtime state is separate. Devlegate stores one opaque state record in a SQLite
database under `STATE_DIR` (by default `$XDG_STATE_HOME/devlegate`, or
`~/.local/state/devlegate`). The database name is derived from repository
identity and is not workflow history.

The control branch defaults to `devlegate/control`. Its worktree is under
`STATE_DIR/worktrees/<repository-key>/control`. A selected ticket uses the
durable `devlegate/work/<ticket-id>` branch for lineage and history, plus a
disposable execution worktree for the active workspace. The execution branch
starts at the exact planned product HEAD.

Control history is never merged or copied into execution or product history.
The control revision binds and authorizes an execution attempt, but the
histories remain separate. Accepted product integration is based on proven
fast-forward ancestry only. Devlegate does not automatically rebase, merge
conflicting histories, resolve merge conflicts, or perform destructive
reconciliation.

## Service Boundary

The production runtime is one foreground service hosting one `ServiceEngine`.
The service host owns the project runtime lock and the project-specific local
Unix IPC endpoint. `ServiceEngine` is the single mutable workflow engine for
workflow decisions, worker execution, and service operations.

Multiple CLI processes may be clients, but they do not independently become
runtime writers. The service serializes mutable operations and owns ticket
movement, worker execution, checkpoint commits, publication, reports, and
product integration.

Mutable requests include `retry` and `reconcile update-base`. The service
admits these requests through its owner loop and acknowledges the exact request
identity. Read-only clients request `status` and `plan` views through IPC when
the service is active.

When no service authority exists, `status` and `plan` can perform a guarded
read-only local observation. If authority is present but IPC is unavailable,
the client fails closed instead of reading or writing around the service. The
IPC endpoint is derived from runtime identity; it is not guaranteed to be
under `STATE_DIR` when platform socket path limits require another location.

## Workflow And Safety Details

Only `todo` tickets whose dependencies are in `done` are runnable. `review` and
`accepted` do not satisfy dependencies. Devlegate selects one runnable ticket
at a time. Completed execution moves the same ticket to `review`; incomplete,
blocked, or failed execution keeps it in `todo` with its evidence. Reviewer
acceptance moves it to `accepted`, and Devlegate integrates accepted work
before moving it to `done`.

The product checkout and control worktree must be clean before synchronization
or scheduling. Devlegate validates configured control and workflow paths and
manages only canonical ticket filenames. Dirty, conflicting, detached,
unsafe, or unexpectedly occupied worktrees fail closed. Worktrees are
recreated only when branch and workspace identity can be proven; Devlegate
does not reset, force-remove, or steal them.

Execution begins with the planned product revision and retains its bound
control revision. A valid prepared generation is not repeatedly materialized
just because worker dispatch remains gated. Product and control Git identities
are observed separately, and a work generation uses both identities plus the
ticket contents that determine the decision.

Ticket frontmatter uses the restricted NanoYAML implementation. Devlegate
validates metadata, dependency references, duplicate IDs, dependency cycles,
and dependency state before dispatch. It does not support free-form tickets or
general YAML compatibility.

## Worker Protocol And Reports

Devlegate owns worker prompt construction. Workers receive one implementation
assignment and a small worker-only contract, not control-worktree paths,
workflow topology, execution IDs, or branch identity.

Worker output is untrusted. Only one strictly validated `devlegate_report` tool
event can provide a semantic claim. Missing, malformed, duplicate, or
technically failed output does not mark work completed. Devlegate keeps worker
process status separate from the worker claim and owns the checkpoint commit
and branch push.

Execution reports are structured records under
`executions/<ticket-id>/<execution-id>.json` in the control worktree. Tickets
remain specifications rather than execution logs; questions and remaining work
stay structured report data.

Workers never commit, push, merge, rebase, switch branches, move tickets,
write reports, or integrate into the product branch. Reviewers do not integrate
product code; product integration is Devlegate-owned.

## Shutdown And Recovery

Operator aborts, orderly service shutdown, and process loss are recorded as
different runtime facts. Pre-worker synchronization transactions may resume
when their result is proven from persisted state and Git observations.

A safely normalized `service_shutdown` or `process_loss` execution may resume
in its existing execution workspace with a fresh worker. An `operator_abort`
and ordinary execution failure require explicit retry where supported.
Ambiguous worker ownership and later or otherwise ambiguous execution states
fail closed rather than receiving unconditional recovery.

The service owns signal handling. A mutable operation is not treated as
accepted unless its result and request identity are established. Durable state
and Git observations are used to distinguish committed effects from uncertain
delivery.

## Development And CI Notes

`./dev check` runs pytest followed by Ruff. `./dev coverage` measures product
code with statement and branch coverage, captures normally terminating Python
subprocesses, combines parallel data, and prints missing lines and branches.
It has no percentage threshold and is not a release gate.

GitHub Actions runs the test suite under coverage and runs Ruff separately.
Successful trusted branch runs generate the disposable Shields coverage
payload; failed runs do not replace the last published measurement.
