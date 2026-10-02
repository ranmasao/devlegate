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


### Follow-up review: local Science mirror layout is not the supported mirror layout

Execution `ed28f8954fb14508b1de48d0586810c8` / checkpoint
`33a6e99c02268eb21928732b1379f48459c177f8` extends the Devlegate-owned
content-addressed cache to the pinned PBS archive and scie-jump blob, and keeps
the A/B `PEX_ROOT` values independent. The cache primitive itself remains
sound.

The remaining blocker is the disposable mirror that is passed to PEX through
`--scie-assets-base-url`.

PEX documents this option as the URL of a mirror created with `science download
...`. Science's supported mirror layout is not the layout currently synthesized
by `materialize_standalone_assets()`.

For scie-jump, `science download scie-jump --version <version> <dest>` stores
the asset under a versioned release path of the form:

```text
jump/download/v<version>/<qualified-scie-jump-asset>
```

The candidate instead writes:

```text
jump/<qualified-scie-jump-asset>
```

For interpreter providers, `science download provider ...` also writes provider
distribution metadata and places the downloaded artifact at the provider
distribution's `url.rel_path` beneath:

```text
providers/<ProviderName>/
```

The candidate writes only:

```text
providers/PythonBuildStandalone/<PBS_ARCHIVE>
```

without the Science-generated provider mirror metadata/layout.

The newly added tests therefore prove only that Devlegate can read its own
synthetic directory tree and that the PEX command contains the
`--scie-assets-base-url` argument. They do not prove that real PEX/Science can
consume that tree without network access.

#### Required rework

Keep the Devlegate SHA-addressed cache as the trust boundary, but materialize a
Science-compatible disposable mirror before the A/B builds. The easiest safe
shape is to reproduce the exact subset of the layout emitted by the pinned
Science `download` commands, using only blobs already verified by Devlegate.

Do not make Science-generated cache metadata the trust boundary; Devlegate must
continue to verify the pinned SHA-256 before any blob is admitted.

Add at least one integration-level regression that uses the real pinned
PEX/Science path rather than a mocked `run()`, and demonstrates that with a
warm Devlegate cache the PBS and scie-jump network locations are not contacted.
A local HTTP server or otherwise deliberately unreachable upstream boundary is
acceptable. The regression should fail if the mirror path/layout is wrong.

Retain the existing corruption, cold-cache, atomic publication, and independent
A/B `PEX_ROOT` regressions.


### Third review: mirror layout improved, but integration proof and lint are still missing

Execution `fced32ab0d004e9f99988e26e2f9dacc` / checkpoint
`cdc754f631abd0e336cbbe3dcc06698ed299b0e5` corrects the previously identified
synthetic mirror paths:

- scie-jump is now materialized beneath
  `jump/download/v<version>/<asset>`;
- PythonBuildStandalone is now materialized beneath
  `providers/PythonBuildStandalone/download/<release>/<archive>`;
- checksum sidecars are written;
- provider distribution metadata is synthesized with the pinned digest, size,
  target triple, version, release, and relative path;
- the Devlegate-owned SHA-addressed cache remains the trust boundary;
- A/B continue to share only the verified immutable asset source while keeping
  independent `PEX_ROOT` values.

The production direction is therefore closer to the supported Science 0.21 mirror
shape.

Two blockers remain.

#### Blocker 1: required real PEX/Science integration regression is still absent

The previous review explicitly required at least one integration-level regression
that invokes the real pinned PEX/Science path and proves the warm mirror is actually
consumed without contacting the PBS/scie-jump upstream locations.

The new test:

`test_standalone_assets_match_science_download_mirror_layout`

does not do that. It constructs fake `b"pbs"` and `b"jump"` blobs, calls
`materialize_standalone_assets()`, and asserts that Devlegate wrote files and
metadata at the expected paths. Likewise,
`test_scie_builds_share_assets_but_not_pex_roots` still replaces the build runner
with `fake_run()`.

Those are useful unit regressions, but they only prove that Devlegate agrees with
its own model of the mirror layout. They cannot detect a mismatch between the
synthesized metadata/layout and what pinned PEX 2.103.2 / Science 0.21.0 actually
consume.

Add the required integration proof. It must:

1. use the real pinned PEX/Science execution path, not a mocked `run()`;
2. start from a warm Devlegate immutable-input cache containing the exact pinned PBS
   and scie-jump blobs;
3. make the normal upstream PBS/scie-jump locations unavailable or otherwise prove
   they were not contacted;
4. successfully build far enough to prove Science consumed the local mirror;
5. fail if the mirror path or provider metadata shape is wrong;
6. preserve independent A/B `PEX_ROOT` and mutable build roots.

A local HTTP fixture, network namespace/boundary, or equivalent deterministic
upstream-denial mechanism is acceptable. The test should validate the consumer
boundary, not merely the producer directory structure.

#### Blocker 2: CI is red on Ruff

GitHub CI run `36988397349` reports:

```text
1089 passed, 1 skipped
Ruff: failed
```

The two failures are:

```text
tests/test_standalone_builder.py:269:89  E501 (91 > 88)
tools/build_standalone.py:1046:89       E501 (92 > 88)
```

Both are in the candidate change line, so the execution report statement that lint
findings are only pre-existing violations outside changed lines is incorrect.

Fix those lines and return with the complete CI run green, including Ruff and
coverage.

No redesign of the cache primitive is requested. The remaining work is proof that
the corrected mirror is genuinely consumable by the pinned toolchain, plus the
straightforward lint cleanup.


### Fourth review: real-toolchain regression exists but is skipped in CI

Execution `823d01c8ef804f07ae7923edcbb597b7` / checkpoint
`ce35a819a9cfbaca70cd6bd75de2ee3c519b28e2` adds the requested real
PEX/Science consumer-boundary test and fixes the previous Ruff violations.

The new regression is structurally appropriate:

- it calls the real `download_packaging_tools()` / `build_scie()` path;
- it uses the pinned PEX/Science/PBS/scie-jump identities;
- it verifies every cached blob before use;
- it points proxy variables at a local HTTP server that rejects upstream access;
- it asserts no upstream request occurred;
- it succeeds only if the generated scie artifact is produced from the local
  Science-compatible mirror.

CI run `36994572608` is green for the tests that actually ran:

```text
1089 passed, 2 skipped
Ruff: all checks passed
```

However, the new integration regression is one of the skipped tests, so the required
proof is still not part of CI.

The test currently begins with:

```python
cache_name = os.environ.get("DEVLEGATE_STANDALONE_INTEGRATION_CACHE")
if not cache_name:
    pytest.skip(...)
```

and then skips again if any pinned blob is absent. The GitHub Actions workflow does
not set `DEVLEGATE_STANDALONE_INTEGRATION_CACHE`, populate such a cache, or restore
one before the test step. Therefore a normal clean CI run cannot execute the
consumer-boundary proof.

This leaves exactly the failure mode the regression was requested to detect
unprotected: the synthesized mirror can become incompatible with pinned
PEX/Science while ordinary CI remains green.

#### Required rework

Make the real-toolchain integration proof execute deterministically in CI.

A suitable implementation may, for example:

1. add a dedicated CI preparation step that populates the exact pinned immutable
   inputs through the normal verified download path;
2. place them in a temporary/dedicated integration cache;
3. set `DEVLEGATE_STANDALONE_INTEGRATION_CACHE` for the integration test;
4. then run the real PEX/Science test with upstream access denied.

The persistent GitHub Actions cache may be used as an optimization, but correctness
must not depend on a warm persisted cache. A cold CI run must be able to prepare the
verified fixture and then exercise the warm-cache/mirror consumer boundary.

Alternatively, make the test self-prepare its exact pinned inputs before imposing
the network-denial boundary. The important invariant is that the integration test
must run, not skip, on the supported Linux CI path.

Keep the verified-blob trust boundary and the existing proxy-denial assertion.
Do not weaken the test into another synthetic directory-layout check.

Return to review with CI evidence showing the real integration regression executed
successfully, along with full tests, coverage, and Ruff green.


### Fifth review: integration proof now runs and exposes a real upstream dependency

Execution `69c20154df2246af933b01d928431572` / checkpoint
`6717af1c4d284352cf55f8c1b67e4b3538f7278e` makes the previously skipped
real-toolchain regression self-prepare its exact pinned immutable-input cache on a
cold run.

This is the correct direction. The integration proof now genuinely reaches the
pinned PEX/Science consumer path instead of skipping.

The execution is correctly reported as `incomplete`: under the upstream-denial
fixture, Science still attempts a CONNECT to `github.com:443` even though the
verified PBS/scie-jump mirror is present.

That is now the primary production blocker. Do not weaken the test, allow GitHub
through the proxy, or turn this into another optional/skipped regression. The test
has exposed exactly the class of hidden network dependency TASK-018 is intended to
eliminate for warm immutable inputs.

#### Required investigation

Identify the exact consumer/request responsible for the remaining
`github.com:443` access before changing the mirror format again.

Determine whether the request is for:

- PythonBuildStandalone provider metadata/distribution discovery;
- the PBS archive itself;
- scie-jump;
- Science bootstrap/update/discovery;
- PEX bootstrap/tooling;
- or another artifact outside the currently modeled immutable-input boundary.

Capture enough request/tool stderr or otherwise instrument the integration fixture so
the requested URL/resource is unambiguous.

Then fix the boundary at the correct layer:

- if the request is for PBS/scie-jump, make the local Science mirror fully satisfy
  the pinned consumer contract;
- if it is for another pinned immutable standalone input, extend the Devlegate-owned
  SHA-addressed cache/mirror boundary to that input;
- if it is nonessential discovery/update behavior, configure the pinned toolchain to
  avoid it deterministically rather than permitting network fallback.

Preserve all existing invariants:

- Devlegate pinned SHA-256 remains the trust boundary;
- A/B `PEX_ROOT` and mutable build roots remain independent;
- cold CI may download and verify inputs during fixture preparation;
- once the denial boundary is enabled, the warm-cache integration phase must perform
  no external network access required for the standalone build;
- the integration regression must run, not skip, on supported Linux CI.

The execution's RECORD fix and cold-cache fixture preparation may remain if they are
needed by the real PEX path.

Return to review only after the real pinned PEX/Science build succeeds under the
denial fixture and full CI, coverage, and Ruff are green.
