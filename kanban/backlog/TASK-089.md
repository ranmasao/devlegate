---
"type": "devlegate.ticket"
"title": "Declare and verify Arch package runtime dependencies"
---

## Dogfooding observation (2026-10-19)

`./dev package arch` produced `devlegate-0.5.7.dev0-1-x86_64.pkg.tar.zst`. On a real Arch host, `pacman -Qip` reported **Зависимости: Нищо** (Depends On: None). Yet `ldd /usr/bin/devlegate` resolves dynamic loader, `libc.so.6`, `libgcc_s.so.1`, `librt`, `libpthread`, `libm`, and `libdl`. Installation and launch succeeded, so this is a **packaging metadata defect candidate**, not a reproduced runtime failure. The standalone embeds Python and must not depend on system Python/venv; bundled libs do not imply all GNU runtime libraries are embedded.

## Required work

1. Inspect the Arch package generator/PKGBUILD metadata and precise ELF linkage requirements, including packaged launcher and bundled interpreter extraction; identify which runtime shared objects remain provided by host packages. Derive appropriate native Arch dependency declarations, normally `glibc` and (for `libgcc_s`) `gcc-libs`, but verify ownership with `pacman -Qo` and Arch packaging conventions; do not add Python or venv dependencies.
2. Ensure `pacman -Qip` for a freshly built package reports correct explicit depends. Test clean dependency resolution or an appropriately isolated Arch environment; keep package reproducibility and no implicit package manager operations during host registration.
3. Add package-metadata assertions/regression tests and update distribution docs if necessary. Verify install, `pacman -Qkk`, `devlegate --version`, and removal behavior.
4. Do not confuse the `.pkg.tar.zst.sha256` file with a package. The earlier `pacman -Qip ./*.pkg.tar.*` error was caused by a user-side glob accidentally including the checksum, not a Devlegate packaging defect.

Priority: medium; no block to tested host's current operation. Backlog only.
