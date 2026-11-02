# Architect correction — TASK-139 normal requeue after failure

Date: 2026-11-02

This is an explicit requirements correction requested by the project owner, not a rework-review amendment. The intended operator action is the ordinary canonical `backlog → todo` transition after a deferred failed worker. The system then attempts owner-serialized continuation automatically, preserving the latest failed worker checkpoint and any genuine earlier curator review, with no user-facing inspection/authorization/repair command. If the product advanced, the owner performs at most one necessary internally guarded rebase of the committed failed worker delta.

The original TASK-139 ticket incorrectly required an operator-initiated historical repair mode, a bounded historical ownership assumption flag and possibly published-ref rewrite flags. The TASK-140..144 design extended that mistaken interface into five separate tasks. Those tasks have been moved to backlog unchanged; the first TASK-140 execution evidence and review remain immutable. They are not prerequisites for the corrected TASK-139.

TASK-137 and TASK-138 already address stale failure supersession and review rework provenance. The remaining real gap is automatic safe checkpoint/review continuation after intentional requeue. Unknown worktree ownership, dirty/foreign/locked resources or ref drift must still block without destructive action; do not invent historical BOUND records.

Canonical TASK-139 stayed in backlog during the specification revision because it has prior review/rework generations bound to the old ticket blob. A new runnable generation must respect Devlegate's guarded Architect specification-revision protocol; it must not casually use the ordinary review/rework transition as proof that the changed assignment was previously reviewed. Neither the architect correction nor the backlog deferral is permission to rewrite old execution reports or reset a live worker.
