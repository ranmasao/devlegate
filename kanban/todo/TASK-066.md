---
"type": "devlegate.ticket"
"title": "Unify ticket log reading and isolate follow from unrelated reports"
---

## Review: focused REWORK required (retain checkpoints)

Latest execution `fc9af7d43447468181f0b664a22e780c`, checkpoint `489005b39a0d4818bef905dfc811612afd92f728`; previous checkpoint `6daa401f3731e4f6a9e837a5966458d07053751e`. **Preserve all existing work.** The two previous blocking defects are fixed:
- `_LogCursor._tail()` uses captured `size` rather than a second EOF seek, so appends during tail assembly are delivered exactly once.
- `follow_ticket()` does not replay an already printed execution while a terminal report is published but SQLite still reports the old execution active.
- Both fixes have focused deterministic tests. Scoped ticket report lookup and shared cursor are retained.

## Remaining substantive acceptance gaps

1. **Prefix execution lookup still depends on every unrelated report being valid.** `execution_id()` at `src/devlegate/log_reader.py:85-88` still calls unfiltered `ExecutionReportStore(control_worktree).list()`. Thus `logs execution <unique-prefix>` aborts if any unrelated historical report is corrupt, despite the task's cross-command failure isolation goal. Resolve prefix uniqueness from safely enumerated candidate IDs/names and validate only matched reports (plus active execution); retain ambiguity rejection and fail-closed behavior for requested candidate corruption, identity mismatch and unsafe paths. Add focused tests: unrelated malformed report cannot block a unique requested prefix; requested malformed report does fail.
2. **Unsafe exact requested report may be misclassified as missing.** `_execution_report(control_worktree, execution, ticket_id)` catches `ExecutionReportError`, then checks `report_path.exists()` before deciding to ignore it. `Path.exists()` is false for a dangling symlink; a malformed **requested** report represented by a dangling symlink can be wrongly treated as absent. Fail closed for symlinks, unsafe report directories, invalid report content/artifacts and other non-absence read failures. Only genuinely absent reports may be treated as not terminal. Add a focused dangling-symlink test for requested execution.
3. **Cross-command parity and journal follow audit still need evidence.** Verify equal initial output for `logs ticket T` and `logs ticket T -f` with historical + active executions, including `--lines=0` and a retry transition, without skipped/duplicated data. Add only narrow missing tests. `logs execution` must share the same cursor semantics. Inspect `stream_journal(..., follow=True)` stderr/exit-status/interruption handling and document why retained behavior is correct or fix concretely observed defect. `cli.py` may remain a thin dispatcher if justified; do not build a generic streaming framework.

## CI clarification

[Exact-checkpoint Actions #38703433750](https://github.com/ranmasao/devlegate/actions/runs/38703433750) failed: 1211 passed, 1 skipped, 1 failed. The failure is `tests/test_worker_process.py::test_worker_process_group_isolated_and_interrupts_descendant[operator_abort]`, which reads a just-created empty `processes.json` before the child finishes writing JSON. This appears to be an unrelated pre-existing test race, **not** a proven log-reader regression. Do not expand this task into a worker-process refactor. Verify the relevant log tests, Ruff, and full exact-checkpoint CI after the targeted fixes; if the same unrelated race recurs, report the evidence rather than misclassifying it.

## Constraints and acceptance

Preserve report schema, canonical ticket state semantics (accepted is not done), ticket-scoped failure isolation, per-execution EOF draining, cursor no-loss/no-dup guarantees, and missing-yet-expected log handling. Retain the prior minimal implementation and test seams. Do not self-declare completion merely because the previously noted two issues were fixed. Reviewer assesses full behavior and exact-checkpoint CI.
