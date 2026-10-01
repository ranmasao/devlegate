---
"type": "devlegate.ticket"
"title": "Cache immutable standalone build inputs locally and in GitHub Actions"
---

## Milestone

Post-0.6 build infrastructure hardening.

## Goal

Avoid repeatedly downloading the same pinned standalone build inputs while preserving
the independence and strength of the A/B reproducibility proof.

## Context

The standalone builder currently creates a fresh temporary build root for every
invocation. Packaging tools and other immutable external inputs are downloaded into
that temporary root, while A/B wheel and scie builds deliberately use distinct build
roots and distinct `PEX_ROOT` state.

The A/B separation is valuable and must remain: the proof should detect accidental
dependence on temporary paths, mutable build state, timestamps, ordering, or reuse of
a previous build result. Re-downloading already pinned immutable blobs does not add
proof strength.

Local builds should therefore reuse verified immutable download inputs when possible.
GitHub Actions should be able to persist the same cache across runs without changing
builder semantics or making CI correctness depend on cache availability.

## Required behavior

- Add a persistent cache for immutable external standalone-build inputs.
- Cache identity is content-addressed or otherwise bound to the expected SHA-256;
  filenames and URLs are not trust boundaries.
- On lookup, a cached object is used only after its SHA-256 matches the pinned
  expected digest.
- Missing or corrupt cache entries fall back to a normal download.
- Downloads are written to a temporary path, verified, and atomically published into
  the cache so interrupted downloads cannot create trusted partial entries.
- Cache use is silent during ordinary builds; verbose/debug output may distinguish
  cache hits and downloads.
- Local builds use an appropriate per-user cache location and require no explicit
  setup for normal use.
- GitHub Actions may persist and restore the same immutable-input cache between runs,
  for example with the platform cache facility, but a cache miss must behave exactly
  like a clean build.
- The cache stores external immutable input blobs only. It must not cache Devlegate
  wheels, A/B wheel-build environments, `PEX_ROOT`, scie outputs, Debian packages,
  proof extraction directories, or other mutable/intermediate build state.
- Build A and build B continue to use independent temporary roots, independent
  wheel-build environments, and independent `PEX_ROOT` directories.
- The cache must not weaken existing hash verification, pinning, reproducibility
  checks, or fail-closed behavior.
- Offline operation is not implied by cache presence. If an input is missing and the
  network is unavailable, the build fails normally unless a separate explicit
  offline mode is introduced later.

## Acceptance criteria

- A second local standalone build with unchanged pinned inputs can reuse previously
  verified external inputs without downloading them again.
- If a cached object is missing, the builder downloads and verifies it normally.
- If a cached object has the wrong content, it is not trusted; the builder replaces
  or bypasses it only after obtaining and verifying the expected content.
- Concurrent or interrupted population cannot leave a partial file that later passes
  as a valid cache entry.
- A/B reproducibility builds remain independent except for read-only reuse of the
  same verified immutable input blobs.
- GitHub Actions can restore/save the build-input cache across workflow runs, while a
  cold-cache run remains fully supported.
- Full tests and lint remain green.

## Required regressions

- Given a valid cached blob with the expected SHA-256, when the corresponding input
  is requested, then no network download is required and the verified cached blob is
  used.
- Given a cached blob whose SHA-256 does not match the expected digest, when the input
  is requested, then it is rejected and cannot be used as build input.
- Given a cache miss, when download succeeds with the expected digest, then the
  verified blob is atomically made available for later builds.
- Given a failed or interrupted download, then no incomplete cache entry is treated
  as valid by a later build.
- Given build A and build B, then they may read the same cached immutable blobs but
  do not share `PEX_ROOT`, wheel-build environments, generated wheels, or scie
  outputs.
- Given GitHub Actions with no restored cache, then the standalone build still
  completes using the normal download path.


## Review findings

Execution `fa7cd60739d7402c93140830c33e2fbf` / checkpoint
`81f80b6ed3671266b7783c5b1fa17e159bc878ab` implements a sound
content-addressed cache primitive:

- cache entries are addressed by the pinned SHA-256 rather than URL or filename;
- every hit is re-verified before use;
- downloads occur in a temporary path under the cache and are atomically published
  with `os.replace` only after verification;
- a corrupt entry is never trusted;
- an interrupted download does not become a cache hit;
- the cache is outside both A/B build roots;
- A/B continue to use independent wheel environments and independent `PEX_ROOT`;
- GitHub Actions persists only the dedicated standalone-input cache directory.

GitHub CI run `36917301255` is green:

```text
1084 passed, 1 skipped
coverage: 79%
Ruff: all checks passed
```

One scope blocker remains.

### Blocker: PBS and scie-jump immutable inputs are still outside the shared cache

The standalone input set explicitly pins and verifies:

```text
PBS_ARCHIVE / PBS_SHA256
SCIE_JUMP_ASSET / SCIE_JUMP_SHA256
```

Both artifacts are embedded into the eager scie and are verified again by
`inspect_scie` / `split_scie`.

The candidate cache currently covers:

- the PEX wheel;
- wheel-build tool wheels;
- PEX bootstrap wheels;
- the Science executable.

It does not materialize the pinned PBS archive or scie-jump through
`cached_input()`, nor otherwise provide those exact cached blobs to the scie build.

Instead each `build_scie()` still invokes PEX/Science with:

```text
--scie-pbs-release <release>
--scie-python-version <version>
--scie-pbs-stripped
--scie-science-binary <science>
```

while build A and build B deliberately receive different `PEX_ROOT` values.

Science 0.21's documented build/download model obtains PythonBuildStandalone
provider artifacts and scie-jump as external inputs and maintains its own download
cache. Therefore caching only the Science executable does not establish the
TASK-018 property that A and B may reuse the same verified immutable external blobs
while keeping their mutable/build state independent.

This is especially relevant because PBS is one of the largest standalone inputs and
is exactly the kind of repeated pinned download this ticket is intended to avoid.

### Required rework

Extend the immutable-input boundary to cover the pinned PBS archive and scie-jump
artifact as well, or prove and configure an equivalent shared immutable-only source
that PEX/Science consumes without sharing mutable build state.

Preserve these constraints:

- do not share `PEX_ROOT`;
- do not share wheel-build environments, generated wheels, scie outputs, extraction
  directories, or other mutable intermediates;
- do not make trust depend on Science/PEX cache metadata or filenames;
- the Devlegate boundary must still verify the exact pinned SHA-256 before an
  external blob is trusted as input;
- a cold cache must retain the existing normal network build behavior;
- a corrupt or partial cached PBS/scie-jump must be rejected exactly like the
  already-covered inputs.

If a supported Science/PEX cache mechanism is reused, constrain it so the persisted
GitHub/local cache still contains only the intended immutable input blobs, or keep a
Devlegate-owned content-addressed blob cache and feed the verified artifacts through
a supported local/offline input mechanism. Do not persist the A/B PEX roots as a
shortcut.

Add regressions demonstrating that:

1. a warm cache supplies the exact pinned PBS and scie-jump without a second network
   fetch;
2. corrupt PBS/scie-jump cache entries are rejected before use;
3. A and B can read the same immutable cached blobs while their `PEX_ROOT`,
   wheel-build environments, generated wheels, and scie outputs remain distinct;
4. a cold GitHub Actions/local cache still follows the ordinary verified download
   path.

The existing cache-hit/corruption/interrupted-download tests should remain.

No change is requested to the overall cache design; the remaining issue is coverage
of the complete pinned standalone external-input boundary.
