---
"type": "devlegate.ticket"
"title": "Re-audit focused test coverage, placement and proof strength against current suite"
---

## Post-audit scope and scheduling — 2026-11-28

Retain whole-suite proof audit after primary architectural substitutions. Phase II inspected only selected test groups, not the entire collected suite. The obsolete tests/FOCUSED_TEST_AUDIT.md is already absent at the audit baseline fb135883ef3ec584f456fddedc8daf7f723f2553; verify references and do not regenerate it. Explicitly distinguish fixtures sustaining obsolete live authority from modern behavioral tests and independent cross-layer proof. TASK-013 should consume this inventory for bounded missing-proof implementation, not repeat the full audit.

## Scheduling

**Future iteration after release 0.5.7; backlog until explicitly scheduled by
the curator.** This is an independent evidence-first assessment of the actual
test suite, not part of the frozen 0.5.7 implementation scope and not a
dependency of release cleanup or CHANGELOG completion. Any resulting bounded
test refactors or gap-coverage additions require separately reviewed tasks.

## Motivation

The old `tests/FOCUSED_TEST_AUDIT.md` describes a snapshot of **1,136 collected
tests across 56 modules**, and earlier local pass/coverage counts. That
snapshot is not current: the checked 0.5.7 product contains **87
`tests/test_*.py` modules**, and hosted CI for the exact historical
0.5.7 cleanup candidate `d64f388b3919a0b48ccd5ee1abde7e2ac2abe35e`
completed **2,273 passed, 3 skipped**. Numbers here establish why the old
audit is obsolete; they are *not* a future inventory or an assertion that
the suite still has the same content when this ticket executes.

The old file contains detailed per-module test counts, historical
before/after refactoring notes, timings and former module ownership. It is
not a current product contract. It should not be retained in maintained
product documentation merely as an archival record; the historical blob
is available in Git. Where an old current-document reference such as
`tests/TEST_PLACEMENT.md` links to it, replace the reference with a
durable current testing rule or `tests/TEST_BOUNDARIES.md`, not another
stale report. Coordinate any **0.5.7 removal** of this old audit with
the already-running TASK-107 review; do not edit TASK-107 or alter its
in-flight canonical ticket from this future audit.

## Goal

Independently inspect the **entire then-current collected pytest suite**,
including parametrized cases and support/fixture modules, to assess:
- the actual semantic domain ownership and test placement;
- the proof strength of each important safety property (pure/component,
  engine semantic, IPC owner handoff, independent service/CLI/process,
  real Git/SQLite/submodule/distribution effects);
- duplicated, brittle, obsolete or implementation-mirroring tests versus
  intentional independent cross-layer coverage;
- important gaps, weak fail-closed cases and ineffective assertions;
- realistic focused edit-loop selections and their relationship to the
  stronger CI, packaging and crash/restart evidence.

Treat the maintained `tests/TEST_BOUNDARIES.md`,
`tests/TEST_PLACEMENT.md`, `tests/RECOVERY_MATRIX.md` and
`tests/CONCURRENCY_MATRIX.md` as existing *claims to verify against code*
rather than unquestioned sources of truth. Keep them focused on current
contracts, not implementation history.

## Required investigation

1. Record the exact integrated product SHA under inspection, the
   collection/test toolchain, module/file and collected case inventory
   from `pytest --collect-only` (including parametrization), skipped/
   deselected conditions and relevant hosted exact-SHA CI/coverage. Do
   not infer case count from Python function counts or static filenames.
   Where CI gives an adequate reliable result, reuse it instead of
   rerunning a costly unrestricted suite simply for the audit.
2. Map every test file to its actual semantic owner/proof boundary
   using fixtures, direct/test doubles, real service process/IPC, Git
   operations, SQLite, subprocess, failure injection and assertions.
   Verify whether support modules contain only reusable setup versus
   hidden test cases, and whether collection actually runs all intended
   cases.
3. Examine overlaps among focused, integration and cross-layer tests.
   Mark genuine redundant coverage, purposeful independent proof,
   repeated setup cost, over-coupled test internals and suspicious
   missing assertions *separately*; do not label two tests redundant
   merely because they cover the same feature or share mock calls.
4. Recheck ownership, provenance, WAL/CAS, uncertain external effects,
   exact identity and crash/restart admission, recovery/reconciliation,
   control/product/workspace isolation, shutdown/retirement, worker
   lifecycle, review feedback, operator IPC, packaging/runtime
   resources and license preservation against the boundaries each
   claim actually requires. Identify examples of gaps and execution
   cost only from verifiable current evidence.
5. Review current focused test selections and unit-to-integration
   escalation guidance. Recommend a *small set* of semantically
   meaningful developer commands rather than an inventory which must
   be manually updated for every newly added test module. Do not
   create permanent "ever removed tests" or "excluded artifacts" lists.
6. Distinguish (a) clearly removable historical audit narrative from
   (b) maintained testing rules and (c) necessary reproduction
   procedures. Preserve stable contracts; do not retain obsolete
   statistics, timings, ticket references or old report tables in the
   current product tree.

## Evidence and deliverables

Publish a substantive, durable **execution report in the control plane**
with exact SHA, methods, collected inventory, proof-boundary matrix,
specific examples of overlap and missing/weak assertions, severity,
focused commands, and bounded prioritized recommendations. Include an
explicit **PASS / FAIL / UNKNOWN** assessment of the maintained suite's
important documented claims, with falsifiable evidence for each.

Do not create another permanent `tests/FOCUSED_TEST_AUDIT.md` or its
replacement carrying counts/tables that will immediately age.
Only update/replace current `TEST_BOUNDARIES.md`,
`TEST_PLACEMENT.md` and other maintained test guides if changes are
**small, immediately justified and current-behavior oriented**; no
historical ticket links or investigation chronology in shipped docs.

## Acceptance

- A complete current-suite accounting, with the boundaries of observed
  versus inferred evidence explicit; no recycled 1,136/56 inventory
  or unverified assumption that historic 2,273 pass/3 skip still applies.
- Specific test-module claims checked against actual tests and production
  behaviors rather than filenames alone.
- Intentional cross-layer duplication distinguished from useless tests;
  gaps are actionable with exact scope and proposed regression proof.
- A compact focused-testing guide based on semantic domains and stronger
  proof requirements; full CI remains the baseline for release readiness.
- The obsolete standalone audit document has an explicit removal/reference
  disposition; its historical content can remain in Git/control evidence.
- Audit-only completion is valid even when the findings are negative.
  Do not silently remove a large number of tests, refactor runtime or
  regenerate reporting frameworks. New implementation work needs
  separate human-reviewed scope and an exact-checkpoint CI gate.
