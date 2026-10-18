---
"type": "devlegate.ticket"
"title": "Complete shared ticket and execution log resolution without parallel read algorithms"
"depends_on": ["TASK-066"]
---

## Why this follow-up exists

TASK-066 was accepted and integrated with green CI and genuine bug fixes, but its original DRY acceptance was **not fully audited or fulfilled**. It extracted a correct shared byte-positioned `_LogCursor`, but retained parallel orchestration of ticket/execution selection, active-state polling and directory traversal. Do not reopen or undo the integrated TASK-066 work. This task closes only the remaining demonstrable duplication and call-path inconsistency.

## Current duplication / evidence (release/0.5.7 at 507da8f926f9)

- `src/devlegate/log_reader.py`: `ticket_log()` (~286) and `follow_ticket()` (~311) separately enumerate reports, resolve active execution, build history output and choose whether to use `_read_file()` or `_follow_execution()`. They share helpers but **do not share one coherent execution-selection model**. This can cause observable initial-output differences when history + current execution overlap.
- `_current_execution_id()` (~47), `_active_ticket_execution()` (~259), and `_execution_active()` (~437) separately read `SQLiteRuntimeStore.load()` and repeat phase/identity checks.
- `_execution_candidates()` (~112) and the unscoped `_execution_report()` (~449) each enumerate the `executions/<ticket>/` directories with partly duplicated validation rules. For `logs execution -f`, `_execution_terminal()` can repeat that unscoped directory walk roughly every 100 ms when `ticket_id` is not supplied; identify execution and owning ticket only once where possible, then use targeted exact report checks.
- `execution_log()` (~215), CLI follow resolution at `src/devlegate/cli.py` (~2281), and `follow_execution()` (~568) do not share a single resolved execution-target abstraction. Keep the same public CLI and minimal number of calls; avoid redundant work.
- `follow_file()` (~421) is not currently invoked by CLI. Verify all call sites and either remove the unused entry point with appropriate test updates or justify why it is intentionally retained; avoid a third independent file-follow engine.
- `journalctl` is a distinct backend. Shared command construction already exists; do not force journal subprocess streaming into file cursor semantics.

## Worker test scope — focused verification, full CI afterward

Use `tests/TEST_BOUNDARIES.md` and `tests/TEST_PLACEMENT.md`. The log reader and its CLI entrypoints are the primary domains; prefer:

```sh
./dev test -q tests/test_log_reader.py tests/test_output.py
./dev test -q tests/test_cli_cross_layer.py -k "log or follow"
./dev lint
```

Add/modify focused tests for the requested initial snapshot/follow parity, bounded polling, corrupt evidence and missing-line cases. Add CLI tests for explicit successful snapshot completion for both ticket and execution logs (including empty output and `--lines=0`) and for no success marker on errors or `--follow`. If CLI/IPC behavior actually changes, select the relevant tests from `tests/test_cli_ipc.py` or `tests/test_cli_topology.py` by name/`-k`, rather than running those entire modules. Ensure measurements cover the complete duplicated resolution algorithm, not only the shared file cursor.

**Do not run by default:** unfiltered `./dev test`, `./dev test-parallel`, `./dev check`, `./dev coverage`, all of the topology suite or unrelated packaging tests. Full exact-SHA pytest with coverage and Ruff is supplied by GitHub Actions after push. The worker documents scoped tests and DRY evidence; the reviewer independently verifies CI and the actual consolidation.

## Required work

1. Inventory before/after actual reuse, including each of the call paths above; distinguish *necessary* differences (snapshot vs follow, ticket vs execution lifetime) from duplicated resolution, validation and common initial-output logic.
2. Consolidate actual duplicated **ticket/execution selection and active-state observation** into a small set of cohesive helpers. Normal log read is the initial finite portion of the *same* conceptual stream; `--follow` continues it, not a separately defined history algorithm. It is acceptable to retain separate public entry points and file/journal process loops if they delegate shared semantic decisions.
3. Avoid rescanning the whole `executions/` tree on each follow poll when the execution's ticket binding can be established and preserved. Share safe candidate enumeration/validation logic, keep requested corrupt evidence fail-closed, and ensure unrelated malformed reports cannot block a bound ticket/execution.
4. Preserve the integrated `_LogCursor` and precisely once handoff; avoid extra file opens, hidden polling amplification, repeated report parsing or new instrumentation frameworks. Handle active/terminal SQLite transition, retries, missing expected logs, accepted-before-done and multiple executions without regressions.
5. Add narrowly focused **parity tests** proving initial output equality for `logs ticket T` vs `logs ticket T -f` and `logs execution E` vs `logs execution E -f` for the same `--lines` and stable snapshot, including existing historical reports plus active execution, `--lines=0`, report-before-SQLite handoff, and subsequent append. Avoid sleeping/flaky integration timing. Retain malformed-unrelated/requested report tests.
6. Keep `cli.py` thin, no generic LogSource/streaming framework, no new dependencies, no cosmetic forwarding layers or Protocol expansion. Do not change report schema, paths or the externally visible command syntax.
7. For successful finite `logs ticket T` and `logs execution E` invocations **without `-f` / `--follow`**, always finish with an explicit, visually recognizable normal-completion notice. Its meaning is **the requested log snapshot was fully printed and the CLI command exited normally**, not that the ticket, worker execution, or underlying log has reached a terminal state. Place this CLI presentation notice **after** the log body, preferably on stderr to keep stdout as unmodified log data (with explicit flush/order handling). Cover empty output, `--lines=0`, historical/completed executions, and currently active executions; do not emit a success notice on a read/validation error, truncation failure, or other unsuccessful command. Follow mode does not emit this finite-snapshot completion notice. Do not introduce duplicate log tail reads, additional state polling, or a new backend abstraction for the notice. Preserve stdout's initial snapshot parity with follow mode; treat the notice as out-of-band CLI presentation, not a line in the log stream.

## Acceptance for curator

- Provide a compact map of former duplicated blocks to actual shared implementations; specify distinct logic intentionally retained and why. **An `_LogCursor` shared beneath otherwise parallel history-selection algorithms does not alone satisfy this task.**
- Quantify whole-directory enumeration and SQLite/report observation frequency for representative snapshot/follow flows, before/after. No new hidden polling, file opens or semantic side effects.
- Demonstrate identical initial log data on stdout and no duplicate/missing lines across the tested modes and transitions. A separate CLI snapshot-completion diagnostic must not be counted as log content or break the initial stdout parity check.
- Both `logs ticket T` and `logs execution E` without `-f` terminate successful finite output with a clearly visible completion notice, even for zero log lines; the notice distinguishes **snapshot output completion** from **ticket/execution completion**. No false success notice appears on errors or in follow mode. Verify stdout/stderr separation and that the notice is shown after the log body.
- Full exact-checkpoint pytest and Ruff green, plus focused regression evidence. No closure based solely on CI without DRY inspection.
