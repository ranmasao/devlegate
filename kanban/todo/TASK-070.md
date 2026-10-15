---
"type": "devlegate.ticket"
"title": "Finish substantive consolidation of repeated Git operations in runtime"
"depends_on": ["TASK-069"]
---

## Review: REWORK REQUIRED — final lint correction

Latest execution: `5f65317b73af454abb97f39b6e64f9dc`; checkpoint `3e5e4226fb516cf3cfacdc4ed2353a7ea8797b53`.

The substantive ancestry and ref-resolution consolidation from the preceding checkpoint is retained. The former force-retry exception regression is fixed: the latest authoritative CI ran **1207 passed, 1 skipped**.

**Exact CI #38683261306 fails Ruff**, not pytest:
`src/devlegate/runtime.py:6570:89 E501 Line too long (91 > 88)` for the string `"control history could not be observed during lifecycle replay"`.

### Required follow-up

1. Shorten or split the offending error-message string without changing the existing `GitAncestryError` -> `WorkflowBlockedError` fail-closed semantics.
2. Retain all current refactor changes and boundary translations; do not repeat the large Git refactor.
3. Run Ruff and relevant focused tests, then wait for full exact-checkpoint CI to pass before review.

### Architectural disposition

Current work adequately removes duplicated ancestry mechanisms (`merge-base` direct runtime calls reportedly 27 -> 1) and repeated commit resolution (`rev-parse` 70 -> 52). Distinct `rev-list`, `update-ref` and object/blob operations are **not declared fully refactored**; further Git decomposition merits a separate follow-up after this checkpoint passes. Passing CI is necessary for accepting this ticket.
