---
"type": "devlegate.ticket"
"title": "Stabilize post-recovery published-plan CI regression without weakening fail-closed recovery"
"depends_on": ["TASK-169"]
---

## Milestone

Devlegate 0.5.7 release CI blocker. Keep the stable `0.5.7` product
version frozen and existing release asset policy unchanged. This is a
narrow regression investigation and correction, not a new feature.

## Goal

Explain and eliminate the intermittent failure of
`tests/test_control_recovery.py::test_live_service_keeps_unsafe_recovery_authority_reachable`
at the exact frozen product checkpoint
`7034823dd47811e55eeefd820e70621b1756d429`. Preserve the
production rule that unknown workspace identity or insufficient authority
must remain blocked; do not turn an actual recovery refusal into a passing
test by weakening admission.

## Reproduced hosted evidence

- The GitHub Actions exact-SHA run
  `43926316663` passed on 2026-11-16.
- The integration-triggered GitHub Actions run
  `43929682090`, on the **same commit SHA**, failed with
  `1 failed, 2272 passed, 3 skipped` at
  `tests/test_control_recovery.py:3817`:
  `assert json.loads(resumed.stdout)["action"] == "run-worker"`
  observed `blocked`.
- In this test, the live service accepts `recover`, the test polls the
  durable SQLite state only until `operator_recovery` disappears, and
  immediately queries a read-only `plan` IPC view. The failure output
  did not include the returned `plan.reason`.
- There is a plausible publication race: explicit workspace recovery
  clears `operator_recovery` in `_save_state`, while the service's
  read-only published plan snapshot is refreshed separately. The owner
  polling loop releases the operator command before invoking
  `status_view()` to refresh its published view. This is a hypothesis
  requiring verification, **not** evidence that any `blocked` result
  is necessarily stale.

## Required behavior

1. Independently inspect the exact test, `LiveService`, owner command
   completion, `published_plan_view()`, `status_view()`,
   `_recover_unsafe_bound_execution_workspace()` and scheduler
   publication ordering. Determine whether the observed `blocked`
   can result from a stale published snapshot, from a still-active
   owner iteration, or from a genuine recovery/identity refusal.
   Make the returned action **and reason** visible in a failing
   assertion or otherwise retained test diagnostic.
2. Correct the smallest proven defect. If the public contract promises
   only eventual consistency of read-only plan/status following a
   mutable command, update the cross-layer test to wait for a
   demonstrably fresh, coherent **published** view using bounded
   evidence-based synchronization (the existing `LiveService.wait_for`
   helper or equivalent), not a longer fixed `sleep`.
   If the owner contract requires an up-to-date view at successful
   command completion, fix the actual publication ordering instead
   and prove the contract with a deterministic test.
3. Do not silently accept a persistent `blocked` status or treat
   `operator_recovery` disappearing from SQLite as proof that
   admission is successful. Distinguish a temporary old snapshot from
   an actual refused recovery, and retain an actionable diagnostic
   on timeout. Ensure the test still detects unsafe recovery attempts
   and genuinely blocked workspaces.
4. Preserve the existing operator recovery, resource-generation, Git,
   worker-ownership and review/integration behavior. No migration
   paths, version bump, release tag, CHANGELOG rewrite, packaging
   policy change, generic polling framework, retry-on-failure CI mask
   or unrelated refactor.

## Acceptance criteria

- State clearly what caused the original failure and which precise
  synchronization or product contract prevents recurrence.
- The affected full live-service test passes repeatedly under the
  existing xdist/CI execution model without dependence on arbitrary
  sleeps or unbounded polling. Assert a coherent resumed plan/status,
  not merely absence of a `operator_recovery` key.
- A real recovery refusal or continuously stale/unavailable published
  view still fails with a meaningful diagnostic; a safety blocker is
  never silently transformed into `run-worker`.
- The stable `0.5.7` version, normal CHANGELOG section, release workflow,
  and runtime behavior unrelated to the proven defect remain intact.
- Focused tests, `git diff --check`, Ruff and **hosted exact-checkpoint
  CI** pass. The curator separately decides the final release/tag gate;
  no release activity is authorized in this ticket.

## Required regressions

1. Exercise the exact live service/operator recovery case repeatedly;
   log or assert the returned action/reason on failure.
2. Cover stale/in-progress published snapshot versus settled recovery
   and a genuinely unsafe/refused recovery as distinct outcomes.
3. Verify no premature worker launch, unauthorized workspace reset,
   mutation by read-only inspection, or ignored fail-closed recovery
   constraint is introduced.
4. Run representative recovery and CLI/IPC tests alongside this case,
   plus the project's configured hosted CI on the exact published SHA.

## Boundaries

Do not repair the red CI by editing only its expected result to `blocked`,
deleting/skipping the test, increasing a fixed timeout/sleep without
causal proof, or rerunning indefinitely until green. Worker edits
implementation and tests only; Devlegate retains all Git lifecycle
operations. Review must verify the complete regression.
