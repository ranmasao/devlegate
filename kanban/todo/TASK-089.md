---
"type": "devlegate.ticket"
"title": "Declare and verify Arch and Debian-family package runtime dependencies"
---

## Dogfooding observation (2026-10-19)

`./dev package arch` produced `devlegate-0.5.7.dev0-1-x86_64.pkg.tar.zst`. On a real Arch host, `pacman -Qip` reported **Зависимости: Нищо** (Depends On: None). Yet `ldd /usr/bin/devlegate` resolves dynamic loader, `libc.so.6`, `libgcc_s.so.1`, `librt`, `libpthread`, `libm`, and `libdl`. Installation and launch succeeded, so this is a **packaging metadata defect candidate**, not a reproduced runtime failure. The standalone embeds Python and must not depend on system Python/venv; bundled libs do not imply all GNU runtime libraries are embedded. Git is a mandatory external executable for project operation, and its package dependency must be declared. Both Arch and Debian-family packages need full dependency validation.

## Canonical dependency model — mandatory architecture

Maintain **one canonical, distribution-family-independent inventory of logical runtime requirements** for the Linux x86_64 standalone payload. Do not duplicate the authoritative list as hard-coded native dependency lists in tools/package_arch.py and tools/package_deb.py. Distinguish:

- Mandatory external application tools and capabilities (Git is required for normal project operations).
- Host-provided ELF/ABI components used by the actual launcher and extracted bundled interpreter, based on verified linkage rather than assumed dependencies.
- Bundled, optional, development-only and build-only components, which must not accidentally become native host runtime package requirements.

Give each required component a stable logical identity independent of Arch/Debian package names. Provide small explicit mappings from these logical identities to native package names and justified version/ABI constraints for each supported target family. For example, logical Git maps to Arch git and Debian git; a verified glibc ABI requirement maps to Arch glibc and Debian libc6; a verified libgcc_s provider maps to Arch gcc-libs and Debian libgcc-s1. These are examples to confirm using actual binary requirements and target packaging conventions, **not a substitute for verification**. The canonical inventory must not consist of native package names.

Both native package generators must consume this same canonical inventory, using their respective mappings; adding a third format should only require a new mapping/renderer, not another logical requirement list. Store the canonical inventory in a small declarative packaging-owned representation (for example, TOML consumed via Python stdlib) and keep any helper deliberately narrow. This is packaging/build-only logic, not a new Devlegate runtime dependency. Do not implement a generic package manager, dynamic runtime resolver, compatibility migration, VCS abstraction or new distribution framework.

A missing mapping for any mandatory logical requirement, or an unrepresentable required ABI constraint, must **fail the package build explicitly**; never silently omit it or substitute a guessed provider. Package metadata and dependency order must be deterministic. Preserve no-system-Python, no-pip, no-venv standalone behavior.
## Required work

1. Inspect the Arch and Debian-family package generators and metadata and precise ELF linkage requirements, including packaged launcher and bundled interpreter extraction; identify which runtime shared objects remain provided by host packages. Derive appropriate native Arch dependency declarations, normally `glibc` and (for `libgcc_s`) `gcc-libs`, but verify ownership with `pacman -Qo` and Arch packaging conventions; do not add Python or venv dependencies.
2. Ensure Arch package depends on `git` plus verified providers of needed system libraries, typically `glibc` and `gcc-libs`. For Debian-family packages validate `Depends` includes `git`, `libc6` and the correct library-provider packages (such as `libgcc-s1`, subject to target ABI/distribution). Derive actual names/constraints from supported release and binaries, not a mechanical Arch-to-Debian translation. Inspect both `pacman -Qip` and `dpkg-deb -f` and test isolated dependency resolution; no Python/venv dependency.
3. Add Arch and Debian-family package-metadata assertions/regression tests and update distribution docs. Test both generators against the shared canonical inventory, including changed requirements, verified mapping behavior, deterministic order, and an unmapped-required-component failure; inspect the actual generated .PKGINFO and DEBIAN/control, not only helper results. Validate runtime Git availability/capabilities without assuming one exact Git version (coordinate with TASK-088); correctly separate optional OpenCode and systemd user services from mandatory Git. Verify install, package integrity, `devlegate --version`, and removal on each target family.
4. Do not confuse the `.pkg.tar.zst.sha256` file with a package. The earlier `pacman -Qip ./*.pkg.tar.*` error was caused by a user-side glob accidentally including the checksum, not a Devlegate packaging defect.

## Acceptance — 0.5.7 Arch dogfooding release gate

- Both package generators derive native runtime dependencies exclusively from the same canonical logical requirements and family-specific mappings; no independent hard-coded requirement list.
- Exact Arch package .PKGINFO contains the verified depend entries (checked through pacman -Qip where available); exact Debian control metadata has validated Depends entries (checked with dpkg-deb -f where available). Target provider/ABI claims are supported by linkage and distribution checks.
- On the Arch dogfooding host, install the resulting package, run devlegate --version and a basic Git-backed project operation, then verify clean package removal/reinstallation. Validate corresponding Debian behavior where target host/tooling is available; portable archive-level validations must still run in CI.
- No host Python, pip, venv, optional OpenCode or optional systemd service is accidentally added as a standalone package dependency. No installed runtime or wheel dependencies are added.
- Deterministic packaging, integrity/compliance validation, build reproducibility and scoped tests continue to pass. Do not change bundling, introduce new platforms or broaden the ticket into generic package management.

Priority: **high; 0.5.7 release blocker for existing Arch-machine dogfooding**. The observed host could launch the existing package, but its missing metadata still requires correction before the iteration is declared ready.
