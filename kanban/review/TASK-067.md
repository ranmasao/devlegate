---
"type": "devlegate.ticket"
"title": "Finalize consolidated Git primitives: correct SPDX header and pass CI"
"depends_on": ["TASK-061"]
---

## Review decision: REQUEST CHANGES — one CI failure

Latest completed execution: `fa6dcfeab0fb4dd69ed7390c3760b1fa`. Checkpoint: `8855cd1a29490f7f94880a8ed8fbc33e13a36a6c`.

The Git refactoring is substantively complete. Preserve the existing `git.run_git()` consolidation and the new `git.resolve_commit()` used for execution-base and reconciliation-target resolution. The worker documented why other apparent overlaps are not safe/common primitives: different CAS preconditions, lineage proof contexts, worktree topology constraints, and owner capability boundaries. **Do not perform another abstraction or architecture iteration.**

[Authoritative GitHub Actions #38638822581](https://github.com/ranmasao/devlegate/actions/runs/38638822581) failed with **1 failed, 1204 passed, 1 skipped**. The sole failing test is `tests/test_licensing.py::test_devlegate_owned_source_has_exact_eupl_header`.

## Required single fix

`src/devlegate/git.py` currently begins with:

```python
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2.
```

The final SPDX line is **wrong**: remove the trailing period so it matches the repository-required exact header:

```python
# SPDX-License-Identifier: EUPL-1.2
```

Make only this fix (and any directly necessary formatting correction if discovered). Do not alter the license text or weaken the licensing test.

## Acceptance

Full existing pytest suite and Ruff green on the new exact checkpoint. No new tests necessary. Preserve all current Git behavior, test seams, WAL/owner-lock/lease-CAS/provenance and publication ordering; unrelated tasks out of scope. No appended review history.
