---
"type": "devlegate.ticket"
"title": "Make pinned Science mirror conformance CI hermetic without weakening standalone provenance"
"depends_on": ["TASK-171"]
---

## Milestone

Devlegate 0.5.7 release-blocking **external GitHub API rate limit** in
the configured hosted CI. Resolve independently of the pending TASK-172
Git-filter stderr correction. No release or product feature work.

## Reproduced evidence

- `devlegate/work/TASK-172` exact-SHA GitHub Actions run
  `43965547365`, checkpoint
  `800a21e1aa52db00c36929f309adc927c1bdd78a`, completed failure:
  1 failed, 2284 passed, 3 skipped.
- Failed test:
  `tests/test_standalone_builder.py::
  test_standalone_assets_match_pinned_science_reference_mirror`.
  The pinned Science CLI `science download provider
  PythonBuildStandalone --version 3.12.14 --release 20260901
  --flavor install_only_stripped --libc gnu` requested the live
  `https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/20260901`
  and received HTTP 403 `rate limit exceeded`.
- This is a live upstream API dependency during the unit/CI suite,
  not evidence of an incorrect PBS SHA-256, invalid Science metadata,
  bad local mirror, or a regression in TASK-172.
- Other existing cases already verify local mirror structure, verified
  cached PBS/scie-jump bytes, correct digest/file identity and real
  pinned Science consumption of the local mirror while upstream
  access is forcibly denied:
  `test_standalone_assets_match_science_download_mirror_layout`,
  `test_pinned_science_consumes_standalone_mirror_under_denial`,
  `test_real_pex_science_consumes_warm_asset_mirror`.
- Shared GitHub-hosted runners may exhaust unauthenticated API
  quotas independently of this repository. CI must not rely on
  their current rate-limit status to prove deterministic pinned
  local asset compatibility.

## Goal

Replace the *mandatory live-reference request* in the ordinary CI
conformance test with a deterministic independent reference or an
equivalent real pinned Science protocol verification against a local
fixture/mirror. Keep meaningful checks of the provenance, layout,
hashes and actual consumer compatibility of standalone inputs.
Production packaging remains pinned, exact-hash verified and
fail-closed; an upstream API outage or rate limit must not make
the standard CI suite fail before checking product behavior.

## Required behavior

1. Inspect the precise failing test, other standalone mirror
   tests, `tools/build_standalone.py`, packaging pins and the
   pinned Science CLI's `download provider --base-url` support.
   Identify exactly which invariants the live upstream reference
   supplied and which are independently verified elsewhere.
2. Prefer a narrow change confined to
   `tests/test_standalone_builder.py` and, only if genuinely needed,
   small versioned test fixtures. Use a deterministic *independent*
   canonical expected representation keyed to the pinned
   Science/PBS version, or exercise the real Science CLI against
   an independently constructed local mirror with explicit
   integrity assertions. Avoid a tautological reference produced
   solely from the unverified output under test.
3. Preserve assertions for PBS archive filename, pinned digest and
   size, target triple, release/version, relative path, file type,
   scie-jump identity and bytes as applicable; keep all real Science
   denial/offline tests intact. Verify the builder still refuses
   corrupt cache and mismatched digests.
4. Make an explicit distinction between a **hermetic conformance
   test** required by routine CI and an optional **live upstream
   audit** that may only run under explicit operator control and
   must report upstream failure honestly. Do **not** simply skip or
   xfail the old test, swallow HTTP 403, retry-until-green, silently
   patch Science requests, lower digest checks, or replace a
   real Science consumer test with only mocked logic.
5. The configured `./dev coverage -n 4 --dist=worksteal`
   must be free of *unbounded third-party API metadata calls*
   along this comparison path. Keep unrelated network-dependent
   integration tests unchanged unless they trigger precisely the
   same issue and can be corrected with equally narrow proof.
6. Do not alter production runtime behavior, `0.5.7` metadata,
   release asset policy, full-source builder, `CHANGELOG.md`,
   `ROADMAP.md`, review/control workflow, or the already reviewed
   TASK-172 implementation. No migrations or unrelated refactor.

## Acceptance criteria

- Demonstrate that routine conformance passes with GitHub API access
  deliberately denied (403 or unreachable) *while still executing
  real pinned Science interoperability checks* and preserving
  independent metadata/digest assertions.
- Negative tests prove malformed mirror metadata and corrupted
  pinned inputs still fail; no weakening of security or provenance.
- Focused standalone builder/packaging tests, configured Ruff,
  `git diff --check`, and complete hosted exact-SHA CI pass.
  Devlegate publishes the checkpoint; independent curator reviews.
- Post-integration `release/0.5.7` CI must be green on a SHA
  containing *both* the TASK-172 filter-state correction and this
  standalone CI correction before the release script is re-run.
- Explain any legitimate live-upstream compliance audit retained,
  its opt-in interface, and why it does not gate routine CI.

## Boundaries and dependencies

This ticket depends only on integrated TASK-171 so it can be
investigated independently of TASK-172 while that ticket is still
in review. Do not claim either ticket complete merely because a
third-party API was unavailable. Do not release v0.5.7, bump
version, change date, publish packages or tags, or bootstrap 0.5.8.
