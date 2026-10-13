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
