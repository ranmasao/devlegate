---
"type": "devlegate.ticket"
"title": "Make native package builds distro-agnostic and hermetic"
"depends_on": ["TASK-018"]
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
