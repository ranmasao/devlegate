---
"type": "devlegate.ticket"
"title": "Use the shared candidate transaction for recoverable REWORK preparation"
"depends_on": ["TASK-198","TASK-083"]
---

## Milestone

Deterministic reconciliation consolidation — 2/5, ordinary REWORK and conflict continuation.

## Goal

A valid reviewed ticket requeued to todo must automatically prepare its implementation on the admitted product generation. Conflict must not strand REWORK in an incomplete resource handoff. A human need only resolve source-level ambiguity and explicitly confirm the result; Devlegate owns all technical steps.

## Context

Use the integrated TASK-198 transfer contract. At f439371 runtime._prepare_execution_workspace drives rework_provenance.preparation_stage, ResourceBinding.prepare_rework writes rework-started before a creation receipt, and _prepare_rework_remote_predecessor publishes the replaceable work ref. TASK-056 and TASK-194 needed external conflict resolution followed by operator-only creation/receipt/SQLite/publication repair. Preserve review attribution and the distinction between a new REWORK generation and prior execution evidence.

## Required behavior

- Replace the REWORK transfer path with the common candidate transaction. The old managed source and H remain available while preparing/resolving C. Bind previous report/review/control, source generation, admitted target and new REWORK identity exactly. Keep existing review→todo/backlog→todo and reset-discard rules; do not select a historical failed source by inference.
- After mechanically ready or explicitly resolved candidate, the single owner performs the generation installation, local CAS, registration/creation receipt, old-generation retirement and required immutable remote evidence / lease-guarded predecessor publication in a safe replayable order. Pin source before destructive effects; use fresh control/product/ref/ownership/retirement checks. Reuse the existing ResourceBinding guarantees rather than mint a binding from topology. A crash at an effect boundary must resume from actual evidence, including a remote push that succeeded despite error or lost acknowledgement. Do not repeat transfer on an already installed candidate.
- Conflict is a durable waiting disposition with exact candidate location and A/B/H/attempt identity, not an abandoned handoff or ordinary worker launch. Report meaningful existing status/inspection/log diagnostics. Preserve candidate edits across restart; no blind repeated cherry-pick, abort, reset, cleanup or busy spin. Permit a single deliberate owner-admitted completion action in the existing reconcile command domain (reuse a suitable surface or add only a narrowly scoped resolution action if needed). The operator edits source files and confirms the observed candidate; no hand-staging, Git commit/rebase/ref operations, receipt generation, SQLite edit, SHA juggling, or stop/start sequence required. Devlegate validates, stages/checkpoints, installs and continues; do not equate Git cleanliness with semantic consent. Bound confirmation to exact candidate content/attempt and re-observe before effect.
- Preserve source and candidate on stale control, product advance or remote race. Recheck required target/assignment before launch; do not apply approval to a different B or silently discard a manually edited candidate. It is acceptable to report a specific stale-target blocker here; TASK-201 handles automatic retarget policy.
- Preserve same-base and no-op REWORK, exact feedback/prompt/change explorer provenance, existing generation retry and reset boundaries. The ordinary REWORK worker performs semantic revalidation; the technical transplant is not review acceptance. Remove superseded REWORK-specific transfer/replay logic, not merely wrap it while keeping a second live route. No role Reconciler, arbitrary historical adoption, migration or changes to real self-hosting state.

## Acceptance criteria

Normal REWORK clean transfer automatically launches the correct worker. Reproduce the TASK-194/TASK-056 conflict class in disposable repositories: service retains a candidate and source, survives restart, accepts one explicit source-level resolution and performs every technical transition without manual Git/SQLite work. Exact identities/reports/reviews preserved; no premature or duplicated worker. Full CI passes.

## Required regressions

1. Real service/repo review requeue on unchanged/advanced product, multi-commit delta, no-op/retired no-op and already incorporated contribution; verify correct REWORK directive, base, review feedback, generation and predecessor/evidence.
2. Real conflict with previous work retained, resolve candidate including an additional file, confirm via supported owner/CLI path and continue. Check no worker or destructive handoff during wait, and restart before/after edits. Refuse unresolved, stale, foreign or changed candidates.
3. Interrupt before/after each irreversible handoff effect, receipt and remote push (including success with lost acknowledgement); restart/replay without repeat retirement, new generation or loss of H/C. Contrast changed product/control/ref, live predecessor and ambiguous ownership.
4. Preserve exact provenance, reset/retry/cleanup, prompt and change explorer regressions, focused tests, lint, diff check and hosted CI.
