---
"type": "devlegate.ticket"
"title": "Finalize Arch-family x86_64 native package and pacman validation"
"depends_on": ["TASK-018", "TASK-060", "TASK-058"]
---

## Milestone

Devlegate 0.5.6 — Arch-family native distribution validation and release readiness.

## Goal

Finish and independently validate the existing Arch-family x86_64 package
produced by the distribution pipeline established by TASK-058. Prove the real
package lifecycle with pacman on an Arch Linux host, retain artifact-bound
evidence, and correct only concrete compatibility or release-readiness defects.

**This is no longer an Arch package builder implementation task.** The
distro-agnostic builder, package validator, `arch` target and `all` graph
integration were implemented by TASK-058 and must be reused, not recreated.

## Existing implementation baseline

TASK-058 provides the implementation foundation:

- `tools/arch_format.py`: deterministic Arch `.pkg.tar.zst` container;
- `tools/package_arch.py`: package the already-built standalone executable;
- `tools/validate_arch.py`: generic, pacman-independent package validation;
- `tools/build_distribution.py`: wheel -> standalone -> Arch build/validation
  graph, plus the `all` target, build reports and package SHA-256 sidecars;
- `./dev package arch`: build and validate Arch distribution artifacts on a
  supported non-Arch host without host pacman, makepkg or zstd tooling.

The verified TASK-058 checkpoint is
`e10eea1572dfc3632de059f8cf1c39e7cf9898f4`;
GitHub Actions run `37925856821` passed tests/coverage and Ruff. That
checkpoint is the implementation reference, not a substitute for real
Arch package-manager validation.

TASK-054 had earlier implementation attempts and review findings, including
checkpoint `b72ee59551219abb6bdb24f124dd22824ac5655e` and CI
`37522140115`. Those historical results do not authorize merging the old
TASK-054 branch into the new implementation. The earlier package-member
mismatch was resolved in that lineage; its remaining native-host requirement
is carried forward here. Historical ticket contents remain in Git history.

## Execution and dependency boundary

- Retain the formal dependency on TASK-058. Perform this work on the current
  product/release line **after TASK-058 is accepted and integrated**. Treat
  the old TASK-054 work branch as historical until its ancestry is reconciled;
  do not blindly rebase/cherry-pick an obsolete native builder/validator over
  the newer TASK-058 modules.
- Avoid rebuilding the standalone runtime inside native package wrapping.
  The package must contain the same validated standalone executable.
- The package is built once on a supported non-Arch host (currently WSL2
  Ubuntu 24.04 is available) and that **exact file** is transferred to a
  separate Arch x86_64 host for native validation.
- Do not modify the source code to accommodate an unverified expectation
  about pacman. First establish package identity and reproduce the problem.
- Keep TASK-059 (source submodule preflight) separate. No watchdog, QA
  transition, RPM, or general distribution-toolchain expansion belongs here.

## Required native Arch validation

Use a disposable environment or a safely controlled Arch x86_64 test host.
Do not overwrite a pre-existing unrelated Devlegate installation or owned
files; inspect package ownership and existing host state first.

### Build-side evidence (non-Arch host)

1. Record the exact Git commit, source checkout cleanliness, architecture,
   build environment, and `./dev package arch` command/exit result.
2. Retain the `.pkg.tar.zst` artifact, its `.sha256` sidecar, the packaging
   build log and the retained standalone build report.
3. Verify the sidecar against the produced artifact; record the SHA-256.
4. Verify the package was produced without Arch build/package-manager tools
   and that the package-format validator independently accepted it.

### Native package-manager evidence (Arch host)

Use the exact transferred `.pkg.tar.zst` (same SHA-256), **no rebuild**:

1. Verify its SHA-256 before any pacman operation.
2. Inspect metadata and payload using `pacman -Qip` and `pacman -Qlp`.
   Check package name, full version/release, x86_64 architecture, dependencies,
   and the expected executable/documentation payload.
3. Install using `pacman -U`, and retain its result.
4. Verify package registration, ownership and file integrity with relevant
   `pacman -Qi`, `pacman -Ql`, `pacman -Qo /usr/bin/devlegate`,
   and `pacman -Qk` commands.
5. Execute `/usr/bin/devlegate --version` and a non-mutating CLI smoke test
   (`/usr/bin/devlegate --help` or equivalent), including a check that the
   installation does not require system Python, pip or a venv.
6. Verify the package's upgrade/reinstallation behavior, where a safe test
   fixture/version is available. Do not fabricate an older released Arch
   package just to claim upgrade coverage. If a genuine upgrade test cannot
   be carried out, record the limitation; the basic install/remove proof
   remains mandatory.
7. Remove through `pacman -R devlegate`. Confirm package-owned files are
   gone, unrelated user/project state survives and no unexpected hook or
   scriptlet has modified it.

A real Arch machine is available for operator-run validation. If native
testing cannot be executed from the worker environment, the worker must
provide an exact reproducible command/transcript plan and stop at review
with the missing host evidence clearly identified. Do not claim native proof
from generic TAR parsing or the project's own validator.

## Targeted hardening only

When native testing exposes a concrete incompatibility, fix it in the
current TASK-058-derived package pipeline, and add a focused regression:

- correct Arch PKGINFO v2 metadata and version/release identity;
- package-manager-readable zstd/tar format and file ownership/permissions;
- accurate version, package type, licensing, and installation provenance;
- no unexpected runtime dependencies (especially Python), files or hooks;
- regular executable at `/usr/bin/devlegate` with correct executable mode;
- no source-only `dev`, project configuration, tests, build tooling or venv;
- embedded payload SHA-256 must match the validated standalone executable;
- removal must not delete user/project state or unrelated files.

Do not duplicate or replace existing successful format tests merely to
satisfy this ticket. Add only evidence-backed missing coverage, especially
cross-checks against pacman/libalpm.

## Acceptance criteria

- Arch package is produced by the TASK-058 portable distribution pipeline on
  a non-Arch x86_64 host and passes generic format validation.
- The exact same package checksum is established on the producer and on the
  native Arch validation host.
- Native pacman inspects, installs, registers, owns and removes the artifact
  without packaging it again; package-managed files are gone after removal.
- Installed executable passes version and non-mutating CLI smoke tests,
  without a system Python, venv or runtime pip dependency.
- Metadata, provenance, licensing and executable payload identity are correct;
  no unexpected package files or scriptlets appear.
- Unrelated state is preserved.
- Any concrete native compatibility defect has a minimal fix and regression
  against the existing implementation; existing standalone/DEB paths stay green.
- Full authoritative CI with coverage and Ruff is green on the exact final
  implementation checkpoint (the existing green TASK-058 run may support an
  unchanged implementation, but does not replace native host evidence).
- The native Arch validation transcript, build-side provenance and package
  SHA-256 are durable and attributable to this ticket.

## Non-goals

This ticket does not reimplement portable package builders, add RPM or other
formats, create an AUR entry, configure package repositories or signing,
implement host systemd policy, or introduce compatibility/migration layers.

## Build-side validation finding — 2026-10-09, checkpoint e10eea1572df

An actual `./dev package arch` build on WSL2 Ubuntu 24.04 using a clean
source checkout at `e10eea1572dfc3632de059f8cf1c39e7cf9898f4`
failed **after** successful wheel and byte-reproducible standalone builds:

```text
Package format tool: {"name": "devlegate-arch-format", ...}
FAILED at arch: validate arch package:
PackageError: Arch package contains unexpected files
```

This is a **generic-build-host** failure and must be corrected before native
pacman inspection/install tests can meaningfully begin. Do not classify it
as a pacman defect or native-host incompatibility. The build log records
`Version: 0.5.6.dev0`, successful duplicate standalone hashes and exact
source identity. The actual archive file and its SHA-256 still need to be
retained from a subsequent successful build.

### Determined implementation mismatch

At this checkpoint `tools/package_arch.py` copies the complete, already
validated standalone `LICENSES/` tree into
`usr/share/doc/devlegate/LICENSES/`.
The standalone assembler dynamically includes several real third-party
license files according to the authoritative compliance manifest, not just
`standalone-compliance-manifest.json`.

However, `tools/validate_arch.py` compares the archive's regular-file names
to a static `expected` set listing **only** the standalone compliance
manifest within the `LICENSES` subtree. Thus real, correctly included
third-party license texts are rejected with `unexpected files`.
The existing unit-test fixtures and mocked package adapter do not exercise
that complete production composition.

### Required focused fix and proof

1. Keep the complete license payload required by the validated standalone
   compliance manifest. Never fix this by deleting redistributable license
   texts or by allowing an arbitrary wildcard `LICENSES/**` set.
2. Build the package's exact authorized file set from the **validated,
   authoritative** standalone compliance manifest/metadata and the fixed Arch
   wrapper members. Retain strict rejection of truly unexpected members.
   Do not trust an unverified manifest embedded in an adversarial package as
   authority for arbitrary extra files.
3. Add a regression executing the **real** Arch package assembly and generic
   validator together against representative standalone input containing
   several compliance license files (including nested categories). Verify
   complete license retention, correct package-member identity, and rejection
   of a genuinely extra member. The existing mocked progress-adapter test
   does not provide this proof.
4. Execute `./dev package arch` on the non-Arch host with a clean checkout
   and record success, generated artifact and SHA-256, generic validator
   result, and retained standalone/build evidence. The existing failed
   build cannot be used for native pacman tests.
5. Only after that, perform the same-checksum native Arch
   `pacman -Qip/-Qlp/-U/-Qi/-Qo/-Qk/-R` and CLI smoke cycle required above.
   Obtain green authoritative CI/Ruff for any source changes.

This is a targeted completion defect in the TASK-058-derived packaging
pipeline, owned by the already revised TASK-054 release-readiness scope.
Do not resurrect or merge the older TASK-054 builder implementation.
