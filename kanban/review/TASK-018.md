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
