---
"type": "devlegate.ticket"
"title": "Declare and verify Arch and Debian-family package runtime dependencies"
---

## Dogfooding observation (2026-10-19)

`./dev package arch` produced `devlegate-0.5.7.dev0-1-x86_64.pkg.tar.zst`. On a real Arch host, `pacman -Qip` reported **Зависимости: Нищо** (Depends On: None). Yet `ldd /usr/bin/devlegate` resolves dynamic loader, `libc.so.6`, `libgcc_s.so.1`, `librt`, `libpthread`, `libm`, and `libdl`. Installation and launch succeeded, so this is a **packaging metadata defect candidate**, not a reproduced runtime failure. The standalone embeds Python and must not depend on system Python/venv; bundled libs do not imply all GNU runtime libraries are embedded. Git is a mandatory external executable for project operation, and its package dependency must be declared. Both Arch and Debian-family packages need full dependency validation.

## Required work

1. Inspect the Arch and Debian-family package generators and metadata and precise ELF linkage requirements, including packaged launcher and bundled interpreter extraction; identify which runtime shared objects remain provided by host packages. Derive appropriate native Arch dependency declarations, normally `glibc` and (for `libgcc_s`) `gcc-libs`, but verify ownership with `pacman -Qo` and Arch packaging conventions; do not add Python or venv dependencies.
2. Ensure Arch package depends on `git` plus verified providers of needed system libraries, typically `glibc` and `gcc-libs`. For Debian-family packages validate `Depends` includes `git`, `libc6` and the correct library-provider packages (such as `libgcc-s1`, subject to target ABI/distribution). Derive actual names/constraints from supported release and binaries, not a mechanical Arch-to-Debian translation. Inspect both `pacman -Qip` and `dpkg-deb -f` and test isolated dependency resolution; no Python/venv dependency.
3. Add Arch and Debian-family package-metadata assertions/regression tests and update distribution docs. Validate runtime Git availability/capabilities without assuming one exact Git version (coordinate with TASK-088); correctly separate optional OpenCode and systemd user services from mandatory Git. Verify install, package integrity, `devlegate --version`, and removal on each target family.
4. Do not confuse the `.pkg.tar.zst.sha256` file with a package. The earlier `pacman -Qip ./*.pkg.tar.*` error was caused by a user-side glob accidentally including the checksum, not a Devlegate packaging defect.

Priority: medium; no block to tested host's current operation. Backlog only.
