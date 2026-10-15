---
"type": "devlegate.ticket"
"title": "Unify ticket log reading and isolate follow from unrelated reports"
---

## Disposition: ACCEPTED

Latest execution `f7b88af37af94d469c6e94bd6d4b154b`; checkpoint `507da8f926f92cd3be25ab97b5c382b74bdb78b5`. Exact [CI #38712174046](https://github.com/ranmasao/devlegate/actions/runs/38712174046) SUCCESS: **1216 passed, 1 skipped, Ruff clean**.

Review evidence across the four TASK-066 checkpoints:

- Shared byte-positioned `_LogCursor` replaces the read-tail / reopen-at-EOF race. The cursor retains the initial snapshot EOF and reads concurrent appends without skipping them. Deterministic regression added.
- Ticket-follow does not re-read an already emitted active execution when its report is terminal but SQLite still reports it active. Deterministic transition/retry test added.
- Terminal detection for `follow_ticket` checks the exact ticket report instead of scanning unrelated history; failures of requested report evidence remain fail-closed.
- Exact execution lookup avoids parsing unrelated reports; abbreviated ID lookup enumerates candidates and validates only the selected report, while preserving ambiguous ID rejection. Focused malformed-unrelated/requested tests added.
- Requested dangling report file is rejected, not mistaken for absence. Latest patch verifies safe `control_worktree`, `executions/` and ticket-directory ancestors before treating an absent JSON file as nonterminal, including a dangling `executions/` symlink regression.
- `logs service` remains a separate `journalctl` backend; full command-level unification with file cursors is intentionally not required. Existing journal follow propagates exit code and inherits stderr.
- Four checkpoints reviewed; transient CI failure in an unrelated worker process timing test in the second iteration did not recur. Final exact-checkpoint CI is green.

No logging-framework redesign, execution report schema changes or unrelated worker-process changes were required.

Accepted for integration under normal Devlegate lifecycle.
