---
"type": "devlegate.ticket"
"title": "Add Arch-family x86_64 native package"
"depends_on": ["TASK-018"]
---

## Milestone

Native Linux distribution packaging.

## Goal

Add a native package for Arch-family x86_64 systems, built from the existing
self-contained standalone Devlegate payload, so Devlegate can be installed,
upgraded, validated, and removed with pacman without requiring a system Python
runtime or project development environment.

The intended artifact is an Arch package in the normal pacman format
(`.pkg.tar.zst`) for `x86_64`.

## Context

Devlegate already has a standalone x86_64 Linux payload and Debian-package
packaging/validation machinery. The Arch-family package should reuse the same
standalone runtime payload and licensing/provenance inputs rather than creating
another Python installation path.

A real Arch Linux x86_64 machine is now available for manual/host smoke testing,
so this ticket should include an explicit native-install validation path rather
than only archive-structure tests.

Runtime remains stdlib-only and dependency-free from the user's point of view:
the package must not depend on a system Python interpreter or a venv.

## Required behavior

- Produce a conventional Arch-family `.pkg.tar.zst` package for `x86_64`.
- Package the already-built standalone Devlegate executable; do not rebuild a
  separate runtime inside the Arch packaging step.
- Install the executable as a regular executable at:
  - `/usr/bin/devlegate`
- Install the maintained licensing and provenance material under an appropriate
  `/usr/share/doc/devlegate/` and/or conventional license location, preserving
  the same source/build provenance guarantees as other native artifacts.
- Preserve executable permissions and deterministic payload identity.
- Do not install the source-tree-only `dev` helper.
- Do not install repository integration, project files, test files, development
  dependencies, venvs, or Python package metadata that are not needed by the
  standalone runtime.
- Do not declare a runtime dependency on Python.
- Avoid package install scripts/hooks unless a concrete Arch packaging invariant
  requires one; ordinary install/remove must be declarative file ownership.
- Package metadata must derive the Devlegate version from the same authoritative
  build metadata used by the existing distribution pipeline.
- Use conventional Arch version/release fields and map Devlegate's current
  version into them deterministically.
- Record enough installation provenance to identify:
  - distribution/package family;
  - Devlegate version;
  - source/build identity;
  - standalone payload digest.
- Add a validator that inspects the built package rather than trusting the
  packaging command's success.
- Fail closed on wrong architecture, unexpected runtime dependencies, unexpected
  files, missing executable, symlink/launcher substitutions where a regular
  executable is required, missing provenance/licensing material, or payload
  digest mismatch.
- Integrate the new package target into the existing distribution graph without
  changing the semantics of current wheel/sdist/standalone/deb/full-source
  targets.
- Keep the packaging/build tooling itself outside the distributed runtime.

## Arch-family host validation

On an actual Arch Linux x86_64 host, demonstrate the package lifecycle with the
normal package manager tooling:

- inspect package metadata before installation;
- install the built package with pacman;
- prove `/usr/bin/devlegate` is owned by the package;
- run at least:
  - `devlegate --version` or the current equivalent version command;
  - a non-mutating/basic CLI smoke command such as `devlegate --help`;
- remove the package with pacman;
- prove package-owned Devlegate files are removed and unrelated user/project
  state is not deleted.

Host validation must use a disposable/test package installation context and must
not rely on copying files manually into `/usr/bin`.

## Acceptance criteria

- A reproducible x86_64 Arch-family package is produced in the distribution
  output.
- The package installs a regular standalone `/usr/bin/devlegate`.
- No system Python/venv/runtime dependency is required.
- Package contents, metadata, provenance, licensing, architecture, and payload
  digest are independently validated.
- Existing standalone and Debian package semantics remain unchanged.
- Distribution graph ordering/dependency tests cover the new target.
- Focused package tests are green.
- Full authoritative CI and Ruff are green.
- Real Arch x86_64 install/smoke/remove evidence is recorded.

## Required regressions / evidence

- production Arch package builder emits the expected package metadata and file
  layout;
- validator accepts a valid dependency-free package;
- validator rejects wrong architecture;
- validator rejects Python/runtime dependencies;
- validator rejects unexpected package payload files;
- validator rejects missing/non-regular `/usr/bin/devlegate`;
- validator rejects missing or inconsistent provenance/licensing data;
- validator rejects standalone payload digest mismatch;
- source-tree-only `dev` helper is absent;
- distribution target expansion and dependency ordering include the Arch package
  only when requested (and in `all` if that is the current native-package
  policy);
- real Arch x86_64 pacman install/ownership/smoke/remove transcript or equivalent
  durable evidence is attached/reported.

## Scope

This ticket adds one Arch-family native package for Linux `x86_64`.

It does not add AUR publication, signing infrastructure, repository hosting,
automatic mirror publication, other CPU architectures, or compatibility layers.
Those can be separate work if/when needed.


## Review findings

The first implementation is not acceptable yet.

Authoritative CI run 37503956346 failed with:

```text
2 failed, 1137 passed, 1 skipped
```

Both failures are in the new Arch package validation path:

- `test_arch_validator_accepts_dependency_free_payload`
- `test_production_arch_builder_emits_valid_package`

Both fail because the package built by the new builder is rejected by the new
validator as:

```text
Arch package contains unexpected files
```

Builder and validator must agree on the exact package member contract before
host validation. Fix the package-member/layout mismatch, rerun focused Arch
package/distribution tests, obtain a fully green authoritative CI run including
Ruff, then perform and retain the real Arch x86_64 pacman
inspect/install/ownership/smoke/remove evidence required by this ticket.


## Review findings — current pass

The previous builder/validator package-member mismatch is fixed.

Latest implementation checkpoint:

```text
b72ee59551219abb6bdb24f124dd22824ac5655e
```

Authoritative CI run `37522140115` is green:

```text
1139 passed, 1 skipped
Ruff: All checks passed!
```

The remaining blocker is the ticket's required real Arch x86_64 host evidence.

The latest valid ExecutionReport (`1ff0c09a11ff4d9d9d44ae445978bbf1`) explicitly leaves:

```text
Build the package and retain real Arch x86_64 pacman
inspect/install/ownership/smoke/remove evidence on a host with zstd and pacman.
```

No durable transcript/evidence for the required native pacman lifecycle is present
in the reviewed control history.

Before returning to review, run the produced package on the actual Arch x86_64
host using the provided validation path and retain evidence covering:

- package metadata inspection before install;
- pacman install;
- ownership of `/usr/bin/devlegate`;
- `devlegate --version`;
- `devlegate --help` (or equivalent basic non-mutating smoke);
- pacman removal;
- proof package-owned files are removed;
- proof unrelated user/project state remains untouched.

Do not change the already-green package implementation unless the native host
validation exposes a real defect.
