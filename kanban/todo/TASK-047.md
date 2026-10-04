---
"type": "devlegate.ticket"
"title": "End execution log follow when the execution becomes terminal"
---

## Milestone

Devlegate 0.5.6 CLI and operator UX.

## Goal

Make:

```text
devlegate logs execution <execution-id> -f
```

follow the lifetime of that exact immutable execution instead of behaving like an
unbounded `tail -f`.

Execution IDs are unique and never reused. Once an execution is terminal, that exact
execution will never start again, so an execution-scoped log follower has a natural
completion boundary.

## Required behavior

For `logs execution <execution-id> -f`:

1. Emit the log content already available.
2. Continue following new content while that exact execution is still non-terminal.
3. When the execution becomes terminal, drain any bytes/lines that were written to
   the log before/around terminalization.
4. Exit after the log has been drained to EOF.

Do not terminate merely because the worker leader PID disappeared. Devlegate owns a
stronger execution lifecycle than a single process PID, and worker-group retirement,
report persistence, checkpointing, and lifecycle processing can outlive the leader.

The follow decision should be based on authoritative Devlegate execution state /
execution records rather than process-name or PID matching.

## Starting states

### Execution already terminal

If `-f` is started after the execution is already terminal:

- print the available log;
- drain to EOF;
- exit immediately;
- do not wait for that execution to become active again.

### Execution active

If the execution is active:

- show current log content;
- continue following it;
- exit automatically when that exact execution reaches terminal state and the log is
  drained.

### Log not created yet

If the execution exists and is active but the log file is not present yet:

- wait for the log to appear while the execution remains active.

If the execution is already terminal and no log exists:

- exit with a clear diagnostic;
- do not wait indefinitely.

## Terminal semantics

Use the existing execution lifecycle/record model. Terminal execution outcomes may
include normal completion and unsuccessful outcomes such as failed, incomplete, or
interrupted execution, as represented by the current execution-report model.

The important invariant is:

```text
this exact execution-id cannot produce a future new run
```

Recovery that continues the same durable execution must not cause premature exit.
A genuinely new execution ID is outside the scope of this command.

## Safety constraints

- Never infer execution completion from PID reuse or process-name matching.
- Do not truncate or lose the final log tail when terminal state is observed.
- Do not follow a later execution merely because it belongs to the same ticket.
- Do not reinterpret `logs execution` as ticket-scoped following.
- Preserve current non-`-f` behavior.

## Acceptance criteria

- Active execution follow exits automatically after terminalization.
- Already-terminal execution with `-f` prints the log and exits.
- Final log output written just before/around terminalization is not lost.
- Recovery of the same execution does not make the follower switch to another
  execution ID.
- A later execution of the same ticket is not followed.
- Missing log + active execution waits safely.
- Missing log + terminal execution exits clearly.
- Existing `logs execution <id>` behavior without `-f` remains unchanged.
- Full tests, coverage, and Ruff remain green.

## Required regressions

Cover at minimum:

- active execution -> terminal -> drain -> exit;
- already-terminal execution;
- final bytes appended immediately before terminal observation;
- worker leader exit before execution terminal state;
- same ticket starts E2 after E1 terminal: follower for E1 exits and never follows E2;
- active execution with delayed log creation;
- terminal execution with missing log.
