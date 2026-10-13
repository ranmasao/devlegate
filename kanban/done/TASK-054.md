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

## Review decision — 2026-10-10, checkpoint a5b3a71bf00e (REQUEST CHANGES)

Execution `b6dc8b7236f0498cad9ee1080345882f` published
`a5b3a71bf00eefe75e9c5f7d2a65e455d23ae3cf` and correctly reported
`incomplete`. Keep this checkpoint as the basis; do not reset or replace
the shared native-payload work. CI run `37976339179` failed:
**1160 passed, 3 failed, 1 skipped**; Ruff was skipped by the workflow.

### Immediate CI repair (no relaxation of production checks)

1. `test_production_deb_builder_sets_maintainer_and_passes_validator`:
   fake `repo=tmp_path` lacks its authoritative compliance manifest.
   Construct a verified manifest/snapshot fixture and matching standalone
   binary, provenance and build report; continue exercising the real builder.
2. `test_deb_validator_accepts_dependency_free_payload`: the legacy fake
   package provides no trusted `repo` and incomplete source/build evidence.
   Exercise the strict validator against a truly valid, verified fixture.
3. `test_deb_validator_rejects_former_private_payload_and_launcher_symlink`:
   validation currently stops on missing source authority instead of
   reaching the bad launcher. Begin with a trusted valid fixture, mutate
   only the launcher/layout, and prove rejection for the intended reason.
   Do not simply change the assertion to expect the authority error.

### Finish the reusable native package contract

4. **Logical vs physical inventory.** Replace separately maintained
   assembly and validation path lists in `tools/native_payload.py` with
   a single authoritative *logical* inventory derived from a validated
   standalone executable, build report and compliance manifest. Each
   logical entry should describe its role, trusted content/digest, type
   and mode. A small per-format path/layout policy must resolve it into
   an exact *physical* inventory used for both assembly and validation.
   Validate resolved-path collisions, unknown roles, unexpected files,
   directory shape and modes. Do not trust package-embedded metadata
   to authorize new paths or change the expected digest.
5. **Distribution path policy.** `native_payload.py` currently hardcodes
   `usr/bin/devlegate` and `usr/share/doc/devlegate` for all formats.
   Keep the existing binary path unless a concrete target policy calls
   for another path. Allow adapters to choose documented layout of
   executable, documentation, legal snapshots and provenance without
   duplicating payload copying/validation. Debian and Arch need not
   have identical physical trees: test equality of normalized logical
   content and compliance with each chosen native layout. Research and
   verify any distribution-specific policy before changing existing
   installed paths (e.g. license directories); do not invent a rule.
6. **Arch dependency-free metadata.** `validate_arch.py` currently
   accepts unrecognized `.PKGINFO` keys and does not explicitly reject
   added runtime `depend` declarations. Enforce the intentional
   dependency-free package policy in the Arch adapter, with a negative
   test using a syntactically valid Arch package carrying `depend`.
   Preserve legitimate format metadata; do not conflate build-only
   metadata with required installed runtime dependencies.
7. **Independent executable authority.** Make the verified standalone
   digest mandatory for full native payload validation, including at
   the common API boundary; do not permit a caller to omit it and rely
   only on a mutable installation-provenance marker from the package.
8. **Minimal extension seam.** A future format should provide its own
   layout mapping, metadata, encoder/decoder and native policy checks,
   and reuse common materialization and strict payload verification.
   Reuse simple deterministic timestamp/checksum helpers where they
   are actually duplicated. Avoid a speculative plugin registry, new
   dependencies, or implementation of RPM/third format in TASK-054.

### Acceptance tests and external proof

9. Add paired DEB/Arch tests using the same trusted standalone inputs:
   compare logical inventory and independently verify allowed physical
   paths; reject injected extra files/dirs, altered nested licenses,
   changed binary+provenance, illegal modes and incorrect native metadata.
   Verify Debian Installed-Size, no maintainer scripts/runtime Depends,
   Arch package size and absence of declared runtime dependencies.
   Retain current byte reproducibility and DEB/standalone regressions.
10. Run authoritative complete CI at the new exact checkpoint, with
    green coverage, licensing and Ruff. Then run the *real* clean-source
    `./dev package arch` on non-Arch x86_64 and retain artifact, hash
    sidecar, provenance, standalone report and build transcript.
11. Test precisely that checksum-matched artifact on a real Arch x86_64
    host using pacman inspection/install/ownership/integrity/CLI smoke/
    removal; retain the native transcript. If Arch access is unavailable,
    provide concrete safe operator steps and report `incomplete`.

**Disposition: REQUEST CHANGES. Move TASK-054 from review to todo.**
Resume existing work at `a5b3a71bf00e`; preserve control and recovery
history. No release acceptance or merge until the above is verified.

## Review decision — 2026-10-10, checkpoint b64885f495c2 (REQUEST CHANGES)

Execution `e20b057ae2254baf9bf8c0ea61fc59ca` published
`b64885f495c2ab629c2198fe040d6046afb51d40` with `incomplete`.
Retain and resume that checkpoint; do not restart the refactor.
Authoritative GitHub Actions run `38035543428` at this exact SHA
**passed**: 1163 passed, 1 skipped, 78% total coverage, Ruff passed.
The prior DEB fixture failures are resolved. Arch now rejects extra
`.PKGINFO` fields including declared runtime dependencies.

### Finish the native packaging design before release evidence

1. `tools/native_payload.py::_inventory()` currently lists destination
   and source paths but assembly still uses separate hardcoded loops,
   and validation independently applies file-mode/content rules.
   Introduce one logical inventory (role, trusted source/digest,
   regular-file type, mode), resolve it via the selected layout, then
   drive BOTH assembly and verification from those same resolved records.
   Keep generated installation provenance as an explicit generated entry.
2. `PayloadLayout` only parameterizes the executable and documentation
   root. Give future adapters a small, independently configurable path
   policy for licenses and provenance, without duplicating copying
   or validation. Reject absolute paths, `..`, unsafe destinations,
   collisions and invalid layouts. Preserve current DEB/Arch installed
   paths unless a verified distribution rule requires changing them.
   Adapter validators must use the selected resolved physical layout.
3. Full validation currently checks only the binary size and SHA-256
   embedded in `BUILD-PROVENANCE.json`; ensure the complete provenance
   document is equal to independently validated standalone evidence,
   and add a regression changing another provenance field.
4. Add genuinely paired DEB/Arch contract regressions built from
   the same validated standalone input, comparing logical payloads
   after independent decoding while respecting format-specific paths.
   Include nested licenses, extra files/directories, path collisions,
   wrong modes, binary/provenance tampering, native metadata and
   dependency checks. No RPM or speculative extension registry.

### Order of final verification (important clarification)

**Stage A — finish code and focused regressions.** The agent finishes
the common contract and runs targeted tests locally. Do not require
native Arch access during iterative implementation.

**Stage B — freeze candidate and produce real artifact.** Choose one
exact implementation commit; on a clean non-Arch x86_64 checkout of
that revision execute real `./dev package arch`. Retain the binary
package, `.sha256` sidecar, build report, standalone/provenance evidence
and reproducible build transcript. Successful synthetic tests do not
replace this prerequisite.

**Stage C — final checks in parallel.** For that frozen commit, run
the authoritative complete GitHub CI (tests, coverage, Ruff, licensing)
**in parallel with** operator-run validation of the Stage B artifact
on native Arch x86_64 using pacman: inspect, install, verify package
registration/ownership/integrity, CLI smoke and safely remove.
Retain native transcript and confirm the exact artifact SHA-256 before
and after transfer. Do NOT make Arch testing a prerequisite for starting
the final GitHub CI, or vice versa.

If CI or native testing finds a defect requiring code changes, publish
a new checkpoint, rebuild from that revision and repeat both final
checks on the new candidate; do not combine evidence from different
code revisions or different package checksums.

If native Arch access is unavailable to the agent, prepare exact safe
operator commands and report missing external evidence as `incomplete`
rather than claiming acceptance. Avoid reworking the successful CI
baseline without cause. No acceptance until both Stage C checks pass.

**Disposition:** REQUEST CHANGES; return TASK-054 `review` -> `todo`.
Resume from `b64885f495c2`; TASK-062 remains unchanged.

## Review decision — 2026-10-12, checkpoint dbc835f4efc2 (REQUEST CHANGES)

Execution `ea3708414f944c2e96fb5e34ecaa5fd6` published
`dbc835f4efc29187512890c3c83437f5268a4d5d` with `incomplete`.
Keep and reuse this checkpoint. GitHub Actions run `38038532534` at
the exact SHA passed (1163 tests passed, 1 skipped; coverage 78%; Ruff
passed). No need to repeat CI solely to establish that this SHA passed.

Positive changes: `PayloadEntry` and `_inventory()` now drive both
native assembly and verification; the layout includes legal/provenance
configuration; executable SHA-256 is required; full standalone provenance
can be reconstructed from a complete build report.

### Focused blockers before the final artifact

1. **License layout is inconsistent.** `_inventory()` resolves the
   compliance-manifest destination using `layout.licenses`, but all
   ordinary third-party license entries still use
   `f'{layout.documentation}/{relative}'` where `relative` starts with
   `LICENSES/`. Therefore changing `PayloadLayout.licenses` relocates
   the manifest only, not license snapshots. Define and document whether
   license destination is a full install-relative directory or a
   subdirectory of documentation, then consistently use that one root
   for BOTH manifest and every license snapshot in the same resolved
   inventory. Cover a custom non-default license path with real assembly
   and validation and verify that no old license directory remains.
2. **Noncanonical layout destinations.** `_safe_destination()` uses
   `PurePosixPath.parts` (which normalizes `.` and repeated separators)
   but returns a normalized path without checking it equals the original
   input. `_inventory()` stores raw destination strings, while the
   filesystem normalizes them; layouts such as doubled separators can
   produce physical aliasing or packages that fail their own validator.
   Reject noncanonical paths, empty components, trailing separators,
   unsafe roots and physical destination collisions in all layout roles.
   Include targeted tests for bad layouts and file-vs-directory clashes.
3. **Strict provenance must not be optional in the full validator.**
   `native_payload.validate()` performs exact `stable_provenance()`
   comparison only if `standalone_report` contains six selected keys.
   A reduced report can therefore bypass the full document check while
   still supplying `scie.sha256`. The release validation route should
   fail closed without the complete verified standalone authority;
   if limited synthetic fixtures are needed, separate that helper
   clearly from the independent full package validator. Add a negative
   test mutating a different field of `BUILD-PROVENANCE.json` and a
   test where full validation is given a reduced report.
4. **Cross-format proof missing.** This checkpoint modified three
   production modules and no test file. No paired DEB/Arch test yet
   builds packages from the identical validated standalone artifact
   and compares normalized logical members after separate decoding.
   Add this mandatory regression with distinct license/provenance
   paths and legal hashes, including tamper cases. Avoid broad
   duplication of all existing single-format tests.

### Final release gate and execution order

After fixing items 1-4 and targeted regressions, freeze the next exact
source SHA; build real `./dev package arch` from a clean non-Arch x86_64
checkout and preserve archive, hash sidecar, standalone report and build
transcript. Then run full authoritative GitHub CI and native Arch pacman
inspection/install/ownership/integrity/CLI-smoke/removal **in parallel**
for that candidate. Use precisely the same package checksum on Arch;
if code changes, rebuild and repeat both final checks for the new SHA.
Native operator access is external; provide safe exact operator commands
if unavailable and keep result `incomplete` rather than claiming native
compatibility. Preserve existing standalone, DEB and Arch functionality;
do not implement RPM or a packaging plugin framework.

**Disposition:** REQUEST CHANGES. Keep in `review` until an explicit
Kanban transition; this commit records the review, not acceptance.

## Kanban transition — 2026-10-12

Following the published `REQUEST CHANGES` review of checkpoint
`dbc835f4efc29187512890c3c83437f5268a4d5d`, explicitly return
TASK-054 from `review` to `todo`. Continue the existing worker branch
without losing the checkpoint. Address the four recorded review findings
before freezing the release candidate; then build the real Arch artifact
and perform native Arch validation in parallel with final GitHub CI
as specified above. Do not mark the ticket accepted without both checks.

## Review decision — 2026-10-12, checkpoint aa1088c55b21 (REQUEST CHANGES)

Execution `d55e50b4814943a0a5fce7b71ab56044` published
`aa1088c55b2189ab25326e5dd254d1aa75cc4bd9` and reported
`incomplete`. Keep this work branch/checkpoint, do not restart.

Confirmed progress: `_inventory()` consistently places the manifest
and ordinary license snapshots under `PayloadLayout.licenses`; path
canonicalization rejects normalized aliases; full provenance validation
now requires a comprehensive standalone report and always recomputes
`stable_provenance()` instead of silently skipping it.

### Remaining corrections before freezing a release candidate

1. **Fix the now-obsolete DEB synthetic fixtures against the strict
   provenance contract.** `tests/test_deb_package.py::make_verified_package()`
   still writes a report containing only `wheel.version` and
   `scie.sha256`; `test_production_deb_builder_sets_maintainer_and_passes_validator`
   also supplies a reduced report. Both positive full-validator tests
   now conflict with `native_payload.validate()` mandatory report keys.
   Build a complete trusted standalone-report fixture and real expected
   BUILD-PROVENANCE values. Preserve strict fail-closed behavior,
   especially for the negative launcher/symlink test. Provide a
   separate focused regression asserting reduced reports are rejected.
   Do not patch the full validator or mock `stable_provenance()` to
   make these integration tests pass.
2. **License path policy must support an independent install-root.**
   `PayloadLayout.licenses` is currently defined/documented as a
   directory *relative to documentation*; a format needing
   `usr/share/licenses/devlegate` cannot specify it independently
   of `usr/share/doc/devlegate`. Define a minimal explicit
   install-relative license root (while preserving the existing
   DEB/Arch defaults) and use it for all snapshots and the manifest.
   Confirm format adapters and validators consume that layout.
   Avoid an RPM implementation or generic plugin mechanism.
3. **Exercise actual nondefault layouts.** The new
   `test_native_payload_custom_license_root_is_used_for_every_license_member`
   examines `_inventory()` only. Add at least one real shared
   `assemble()` plus `validate()` round-trip for a custom
   independent license root; reject a leftover old LICENSES tree,
   path aliasing, `..`, slash variants, file/directory collisions
   and unauthorized extras. Keep the authority/report checks enabled.
4. **Paired cross-format proof is still missing.** Assemble real DEB
   and Arch packages from the SAME independently verified standalone
   input, decode each with its own format reader, map physical paths
   back to canonical logical roles and compare content, SHA-256 and
   executable/document modes. Include nested licenses, provenance
   tampering (non-hash field), unexpected members, native metadata
   and wrong modes. The existing isolated Arch tests use mocked
   `split_inventory`/`stable_provenance` and cannot substitute.

### CI and final release evidence

At review time, GitHub Actions run `38223077104` for exactly
`aa1088c55b21` was still **in progress**; do not claim it green
without its completed conclusion. The prior checkpoint's run
`38038532534` succeeded, but does not establish this SHA's status.
Run full coverage/tests, Ruff and licensing successfully for final SHA.

After the corrections above, freeze the final implementation SHA and
perform the REAL `./dev package arch` from a clean non-Arch x86_64
checkout. Retain `.pkg.tar.zst`, SHA-256 sidecar, full standalone
build report and build transcript. Then perform authoritative GitHub
CI and actual Arch pacman inspection/install/ownership/integrity/CLI
smoke/removal IN PARALLEL for that candidate and the exact transferred
artifact hash. If access to native Arch is limited to the operator,
provide precise safe commands and keep the ticket `incomplete` until
the evidence exists. Any change to final code or artifact demands
new candidate and corresponding evidence.

**Disposition:** REQUEST CHANGES. Move `review` -> `todo` and
resume from checkpoint `aa1088c55b21`; leave TASK-062 untouched.

## Review decision — 2026-10-12, checkpoint fe94f1131b51 (REQUEST CHANGES)

Execution `e7b5837a2ef6428587b194b82f7799bd` was successfully
replayed by the control lifecycle at `c747089515e0` and published
checkpoint `fe94f1131b51711f8efe7155612f2bb271ea5150`.
The earlier concurrent-edit lifecycle block is resolved. Preserve
this published checkpoint and execution branch; do not reset history.

### Accepted implementation progress

- `PayloadLayout` now defines independently install-relative executable,
  documentation, license, and installation-provenance destinations,
  preserving the current Debian/Arch on-disk defaults.
- A custom `usr/share/licenses/devlegate` root now exercises actual
  shared `assemble()` and `validate()` and checks absence of the old
  license path. The shared resolved inventory is retained.
- Keep strict full provenance authority; do not revert to permissive
  or partial validation to make tests pass.

### Remaining work — before the final package candidate

1. **Repair exact CI regressions.** GitHub Actions run `38223888098`
   on `fe94f1131b51` finished FAILURE: 1169 passed, 3 failed,
   1 skipped; Ruff was skipped. Repair without weakening validation:
   - `test_production_deb_builder_sets_maintainer_and_passes_validator`
     fails `native validation requires complete standalone evidence`.
   - `test_deb_validator_accepts_dependency_free_payload` fails the
     same strict-report requirement.
   - `test_deb_validator_rejects_former_private_payload_and_launcher_symlink`
     checks for `regular file`, but encounters incomplete report first.
   Create a reusable, complete, verified source/build-report fixture
   whose `BUILD-PROVENANCE.json` corresponds to the independently
   computed expected data. For the invalid-launcher test, start from
   a fully valid package and mutate only the launcher, then prove
   rejection for the intended reason. Add a separate negative test
   that a reduced report fails closed. Do not merely mock out
   `stable_provenance()` in the strict integration tests.
2. **Finish one paired DEB/Arch integration regression.** Assemble
   genuine DEB and Arch format archives using one and the same
   verified standalone input, decode with the appropriate readers,
   normalize each layout back to logical file roles, and compare
   content digests and modes. Verify nested license snapshots;
   ensure format-specific metadata is checked independently.
   Include meaningful tamper cases (an unexpected path, changed
   license or non-hash field of build provenance, bad mode).
   Existing single-format tests are useful but insufficient.
3. **Keep layout policy contract consistent through adapters.**
   The shared module supports custom install-relative roots; its
   format-specific validators currently assume default installed
   paths and expose no custom layout parameter. For the current
   Debian/Arch defaults this is fine. Ensure the paired tests prove
   the current default policies; if an adapter needs a nondefault
   layout, pass its selected layout through both assembly and
   validation rather than introducing a third path mapping.
   No RPM builder or speculative plugin framework in this task.

### Final-stage sequencing (unchanged)

After focused regressions pass, choose one exact final source SHA
and build the real Arch package by `./dev package arch` from a clean
non-Arch x86_64 checkout. Retain `.pkg.tar.zst`, `.sha256`, build
report and transcript. For that frozen candidate perform GitHub CI
(tests/coverage/Ruff/licensing) and native Arch pacman inspection,
install, ownership/integrity, CLI smoke and removal **in parallel**.
Ensure the Arch host validates the identical checksum-matched
artifact. If external operator host access is unavailable, publish
safe exact commands and report `incomplete`, not acceptance. Repeat
both final checks on a changed source revision or rebuilt package.

**Disposition: REQUEST CHANGES. Move TASK-054 from `review` to `todo`**
and resume from `fe94f1131b51`. Leave TASK-062 unchanged. After
admission of the next worker execution, do not edit this active
ticket again until its lifecycle completes.

## Review decision — 2026-10-12, checkpoint c6edbb56189d (REQUEST CHANGES)

Execution `5b12060d75794173ab975ae2c57313f8` published
`c6edbb56189d4072de0135f55af8098bb3bcfaad` with `incomplete`.
Preserve and resume that work branch/checkpoint.

### Confirmed finished work

- The previous three DEB failures are resolved. GitHub Actions run
  `38240582856` for this EXACT SHA succeeded: 1173 tests passed,
  1 skipped, 78% overall coverage; Ruff passed.
- The strict full standalone provenance validation remains enabled.
  The DEB integration fixtures now create complete build-report fields,
  reconstruct `BUILD-PROVENANCE.json` using the real
  `stable_provenance()` function, and explicitly test that reduced
  reports are rejected. Keep this improvement; no return to weaker
  verification or another architectural rewrite.
- The common native payload inventory and independent install-relative
  legal/provenance layout from earlier checkpoints remain in place.

### Remaining changes before freezing a release candidate

1. **Restore test isolation.** At `tests/test_deb_package.py:211`,
   `complete_report()` assigns `standalone.split_inventory = lambda ...`
   directly to a loaded module. This fake split implementation persists
   beyond the fixture and can affect other tests sharing that Python
   process, making results depend on test order. Use a scoped pytest
   `monkeypatch` or an isolated explicit dependency for the synthetic
   inventory, and restore state on teardown. Keep the synthetic
   fixture clearly identified as such; do not claim that fake scie
   bytes were really split/verified. Add a focused assertion that
   mutation is scoped if necessary.
2. **Add the missing paired DEB/Arch contract regression.** No changes
   to the common packaging code are needed unless the test exposes
   a defect. Build both native formats from one identical independently
   verified standalone input, extract with each format reader,
   normalize physical layout to logical roles and compare expected
   files, SHA-256 and modes, including nested license snapshots.
   Keep native metadata checks separate. Cover representative
   rejections (unexpected file/path, altered legal snapshot,
   non-hash-field provenance tamper, bad mode). Unit tests using
   artificial standalone bytes and split-inventory stubs are useful
   for focused validation, but must not replace the requested paired
   verification of real inputs. Avoid duplicating all format tests.

### Final package evidence (as previously agreed)

3. After items 1-2, freeze a candidate SHA and perform a clean-source
   non-Arch Linux x86_64 `./dev package arch`; retain the actual
   `.pkg.tar.zst`, SHA-256 sidecar, standalone build report and
   reproducible build transcript. Do not substitute a synthetic
   Arch archive for this proof.
4. For that same candidate run complete GitHub CI and native Arch
   pacman inspect/install/ownership/integrity/CLI smoke/removal in
   PARALLEL, preserving the exact package checksum on transfer.
   A previously green CI (38240582856) proves this checkpoint, not
   the future changed candidate. If native Arch access is external,
   provide safe precise operator commands and retain `incomplete`
   until attributable results exist. Any later code or artifact
   change requires corresponding fresh final evidence.

**Disposition: REQUEST CHANGES. Move TASK-054 `review` -> `todo`.**
Resume `c6edbb56189d`; keep TASK-062 untouched. Commit this review
and Kanban transition atomically; do not edit the active ticket
again after a new worker is admitted until its lifecycle completes.

## Review decision — 2026-10-12, checkpoint 4ac0475eb937 (REQUEST CHANGES: release proof pending)

Execution `58a54e871f65405794c48dc22ff0d062` published
`4ac0475eb937c2474a28cc031fd81d21bad4d25c` with `incomplete`.
Preserve this successful checkpoint and the existing worker branch.

### Confirmed complete

- GitHub Actions run `38244474915` on the exact SHA **succeeded**:
  1174 passed, 1 skipped, 78% coverage; Ruff passed.
- The DEB test fixture now scopes its `split_inventory` override
  through pytest `monkeypatch` instead of mutating a shared module
  permanently. The previous isolation issue is fixed.
- The new `test_deb_and_arch_adapters_preserve_one_verified_logical_payload`
  builds native DEB and Arch format archives from the same prepared
  synthetic payload; both native validators decode/check the results,
  compare file contents/digests/modes while excluding their distinct
  native metadata and installation-provenance fields, and reject an
  unexpected Arch payload member. The shared packaging contract
  now has useful paired regression coverage.

### Remaining acceptance gap: real source-to-native proof

1. **Do not overstate synthetic integration evidence.**
   The new paired test's input is a synthetic `b'standalone'`
   executable; `complete_report()` replaces `split_inventory` with
   synthetic split data, and the Arch builder's input
   `validate()` is monkeypatched to return the prepared tree.
   Thus the test verifies native adapter parity, NOT real
   standalone input authentication or true self-contained runtime.
   Keep this fast regression rather than trying to make every CI
   run build an expensive production scie. Satisfy the remaining
   real-input parity requirement with an attributable end-to-end
   release proof using the same actual independently validated
   standalone archive for the DEB/Arch payload comparison, without
   adding another generic framework. Document precisely what is
   tested with real input and what remains synthetic.
2. **Targeted negative gaps.** The paired test exercises an
   unexpected Arch member but does not mutate a nested license
   snapshot, an unrelated BUILD-PROVENANCE field or a file mode.
   Add focused fail-closed regressions for these material cases
   unless already explicitly covered by another existing test.
   Verify failures occur for the intended validation reason.
   Preserve the currently green DEB/Arch suites.

### Next execution / final-stage sequence

3. On a clean non-Arch Linux x86_64 checkout at the frozen candidate
   SHA, run the genuine `./dev package arch` build. Preserve the
   built `.pkg.tar.zst`, SHA-256 sidecar, standalone build report,
   exact source SHA and build transcript. Verify the package by
   the independent Arch decoder and validator before transfer.
   Preserve or create corresponding real DEB evidence from the
   SAME standalone input to close the source-to-native parity gap.
4. On that same candidate, perform final GitHub CI and **native Arch
   pacman validation in parallel** after the artifact has been built.
   The operator on Arch must verify the exact sidecar SHA-256, inspect
   metadata/files, install, inspect ownership and integrity, smoke
   test CLI and remove safely; retain transcript. If operator-only
   host access is unavailable, prepare executable safe instructions
   and report `incomplete` until native evidence is received.
   Repeat affected final evidence if source or artifact changes.

**Disposition: REQUEST CHANGES / evidence incomplete.** Move
`TASK-054` from `review` to `todo` now and resume at
`4ac0475eb937`. Do not revisit solved design questions or weaken
validators. Leave TASK-062 unchanged. The ticket and transition
must be committed atomically; do not modify the active ticket
after the next worker starts until the lifecycle concludes.

## Review decision — 2026-10-12, real Arch Docker/pacman smoke (REQUEST CHANGES)

Operator supplied a genuine Arch pacman smoke transcript, an Arch package
SHA-256 sidecar and the complete standalone-build report for exact source
checkpoint `35f8d83986e8b4d38528384f93a0894db2b5a543`.
The SHA-256 sidecar states:

    6568bae94acb9c7fe71cfe6642a0a8c361ee58ec9ff618e57da20873456f491a  devlegate-0.5.6.dev0-1-x86_64.pkg.tar.zst

Report scie SHA-256 is
`72b99ba24e54e64e243e936798f0ef1839232505abd62bc192b61e7eba8d58ba`;
its `source_commit` matches the work checkpoint. `sha256sum -c` in the
Arch-container log prints `OK`; metadata shows 0.5.6.dev0-1 x86_64,
no runtime dependencies or install scripts. `pacman -U` installed,
`pacman -Qo /usr/bin/devlegate` succeeded, `devlegate --help` ran,
and `pacman -R` removed it. Missing core/extra sync databases are expected
in the network-isolated disposable container. GitHub Actions
run `38253246179` for that same checkpoint passed: 1175 tests,
1 skip, 78% coverage and Ruff.

**Do not yet accept the Arch package. Two defects are proven by the
native pacman log and independently visible in current source:**

1. **Package directory permissions are wrong / umask-dependent.**
   `pacman -U` warned that archive entries `/usr`, `/usr/bin` and
   `/usr/share` advertise mode `0775` while existing system directories
   are `0755`. `tools/arch_format.py::_tar_bytes()` exports
   `TarInfo.mode` directly from the temporary directory tree, while
   shared native assembly uses `mkdir(..., parents=True)` without
   a deterministic directory mode policy. Set canonical explicit
   directory modes (normally `0755`) for ALL package-owned directories,
   including top-level system paths and nested license directories,
   independent of build umask; retain defined file modes.
   Add a regression that builds under umask `0002` and `0022`,
   checks exact directory modes and archive reproducibility,
   and rejects unexpected modes during native validation.
2. **Missing `.MTREE` prevents the promised full pacman integrity check.**
   Despite a zero command exit status, `pacman -Qkk devlegate` printed
   `devlegate: no mtree file`. The current `tools/package_arch.py`
   writes `.PKGINFO` but no `.MTREE`, and `arch_format` archives
   only the existing staging tree. Generate deterministic Arch-compatible
   `.MTREE` integrity metadata from the finalized payload, include it
   in the package, and update `tools/validate_arch.py`, native format
   tests and metadata membership policy accordingly. Validate its
   contents/hash/ownership/mode assertions rather than merely
   permitting a new unchecked metadata member. Re-run an actual
   `pacman -Qkk devlegate` proving mtree verification works; update
   smoke validation to fail if `no mtree file` appears.

Minor informational observations: the package is currently unsigned
and `builddate = 0` displays Jan 1 1970; signatures are not part
of this prerelease local smoke gate. Do not expand the task into
repository signing, unrelated system integration or packaging redesign.

**Evidence limitation:** the user supplied the sidecar, build report
and transcript, but not the actual `.pkg.tar.zst` bytes. Thus
we independently checked their internal identities and results, not
the package digest by hashing the archive ourselves. Preserve the
actual artifact and rebuild/publish a new sidecar after corrections.

**Next gate:** Resume from checkpoint `35f8d83986e8`. Fix only the
two identified packaging defects. Freeze the new candidate commit,
run authoritative CI on that SHA and repeat the disposable Arch
container install/ownership/`pacman -Qkk`/CLI/removal smoke for the
checksum-matched NEW artifact. Operator does not currently have
access to a physical Arch PC, so Docker native pacman testing is
the accepted provisional host validation. Keep any physical host
service/systemd test separate from this packaging smoke gate.

**Disposition: REQUEST CHANGES. Move `review` -> `todo` atomically.**
Leave TASK-062 unchanged and preserve all prior review history.
Do not edit this ticket after the next worker admission until
Devlegate completes its lifecycle.

## Review decision — 2026-10-12, checkpoint 55040cc8eab0 (REQUEST CHANGES: final umask regression)

Execution `5e36baeee5dd46bba87055d384584e27` produced
`55040cc8eab03d818d7ee0a1fb74a940ac4f16bd` with outcome
`incomplete`. Preserve and resume this checkpoint.

### Fixed and confirmed

- Directory modes are now normalized to 0755 in shared native assembly
  and Arch tar serialization. All package payload directories are
  checked during validation.
- `tools/arch_format.py` now creates deterministic gzip `.MTREE`
  payload metadata; `tools/validate_arch.py` requires and validates
  it. Regression tests cover mtree tampering, directory modes and
  DEB/Arch payload parity.
- CI run `38287502818` passed on this exact work SHA: **1179 passed,
  1 skipped, coverage 78%, Ruff green**.

### One remaining concrete code correction

**`.PKGINFO` file mode still depends on the host umask.**
In `tools/package_arch.py::_package()`, the `.PKGINFO` file is
created using `.write_text()` and is not chmod'd. In
`tools/arch_format.py::_tar_bytes()`, regular-file modes are copied
from `tarfile.gettarinfo()` without normalization, so identical
native builds can emit mode `0664` under umask `0002` and mode `0644`
under umask `0022`. This breaks claimed byte reproducibility across
supported build umasks, even though it does not change installed
payload file permissions. A minimal Python/tarfile reproduction
confirmed the mode difference.

**Required small fix:** Normalize the `.PKGINFO` mode explicitly to
`0644` in the production Arch assembly path before constructing
`.MTREE` and the package. Keep `.MTREE` at `0644`. Do not silently
change the modes of actual installed executable or legal files.

**Test correction:** Existing
`test_arch_archive_is_reproducible_across_umask` and
`test_arch_directory_modes_and_mtree_are_umask_independent` set
`os.umask()` *after* creating their staging files and directories.
They therefore do not actually exercise file creation under differing
umasks. Add/adjust a production-level regression where staging
metadata and directories are created under each of `0002` and
`0022`, and prove identical archive bytes and `.PKGINFO` mode `0644`
(with full restoration of the process umask). Ideally exercise
the actual Arch adapter, not just a hand-populated normalized tree.

### Final external evidence (no further redesign)

After this single targeted correction freeze the NEW candidate SHA.
Run the authoritative CI again if code changes; retain the real
`./dev package arch` artifact, `.sha256`, standalone report and
build log from a clean non-Arch x86_64 source checkout. Repeat
the disposable Arch Linux pacman Qip/Qlp/U/Qi/Qo/Qkk/CLI/R test
against that exact checksum-matched package. Specifically assert
there are **no** `directory permissions differ` warnings and
**no** `no mtree file` warning, and that `pacman -Qkk devlegate`
actually checks installed integrity. Do not reuse the operator's
older package SHA or transcript from checkpoint `35f8d83986e8`.
No physical Arch PC is required for this provisional smoke gate;
the previously agreed isolated Arch Docker/pacman proof is valid.

**Disposition: REQUEST CHANGES.** Move `TASK-054` from `review`
to `todo` atomically, preserve checkpoint `55040cc8eab0`,
leave TASK-062 and TASK-063 untouched. Do not edit the ticket
after a worker has been newly admitted; avoid unrelated changes.

## Review decision — 2026-10-12, checkpoint 3cfb40886edb (REQUEST CHANGES: release evidence only)

Execution `945a48a1bfca484696d1c1386b229976` published
`3cfb40886edbe752b3e040764b02bea84df95db3` as `incomplete`.
Preserve this checkpoint.

### Code correction accepted

The last issue from review `ea3bb72fd0ea` is closed:

- `tools/package_arch.py::_package()` explicitly chmods the actual
  `.PKGINFO` to `0644` after writing it and before generating
  `.MTREE`/tar bytes. `.MTREE` is already explicitly `0644`.
- `tests/test_arch_format.py` now runs the real production Arch
  packaging adapter under umasks `0002` and `0022`, including
  construction of staging files in that scope, compares both
  generated package byte streams, and checks extracted
  `.PKGINFO`/`.MTREE` mode `0644`.
- The fix changes only the Arch builder and associated tests. Prior
  directory-mode normalization and deterministic mtree validation
  are preserved. No more architecture changes requested.

The exact-SHA GitHub Actions run `38297334137` was
**in progress at review time**; do not infer success until it completes.

### Final acceptance proof only

1. On a clean non-Arch Linux x86_64 checkout of this exact candidate,
   run the real `./dev package arch` production pipeline. Preserve
   the generated `.pkg.tar.zst`, its `.sha256` sidecar,
   `devlegate-arch-standalone-build.json`, and build log. The
   standalone build report's `source_commit` must equal the
   finalized work checkpoint. Do not substitute the earlier
   `35f8d83986e8` artifact and checksum.
2. Confirm GitHub Actions `38297334137` passes all authoritative
   tests, coverage and Ruff on this exact checkpoint. If it fails,
   fix the actual regression, re-freeze and rerun CI.
3. Repeat the disposable Arch Linux Docker/pacman smoke test against
   the newly built, checksum-matched artifact: verify sidecar;
   `pacman -Qip/-Qlp/-U/-Qi/-Qo/-Qkk/-R`, nonmutating
   `devlegate --help`, and removal check. Preserve a transcript.
   The native result must NOT report
   `directory permissions differ` or `no mtree file`;
   `pacman -Qkk devlegate` must provide a substantive integrity
   check. The operator has temporarily no physical Arch host;
   validated disposable Arch Docker is the agreed provisional
   package-manager evidence.
4. If host-dependent build or Docker execution is unavailable to
   the worker, give the operator *precise build/output commands*
   and report `incomplete`, not fictitious evidence. Do not change
   code just to satisfy absent external evidence.

**Disposition: REQUEST CHANGES / release evidence pending.**
Move TASK-054 `review` -> `todo` atomically and continue from
`3cfb40886edb`. Leave TASK-062 and TASK-063 untouched. Do not
edit an active ticket while a worker is running.
