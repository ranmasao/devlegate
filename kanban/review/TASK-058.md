---
"type": "devlegate.ticket"
"title": "Make native package builds distro-agnostic and hermetic"
"depends_on": ["TASK-018", "TASK-060"]
---

## Milestone

Distribution build portability and hermetic packaging.

## Goal

Make Devlegate able to build supported native Linux package artifacts from one
supported build environment without requiring the build host to belong to the
target distribution family.

Building a native package must depend on a reproducible package-format toolchain,
not on the presence of the target distribution's package manager or build
environment.

Target distributions remain valuable as native validation environments, but not
as mandatory build environments.

## Core principle

    distribution artifact build != native distribution validation

Desired model:

    source
      |
      +-- standalone
            |
            +-- deb
            +-- arch
            +-- rpm
            +-- future package formats

All package artifacts above should be buildable from the same supported build
host when their managed build inputs are available.

A developer or CI runner must not need one Debian-family, one Arch-family, one
Fedora/RHEL-family, and additional physical/VM hosts merely to produce the
artifacts.

## Motivation

Current native packaging paths still depend on host-installed tooling in places,
for example:

- Debian packaging expects host `dpkg-deb`;
- Arch packaging relies on host `tar --zstd` / `zstd`.

This is acceptable as an intermediate implementation, but it is not the desired
distribution architecture.

If each package target accumulates distro-native build dependencies, the build
host turns into an uncontrolled union of package-manager toolchains and package
production becomes unnecessarily tied to host distribution state.

## Required architecture

Introduce a distro-agnostic package build layer.

Each package target must consume:

- the already-built standalone payload;
- authoritative Devlegate build/version/provenance inputs;
- a deterministic package-format implementation/toolchain;
- pinned external build inputs where external tools are still necessary.

Do not rebuild a separate runtime per package family.

## Managed build tools

When an external build tool is required, model it as an explicit build input,
not as an undeclared host prerequisite.

At minimum record:

- tool name;
- version/release;
- platform/architecture;
- source URL or other authoritative source identity;
- expected digest;
- cache identity/location;
- validation of the downloaded artifact.

The build should fetch/cache/validate the tool deterministically when practical.

Tool availability must not depend on whether the host is Debian, Arch, Fedora,
openSUSE, etc.

Build tools are build-time inputs only. They must not become runtime
dependencies of the produced Devlegate package.

## Prefer minimal format-native builders where practical

Do not automatically pull a complete distribution toolchain merely because a
package format has an official distro build utility.

If a package format is sufficiently simple and well-specified, prefer a small,
deterministic builder owned by Devlegate over a heavyweight host-native
toolchain.

Examples:

- Arch `.pkg.tar.zst` can be treated as a deterministic package-format
  container with required metadata and zstd compression; Arch Linux and pacman
  are not required to construct it.
- Debian package production should be evaluated for a minimal deterministic
  builder rather than permanently requiring host `dpkg-deb`.
- Future RPM production should avoid needlessly importing distro-specific
  rpmbuild policy into a simple standalone binary package if a smaller reliable
  builder is practical.

Do not reimplement complex formats casually. The choice between a minimal owned
builder and a pinned external tool must be justified by format complexity,
correctness, reproducibility, and maintenance cost.

## Validation layers

Separate validation into two explicit layers.

### 1. Package-format validation

Must be runnable on the generic build host.

It verifies the artifact itself, including as appropriate:

- package metadata;
- architecture;
- file layout;
- permissions/file types;
- runtime dependency declarations;
- licensing/provenance payload;
- payload digest;
- absence of unexpected files;
- source-tree-only development files are excluded;
- deterministic/version identity invariants.

This validation must not require the target distribution package manager unless
that package manager is itself provided as a managed/pinned validation tool.

### 2. Native distribution validation

Runs the already-built artifact inside a target distribution environment.

Examples:

- Debian-family: apt/dpkg install/query/remove;
- Arch-family: pacman install/query/remove;
- RPM-family: dnf/rpm and/or zypper/rpm install/query/remove.

Native validation checks actual package-manager semantics, ownership, smoke
execution, uninstall behavior, and preservation of unrelated state.

It is validation infrastructure, not part of the package build prerequisite.

## Native validation environments

Prefer disposable reproducible environments over requiring dedicated physical
machines for every distribution.

Use containers or VMs where they accurately exercise the required package
manager behavior.

Reserve real-host testing for system-level behavior that cannot be honestly
validated in a container/VM.

The architecture should allow a CI matrix such as:

    built once
      |
      +-- Debian validation environment
      +-- Arch validation environment
      +-- Fedora validation environment
      +-- Rocky/Alma validation environment
      +-- openSUSE validation environment

The package artifact under validation must be the same artifact that the build
stage produced, not a distro-rebuilt equivalent.

## Package-family roadmap

Treat package formats and distribution validation profiles separately.

Initial/near-term package families:

- DEB;
- Arch `.pkg.tar.zst`;
- RPM.

Likely later independent package families:

- Alpine APK;
- Nix distribution/channel integration;
- possibly Void/XBPS;
- possibly Gentoo ebuild metadata/install path;
- lower-priority Slackware/other niche families.

Do not create separate package builders merely because several distributions use
the same package format.

## RPM policy

RPM should be modeled primarily as one distro-neutral binary artifact for the
simple Devlegate standalone payload.

Do not create separate packages such as:

    devlegate-fedora.rpm
    devlegate-rhel.rpm
    devlegate-opensuse.rpm

unless native validation proves a concrete incompatibility requiring divergent
artifacts.

The common RPM metadata model is expected to cover the Devlegate package:

- Name;
- Version;
- Release;
- Arch;
- License;
- Summary/Description;
- URL;
- Requires/Provides/Conflicts/Obsoletes where needed;
- payload file list.

Distribution differences should initially be represented as validation profiles
and packaging-policy checks, not different artifact formats.

Desired model:

    one RPM artifact
      |
      +-- Fedora validation
      +-- RHEL-compatible validation
      +-- openSUSE validation

Fedora/RHEL/openSUSE may differ in spec conventions, macros, repository policy,
automatic dependency generation, release conventions, scriptlet policy, and
repository metadata. Those differences must not be imported into the vendor
binary artifact unless they produce an actual runtime/package-manager
compatibility requirement.

For Devlegate's intended RPM payload:

    /usr/bin/devlegate
    /usr/share/doc/devlegate/...

with a self-contained executable, no Python runtime dependency, no package-owned
users/groups, no config migration, and ideally no install scriptlets, one common
RPM artifact should be the default assumption.

## Automatic dependency generation

Be careful with distro-native builders that inspect ELF and automatically add
host-environment-specific dependency capabilities.

For standalone Devlegate, generated dependencies must reflect the real runtime
ABI requirement, not incidental details of the build distro.

Any automatic dependency generation must be deterministic, reviewed, and
validated across supported RPM-family environments.

If a generic builder can emit a smaller stable dependency contract without
losing correctness, prefer that over environment-specific generated metadata.

## Version/release policy

Package version/release mapping must derive from the same authoritative
Devlegate build metadata across package families.

Avoid injecting target-distro-specific release suffixes into vendor-built
artifacts unless a concrete repository/publication requirement demands them.

For example, a common vendor RPM should prefer a stable mapping equivalent to:

    Version: <Devlegate version>
    Release: 1

rather than requiring Fedora `%{?dist}` or SUSE-specific release policy merely
to produce a portable binary artifact.

## Hermeticity and reproducibility

Required properties:

- package build result does not vary merely because the host distribution
  changes;
- package build tools and versions are explicit inputs;
- fetched build tools are digest-verified;
- package timestamps/ownership/order are deterministic where supported;
- package metadata comes from authoritative Devlegate build inputs;
- no accidental host files or package-manager state enter the artifact;
- the source-tree-only `dev` helper remains excluded;
- full build evidence identifies all external tool inputs used.

Where byte-for-byte reproducibility is practical, test it.

Where it is not yet practical, record the exact remaining nondeterministic
inputs rather than silently depending on them.

## Distribution graph integration

The distribution graph should express package-format dependencies, not host
distribution assumptions.

For example:

    wheel
      -> standalone
           -> deb
           -> arch
           -> rpm

Selecting `arch`, `deb`, or future `rpm` should cause Devlegate to resolve
the necessary managed build tools automatically.

Do not require users to manually install target-specific build toolchains as an
undocumented prerequisite.

## Tool cache

Managed build inputs should use a stable cache analogous in spirit to existing
standalone bundling inputs.

The cache must:

- key entries by exact tool identity/version/platform/digest;
- validate cached files before use;
- fail closed on digest mismatch;
- allow offline reuse after successful acquisition;
- avoid modifying the source tree.

Do not treat the cache as provenance; provenance must record the exact resolved
tool identities separately.

## Acceptance criteria

- Arch package can be built on a non-Arch supported build host without relying
  on pacman or Arch system packages.
- Debian package can be built without requiring the build host to be a
  Debian-family system.
- Host-native build utilities currently used by package targets are either:
  - replaced with deterministic format-native builders; or
  - resolved as pinned managed build inputs.
- Build tool identity/version/digest is retained in durable build evidence.
- Generic format validators run independently of native distro environments.
- Native package-manager validation remains a separate stage.
- Distribution graph has no semantic dependency on host distro family.
- Cross-distro regression coverage proves the same source/build inputs produce
  the same intended package metadata/layout contract.
- Existing standalone semantics remain unchanged.
- Existing DEB and Arch package behavior remains green.
- Full authoritative CI and Ruff are green.

## Required regressions

At minimum:

1. package build does not branch on `/etc/os-release` or equivalent host distro
   detection;
2. missing host `dpkg-deb` does not make DEB production impossible once its
   managed builder/tool path is available;
3. missing host `zstd` does not make Arch production impossible once its
   managed compressor/tool path is available;
4. managed tool digest mismatch fails closed;
5. corrupt cached tool is rejected;
6. repeated build reuses a verified cache entry;
7. build evidence records exact managed tool identities;
8. package-format validator runs without target package manager installed;
9. native package-manager validation consumes the already-built artifact;
10. native validation cannot silently rebuild/repackage the artifact;
11. host distro identity does not change package metadata/layout policy;
12. common RPM artifact contract is not split by Fedora/RHEL/openSUSE without a
    proven incompatibility;
13. distro-specific RPM validation profiles may differ without changing the
    package artifact;
14. standalone runtime payload digest remains identical across native wrappers
    where expected;
15. source-tree-only development files remain absent.

## Relationship to existing work

- TASK-018 established self-contained Python bundling with pinned external build
  inputs and provides a useful precedent for managed tool acquisition.
- TASK-054 adds the first Arch-family package and may retain its current host-tool
  implementation until this foundational work replaces it.
- Future RPM/APK/native-package tickets should depend on or reuse this layer
  rather than adding another host-distro-specific build prerequisite.

## Non-goals

This ticket does not:

- publish packages to distro repositories;
- add AUR/Fedora COPR/openSUSE Build Service integration;
- add package signing infrastructure;
- require one physical machine per supported distribution;
- introduce compatibility/migration layers;
- require all future Linux package formats to be implemented immediately;
- make containers the only allowed native validation environment.

## Review hardening — execution 17e40915b0774edfa3797217bab2c7d2

Review of checkpoint `830e30ec161014ac3174730e838a45564d1b9d39` against
base `6e0d3ff8972ae0c1dba1aab0e8cb02bb18afe3a9`:
NOT ACCEPTED. Preserve the existing implementation, ticket identity, and
execution evidence. Continue on the same ticket.

### Required corrections

1. Complete the distro-independent Arch package-format path. TASK-054
   still owns the full Arch-family product package rollout; TASK-058 owns
   removing the host `tar --zstd`/`zstd` build dependency and establishing a
   reusable deterministic format/tool layer before TASK-054. Do not solve
   the omission by silently weakening the existing Arch acceptance criterion.
   If no Arch target currently exists, provide an independently exercised
   package-format builder/validator and integration seam, with a clear boundary
   for TASK-054 to consume.
2. For external format tools, implement and test managed pinned acquisition
   (identity, version, platform, source, SHA-256, verified cache and offline
   reuse), or eliminate external dependencies with an owned deterministic
   implementation. The absence of a future RPM implementation is not itself
   a reason to introduce RPM in this ticket.
3. Replace `TOOL_IDENTITY["digest"] = "owned-source-v1"` with meaningful,
   verifiable source identity/provenance. A descriptive string must not be
   presented as a cryptographic digest. Document how an owned format
   implementation is bound to the exact reviewed build source.
4. Harden `tools/deb_format.py` against unsafe archive extraction, including
   symlink/hardlink escape from the extraction root. Add malformed archive,
   duplicate member, and link traversal regressions as appropriate. Do not
   accept archives based solely on direct member-path checks before invoking
   `tarfile.extract`.
5. Demonstrate all relevant original acceptance criteria and regressions,
   including build and format validation without native package managers,
   stable metadata/layout across host environments, retained payload digest,
   and a native-validation stage which consumes—not rebuilds—the artifact.
   Keep standalone behavior unchanged.
6. Obtain a successful authoritative GitHub CI run (tests with coverage and
   Ruff) for the resulting exact execution checkpoint, and identify the run
   in the worker report. Local focused checks alone are not acceptance proof.

### Review observations

The current checkpoint usefully removes the Debian `dpkg-deb` dependency,
adds an owned DEB format implementation, and includes a repeatability test.
Those changes should be retained and hardened. The worker explicitly marked
the result `incomplete` and listed unfinished Arch/toolchain work; the
required cross-format portable packaging outcome is not yet delivered.

## Review hardening — execution 9a21b09a8821431eb71a6615dac0bcf8

Review of execution checkpoint `af97e69904a33c5e76e5c1cfec79788046140d08`
against recorded product base `6e0d3ff8972ae0c1dba1aab0e8cb02bb18afe3a9`:
NOT ACCEPTED. This remains the same TASK-058; preserve all prior execution
history and the existing DEB/Arch implementation for a focused correction pass.

Authoritative CI: https://github.com/ranmasao/devlegate/actions/runs/37920061056
on the exact checkpoint. It completed with failure: 1158 passed, 1 skipped,
1 failed in `tests/test_licensing.py::test_devlegate_owned_source_has_exact_eupl_header`,
because the new `tests/test_arch_format.py` lacks the mandatory EUPL header.
Ruff was skipped after the test failure.

### Additional required corrections

1. Apply the repository's exact EUPL copyright/license/SPDX source header to
   every new Devlegate-owned Python file, including `tests/test_arch_format.py`,
   `tools/arch_format.py`, `tools/package_arch.py`, and
   `tools/validate_arch.py`. Verify licensing checks in full CI.
2. Correct the generated Arch `.PKGINFO` against the actual ALPM package
   metadata specification rather than against only Devlegate's own parser.
   In particular `pkgver` in package metadata is the full package version
   (including release, e.g. `0.5.6.dev0-1`), and current PKGINFO v2 requires
   `pkgbase`, `size`, and `xdata = pkgtype=pkg`; do not substitute a
   standalone `pkgrel` metadata field for the full package version.
   Check actual native pacman/libalpm read/install/query/remove behavior in
   a suitable isolated Arch validation environment using the SAME built
   artifact, not a reconstructed equivalent. Keep package-format validation
   separate and make it reject invalid/missing mandatory metadata.
   Reference: https://man.archlinux.org/man/PKGINFO.5.en
3. Fix the distribution graph so that selecting `all` also builds the Arch
   target before `selected_final_files("all", ...)` accesses `values["arch"]`.
   Add an executable regression for `all` including the expected Arch
   artifact and checksum, not only graph-constant assertions.
4. Fix Arch component progress reporting: the announced `package_arch`
   `build` leaf must emit start/complete/fail events when invoked through
   `_component_for_target`. Otherwise the frozen progress plan and subsequent
   validator events become inconsistent. Exercise the real `arch` packaging
   target with the normal progress reporter.
5. Reconcile Arch-specific build preflight/report retention with the existing
   standalone and DEB paths: a selected `arch` target must have the same
   prerequisite checks and retain the standalone build report alongside the
   package evidence. Avoid using a DEB-named work directory for Arch.
6. Obtain a green authoritative GitHub CI on the exact next checkpoint with
   the full test suite, coverage, licensing checks, and Ruff. Add focused
   end-to-end packaging tests verifying native package metadata, payload
   checksum, reproducibility, and `arch`/`all` command execution without
   host Arch toolchain dependencies.

### Review conclusion

The second execution materially addressed the previous review's missing Arch
format path and safer DEB extraction, but neither the full acceptance criteria
nor the exact-checkpoint quality gate is satisfied. The specific regressions
above must be verified before moving TASK-058 to accepted.

## Review hardening — execution 2906202d662c4a85967185f352f6efce

Review of checkpoint `8dce71666ea8e74834416b67d7ad5a3d015f82c8`
against the recorded product base `6e0d3ff8972ae0c1dba1aab0e8cb02bb18afe3a9`:
NOT ACCEPTED. Continue the SAME TASK-058 and preserve all implementation and
execution history. The current execution claim was `incomplete`.

Authoritative GitHub CI:
https://github.com/ranmasao/devlegate/actions/runs/37921988442
(commit `8dce71666ea8e74834416b67d7ad5a3d015f82c8`).
Result: failure; 1159 passed, 1 skipped, 1 failed.
`tests/test_licensing.py::test_devlegate_owned_source_has_exact_eupl_header`
failed on `tools/deb_format.py` (missing EUPL/SPDX header).
Ruff did not run because the test step failed.

### Focused required corrections

1. Add the repository-standard EUPL copyright/license/SPDX header to
   `tools/deb_format.py`. Verify all Devlegate-owned newly introduced sources,
   not only the file most recently named by the licensing assertion.
2. `tools/package_arch.py` calls `progress_stage(emit, semantic_plan()[0])`,
   but defines/imports no `progress_stage`. Implement or import a valid
   context manager matching the component event protocol (start/complete/fail,
   the exact `build` leaf identity). Avoid silently disabling progress events.
   This is a direct runtime NameError on the Arch build path.
3. Add executable regressions invoking actual `arch` packaging and `all`
   through the normal distribution component/progress machinery, with controlled
   immutable standalone fixture inputs; prove no NameError, complete ordered
   events, generated/validated Arch artifact and checksum, and an `all` run
   producing every declared target. Graph constants alone do not prove this.
4. Demonstrate native Arch `pacman`/libalpm install, query, and removal
   validation against the SAME already-built package (no native rebuild),
   independently of generic package-format validation. Preserve relevant
   output/artifact identity in review evidence. If integration infrastructure
   cannot execute this, report the limitation explicitly; do not claim the
   requirement has passed.
5. Ensure a green authoritative GitHub CI on the next exact checkpoint,
   including full tests, licensing checks, and Ruff. Link that run in the
   execution claim when available. The existing offline/no-host-package-tool
   assumptions and standalone/DEB behavior must remain intact.

### Review observations

This checkpoint corrected the prior Arch graph omission and moved toward
PKGINFO v2 metadata and stronger extraction safety. Those improvements are
valuable and must be retained. Acceptance remains blocked by the exact CI
failure and an independent source-level Arch build defect, even before native
package-manager validation is considered.
