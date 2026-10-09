---
"type": "devlegate.ticket"
"title": "Make PEX bootstrap inputs authoritative and fully offline"
"depends_on": ["TASK-018"]
---

## Milestone

Hermetic standalone distribution build.

## Goal

Fix the standalone/scie build so every Python packaging tool required by the
pinned PEX build is an explicit, complete, verified offline build input.

A clean supported build host with an empty Devlegate standalone-input cache must
be able to build the standalone artifact without PEX attempting to resolve an
unmaterialized pip/setuptools/wheel version from PyPI.

## Incident

A fresh Arch Linux checkout running the TASK-054 distribution build reached:

    standalone: build standalone executable A

and failed with:

    Failed to download its dependencies: pip

    ERROR: Could not find a version that satisfies the requirement pip==24.1
    (from versions: 23.2, 24.3.1)
    ERROR: No matching distribution found for pip==24.1

The source currently exposes three pip versions in the build:

- pip 24.3.1 in the Devlegate wheel-build toolchain;
- pip 23.2 in the manually maintained PEX_BOOTSTRAP_TOOLS set;
- pip 24.1 selected internally by pinned PEX 2.103.2 for the scie/bootstrap path.

The first version belongs to a separate Devlegate wheel-build role. The latter
two reveal duplicated authority over one PEX bootstrap role.

The build invokes PEX with:

    --no-pypi
    --find-links <canonical-wheel-dir>
    --find-links <tools-dir>

so PEX can only resolve artifacts present in the managed local wheelhouse. The
wheelhouse is currently incomplete for the actual resolver behavior of the
pinned PEX release.

## Architectural rule

There must be one authority for the PEX bootstrap dependency set.

Prefer the pinned PEX release as the authority for the exact bootstrap tool
versions it requires. Devlegate should materialize and verify that complete set
rather than maintain an independent, stale approximation of PEX internals.

Do not fix this by removing --no-pypi or allowing opportunistic network
resolution during the executable-build stage.

Desired flow:

    pinned PEX release
        |
        +-- deterministic bootstrap requirements
                |
                +-- materialize exact wheels
                +-- verify exact digests
                +-- populate local wheelhouse
                |
                +-- freeze network/package-index access
                |
                +-- build eager scie completely offline

The Devlegate wheel-build toolchain remains a separate concern and may use a
different pip version when justified.

## Required behavior

- Identify why pinned PEX 2.103.2 selects pip 24.1 for this build path.
- Determine the complete pip/setuptools/wheel bootstrap set required by the
  exact PEX/scie invocation used by Devlegate.
- Eliminate competing manual bootstrap policy where possible.
- Materialize every required bootstrap artifact before executable A/B build.
- Pin each acquired artifact by exact filename/version and SHA-256.
- Reuse the existing digest-addressed standalone input cache.
- After the toolchain/materialization stage completes, PEX/scie executable
  construction must succeed with package-index access unavailable.
- Keep --no-pypi or an equivalent fail-closed offline boundary.
- Do not silently fall back to the host pip/setuptools/wheel.
- Record the resolved PEX bootstrap tool identities in the standalone build
  report/provenance.
- Keep the wheel-build toolchain identity distinct from the PEX bootstrap
  toolchain in reports and code.

## Authority and version policy

Do not assume that all packaging stages must use one pip version.

Different versions are acceptable only where they serve different explicit
roles.

For example:

    wheel build pip
        Devlegate-owned toolchain

    PEX bootstrap pip
        PEX-owned compatibility requirement

What is not acceptable is having two independently maintained versions both
claim to represent the PEX bootstrap requirement.

If PEX provides a stable programmatic/API-level way to expose the required
bootstrap artifacts, use or adapt that.

If PEX does not expose such an interface, encode the exact requirement as a
derived/pinned property of PEX 2.103.2 with tests proving that it matches actual
PEX resolver behavior. Document why the coupling exists.

Do not upgrade PEX merely to hide the mismatch unless the upgrade is separately
justified and its complete distribution/reproducibility impact is validated.

## Clean-host proof

Add a proof that starts from:

- empty standalone input cache;
- no relevant pip/setuptools/wheel artifacts outside the managed inputs;
- no package-index/network access after input acquisition;
- no dependence on host-installed Python packaging tools beyond the supported
  builder Python needed to bootstrap the explicitly managed environment.

The proof must exercise the real PEX eager-scie build path, not merely inspect a
constant table.

## Diagnostics

If a future PEX bootstrap artifact is absent, fail before or at the explicit
input-materialization boundary with a diagnostic naming the missing managed
artifact.

Do not allow the first useful diagnostic to be a nested PEX/pip resolver error
deep inside "build standalone executable A".

## Acceptance criteria

- The reported pip==24.1 clean-host failure is reproduced by a regression.
- Root cause is proven against the pinned PEX 2.103.2 behavior.
- The complete actual PEX bootstrap dependency set is managed and digest-pinned.
- There is exactly one authority for the PEX bootstrap version policy.
- Devlegate wheel-build pip remains a separately named/owned role.
- A clean-cache standalone build succeeds.
- Executable A and B build with PyPI/package-index access disabled after input
  materialization.
- Existing standalone reproducibility checks remain green.
- Standalone build report records both wheel-build and PEX-bootstrap toolchains
  without conflating them.
- Full authoritative CI and Ruff are green.

## Required regressions

At minimum:

1. clean empty-cache build materializes the exact PEX bootstrap pip required by
   pinned PEX;
2. PEX eager-scie build succeeds when package-index access is unavailable after
   materialization;
3. missing required PEX bootstrap wheel fails closed before nested resolver
   fallback;
4. wrong/corrupt bootstrap wheel digest fails closed;
5. verified cached bootstrap artifacts are reused;
6. host pip version does not affect selected PEX bootstrap inputs;
7. wheel-build pip and PEX-bootstrap pip are reported as distinct roles;
8. changing the manual stale 23.2-style approximation cannot silently override
   actual PEX bootstrap requirements;
9. reproducibility build A/B continues to use identical managed inputs;
10. no runtime dependency on pip/setuptools/wheel is introduced.

## Relationship to other work

- TASK-018 established the pinned standalone/scie build and managed input cache.
- TASK-054 exposed this defect from a clean Arch host while building its
  prerequisite standalone artifact.
- TASK-058 should build on this corrected managed-tool/input boundary rather
  than generalizing the current incomplete wheelhouse behavior.

## Non-goals

This ticket does not:

- change the Devlegate runtime dependency model;
- make pip a runtime dependency;
- remove offline/hard-pinned standalone builds;
- add native package formats;
- solve distro-specific package tooling;
- add compatibility/migration layers.
