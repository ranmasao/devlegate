---
"type": "devlegate.ticket"
"title": "Fail packaging preflight on missing or uninitialized source submodules"
---

## Milestone

Distribution source validation.

## Goal

Make distribution builds fail immediately and explicitly when the selected source
checkout is incomplete because a required Git submodule is missing,
uninitialized, at the wrong commit, or otherwise unavailable.

Do not let packaging discover missing source dependencies indirectly through a
later wheel/license/build failure.

## Incident motivation

On a fresh Arch Linux checkout of the TASK-054 implementation branch, the
repository had been cloned without initializing the NanoYAML submodule at:

    src/devlegate/_vendor/nanoyaml

Running the Arch distribution build proceeded into wheel packaging and failed
only because the wheel/license boundary could not find NanoYAML's license file.

The failure therefore appeared as a licensing/package-content defect rather than
the actual source-tree defect:

    required source submodule is not materialized

This is too late and too indirect.

The distribution build owns enough Git/source information to validate source
completeness before starting the semantic package graph.

## Required behavior

Before any wheel/sdist/standalone/native-package build step begins, perform a
source preflight that validates all Git submodules required by the selected
source commit.

At minimum:

- discover gitlinks/submodule paths from the selected source tree;
- validate that every required submodule path is initialized/materialized;
- validate that the checked-out submodule HEAD matches the gitlink commit pinned
  by the selected superproject commit;
- fail if a required submodule directory is empty, missing, not a Git worktree,
  or resolves to the wrong commit;
- fail closed on malformed or unsafe submodule paths;
- report the actual missing/mismatched submodule path and expected identity;
- do this before invoking Python package build tooling or distribution component
  execution.

The error should tell the operator what is wrong and, where appropriate, point
to the standard Git repair command, for example:

    git submodule update --init --recursive

The diagnostic must remain factual. Do not silently initialize or update
submodules as part of ordinary packaging unless a later explicit policy chooses
to make source acquisition a build responsibility.

## Architectural boundary

This is source-input validation, not package-format validation.

The distribution graph should begin only after the source checkout has been
proven complete enough to build.

Desired ordering:

    source identity
        |
        +-- source/submodule preflight
        |
        +-- freeze semantic distribution plan
        |
        +-- wheel/sdist/standalone/native package build

Do not rely on downstream effects such as:

- missing license files;
- failed imports;
- missing vendored Python files;
- incomplete manifests;
- wheel-content validation;

to prove source completeness.

## Reuse existing submodule semantics

Devlegate already has submodule verification logic around execution workspaces
and full-source materialization.

Reuse or extract the narrow Git/submodule verification semantics where practical
instead of creating a second inconsistent notion of "valid submodule".

However, packaging preflight must validate the actual source checkout selected
for the distribution build, not an execution workspace abstraction.

Keep the implementation small and explicit.

## Scope

The preflight applies to every distribution target whose source comes from the
repository checkout, including at least:

- wheel;
- sdist;
- standalone;
- deb;
- arch;
- full-source where applicable;
- future rpm/apk/native wrappers.

If a future distribution target deliberately consumes a fully materialized
source archive rather than a Git checkout, it may use an equivalent source
completeness validator rather than Git submodule inspection.

## Diagnostics

A missing/uninitialized NanoYAML checkout should produce a direct error similar
to:

    packaging source is incomplete:
    submodule src/devlegate/_vendor/nanoyaml is not initialized
    expected <gitlink-commit>
    run: git submodule update --init --recursive

For a wrong checkout:

    packaging source submodule mismatch:
    src/devlegate/_vendor/nanoyaml
    expected <gitlink-commit>
    observed <submodule-head>

Exact wording is implementation-defined, but the primary failure reason must be
the submodule/source defect, not a later missing-file symptom.

## Acceptance criteria

- A fresh clone without initialized NanoYAML fails before wheel build starts.
- Failure identifies the submodule path directly.
- Failure identifies expected pinned Git identity where available.
- Correctly initialized recursive submodules pass preflight.
- A submodule checked out at the wrong commit fails preflight.
- Missing files inside an otherwise correctly identified submodule remain the
  responsibility of normal source/package validation; do not turn this check
  into a complete source-content manifest.
- Existing valid package builds remain unchanged after preflight succeeds.
- Full authoritative CI and Ruff are green.

## Required regressions

At minimum:

1. uninitialized gitlink path fails package source preflight;
2. missing submodule directory fails package source preflight;
3. initialized submodule at the pinned commit passes;
4. initialized submodule at a different commit fails;
5. diagnostic contains the relative submodule path;
6. wheel builder is not invoked after source preflight failure;
7. Arch target is not allowed to progress into wheel/standalone work after
   source preflight failure;
8. source preflight does not mutate or initialize the checkout;
9. recursive/nested submodule validation is handled consistently where present;
10. normal clean checkout with NanoYAML initialized preserves existing
    distribution behavior.

## Relationship to other work

- TASK-054 exposed this defect while testing Arch packaging.
- TASK-058 addresses distro-agnostic/hermetic package build tools; this ticket is
  orthogonal and validates the source input before those tools are used.
- Existing execution-workspace submodule verification and full-source
  materialization provide useful semantics to reuse.

## Non-goals

This ticket does not:

- automatically clone/init/update submodules during ordinary package builds;
- redesign Git submodule ownership globally;
- change NanoYAML versioning;
- replace package/license/content validation;
- add compatibility or migration layers.

## Review decision — 2026-10-13, checkpoint 9a8ee8ebc745 (REQUEST CHANGES)

Execution `2f472847658d44118f2317f4d3356585` completed at
`9a8ee8ebc745dd4a3db12880cdc8e08ff2a18670`. Authoritative
[GitHub Actions run 38419251350](https://github.com/ranmasao/devlegate/actions/runs/38419251350)
passed. The new recursive Git submodule preflight and early invocation
in `tools/build_distribution.py` are appropriate, but scope coverage
is incomplete.

### Required corrections

1. `tools/build_standalone.py` is also an independently callable build
   entry point (`build` calls `build_wheel`). Direct standalone builds
   currently bypass `validate_source_submodules`. Ensure source
   validation precedes its wheel/build work too, reusing the same
   validator and respecting its selected source checkout. Do not duplicate
   conflicting validation logic. Independently materialized full-source
   packaging should preserve its existing pinned-submodule validation
   semantics rather than impose checkout requirements where the builder
   intentionally materializes Git objects itself.
2. Add explicit regression that a missing or uninitialized NanoYAML
   blocks the `arch` target before any wheel/standalone/native packaging
   component runs. Include a direct standalone entrypoint regression.
3. Preserve clean-source success, pinned recursive gitlinks, fail-closed
   diagnostics and no implicit submodule mutation. Rerun focused tests
   followed by full authoritative CI including Ruff on the new checkpoint.

**Disposition: REQUEST CHANGES; move `review` -> `todo`.** Resume
from `9a8ee8ebc745dd4a3db12880cdc8e08ff2a18670`.

## Review decision — 2026-10-13, checkpoint 786fdd02a123 (REQUEST CHANGES: CI)

Execution `8e318052c235427aae3766907e8e10d2` completed at
`786fdd02a12371db9d900371f240acb7937acdac`. Prior scope
blockers are addressed: standalone direct build now invokes the shared
`validate_source_submodules` before builder/wheel work; Arch and direct
standalone failure-path regressions are present. DRY boundary is preserved.

However authoritative [GitHub Actions run 38422404035](https://github.com/ranmasao/devlegate/actions/runs/38422404035)
**failed: 1189 passed, 2 failed, 1 skipped**. Ruff did not run.
Failing real-service integration cases:

- `tests/test_cli_ipc.py::test_real_service_receipt_restart_is_not_a_persistent_command_queue`
- `tests/test_cli_topology.py::test_real_service_transaction_stage_restart_preserves_execution[lifecycle_after_effect]`

Both timed out waiting for a service condition; diagnostics include
`worker identity persistence failed: [Errno 3] No such process`.
These failures are outside the changed packaging code, but the full CI
acceptance gate is not met. Do not assert causation or dismiss them as
flaky without evidence.

### Required follow-up

1. Investigate or reproduce the two failures, including whether concurrent
   real-service tests trigger worker startup/identity races. Preserve the
   existing lifecycle semantics and avoid unrelated refactoring in TASK-059.
2. If independently proven nondeterministic, document evidence and rerun
   authoritative CI on the reviewed exact checkpoint; if a genuine defect
   requires a separate ticket, record its ownership explicitly rather than
   silently weakening assertions or timeouts.
3. Require green full tests/coverage and Ruff on the exact accepted
   checkpoint. Keep the shared source-preflight implementation and tests.

**Disposition: REQUEST CHANGES; move `review` -> `todo`.** Resume from
`786fdd02a12371db9d900371f240acb7937acdac`.
