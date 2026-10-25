---
"type": "devlegate.ticket"
"title": "Audit rework, resume and autonomous recovery boundaries"
---

## Context
Repeated TASK-083 regressions show that reviewed-ticket `REWORK` (new execution generation), interrupted `RESUME` (same execution ID), replay of a partially committed transaction, and exceptional unsafe-workspace `recovery` are not consistently distinguished. The project's objective is to minimize human-in-the-loop intervention. Real incidents often lacked diagnostic evidence accessible through normal `status --json`; an external architect could diagnose and repair them using additional reads.

## Goal
Audit and redesign the conceptual boundaries and observability for **autonomous, evidence-based** continuation. Do not assume every unfamiliar state requires operator `recover`; do not trade away fail-closed mutation safety in the name of autonomy.

## Investigation and deliverables
1. Build a concrete taxonomy: execution intent `fresh/resume/rework` versus state disposition `verified/reconcilable/unresolved/blocked`. Distinguish valid but unexpected state, incomplete replayable transaction, remediable invariant violation, unproven authority and true external-authority requirement.
2. Trace actual transitions, durable identity/stages, ownership, remote evidence, worktree state and recovery/reconciliation code. Use TASK-079 and TASK-083 incidents/tests as case studies and identify where ordinary transitions are mislabeled recovery.
3. Specify a **read-only diagnosis contract** (possibly `diagnose --json`, not necessarily extending `status --json`) that exposes expected/observed identities, failed invariants, exact evidence and missing proofs, prior attempts and safe next actions. It must work during blocked or restarted service states and avoid exposing credentials.
4. Classify repair actions by provable pre/postconditions, mutation authority, idempotency, expected SHA leases, remote race handling, and rollback/evidence preservation. Define how deterministic reconciliation verifies success after uncertain outcomes.
5. Evaluate a bounded agent-assisted diagnostic/planning tier: LLM may request safe observations and propose repairs; the deterministic engine validates every operation and retains mutation authority. Require structured diagnostic bundles and explicit escalation criteria before asking a human.
6. Produce an architecture proposal and a staged set of *small implementation tickets* with concrete invariant/regression requirements, rather than silently rewriting the entire lifecycle subsystem in one execution.

## Boundaries and acceptance
- This is a research/audit/design ticket first; implementation must be separately planned, reviewed and bounded.
- Do not weaken provenance proof, permit free-form destructive Git commands from agents, or silently overwrite remote refs.
- Do not redo active TASK-083 or TASK-076; assess after their respective behavior is known.
- Deliver failure-mode matrix, transition/state diagram, diagnostics schema draft, autonomous-reconciliation safety criteria, and phased plan with HitL reduction as measurable objective.
- Backlog only until current runtime stabilization and scheduling decision.

## Additional recovery-boundary evidence to incorporate

A static architecture scan of the measured 0.5.7 runtime identified a 1,292-line `_run_iteration_body` calling **49 distinct internal methods** and a cross-cutting recovery cluster: `_recover_interrupted_execution` (~189 LOC), `_normalize_stranded_execution` (~147), `_reconcile_resume_owned` (~156), `_prepare_execution_workspace` (~98), alongside `_make_execution_plan`, `_make_status_snapshot` and durable `_save_state`. These counts describe static shape, **not** unsafe behavior or complete call/effect reachability.

Use those entrypoints to map **observation → proof/validation → authorized resolution → disposition** and persistent phase/re-entry boundaries. In particular, track immutable admission identity separately from mutable Git/control observations, exact worker ownership and pre-worker markers, review provenance compatibility, preserved dirty worktree contents, predecessor publication, restart idempotency and diagnosable missing proofs. A safe read-only `status`/`diagnose` projection must not silently acquire mutation authority or treat stale snapshots as current admission evidence.

**Avoid duplicate implementation:** reconcile the proposed failure-mode and autonomous recovery taxonomy with TASK-097's existing recovery contract and actual accepted integrated code when this ticket is scheduled. Explicitly label already-implemented semantics, unresolved gaps and incident-specific failure evidence; do not rewrite recovery solely because the methods are large. The deliverable remains a diagnosis/contract proposal plus narrowly bounded follow-ups, not an immediate systemwide runtime extraction.
