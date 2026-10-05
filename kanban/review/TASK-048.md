---
"type": "devlegate.ticket"
"title": "Add ticket-scoped log viewing and follow through review cycles"
---

## Milestone

Devlegate 0.5.6 CLI and operator UX.

## Goal

Add:

```text
devlegate logs ticket <ticket-id>
devlegate logs ticket <ticket-id> -f
```

A ticket may have multiple executions because review can return it to TODO and cause
another worker execution. Ticket-scoped logs should present that execution history as
one operator-facing stream.

The terminal boundary for `logs ticket <ticket-id> -f` is the ticket reaching
`accepted`.

Do not wait for `done`: `accepted -> done` is Devlegate integration/publication,
not further worker implementation of the ticket.

## Non-follow mode

For:

```text
devlegate logs ticket <ticket-id>
```

show all known executions for the ticket in deterministic chronological order.

Separate executions clearly, for example with an execution header containing at
least:

- execution ID;
- conclusion/state when known.

Do not concatenate executions without an identity boundary.

## Follow mode

For:

```text
devlegate logs ticket <ticket-id> -f
```

the command follows the implementation lifetime of the ticket:

```text
todo -> E1 -> review
             |
             +-> todo -> E2 -> review
                         |
                         +-> ... -> review -> accepted
```

Required behavior:

1. Show existing execution history.
2. If an execution is active, follow it.
3. When that execution ends, remain attached to the ticket.
4. If review returns the ticket to `todo` and a new execution starts, follow the new
   execution automatically.
5. Continue across any number of TODO/review/retry cycles.
6. Exit when the ticket first reaches `accepted`, after draining the current
   execution log/history already available.

## Ticket-state semantics

### already accepted or done

If the ticket is already `accepted` or `done` when `-f` starts:

- print the known execution history;
- exit immediately.

### review

If the ticket is in `review`:

- keep waiting;
- the reviewer may return it to TODO and a later execution may start;
- or the reviewer may move it to `accepted`, which terminates follow.

### todo

If the ticket is in TODO without an active execution:

- wait for the next execution.

### failed/incomplete individual execution

A failed, incomplete, interrupted, or otherwise terminal individual execution does
not terminate ticket follow while the ticket remains before `accepted`.

### done

Treat `done` as terminal as well for callers attaching after integration or racing
with a fast accepted->done transition.

## Identity and ordering

Ticket follow must track executions by their immutable execution IDs.

It must not:

- replay the same execution twice;
- lose an execution that appears between observations;
- follow an execution belonging to another ticket;
- assume there is only one execution per ticket.

Use durable control/execution history as the source of truth, not filename mtime
alone.

## Safety constraints

- `accepted` is the implementation-completion boundary.
- Do not wait for `done` once `accepted` is observed.
- Do not make ticket follow depend on a worker PID.
- If the ticket disappears or control history becomes ambiguous, fail closed with a
  clear diagnostic instead of waiting forever.
- Preserve `logs execution` semantics as execution-scoped.
- Do not hide execution boundaries from the user.

## Acceptance criteria

- `logs ticket <id>` prints all known execution logs in deterministic order.
- Each execution is clearly identified.
- `-f` follows the current execution and automatically switches to later executions
  of the same ticket.
- Review -> TODO -> new execution is followed without restarting the CLI command.
- Review -> accepted terminates follow.
- Starting on accepted/done prints history and exits.
- A terminal execution while ticket is still TODO/review does not terminate ticket
  follow.
- No execution is duplicated or skipped across observation races.
- Unknown/disappeared/ambiguous ticket state fails clearly.
- Full tests, coverage, and Ruff remain green.

## Required regressions

Cover at minimum:

- one-execution ticket history;
- multiple execution history with stable ordering;
- active E1 -> review -> TODO -> E2 -> review -> accepted;
- execution failure followed by later retry execution;
- start follower while ticket is in review;
- start follower when ticket is already accepted;
- accepted -> done race;
- no active execution while TODO;
- no duplicate output when polling/observing the same execution repeatedly;
- ticket/control ambiguity fail-closed.
