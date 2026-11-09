# Release Packaging

GitHub's automatic `Source code.zip` and `Source code.tar.gz` archives preserve
Git submodule entries as gitlinks. They do not contain the submodule files.
Devlegate releases whose selected source tree contains a submodule therefore
also need a `devlegate-X.Y.Z-full-source.tar.gz` asset. For a `vX.Y.Z` release
tag, the public asset is named without the leading `v`:

```text
vX.Y.Z -> devlegate-X.Y.Z-full-source.tar.gz
```

That archive includes
the pinned NanoYAML source and can be built without network access to obtain
repository source dependencies.

## Standalone Binary Archive

The standalone executable has a separate deterministic distribution unit:

```text
devlegate-X.Y.Z-linux-x86_64.tar.gz
devlegate-X.Y.Z-linux-x86_64.tar.gz.sha256
```

`tools/package_standalone.py` assembles this archive from a verified standalone
executable, its build record, and the repository-owned compliance snapshot
under `packaging/standalone-compliance/`. Package assembly does not download
legal material. The archive contains one top-level directory with the
unversioned `devlegate` executable, Devlegate legal files,
`THIRD_PARTY_NOTICES.md`, `BUILD-PROVENANCE.json`, and the exact third-party
license snapshots used by the manifest. Its Linux x86_64 target is explicitly
the glibc target and records the GNU scie-jump asset identity.

`tools/validate_standalone_package.py` independently checks the archive root,
safe extraction, executable modes, artifact identity, manifest hashes, notice
references, and absence of transient build paths.

The packaged `DEVLEGATE-LICENSING.md` is a byte-identical compliance snapshot of
the repository-root `LICENSING.md`, required by the package integrity checks. It
is distribution payload, not an ordinary repository-relative Markdown document;
generic repository link audits must exclude this snapshot. Validate its content
through the snapshot identity and package compliance checks instead.

## Debian Proof Package

When standalone publication is enabled by release policy, the workflow also
builds and publishes:

```text
devlegate_<version>_amd64.deb
```

The package is assembled only from a validated standalone archive. It contains
the standalone executable directly at `/usr/bin/devlegate`, with no private
executable plus launcher symlink, legal files, and the archive hash proof.
It declares Git and the verified host ELF providers, has no maintainer scripts, and does not
install a systemd unit. The Debian package is built twice and byte-compared
before either copy can reach the release upload job.

The standalone payload can also be assembled as the native Arch package
`devlegate-X.Y.Z-1-x86_64.pkg.tar.zst`. Its metadata and mtree are deterministic
and validated without `pacman`, and its extracted executable is smoke-tested.
The current release workflow does not upload this package. It publishes
standalone and Debian assets only for selected release versions at or above
`0.6.0`; having a validated 0.5.6 builder does not itself publish a 0.5.6
binary asset.

## Native Runtime Dependency Evidence

`packaging/linux-runtime.toml` is the sole mandatory logical inventory for the
Linux x86_64 GNU standalone. `tools/native_dependencies.py` reads it with stdlib
`tomllib` during packaging and validation. Neither is installed runtime code or
a wheel dependency. Requirements are ordered by logical identity, independent
of TOML insertion order. Both package validators compare the actual archive
metadata to this authority, including repeated Arch `depend` fields. An absent
mandatory mapping or an ABI without an explicit matching translation fails
the build. A new native format needs a mapping/renderer, not another requirement
list. When an ABI changes, verify its provider and package floor before changing
both the logical ABI and the corresponding family mappings.

The pinned inputs inspected for 0.5.7 are GNU scie-jump 1.13.0 (SHA-256
`a5afd5cd99ac201865d329980e9856521d394e2780441c4abc2f73add5b7e2d0`) and PBS
CPython 3.12.14 / 20260901 (archive SHA-256
`72748da13197c1fb161e3afeef20a6a385ff24f2165e6e2758e47008e7faba4c`).
Inspection used the packaged `/usr/bin/devlegate`, `SCIE=split` to extract its
actual embedded PBS archive, and `readelf -lW`, `-dW`, and `-VW` on the launcher,
`python/bin/python3.12`, `libpython3.12.so.1.0`, and the remaining PBS ELF files.
Do not infer host dependencies merely from bundled library license records.

| Logical requirement | Verified payload requirement | Arch declaration | Debian declaration |
| --- | --- | --- | --- |
| `gcc-unwind` | Launcher needs `libgcc_s.so.1`: `GCC_3.0`, `GCC_3.3`, `GCC_4.2.0` | `gcc-libs>=4.2.0` | `libgcc-s1 (>= 10)` |
| `git-tool` | External repository operations and worktree management | `git>=2.31` | `git (>= 1:2.31)` |
| `gnu-libc` | GNU loader and libc ABI through `GLIBC_2.17` | `glibc>=2.17` | `libc6 (>= 2.17)` |

The launcher's direct `DT_NEEDED` entries are `libgcc_s.so.1`, `librt.so.1`,
`libpthread.so.0`, `libm.so.6`, `libdl.so.2`, and `libc.so.6`; its maximum glibc
symbol version is `GLIBC_2.16`. Its ELF interpreter is
`/lib64/ld-linux-x86-64.so.2`. The extracted interpreter additionally needs
`libutil.so.1`, and raises the combined floor to `GLIBC_2.17`.
The bundled libpython also references `ld-linux-x86-64.so.2` directly.
All these objects except `libgcc_s.so.1` belong to the glibc provider.

Provider checks on Arch used `pacman -Qo /usr/lib/libc.so.6
/usr/lib/ld-linux-x86-64.so.2 /usr/lib/libgcc_s.so.1` and the other glibc SONAMEs.
On the available rolling image, glibc was `2.44+r50+g1848099f063e-1`;
`libgcc` owned `libgcc_s.so.1` at `16.2.1+r23+gd564253eb6c8-1`.
`pacman -Qi gcc-libs libgcc` confirmed that `gcc-libs` depends on `libgcc` and
the latter provides `libgcc_s.so=1-64`. The traditional `gcc-libs` dependency
therefore remains valid across that package split; it is not a claim that the
new meta-package directly owns the file. GCC 4.2 is the floor for the required
unwind symbol generation, not a requirement for a host compiler.

Debian 12 `dpkg-query -S` verified `libc6:amd64` ownership of
`/lib/x86_64-linux-gnu/libc.so.6` and `libgcc-s1:amd64` ownership of
`/lib/x86_64-linux-gnu/libgcc_s.so.1` (versions `2.36-9+deb12u10` and
`12.2.0-14+deb12u1`). The mapping targets current Debian-family releases using
GCC 10+ `libgcc-s1` naming, whose exports include all the required
unwind versions. Its conservative `>= 10` floor is explicit, rather than a
mechanical translation of `GCC_4.2.0` into a Debian package version. Historical
Debian releases with only `libgcc1` are not covered by this mapping. Provider
packages can themselves impose newer libc requirements than the payload floor;
the native resolver must honor those transitive constraints.

OpenSSL, SQLite, zlib and the other bundled CPython facilities do not require
their native development packages. PBS's unused `_crypt` extension has an
additional `DT_NEEDED` for `libcrypt.so.1`; it is not imported by Devlegate or
its normal project operations and is not a mandatory capability. Tcl/Tk DSOs
are bundled. Optional OpenCode and systemd user-service integration are not
mandatory native dependencies. The standalone requires no host Python, pip,
or venv. Git remains mandatory even when `devlegate --version` works without it.
The package metadata intentionally does not pin one exact Git version. Its
minimum is Git 2.31 because resource binding uses `rev-parse
--path-format=absolute`, introduced in the upstream
[2.31 release notes](https://github.com/git/git/blob/v2.31.0/Documentation/RelNotes/2.31.0.txt).
Debian's version constraint includes epoch `1:` to avoid accidentally accepting
older `1:2.x` packages. Debian 12 and Ubuntu 22.04+ have suitable stock Git;
older releases need a suitable backport. Validate actual repository discovery
and worktree capabilities on the target as well as package version resolution.

### Target installation checks

Build the two packages from a clean source checkout with `./dev package arch`
and `./dev package deb`. Portable tests in `tests/test_arch_format.py` and
`tests/test_deb_package.py` inspect the actual generated `.PKGINFO` and Debian
control archive, exercise changed inventories/providers, missing mappings,
unsupported ABIs, deterministic output, and metadata tampering.

For a disposable target installation, mount the artifacts and smoke script:

```sh
docker run --rm -v "$PWD/dist:/packages:ro" \
  -v "$PWD/tests/native_package_smoke.sh:/smoke.sh:ro" \
  archlinux:base sh /smoke.sh arch \
  /packages/devlegate-0.5.7.dev0-1-x86_64.pkg.tar.zst
docker run --rm -v "$PWD/dist:/packages:ro" \
  -v "$PWD/tests/native_package_smoke.sh:/smoke.sh:ro" \
  debian:12 sh /smoke.sh debian \
  /packages/devlegate_0.5.7.dev0_amd64.deb
```

The script checks native metadata (`pacman -Qip` / `dpkg-deb -f`), dependency
resolution, package integrity, `devlegate --version`, the installed application's
Git adapter via its bundled interpreter, Git capabilities, and removal followed
by reinstallation. It rejects host Python/pip/OpenCode executables. Arch's base
container has documentation `NoExtract` rules; the script uses a container-local
config without these rules to test the complete package integrity. Run it only
in disposable containers, never as a host maintenance command.

On the real Arch dogfooding host, also install with `pacman -U` after resolving
the declared dependencies, run a Git-backed project operation, check
`pacman -Qkk devlegate`, remove, confirm the binary and package-owned docs are
gone, and reinstall. Container success does not replace this release gate.
Use the exact `.pkg.tar.zst` filename with `pacman -Qip`; the adjacent
`.pkg.tar.zst.sha256` is a checksum, not a package. A wildcard such as
`*.pkg.tar.*` includes it and produces a user-side package-inspection error.

## Builder

`tools/build_full_source.py` (also exposed as `tools/build-full-source`)
packages an explicitly selected ref or commit:

```sh
python3 tools/build_full_source.py \
  --repo /path/to/devlegate \
  --ref vX.Y.Z \
  --version vX.Y.Z \
  --output-dir /tmp/release-assets
```

The command writes (the tag/version argument retains its leading `v`):

- `devlegate-X.Y.Z-full-source.tar.gz`
- `devlegate-X.Y.Z-full-source.tar.gz.sha256`

The archive root is `devlegate-X.Y.Z/`. `SOURCE-MANIFEST` records the release
as `vX.Y.Z`,
the exact Devlegate commit, and every recursively materialized submodule path
and commit. Git metadata is removed before archiving. The builder refuses an
unresolvable ref, malformed version, unavailable submodule object, or
gitlink/check-out mismatch.

The archive member order, metadata, timestamps, and gzip header are normalized.
The timestamp is derived from the selected commit, so repeated builds from the
same source state produce identical bytes and SHA-256 values.

Historical source trees are handled from their actual Git tree shape. A
vendored directory such as `src/nanoyaml` or
`src/devlegate/_vendor/nanoyaml` is copied as ordinary source. A gitlink is
initialized at exactly its recorded commit, recursively, and recorded in the
manifest. The builder never uses `git submodule update --remote`.

## GitHub Workflow

`.github/workflows/release.yaml` reacts to an already published GitHub Release.
It first selects and validates the existing annotated tag, release object, commit,
source shape, and release version. Normal release tags must match `vX.Y.Z`.

The workflow has separate read-only build paths:

```text
read-only release selection
        |
        +-- trusted master full-source tooling against selected tag
        |
        +-- selected-tag standalone tooling, pins, and compliance manifest
                         |
                         v
                 validated workflow artifacts
                         |
                         v
                 write-only upload job
```

The full-source path is conditional on gitlinks and uses trusted tooling from
`master` against the selected tag. When enabled, the standalone path checks out
the exact annotated-tag commit recursively and executes that tag's own
standalone builder, packager, validator, and compliance manifest.
The standalone builder uses CPython `3.12.14` as its build-host interpreter.

The workflow also supports `workflow_dispatch` with an explicit `tag` input for
retry or historical recovery. The release and tag must already exist. Existing
asset names are replaced only by the workflow's explicit `--clobber` retry
policy after all tag checks pass.

Before upload, the read-only build job extracts the actual archive and checks
its root, SHA-256 sidecar, `SOURCE-MANIFEST`, selected tag target, absence of
Git metadata, and every materialized submodule path and pinned commit. The
standalone path builds and proves the executable, packages twice under distinct
assembly roots, validates both compliance archives, and runs `version` from the
extracted archive with an isolated environment.

The validated archive and sidecar cross the job boundary as a workflow
artifact. The full-source and standalone pairs use separate workflow artifact
names. Only the separate upload job has `contents: write`; it checks exact
filenames, re-checks the existing release, and uploads only to that exact tag.
The explicit `--clobber` behavior is the documented idempotent retry policy for
a requested release. The upload job does not check out code, set up Python, or
execute repository tooling. Same-tag runs are serialized without cancelling an
earlier run.
