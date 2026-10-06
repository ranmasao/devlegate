# Arch Host Validation

Validated on 2026-10-06 in an `archlinux:latest` x86_64 container with the
package manager operating on a disposable pacman root. The package was
`devlegate-0.5.6.dev0-1-x86_64.pkg.tar.zst`.

Command:

```sh
docker run --rm --network none -v "$PWD:/workspace:ro" archlinux:latest \
  /workspace/tools/validate_arch_host.sh \
  /workspace/dist/arch-validation/devlegate-0.5.6.dev0-1-x86_64.pkg.tar.zst
```

Observed lifecycle evidence:

```text
Name            : devlegate
Version         : 0.5.6.dev0-1
Architecture    : x86_64
Depends On      : None
Install Script  : No
owner: devlegate
devlegate 0.5.6.dev0
package-owned files removed; unrelated state preserved
```

The script also completed `devlegate --help` successfully, verified removal of
the package-owned executable after `pacman -Rns`, and verified that the
disposable unrelated user/project state file remained present.
