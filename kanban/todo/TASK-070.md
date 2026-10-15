---
"type": "devlegate.ticket"
"title": "Finish substantive consolidation of repeated Git operations in runtime"
"depends_on": ["TASK-069"]
---

## Review: REWORK REQUIRED

Checkpoint `ad460842d17387eb566ea8241de128d26b78e220` from execution `a0825ba8000b47edb70ade6781fd5991` is **not accepted**.

The architectural work is substantially complete: introduced `git.is_ancestor()` (0=true, 1=false, other errors raise), reused `resolve_commit()`, and reduced raw `runtime.py` command sites: `merge-base` 27 -> 1, `rev-parse` 70 -> 52; `rev-list` 7 -> 7 and `update-ref` 10 -> 10 retained where semantics differ. **Keep these changes. Do not restart the refactor.**

### Exact blocker

[GitHub Actions CI #38679838713](https://github.com/ranmasao/devlegate/actions/runs/38679838713) **FAILED**: `1206 passed, 1 skipped, 1 failed`.

`tests/test_force_retry.py::test_force_retry_rejects_divergent_control_without_mutation` now raises `devlegate.git.GitAncestryError` when the saved admitted control SHA is invalid/nonexistent (`ffff...`). Previously the admission boundary safely rejected this state with `DevlegateError`.

In `ServiceEngine._validate_force_retry_admission()`, the new direct `is_ancestor(..., runner=_git)` call does not translate Git observation failure to the domain-specific fail-closed error. Fix the boundary so forced retry is rejected with `DevlegateError` and does not mutate state. Preserve distinct Git statuses (true, valid negative, observation failure) within the shared primitive; translate at the domain boundary rather than changing `is_ancestor()` to swallow errors.

### Required focused follow-up

1. Repair the above error translation while keeping the existing ancestry helper and substantive call-site consolidation.
2. Inspect all new `is_ancestor()` call sites for analogous unhandled `GitAncestryError` at published/domain entrypoints, and ensure intended error type/fail-closed behavior. Adapt only the affected sites; avoid broad redesign.
3. Run the failing test, relevant retry/recovery/ancestry regressions, and full pytest/Ruff on the exact new checkpoint. Green CI mandatory.
4. Worker report must identify concrete remaining distinctions for `rev-list`/`update-ref` as needed. Do not introduce extra generic wrappers or redundant integration suites.

The original refactor has sufficient architectural substance; rework is for a concrete safety regression, not for increasing LOC reduction.
