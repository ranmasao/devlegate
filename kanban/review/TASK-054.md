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

## Review decision — 2026-10-09, checkpoint f330f4ae36f8 (REQUEST CHANGES)

Do not accept or integrate checkpoint
`f330f4ae36f81ac15e2db17f2e86e30d96ac3534` yet. It contains a focused
fix, but is not release-ready:

- Positive: `tools/validate_arch.py` now derives expected license members
  from the checked-out, verified standalone compliance manifest, verifies
  the embedded manifest bytes, and the new `test_arch_format.py` regression
  exercises real Arch assembly, nested license names, and rejection of an
  extra member. The packaging graph passes the authoritative manifest context.
- CI run `37947874400` for the exact checkpoint **failed**, although the
  tests-with-coverage step passed. Ruff reported two E501 violations in
  `tools/validate_arch.py` at lines 144 and 147. Fix and rerun.
- The regression mocks standalone validation and uses a synthetic binary:
  it is **not** a successful clean-source `./dev package arch` end-to-end
  proof, much less native pacman compatibility evidence.
- `tools/package_deb.py` and `tools/package_arch.py` still separately
  validate the input, copy the same executable/legal tree, create
  installation provenance, normalize timestamps and generate checksums.
  Meanwhile `validate_deb.py` admits additional payload members under
  documentation / LICENSES that `validate_arch.py` rejects. Shared
  installed-payload policy is still missing.
- The Arch validator matches expected **member names** from the manifest,
  but does not compare the unpacked license snapshot **bytes/hashes** to
  the authoritative standalone material. Matching filenames alone cannot
  prove preservation of exact legal texts.

### Required scope clarification: reusable native payload contract

The next iteration must finish TASK-054 through a **small shared native
packaging layer** reused by both existing DEB and Arch adapters. This is a
targeted cross-format correction to the duplicated payload policy and
supersedes the earlier "targeted hardening only" restriction **only to this
extent**. It must not become a generic distro framework or implement RPM
or any additional output target in this ticket.

1. **Single source of truth.** Use the one independently validated standalone
   archive / executable, associated build report and checked-out validated
   compliance manifest as authority. Represent the logical installed payload
   with one explicit, normalized inventory/contract: relative destination
   paths, required file type and permissions, expected source/hash, required
   license snapshots and provenance rules. Derive the legal inventory from
   the authoritative manifest, including nested files. Never accept a
   self-declared embedded manifest as authorization for extra package members.
2. **Shared assembly.** Factor the *actually duplicated* work out of
   `package_deb.py` and `package_arch.py`: executable and legal-file copying,
   installation-provenance construction, deterministic file/timestamp policy
   and checksum helpers where appropriate. A new format adapter should
   consume this prepared logical native payload instead of reimplementing
   its contents. Avoid double-building the standalone runtime or copying
   source-only `dev`/source integrations into released payloads.
3. **Explicit format boundary.** Keep Debian control fields, version and
   architecture mapping, Installed-Size semantics, `ar`/`data.tar` encoding
   and Debian-specific policy in the DEB adapter. Keep Arch `.PKGINFO`,
   pkgrel, architecture mapping, installed-size semantics and `tar.zst`
   encoding in the Arch adapter. Each adapter supplies only necessary
   distribution-specific metadata/layout mapping and selects its package
   container writer. Do not assume all future distributions use Debian/Arch
   installation paths, metadata or the same dependency model. No dynamic
   adapter registry/plugin system is required.
4. **Shared post-extraction assertions.** Both independent format decoders
   must call a common policy validator on the *actual extracted installed
   payload*. Verify exact authorized regular files and permitted directory
   layout, no unauthorized extra files/symlinks/hooks/runtime dependencies,
   executable mode and SHA-256 identity, provenance marker identity and
   metadata consistency, and manifest-derived license files with the
   **correct exact content/digests**, not only the correct names. The
   `INSTALLATION-PROVENANCE.json` distribution and tool identity are
   adapter inputs, not an excuse to duplicate the shared validation.
   Native-format metadata validation must remain separately strict.
5. **Cross-format parity and regression.** Use the same representative
   validated standalone input (including several nested license snapshots)
   to assemble DEB and Arch through their real adapters. Assert their
   normalized logical payload is identical except for deliberately
   format-specific provenance/metadata/layout. Both must reject injected
   extras, missing licenses, changed license contents, changed manifest,
   binary tampering, wrong execution mode and a broken provenance marker.
   Keep the existing valid DEB compatibility and format-specific tests green.
   Do not weaken Arch validation to match the previous DEB gaps.
6. **Minimal integration contract.** Document the short, concrete path for
   adding a third native format later: supply metadata/layout mapping,
   format encoder/decoder, native-format checks and an integration target;
   reuse the common payload assembly and verification without copying
   DEB/Arch code. Show this seam in tested interfaces or a focused
   design note. Do not add speculative generic metadata schemas, extension
   registries or build-time dependencies just to demonstrate extensibility.
7. **Build and release proof.** Fix Ruff E501, obtain authoritative green
   tests/coverage/Ruff on the new exact checkpoint. Run the **real**
   `./dev package arch` from a clean non-Arch checkout and retain artifact,
   sidecar, hash, report and logs. Then validate the **same hash** on native
   Arch with the full pacman inspection/install/ownership/integrity/CLI/
   removal transcript. Preserve all existing evidence boundaries above.
   Do not claim native proof from mock tests, self-produced tar parsing or
   a different archive. If native host access is unavailable to the agent,
   state the limitation and provide reproducible operator-run commands.

### Review acceptance delta

In addition to the pre-existing TASK-054 criteria, acceptance now requires:

- One shared authoritative native payload inventory, used by **both**
  builders and independent package validators; no duplicated fixed license
  allowlists or divergent permissive DEB validation.
- DEB and Arch produce equal logical payload content and enforce the same
  allowlist/integrity tests, while retaining their respective native rules.
- A future distro adapter needs no reimplementation of payload-copying,
  legal manifest handling or shared installed-file checks.
- Clean `./dev package arch` proof, exact-artifact native pacman lifecycle
  evidence, and fully green CI at the final reviewed code SHA.

Retain the existing `f330f4ae` changes as a starting point. This review
is a request to **complete and refactor the current implementation**, not
permission to resurrect the pre-TASK-058 Arch packaging branch.

## Review decision — 2026-10-09, checkpoint 6d366ee1877d (REQUEST CHANGES)

Execution `e18bf6d7fbb640578cf802f750afbe1d` produced
`6d366ee1877dc773bc5fbe3a1bc67768b00872a6`, conclusion `incomplete`.
The new `tools/native_payload.py` is a useful shared starting point:
DEB and Arch builders call `assemble()`, and their production graph
validators call `validate()`. Retain the checkpoint and complete the
implementation rather than starting again.

**CI is not green.** Run `37950620748` finished with 1161 passing tests,
2 failures and 1 skip; Ruff did not run. Required immediate corrections:

1. `test_real_arch_assembly_retains_manifest_license_tree_and_rejects_extra`
   still expects `unexpected files`, but shared validation raises
   `native package contains unexpected files`. Update the negative
   assertion to the intended shared diagnostic, preserving coverage.
2. `test_devlegate_owned_source_has_exact_eupl_header` fails because
   `tools/native_payload.py` lacks the mandatory EUPL-1.2 header after
   the shebang.

**Remaining shared-contract review findings:**

3. `validate_deb.validate()` uses strict shared checks only when
   `repo` is provided. The existing fallback is less strict; make
   successful package validation consistently require verified source
   authority. Keep limited metadata inspection separate if necessary.
4. The strict DEB branch currently returns before checking actual
   `Installed-Size` against the extracted payload. Preserve this
   Debian-specific invariant on every successful validation path.
5. `native_payload.validate()` unconditionally discards `.PKGINFO`
   from its file inventory. Only the Arch adapter should exclude its
   package metadata; an unexpected Debian payload member must fail.
6. Comparing a binary SHA-256 only with a provenance marker inside the
   *same* package is insufficient independent verification. Bind the
   installed executable to the previously validated standalone build
   report or equivalent authoritative inventory. Check required fixed
   legal documents as well as manifest-listed license snapshots.
7. Enforce exact file and directory inventory, member types and
   non-executable document modes consistently across the two formats.
   Both should reject altered nested licenses, missing files and
   additional payload members through their real decoders.
8. `native_payload.assemble()` currently copies the full
   `LICENSES/` tree if the authoritative manifest is absent. Make
   manifest validation mandatory in the shared API, with no permissive
   alternate route.
9. Add paired DEB/Arch tests from the same validated input, asserting
   equivalent logical payload except deliberately format-specific
   metadata/provenance. Preserve DEB-only and Arch-only metadata tests,
   byte reproducibility, existing standalone behavior, and stdlib-only
   runtime packaging. Do not add third-format builders in TASK-054.

**Outstanding release evidence (mandatory):**

- From a clean non-Arch x86_64 checkout at the exact final SHA, run
  `./dev package arch`; retain artifact, SHA-256 sidecar, build report
  and evidence log. The isolated builder regression is not sufficient.
- On a real Arch x86_64 host, test the same checksum-matched artifact
  with native `pacman` inspection, installation, package registration,
  ownership/integrity, CLI smoke and removal; retain the transcript.
- Obtain fully green CI (tests, coverage, Ruff, licensing) at the
  final implementation SHA. Do not mark complete without the evidence.

**Disposition:** Return TASK-054 from `review` to `todo`.
Resume from `6d366ee1877d` with the existing shared payload layer.
The scope remains the DEB/Arch native payload contract and real Arch
release validation, not development of RPM or a speculative packaging
framework.

## Review decision — 2026-10-09, checkpoint 80e449e36b7d (REQUEST CHANGES)

Execution `13b3f65e8b6449829fef2262ef618c5a` returned `incomplete` at
`80e449e36b7d5bbf1e098ae1b13ecb9aaaf23128`. Preserve and resume this
published checkpoint; do not restart the DEB/Arch shared-payload refactor.

### Latest authoritative CI: run 37960817292

**FAILED: 4 failed, 1159 passed, 1 skipped.** Ruff was skipped.
Repair the four exact failures **without weakening strict validation**:

1. `test_real_arch_assembly_retains_manifest_license_tree_and_rejects_extra`
   (`tests/test_arch_format.py:126`): the validator raises
   `package_standalone.PackageError('native package contains unexpected files')`
   with the expected diagnostic, but `pytest.raises(validate_arch.PackageError)`
   does not catch it. Investigate class identity across test
   `importlib.util.spec_from_file_location` loading and
   `package_standalone` vs `tools.package_standalone` import paths.
   Ensure one consistent production exception identity and retain this
   real negative test; do not catch broad `Exception` to mask the mismatch.
2. `test_production_deb_builder_sets_maintainer_and_passes_validator`: the
   mocked standalone fixture uses `repo=tmp_path`, which has no validated
   `packaging/standalone-compliance/manifest.json`, so shared assembly
   fails correctly. Supply authoritative manifest, correct snapshot
   contents, fixed legal docs, provenance and matching report/digest,
   while still exercising the actual DEB package builder and validator.
3. `test_deb_validator_accepts_dependency_free_payload`: the legacy
   fixture calls full validation without trusted `repo` or a complete
   standalone build report. Construct a genuinely valid trusted fixture
   and pass required authority; do not restore permissive validation.
4. `test_deb_validator_rejects_former_private_payload_and_launcher_symlink`:
   absence of authority fails first, so the target invalid symlink is
   never tested. Use a valid trusted baseline, mutate only the intended
   invalid member, and assert the actual link/file-type rejection.
   Merely changing the expected error to the authority error is not valid.

### Contract follow-through

- Keep metadata-only inspection separate from complete validation. Both
  DEB and Arch full validators must fail closed without the trusted
  manifest and standalone build report.
- Fix the `tools/validate_deb.py` CLI: `main()` currently calls `validate()`
  without `repo`, although full validation now requires verified source
  authority. Add the necessary explicit CLI input and a real CLI test.
- Confirm format-specific checks are retained (Debian Installed-Size,
  scripts, runtime dependencies; Arch .PKGINFO, size, format identity).
  Do not let the shared payload layer accept extra paths or weakened modes.
- Add paired real DEB/Arch tests against the same verified standalone
  source/manifest, covering exact payload parity, nested license hashes,
  unexpected files, binary+provenance tampering, and invalid file types.
  Preserve existing reproducibility and stdlib-only runtime properties.
- Run targeted regressions first, then authoritative complete tests,
  coverage, licensing checks and Ruff at the exact final checkpoint.

### Release proof is still mandatory

- Build the real `./dev package arch` in a clean non-Arch x86_64 checkout;
  retain the `.pkg.tar.zst`, `.sha256` sidecar, logs, report and provenance.
- On a real Arch x86_64 host, validate that exact SHA-256 package with
  `pacman -Qip/-Qlp/-U/-Qi/-Ql/-Qo/-Qk/-R`, CLI smoke, and safe removal;
  retain attributable transcript and checksums. No rebuild on Arch.
- If the worker cannot access the host, provide safe exact operator
  commands, state the missing evidence, and return `incomplete`.
- No RPM/third format, general plugin registry or unrelated feature work.

**Disposition:** Return TASK-054 `review` -> `todo`, starting from
`80e449e36b7d`; do not accept or integrate until CI and native evidence
are complete.
