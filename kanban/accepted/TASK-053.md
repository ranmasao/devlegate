---
"type": "devlegate.ticket"
"title": "Refresh test audit documentation after physical decomposition"
"depends_on": ["TASK-050"]
---

## Milestone

Test architecture completion.

## Goal

Bring the maintained test-audit documentation into agreement with the physical test
ownership established by TASK-049 and TASK-050.

The source tree is already decomposed. This ticket is documentation-only cleanup so
future coverage and testing-policy work starts from an accurate description of the
suite.

## Context

`TEST_PLACEMENT.md` now correctly states that semantic test modules physically own
their regressions and support modules own reusable helpers/fixtures. There are no
collection facades or hidden moved-test inventories.

However `FOCUSED_TEST_AUDIT.md` still describes an intermediate TASK-050 attempt in
which moved tests were renamed to private `_test_*` functions and re-exported through
facade modules. That description is stale and contradicts the accepted tree.

The authoritative recent suite baseline after TASK-052 is:

```text
1135 passed, 1 skipped
coverage: 79%
Ruff: green
```

Do not treat this ticket as a new decomposition pass.

## Required behavior

- Update `tests/FOCUSED_TEST_AUDIT.md` to describe the accepted physical ownership
  model.
- Remove references to collection facades, private moved-test inventories, and
  re-export-based ownership.
- Refresh module inventory/counts where TASK-050/TASK-052 changed them.
- Keep the distinction between semantic ownership and proof strength.
- Preserve the current guidance that cohesive modules should not be split merely to
  make files smaller.
- Keep focused commands as examples/edit-loop evidence, not machine-readable
  validation profiles.
- Ensure `TEST_PLACEMENT.md`, `FOCUSED_TEST_AUDIT.md`, and
  `TEST_BOUNDARIES.md` do not contradict one another about current file ownership.
- Do not redesign test taxonomy or introduce testing capability/profile machinery.

## Acceptance criteria

- Maintained audit documents describe the actual current physical test layout.
- No maintained document claims that worker/project semantic tests are collected
  through facades or remain as hidden `_test_*` bodies elsewhere.
- Inventory/count statements are refreshed from current collection evidence rather
  than copied from the intermediate TASK-050 attempt.
- Documentation remains explicit that stronger IPC/service/production-topology
  evidence is not replaced by cheaper semantic tests.
- No production source behavior changes.

## Required evidence

- Show the current pytest collection count used to refresh the inventory.
- Run documentation/repository lint checks applicable to the changed files.
- Diff must be documentation-only unless a tiny test metadata correction is required
  to make the documented inventory truthful.
