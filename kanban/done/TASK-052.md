---
"type": "devlegate.ticket"
"title": "Make ticket log follow span executions until ticket completion"
"depends_on": ["TASK-048"]
---

## Milestone

Operator log UX correctness.

## Goal

Make `devlegate logs ticket <ticket> -f` follow the lifecycle of the ticket rather
than requiring an already-persisted execution-report directory or stopping after
one execution/review cycle.

The command must print existing execution history, follow the exact active execution
when one exists, wait safely while the ticket has no active execution, attach to
later executions of the same ticket, and exit only when the ticket reaches the
current workflow's terminal `done` state.

## Context

TASK-048 introduced ticket-oriented log following, but the current implementation
has two incorrect assumptions.

First, `ExecutionReportStore.list(ticket_id)` treats a missing
`executions/<ticket>/` directory as unsafe:

```python
if root.is_symlink() or not root.is_dir():
    raise ExecutionReportError(
        "execution report ticket directory is unsafe"
    )
```

For a valid ticket with no completed execution report yet, absence of that directory
means only that durable execution history is empty. It must not fail closed.

Second, ticket follow currently exits on `accepted` as well as `done`. Accepted
is not the terminal lifecycle state: Devlegate may still need to finalize/integrate
the accepted result before moving the ticket to done.

The intended abstraction is ticket-oriented observation:

```text
existing durable execution logs
        |
        v
active execution, if any -------- follow exact execution
        |                              |
        |                              v
        +------------------------- execution ends
        |
        v
ticket still non-terminal -------- wait for state/runtime change
        |
        +---- later execution appears -> follow it
        |
        +---- ticket reaches done ----> drain known output and exit
```

A failed/incomplete execution, review->todo cycle, or a period with no active worker
must not terminate `-f`.

## Required behavior

### Execution-report history

- `ExecutionReportStore.list(ticket_id)` returns an empty tuple when the validated
  report root exists but the exact ticket directory does not yet exist.
- A ticket report path that exists as a symlink or as a non-directory object still
  fails closed.
- Existing report files retain the current symlink, identity, artifact, and digest
  validation.
- Do not weaken root-level execution-report safety checks.

### Non-follow ticket logs

- `devlegate logs ticket <ticket>` prints all existing durable execution logs for
  that ticket in deterministic chronological order and exits.
- A valid ticket with no durable executions is not an error; it simply has no
  execution-history output.
- Non-follow mode does not wait for a future execution.

### Follow mode

- `devlegate logs ticket <ticket> -f` first prints all existing durable execution
  history.
- If the ticket has an active execution not already represented by the printed
  history, attach to that exact execution and follow it through its terminal
  execution state.
- After that execution ends, return to ticket observation rather than terminating.
- If the ticket is non-terminal and has no active execution, wait and poll for either:
  - a later active execution of the same ticket;
  - newly durable history/state advancement;
  - terminal ticket completion.
- Review, todo, backlog-like waiting states, and accepted are not by themselves
  reasons to exit follow mode.
- Failed, incomplete, blocked, interrupted, or otherwise terminal worker executions
  do not terminate ticket follow while the ticket itself remains non-terminal.
- If a later retry/rework execution starts for the same ticket, print its execution
  header once and follow it automatically.
- Never attach to an active execution belonging to another ticket.
- Never print the same execution history twice when an active execution later gains
  its durable execution report.
- On the current canonical workflow, exit only when the ticket reaches `done`,
  after any already-followed execution output has been drained.
- Keep the implementation structured so a future declarative workflow can replace
  the hard-coded terminal-state predicate; do not implement that future workflow
  model in this ticket.

### Waiting and safety

- Waiting with no active execution must be bounded polling/observation, not a busy
  loop.
- Preserve current exact execution-log path safety and runtime execution identity
  validation.
- Ticket disappearance, ambiguous ticket placement, malformed runtime state, or
  unsafe report/log paths still fail closed with a precise diagnostic.

## Acceptance criteria

- A newly executing ticket with no `executions/<ticket>/` directory can be followed
  immediately.
- A valid ticket with no previous executions and no current execution can be
  followed; the command waits instead of reporting the directory as unsafe.
- Existing execution history is printed before following a current execution.
- E1 completion followed by review/todo waiting and later E2 automatically switches
  the same `logs ticket -f` session to E2.
- An incomplete/failed/blocked E1 does not end follow mode if the ticket remains
  non-terminal.
- Accepted without an active execution waits for finalization rather than exiting.
- Done causes clean exit.
- A report directory that is a symlink or non-directory still fails closed.
- Non-follow behavior remains immediate and never waits.
- Runtime remains stdlib-only.

## Required regressions

- ticket exists + report directory absent + active E1 -> follow E1.
- ticket exists + report directory absent + no active execution + no `-f` -> empty
  successful history result.
- ticket exists + report directory absent + no active execution + `-f` -> wait;
  when E1 appears, attach to E1.
- historical E1 + active E2 -> print E1 once, then follow E2.
- active E1 becomes durable after completion -> do not print E1 a second time.
- E1 terminal + ticket review/todo + later E2 -> continue and follow E2.
- ticket accepted + no active execution -> continue waiting.
- ticket done -> exit after draining already-observed output.
- active execution for another ticket -> ignore it.
- `executions/<ticket>` symlink -> fail closed.
- `executions/<ticket>` regular file/non-directory -> fail closed.
