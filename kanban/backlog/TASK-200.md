---
"type": "devlegate.ticket"
"title": "Route active product reconciliation through the shared transfer and installation transaction"
"depends_on": ["TASK-199","TASK-076","TASK-118"]
---

## Milestone

Deterministic reconciliation consolidation — 3/5, unify both lifecycle origins.

## Goal

Use the same candidate preparation, conflict waiting, result proof and installation machinery for active product-drift update-base and reviewed REWORK, while preserving distinct downstream lifecycle semantics.

## Context

Use TASK-198/199. At f439371 reconciliation_transactions.run_update_base/start_fresh_rewrite/recover_local_pending/recover_remote_pending rewrite the active checkout in place and resolve through _resolve and resume_required, while REWORK has a separate resource handoff. Same-base _reconcile_resume_owned can replay an unpublished report; it is not a base transfer. Existing published-ref rewrite authorization and exact checkpoint preservation remain required. TASK-142's historical extraction is not a prerequisite: do not implement TASK-140–144 operator-driven historical repair.

## Required behavior

- Route active drift's eligible exact A/H/B through the common owner-held transfer and install contract. Share actual mutation/replay implementations across origins, not just names or helper wrappers; retire old in-place rebase/rewrite-stage paths once replaced. Consolidate durable transfer progress into one authoritative representation under existing state ownership; retain origin-specific admission/report/continuation context, not a third FSM. No compatibility shim for obsolete state formats; document required clean upgrade boundary.
- Preserve active checkpoint evidence and independent ResourceBinding/worker-retirement proofs, explicit published rewrite permission under existing policy, local/remote lease/CAS and fresh product/control authority. Source and target generation facts remain exact, including same-parent replacement only under its currently permitted explicit policy. Do not rewrite canonical product/control history.
- On mechanical result or explicit resolved conflict, complete the owned handoff and enter existing RESUME/revalidation semantics with correct execution attribution, base and prompt/history. Do not republish the old report as successful against B or classify REWORK as RESUME. Same-base resume should use existing checkpoint/publication replay, not manufacture a transfer. Preserve zero-delta automatic retry behavior or prove an equivalent simpler path.
- Use the same conflict candidate and deliberate confirmation mechanism as TASK-199. No separate manual Git procedure or historical-repair CLI. Keep existing public command behavior until TASK-202; no new general-purpose operator API. Do not broaden automatic policy yet.
- Audit consumers of pending/replay/terminal transfer facts in scheduler, recovery, state validation, cleanup, inspection, change explorer and review/report history. Ensure no duplicate independently actionable intent or stale authority. Preserve original immutable reports and evidence pins, including displaced source when effective base changes. Avoid repeated equivalent proofs at the same read boundary but retain mutation-time re-observation.

- Treat removal of superseded code and its implementation-specific tests as one substitutive refactor. Remove obsolete tests, parametrizations, mocks, fault hooks, fixtures and helpers that exist solely to exercise the retired in-place rewrite/replay implementation. Do not preserve old internal stages or interfaces merely to keep their tests passing. Consolidate equivalent transfer and handoff checks into the shared transaction suite; retain distinct origin-level end-to-end scenarios and all still-required behavioral, authority, provenance, interruption and refusal guarantees. Replace a removed test with a shared or lifecycle-level regression only where its still-required guarantee is not already covered. Do not weaken assertions, skip tests or discard valid regression evidence to reduce counts.

## Acceptance criteria

Both actual lifecycle origins use one transfer/installation transaction; origin-specific continuation is independently tested. No old active in-place rewrite path remains in use. Conflicts, restarts and publication uncertainty converge or report exact preserved blockers. Existing manual authorization, same-base replay, zero-delta and full CI remain correct. No tests or test infrastructure require the retired implementation; shared guarantees are tested once at their owning boundary, with distinct REWORK and RESUME integration coverage. Report the before/after production and test LOC, collected test-case counts, removed obsolete paths/tests and retained or consolidated guarantee coverage; include comparable focused test duration when available, without arbitrary reduction quotas.

## Required regressions

1. Real active worker checkpoint on A with product B advanced, unpublished/published source with correct authorization, multiple commits, and conflict with explicit resolution; verify same transaction implementation as REWORK, immutable H and correct revalidation directive/history.
2. Contrast same-base resume, zero-delta retry, ineligible target, absent authorization, wrong generation and external ref changes. Preserve exact current refusal behavior where appropriate.
3. Crash/restart at shared candidate and install effects for both origins, including publication acknowledgement loss and control/product change; verify no false report, repeated mutation, lost source or duplicate worker.
4. Preserve the still-required integration/recovery/reconciliation/REWORK/cleanup behavior and regression coverage, not obsolete implementation-specific tests; update architecture and operation docs for the shared implementation; focused tests, lint, diff check, exact hosted CI.
