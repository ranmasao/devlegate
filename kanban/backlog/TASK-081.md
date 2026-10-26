---
"type": "devlegate.ticket"
"title": "Consolidate reconciliation Git proofs and mutation boundary"
"depends_on": ["TASK-079"]
---

## Milestone

Git architecture hardening: reconciliation semantics and executed-command proof for TASK-073.

## Goal

Align reconciliation Git observations and mutations with the established typed/owner-aware layer without changing published-lineage, WAL, evidence or replay guarantees.

## Worker test scope — targeted verification, full suite in CI

Use the current proof map `tests/TEST_BOUNDARIES.md` and `tests/TEST_PLACEMENT.md`. Start with Git history/error semantics and transaction proof cases, then narrowly selected end-to-end reconciliation scenarios:

```sh
./dev test -q tests/test_git.py tests/test_recovery_boundaries.py
./dev test -q tests/test_control_recovery.py -k "reconcil or update_base or rewrite or lineage or evidence"
./dev test -q tests/test_control_workspace.py -k "update_base or lineage or rebase"
./dev lint
```

If publication/restart behavior at the real-service boundary is changed, add **selected named** tests from `tests/test_cli_topology.py` using `-k "reconciliation_from_real_cli or product_movement_during_downtime"`, not an unfiltered run. Keep required rebase conflict, abort and restore proofs; do not replace stateful Git tests solely with mocked return values.

**Do not run by default:** unfiltered `./dev test`, `./dev test-parallel`, `./dev check`, `./dev coverage`, all topology tests, or unrelated packaging suites. The worker records selected tests/command traces; GitHub Actions owns full exact-commit pytest with coverage and Ruff. Reviewer acceptance requires green CI, but not a local full-suite run by the worker.

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
- Exact-checkpoint full pytest with coverage and Ruff passes in GitHub Actions; the worker executes the focused transaction and lineage regressions rather than a mandatory full-suite local run.

## Required regressions

- Setup: valid linear lineage and valid root/boundary ancestry; action: prove lineage; expected: exact accepted history.
- Setup: merge commit or malformed parent list in candidate lineage; action: prove; expected: reject with precise diagnostic.
- Setup: Git history observation fails or repository is unreadable; action: reconcile; expected: fail closed, not empty history.
- Setup: rebase conflict followed by successful abort; action: recover; expected: original tracked state/refs preserved.
- Setup: abort or restoration fails; action: recover; expected: blocked with durable evidence and no speculative publication.
- Setup: remote target drifts before CAS/lease publication; action: publish; expected: refuse stale write.
- Setup: crash at `local-pending` and `remote-pending`; action: replay; expected: exact-stage safe continuation or halt.
- Setup: displaced lineage is pinned but publication is interrupted; action: retry; expected: verify exact evidence and never destroy old history.

## Supplemental reconciliation/recovery architecture focus

The measured 0.5.7 architecture grouped `_reconcile_resume_owned` (~156 LOC), `_normalize_stranded_execution` (~147), `_recover_interrupted_execution` (~189), `_merge_accepted_product_descendant` (~138), and `_integrate_accepted` (~136) under the central engine. The measured `reconciliation_transactions.py` was ~568 LOC. This **does not establish faulty coupling**: these routines legitimately share durable phases and lineage proof obligations.

Expand the transaction inventory into an explicit boundary table: **observation and identity**, **lineage/evidence proof**, **guarded effect** (including rebase, Git ref CAS, remote publication and cleanup), **durable stage**, **re-entry after crash or ambiguous transport outcome**, and **operator disposition**. Identify minimum persisted evidence for recognizing an already completed effect; preserve the exact pre-effect re-observation/lease that excludes races. Prefer narrow typed proof helpers to methods that silently mutate shared `_state` or implicitly fetch Git refs.

Cross-check recovery contracts accepted through TASK-097 rather than creating a parallel recovery architecture. Document concrete semantic or executed-command gains; do not treat a smaller runtime module, fewer import edges or lack of direct self-call cycles as evidence of safety or throughput improvement.

## Integrated baseline and scope clarification — 2026-10-26

TASK-100 is integrated: `reconciliation.py` owns shared canonical/rewritten evidence-ref construction and identity validation, reused by `reconciliation_transactions.py` and runtime cleanup. A valid historical `resolved / update-base` reconciliation can have an exact `-rewritten` evidence ref plus canonical `displaced_evidence_ref` without a `resolution_class` field; current producer may include that optional classification. Do not invent a second evidence identity contract, require optional historical fields, mutate stored records, or delete protected refs as part of refactoring. Preserve TASK-099/100 regressions and include both canonical and rewritten cases in actual transaction/effect traces. Avoid hidden fetches in read-only proof helpers.
