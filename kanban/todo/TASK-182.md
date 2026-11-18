---
"type": "devlegate.ticket"
"title": "Refresh pinned bundled CPython 3.12 to security release 3.12.16"
---

## Milestone

Devlegate 0.5.8. Backlog until explicitly scheduled by the curator. This is a bounded security refresh of the existing Linux x86_64 GNU standalone runtime, not a decision to change the Python minor-version compatibility policy or replace the packaging architecture.

## Context

As of 2026-11-17, the integrated 0.5.8 standalone builder in `tools/build_standalone.py` pins CPython 3.12.14 from Python Standalone Builds (PBS) release `20260901`, with exact archive identity/hash, Science/PEX toolchain inputs, mirrored provider metadata, and reproducible build proofs. CPython 3.12.15 was released on 2026-09-30, followed by **3.12.16 on 2026-10-25**, an expedited source-only security release. The 3.12.16 release addresses compromised eMudhra intermediate CA keys in `ssl` (CVE-2026-115557), along with other security fixes including bundled Expat and tarfile filtering. 3.12 remains in security-only maintenance until October 2028.

Verified public entry points:
- https://www.python.org/downloads/release/python-31216/
- https://github.com/astral-sh/python-build-standalone/releases
- https://github.com/astral-sh/python-build-standalone/releases/tag/20261026 (candidate containing 3.12.16; inspect exact assets and any newer applicable immutable PBS release at execution time)

The official CPython 3.12 security release is source-only; the packaged executable uses a third-party PBS binary distribution, whose independent artifact identity must be proven. Do not assume the existing PBS archive SHA or glibc/ELF requirements continue unchanged.

## Goal

Update the pinned **bundled** CPython runtime from 3.12.14 to the newest proven 3.12 security-patched build **at least 3.12.16** available for the existing Linux x86_64 GNU `install_only_stripped` packaging contract. Keep the canonical minimum source/wheel Python requirement `>=3.12`, PEX eager-scie design, and zero-runtime-dependency goals unchanged.

## Required work

1. Confirm official CPython release status and select an immutable PBS release that contains CPython >=3.12.16 for `x86_64-unknown-linux-gnu`, `install_only_stripped` with the correct libc and standard GIL-enabled flavor. Record exact PBS release, archive filename, source/version identity, independently obtained SHA-256 and size. Do not pin an unverified URL, invent hashes, or substitute another ABI/platform/variant. Select the newest usable security-patched 3.12 build without automatically switching minor versions or repinning unrelated tools.
2. Update the exact PBS constants and associated mirrors/build reports in `tools/build_standalone.py`, plus all pinned-reference assertions and independent protocol fixtures in `tests/test_standalone_builder.py` and other genuinely affected tests. Do not weaken checks by making expected values tautologically derive from the builder's output. Preserve the offline/hardened Science consumer conformance, provenance, SHA/size rejection and reproducibility policy, including the absence of mandatory live third-party metadata requests in routine CI.
3. Re-evaluate bundled runtime library/ELF identity and host ABI requirements from the actual new executable/archive: glibc, libgcc and Git floors, linked libraries, native Debian/Arch dependency declarations, and license/notice/provenance inventory as needed. Modify `packaging/linux-runtime.toml`, validated compliance snapshots or docs only if justified by newly observed evidence. Do not conflate CPython upstream source version, PBS release, and host Python/build Python versions.
4. Demonstrate the packaged executable reports the expected bundled Python version and supports ordinary Devlegate CLI/real Git smoke paths. Retain the existing reproducible A/B standalone build validation and relevant negative corrupted/mismatched-input tests. Exercise impacted standalone/native packaging checks as appropriate; the exact checkpoint must pass hosted full CI and Ruff. Avoid redundant unrestricted full local pytest during ordinary edits; use semantic focused tests plus `./dev lint` and `git diff --check` before `completed`.
5. Update directly relevant distribution/build reference documentation to the chosen version/release/hash and document a concise provenance/security rationale. Ensure no stale 3.12.14 assertions remain where they represent **current** runtime identity; do not rewrite historical evidence or invent migration support.

## Acceptance

- The integrated 0.5.8 builder pins a verified CPython 3.12.16-or-newer security-patched PBS artifact, for exactly the existing Linux x86_64 GNU target/flavor, with immutable release and independently verified digest.
- Real produced standalone output embeds the selected CPython; provenance reports and independent pinned Science/PBS conformance tests agree.
- Offline builder/source verification, reproducibility, native dependency/compliance checks and focused negative paths remain effective; no accidental loosening of security or ABI guarantees.
- The worker checkpoint passes exact-SHA hosted GitHub Actions CI, including the existing full test suite and Ruff. Independent reviewer checks artifact provenance and the actual smoke/build evidence, not merely a green unit test.

## Boundaries

No Python minor-version migration, no change to `requires-python`, no new interpreter support matrix or security-update daemon, no dependency on hosted interpreter version, no changes to service state/control plane, unrelated release workflows or agent permissions. Existing v0.5.7 distributed artifacts stay immutable. Do not modify the installed self-hosting daemon or real execution resources during implementation.
