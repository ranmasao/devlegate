# Decision: implement TASK-129 preview as a fresh generation

**Date:** 2026-11-12
**Scope:** TASK-129, TASK-160; downstream TASK-130/131/132
**Status:** Fresh successor scheduled; original ticket retained in backlog

## Basis

TASK-129 is still incomplete. Its latest reviewed worker checkpoint
`a96f51d670e0c0843dc1bda02c51c1ae62da74d0` belongs to historical
execution `5c739d11a3d0471e811124cf9b0df088`, which predates the
independent generation receipts introduced by TASK-150.

TASK-125 and TASK-150 are durably done. TASK-150 superseded the archived
TASK-134 ownership-binding attempt, after the operator's 2026-11-05
decision. Its new ResourceBinding mechanism intentionally refuses to adopt
historical worktrees on matching branch/path/SHA alone. Simply moving
TASK-129 from backlog to todo would risk a repeat of the TASK-089
`execution has no unique independent generation intent` block.

## Decision

1. Keep the unchanged original TASK-129 in backlog, including its existing
   worker branch/worktree, immutable reports, reviews and pinned evidence.
2. Schedule fresh TASK-160 to implement the remaining TASK-129 read-only
   operator preview from current integrated product and TASK-150 proof APIs.
   Give it a fresh ticket/execution/generation; treat TASK-129 historical
   code as informational evidence, not an ownership or replay grant.
3. Do not alter TASK-130/131/132 dependencies now. They remain blocked on
   TASK-129 until an explicit later operator decision.
4. After TASK-160 is independently reviewed and integrated, choose a
   separate documented disposition for original TASK-129 and the downstream
   dependency chain. Do not silently mark the original worker accepted or
   mutate the older execution reports.
5. Do not touch actual TASK-073, TASK-129, or archived TASK-134 resources.

This is a one-time control-plane scheduling decision, not a production
migration, reset, or relaxation of resource ownership.
