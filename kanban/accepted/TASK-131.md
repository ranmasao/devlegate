---
"type": "devlegate.ticket"
"title": "CAS and lease guarded staged reset retirement with restart replay"
"depends_on": ["TASK-130"]
---

## Goal
Implement destructive stages for an explicitly authorized durable reset intent from TASK-130: reobserve ticket/owner/worktree/ref state immediately before each effect; retire only precisely identified old execution worktree and local/remote execution refs. Respect Git worktree lock incl reason, dirty/untracked/submodule safety, path and branch registration; never remove unrelated refs, historical reports or pinned evidence. Use exact local expected-old CAS and remote exact-ref lease, distinguish confirmed absence from observation failure, handle partial progress safely, record effects durably before advancing, and restore/replay after interruption without widening authorization. Reject changed SHA, mixed-generation ownership or unknown outcomes. Keep historical cleanup policy unchanged. Focused isolated real-Git tests: success, stale local/remote, changed worktree, errors, interrupted worktree/local/remote stages, safe restart/replay and duplicate request. No actual operator resources touched.

## Boundaries
No migration/compatibility scaffolding, no generic VCS abstraction, no TASK-073-specific production branches, and no destructive mutation of live operator resources. Preserve WAL/CAS, exact provenance, and fail-closed semantics. Changes must be reviewed independently before downstream tasks proceed.
