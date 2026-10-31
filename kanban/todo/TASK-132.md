---
"type": "devlegate.ticket"
"title": "End-to-end operator reset validation and fresh deferred-ticket execution"
"depends_on": ["TASK-131"]
---

## Goal
Integrate and verify TASK-129–131 against exact integrated product HEAD. Prove preview -> explicit operator confirmation -> durable intent -> staged cleanup -> restart replay -> eventual backlog-to-todo fresh execution ID and worktree, with historical reports/reviews/pinned evidence preserved and TASK-073 human-only acceptance unaffected. Test no-op/partial retry, active owner, remote failures, lease races, and no automatic non-done deletion. Document exact CLI usage and recovery/operator procedure. Run focused real-Git tests, complete configured CI and audit execution-effect costs/owner WAL/CAS constraints. Report precise remaining gaps; do not self-accept TASK-126 or touch its actual historical resources.

## Boundaries
No migration/compatibility scaffolding, no generic VCS abstraction, no TASK-073-specific production branches, and no destructive mutation of live operator resources. Preserve WAL/CAS, exact provenance, and fail-closed semantics. Changes must be reviewed independently before downstream tasks proceed.
