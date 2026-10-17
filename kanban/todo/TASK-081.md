---
"type": "devlegate.ticket"
"title": "Consolidate reconciliation Git proofs and mutation boundary"
"depends_on": ["TASK-079"]
---

## Milestone

Git architecture hardening: reconciliation semantics and executed-command proof for TASK-073.

## Goal

Align reconciliation Git observations and mutations with the established typed/owner-aware layer without changing published-lineage, WAL, evidence or replay guarantees.

## Context

`reconciliation_transactions.py` uses direct `run_git` through a local `_git` wrapper and owns domain-specific Git choreography. `prove_workspace_lineage()` performs a `rev-list` history observation and per-commit parent observations; this may be a preexisting N+1 pattern, not an established regression.

## Required behavior

1. Inventory reconciliation Git call sites and complete executed-command traces, explicitly separating observations from state-changing rebase/reset/ref operations and identifying current caller authorization, lock, shutdown and error mapping.
2. Reuse typed operations when suitable for ref/commit resolution, ancestry and history; malformed output, Git transport failures and valid negative/absent observations must be distinguishable, including root and merge commits.
3. Preserve serialized `ServiceEngine` authority, `local-pending` and `remote-pending` WAL behavior, exact checkpoint identity, displaced-lineage evidence refs, remote lease/CAS checks and required pre-mutation re-observation.
4. Preserve conflict/abort/restore and crash-replay safety. An uncertain or partially completed rebase/push must be re-observed before any further mutation, never blindly repeated.
5. Evaluate the per-commit parent observation in `prove_workspace_lineage()` for whole-range history alternatives; optimize only after verifying identical proofs for merge commits, root commits and range boundaries. No obligation to optimize if proof or subprocess effect equivalence is not established.
6. Document whole-path before/after command traces and Tier 1/2/3 effects, including all nested helpers; identify every newly introduced command, remote call and mutation.

## Acceptance criteria

- Reconciliation observations fail closed on Git errors; lineage proofs reject invalid ancestry or unexpected merge topology and handle roots correctly.
- WAL transitions, evidence-before-publication, lease/CAS, rebase/abort and recovery semantics are preserved across failures and restarts.
- No unproven mutation under missing authority or after an ambiguous interruption.
- Actual executed-command/effect accounting proves no hidden new N+1, network access or state changes; any added invocation is individually justified.
- Full pytest, coverage and Ruff pass at the exact checkpoint.

## Required regressions

- Setup: valid linear lineage and valid root/boundary ancestry; action: prove lineage; expected: exact accepted history.
- Setup: merge commit or malformed parent list in candidate lineage; action: prove; expected: reject with precise diagnostic.
- Setup: Git history observation fails or repository is unreadable; action: reconcile; expected: fail closed, not empty history.
- Setup: rebase conflict followed by successful abort; action: recover; expected: original tracked state/refs preserved.
- Setup: abort or restoration fails; action: recover; expected: blocked with durable evidence and no speculative publication.
- Setup: remote target drifts before CAS/lease publication; action: publish; expected: refuse stale write.
- Setup: crash at `local-pending` and `remote-pending`; action: replay; expected: exact-stage safe continuation or halt.
- Setup: displaced lineage is pinned but publication is interrupted; action: retry; expected: verify exact evidence and never destroy old history.
