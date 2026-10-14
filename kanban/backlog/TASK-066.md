---
"type": "devlegate.ticket"
"title": "Isolate live log following from unrelated historical execution reports"
"depends_on": []
---

## Milestone

Devlegate 0.5.7 — operator log-reader regression fix.

## Reproduction

While TASK-061 is actively running, `devlegate @devlegate logs ticket TASK-061`
prints its current execution log, but `devlegate @devlegate logs ticket TASK-061 -f`
fails with:

```text
devlegate: cannot read execution report: .../control/executions/TASK-054/2a08fb43fa6d42308cc73f24c849b918.json
```

The active execution has ID `5e566f156c1a4093885abcd4ce72bffc`.
An unrelated historical TASK-054 report should not prevent following its log.

## Root cause to verify

`src/devlegate/log_reader.py`:
- `ticket_log()` and the main `follow_ticket()` loop already use ticket-scoped
  `ExecutionReportStore.list(ticket_id)`.
- But `follow_ticket()` delegates to `follow_execution()`, which calls
  `_execution_terminal(control_worktree, execution)` repeatedly.
- `_execution_terminal()` calls `ExecutionReportStore.list()` **without a
  ticket filter** to detect a single execution's terminal report.
- The global list validates unrelated report files; one missing, malformed,
  or inaccessible historical report thus aborts the otherwise valid log stream.
- `execution_id()` similarly resolves execution prefixes by scanning all
  reports; review scope and failure-isolation behavior there, without
  allowing ambiguous IDs or unsafe path traversal.

## Required behavior

- Following a specific active execution, or a ticket's current execution,
  must not depend on validity or availability of unrelated reports.
- Terminal detection must use a narrowly scoped, validated exact execution
  identity/binding, or an equally reliable local index. Preserve fail-closed
  behavior for corruption or invalid evidence *belonging to the requested
  execution/ticket*; do not globally suppress `ExecutionReportError`.
- Do not change the durable report schema or destroy/repair historical reports.
- Preserve end-of-log draining, ticket lifecycle behavior (including accepted
  as intermediate), multi-execution replay, and non-follow logging.
- Add regression tests: an unrelated invalid/missing historical report must
  not prevent following a live execution or ticket; malformed requested
  execution evidence must still produce an appropriate error. Check
  execution-prefix lookup behavior separately.

## Constraints

Do not modify active TASK-061 execution worktrees, alter the published 0.5.6
tag/release, or bypass the normal kanban execution/review process. Keep the
fix focused; broader runtime recovery restructuring belongs to TASK-061.

## Verification

Run focused log-reader tests and lint. Reviewer checks CI on exact checkpoint.
