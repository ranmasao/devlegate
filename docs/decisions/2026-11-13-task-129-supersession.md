# Decision: retire historical TASK-129; continue operator reset via TASK-160

**Date:** 2026-11-13
**Scope:** TASK-129, TASK-160, TASK-130/131/132
**Control baseline:** `6826ac571a9d186d8e07a3dc202c81367735a69e`
**Product baseline:** `54ca0a65bcb7cdf8bb3d0b7d1ae6caab704a782c`
**Disposition:** TASK-129 superseded and archived, explicitly **not completed or accepted**

## Evidence

Original TASK-129 was left in backlog after multiple incomplete reviews.
Its last checkpoint `a96f51d670e0c0843dc1bda02c51c1ae62da74d0`
(execution `5c739d11a3d0471e811124cf9b0df088`)
predates independent ResourceBinding generation receipts and cannot be
silently adopted into a new owner-controlled execution. Its original ticket
blob SHA is `43d783269031c0a0933652e24e8d12c9c1a1f2fc`.

The earlier decision `docs/decisions/2026-11-12-task-129-fresh-preview.md`
scheduled a genuinely fresh TASK-160 instead. TASK-160 was reviewed
and independently integrated; its product checkpoint is
`54ca0a65bcb7cdf8bb3d0b7d1ae6caab704a782c` and the
ordinary control integration is `6826ac571a9d186d8e07a3dc202c81367735a69e`.
The latest TASK-160 GitHub Actions run `43394927495` passed exact-checkpoint
tests and Ruff. TASK-160 is durably done.

## Operator control-plane decision

1. Move original `kanban/backlog/TASK-129.md` to
   `archive/tickets/TASK-129.md` preserving the exact blob. This does
   **not** assert TASK-129's historical execution was completed or accepted.
2. Preserve all `executions/TASK-129/` and `reviews/TASK-129/` evidence,
   old Git refs and worktree, operator state and pinned evidence. Do not
   run workspace cleanup, reset or fabricate ResourceBinding receipts.
   Archived unbound resources remain ineligible for generic reset.
3. Before its first admission, update `kanban/todo/TASK-130.md` so its
   `depends_on` and prose reference integrated TASK-160 preview/proof
   rather than archived TASK-129. The successor, not the old checkpoint,
   supplies TASK-130's implementation baseline.
4. Correct TASK-132's unexecuted description accordingly; leave its
   dependency on TASK-131. TASK-131 still depends on TASK-130.
   Validate end-to-end reset only on disposable, independently
   generation-bound resources. Do not alter real TASK-073, TASK-126,
   TASK-129 or archived TASK-134 resources.
5. Make all these control changes atomically. Let the normal scheduler
   admit TASK-130 with a new execution ID, independent generation and
   integrated current product HEAD. No production migration, no manual
   SQLite surgery, and no implicit history-adoption authority.

This decision supersedes the temporary TASK-129/backlog dependency gate
from the 2026-11-12 scheduling decision. The original ticket and its
historical record remain audit evidence, not a current executable ticket.
