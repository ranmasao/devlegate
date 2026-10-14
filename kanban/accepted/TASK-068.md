---
"type": "devlegate.ticket"
"title": "Reduce Git operation indirection and consolidate reusable Git mechanics"
"depends_on": ["TASK-067"]
---

## Disposition: ACCEPTED

Execution `cd0249215e734617b7028b2f91e85f6f`; approved checkpoint `f95996d2af82cd4b5698616d6a7dfda2da3ce104`.

[Exact checkpoint CI #38646780092](https://github.com/ranmasao/devlegate/actions/runs/38646780092): **SUCCESS**, 1205 tests passed, 1 skipped; Ruff clean.

## Delivered

- Removed redundant `ServiceEngine.recovery_git()` and `ServiceEngine.reconciliation_git()` forwarding methods and corresponding Protocol requirements.
- Recovery transactions/workspace and reconciliation Git calls now use the shared infrastructure `git.run_git()` without owner call-through for raw Git execution.
- Kept a small non-raising `_git()` local test seam in reconciliation while preserving `check=False` behavior.
- Preserved existing owner lock, state mutation/admission, WAL stages, evidence pinning and expected-old Git CAS safeguards.
- Adapted existing monkeypatch tests for the new call locations rather than creating broad redundant suites.

No remaining TASK-068 rework required. Proceed with normal accepted integration; do not reopen the task for unrelated `_run_iteration_body()` or further speculative abstraction.
