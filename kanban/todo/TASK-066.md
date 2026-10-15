---
"type": "devlegate.ticket"
"title": "Unify ticket log reading and isolate follow from unrelated reports"
---

## Review: focused REWORK — unsafe report-root fallback

Execution `bf6a9b0cb1cc4cd29789a945f6de9403`, checkpoint `d9e17f285da91419cbaed76473dc77faed9f2ca5`; exact [CI #38711100707](https://github.com/ranmasao/devlegate/actions/runs/38711100707) **SUCCESS** (1215 passed, 1 skipped, Ruff clean).

**Preserve all existing checkpoint work**: shared byte cursor, lost-byte and duplicate-follow fixes, scoped report validation, and the third pass's filename-based execution prefix lookup all address real problems. Do not redo the logging architecture.

### Remaining fail-closed defect (blocking)

`src/devlegate/log_reader.py`, `_execution_report(control_worktree, execution, ticket_id=...)`, `except ExecutionReportError` fallback:

```python
try:
    report_path.lstat()
except FileNotFoundError:
    if report_path.parent.is_symlink():
        raise LogReaderError(str(error)) from error
    return None
```

If `control_worktree/executions` itself is a **dangling symlink** (or points to an unavailable tree), `ExecutionReportStore.read()` correctly raises `ExecutionReportError("execution report root is a symlink")`, but `report_path.lstat()` sees FileNotFoundError and `report_path.parent.is_symlink()` is false because it tests the **ticket subdirectory**, not the `executions` root. The caller incorrectly treats a broken/unsafe report root as an absent terminal report. The same applies to a symlinked control-worktree root when its requested descendant is missing.

**Fix:** distinguish only a genuinely absent report file inside validated safe ancestors from any unsafe root, symlinked ancestor or failed read/inspection. Prefer checking ancestor/root invariants before the missing-file fallback; avoid attempting to infer every `ExecutionReportError` solely from `lstat()`. Keep requested invalid evidence fail-closed and not-yet-published reports legitimately nonterminal.

Add a focused test: `executions` is a dangling symlink and the requested ticket/execution report is absent; `follow_execution` or `_execution_terminal` must raise `LogReaderError`, not silently continue/return false. Consider equivalent unsafe control root and normal absent report cases.

### Scope and validation

No new logging framework or broad refactor. Keep exact-id and prefix uniqueness, unrelated malformed report isolation, no-loss/no-duplicate streaming, accepted-before-done, and timeout/interrupt semantics. The `journalctl --follow` path legitimately inherits stderr and propagates its exit code through `process.wait()`; preserve or document that behavior (do not force journal into the file reader).

Run focused log-reader/CLI tests, Ruff and full exact-checkpoint CI. Re-review against end-to-end TASK-066 acceptance rather than green CI alone.
