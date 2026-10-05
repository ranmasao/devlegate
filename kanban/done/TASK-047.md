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


### First review: execution-follow semantics are sound; one new regression test breaks CI

Execution `e0c3b759e31a4bdf8997d332b9cc5e96` / checkpoint
`5c0dc12e1e856a34ea545a4483015be6a112099d` implements the intended
execution-scoped follow behavior.

The production design is appropriate:

- follow is pinned to one immutable execution ID;
- active state comes from the Devlegate runtime store rather than PID/process-name
  matching;
- recoverable execution stages remain under `phase=agent_running`, so
  checkpointing/post-checkpoint/publication/lifecycle recovery does not prematurely
  end follow;
- terminality is proven by the durable execution report;
- terminal follow drains the file to EOF and exits;
- an already-terminal execution exits after printing available log content;
- a missing active log waits for creation;
- a missing terminal log fails clearly;
- later executions of the same ticket are not selected.

The added tests cover the required behavioral matrix, including worker-leader exit
before the durable execution report.

#### Only current blocker: the new same-ticket test helper is not reusable

Authoritative CI run `37231222744` fails:

```text
1 failed, 1129 passed, 1 skipped
```

The only failure is:

```text
tests/test_log_reader.py::
test_execution_follow_does_not_switch_to_later_ticket_execution

FileExistsError:
.../control/executions/same-ticket
```

The test intentionally creates two execution reports for the same ticket, but the
local helper does:

```python
root = control / "executions" / ticket
root.mkdir(parents=True)
```

on each call. The second report therefore fails before exercising
`follow_execution()`.

Fix the helper or this test setup so multiple reports for one ticket are valid, for
example by making the directory creation idempotent. Do not weaken the test: it must
still create E1 and E2 for the same ticket and prove that following E1 never emits
E2's log.

No production-code change is requested unless the corrected test exposes another
defect.

### Acceptance boundary

Return to review when:

```text
same-ticket E1/E2 regression executes and passes
full coverage+xdist CI: green
Ruff: green
```
