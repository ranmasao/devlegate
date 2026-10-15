---
"type": "devlegate.ticket"
"title": "Treat safe product and control advancement as normal execution progress"
"depends_on": ["TASK-075"]
---

## Principle

In future parallel execution, other workers will routinely advance shared product and control branches while an individual execution is planned, pending, running or awaiting integration. **Descendant advancement is an expected concurrency event, not a crash, an unsafe-workspace incident, or an automatic recovery trigger.** Recovery is reserved for interrupted/inconsistent durable operations, lost ownership/evidence or truly ambiguous provenance. This ticket establishes the correct single-execution revision-transition policy **without implementing a parallel scheduler**.

## Incident / existing incorrect coupling

After automatic Git workspace repair for `TASK-071`, the execution branch and product HEAD both reached `507da8f` while the active persisted control binding remained `a935c8e`. The service observed `491e902`, a direct descendant that merely added `TASK-074`, but `_prepare_execution_workspace()` rejected it through literal control SHA inequality. A manual, provenance-checked one-field control-binding update enabled the original execution `2de154d82c314bab9e269aaf90db1f1a` to enter `worker-running`. Safe advancement must be handled as a normal, owner-serialized state transition rather than through operator SQLite surgery or a workspace recovery protocol.

## Required semantics

1. Separate **immutable execution/admission identity** (ticket body and metadata, dependencies and relevant eligibility constraints, execution ID, original code snapshot, worker ownership) from **mutable shared branch observations**. Do not treat one global control/product HEAD as the identity of all active jobs.
2. **Control advancement:** when `devlegate/control` fast-forwards, prove ancestry and revalidate that the selected ticket and all admission-relevant prerequisites are still valid in the new tree; unchanged ticket text alone is not sufficient if dependency/eligibility semantics changed. In a pre-worker phase, atomically adopt the newer control binding under the service owner/WAL rules. If an actual relevant change is detected, block **that execution** with a precise conflict instead of crashing, discarding work or using recovery as the normal path.
3. **Product advancement before worker launch:** inspect code ancestry and exact workspace/generation ownership. For a clean, unstarted workspace, support a proven-safe new-base transition with consistent Git refs/worktree, remote predecessor and persisted state, crash-resumable across each effect boundary. Existing committed work, ambiguous provenance or a non-descendant revision require explicit conflict handling; never silently overwrite work.
4. **Product advancement during worker execution:** keep the worker's checkout, admitted code base and execution identity pinned; neither reset nor transparently rebase its working tree. Revalidate the product HEAD at checkpoint publication/integration. Nonconflicting descendant advancement should follow an ordinary optimistic-concurrency/integration path where safety is provable, while conflicting or unsupported lineage remains explicitly pending for operator resolution. Do not rewrite published history without the existing evidence, authorization and remote lease protocol.
5. **Control advancement during worker execution/lifecycle:** allow unrelated descendant control changes, preserving original execution provenance and checking ticket identity and relevant invariants at lifecycle commit/publication (reuse or consolidate existing descendant proof, e.g. `_prove_bound_ticket_descendant`). Preserve report/control ancestry and exact ownership checks.
6. Define a clear outcome taxonomy: `unchanged`, `safe descendant/adopted`, `conflict requiring per-execution reconciliation`, `genuinely unsafe/incomplete transaction requiring recovery`. A safe descendant must not emit an incident-level `blocked`/recovery requirement. Any unresolved actual conflict must not silently launch a worker.
7. Preserve serialized state ownership, evidence-first ordering, compare-and-swap refs, remote force-with-lease, worker identity, crash replay and Git-effect visibility. Avoid global scheduler serialization beyond mutations that truly need serialization; do not introduce speculative distributed locks, broad polling, extra remote round trips or a multi-VCS abstraction.

## Acceptance

- Deterministic tests for control fast-forward between admission and `_prepare_execution_workspace()`, including the exact `a935c8e → 491e902` pattern: original execution ID survives, worker is admitted without operator recovery, no service crash and no rewritten history.
- Tests distinguish unchanged ticket with changed relevant dependencies/eligibility, altered ticket content, unrelated control additions and diverged control history. Only provably safe cases auto-adopt.
- Tests for code fast-forward both before launch and while a worker is running: no duplicate worker, no lost checkpoint, no unapproved worktree rebase; normal nonconflicting descendant changes do not force emergency recovery. Prove fail-closed behavior on genuine conflict, dirty/unowned workspace and interrupted transition replay.
- Simulate two distinct execution generations advancing shared branches in interleaved order, **without adding actual concurrent scheduling**. Prove one execution's safe advancement or conflict does not invalidate another's pinned snapshot or durable evidence.
- Report the explicit revision-binding rules, transitions, command/effect trace deltas and which existing reconcile/recovery mechanisms remain for exceptional cases. Full exact-checkpoint pytest and Ruff green; no broad scheduler redesign.
