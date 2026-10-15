---
"type": "devlegate.ticket"
"title": "Unify ticket log reading and isolate follow from unrelated reports"
---

## Review: REWORK REQUIRED (preserve checkpoint)

Execution `0be9c1f22dd441b484a3e05d13d57fbd`, checkpoint `6daa401f3731e4f6a9e837a5966458d07053751e`. [CI #38699189510](https://github.com/ranmasao/devlegate/actions/runs/38699189510) SUCCESS: 1210 passed, 1 skipped, Ruff clean.

**Retain the existing work.** Its ticket-scoped terminal report lookup and shared `_LogCursor` are useful and the original unrelated malformed-report regression is addressed. Correct concrete streaming regressions before acceptance.

### Blocking defects

1. **Lost appended bytes at initial-tail/follow handoff.** `src/devlegate/log_reader.py`, `_LogCursor._tail()`: obtains original EOF size, reads the tail, then unconditionally `handle.seek(0, 2)` at the end. Bytes appended between taking the size and this second EOF seek disappear from both initial output and subsequent `read_available()`. Position the cursor at the original captured EOF boundary (after reading the snapshot), not the *new* EOF. Test deterministic append during tail assembly, verifying exactly-once output.
2. **Re-following a still-active but already terminal execution can duplicate tail (and spin).** `follow_ticket()` calls `_follow_execution(..., lines)` on every pass, even after the ID entered `printed`. When its execution report has been persisted but the SQLite phase remains `agent_running`, `_follow_execution()` returns immediately, `follow_ticket()` sees the same active ID again and prints that tail repeatedly. Preserve the cursor/handoff result and wait for state transition without replay; test this exact report-before-SQLite transition and later execution transition.
3. **Honor identical initial-output semantics** of `logs ticket T` vs `logs ticket T -f`, and likewise execution non-follow/follow, including existing historical reports plus active execution, `--lines=0`, incomplete final lines and concurrent append. Validate log truncation/rotation behavior if supported; do not claim unsupported behavior.

### Remaining original scope

- `logs execution <prefix>` still calls unfiltered `ExecutionReportStore.list()`. Review whether an unrelated malformed historical report may block a uniquely identifiable requested execution and propose a scoped lookup without weakening ambiguity detection; exact full-ID lookup is already improved.
- `stream_journal(..., follow=True)` retained its old stderr/process cleanup handling. Investigate and align follow/non-follow diagnostics and interruption cleanup with focused tests; do not force journalctl into the file cursor architecture.
- `cli.py` may legitimately remain unchanged if its dispatch is already thin, but assess and document whether the original requested cross-command selection/follow unification is actually satisfied.
- Keep requested evidence fail-closed; no schema changes, no unsafe symlink/path handling, no swallowing corrupt reports belonging to the requested ticket; keep retries, accepted-before-done and end-of-log draining correct.

### Acceptance

Fix at least the two concrete blocking defects with focused deterministic regressions. Report what was genuinely unified versus intentionally separate (ticket vs execution vs journalctl). Reuse the current checkpoint; no new logging framework. Exact-checkpoint full CI and Ruff green, followed by substantive curator review; green tests alone do not complete the ticket.
