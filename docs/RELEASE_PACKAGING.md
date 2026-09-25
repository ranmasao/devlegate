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
executable, its build provenance, and the repository-owned compliance snapshot
under `packaging/standalone-compliance/`. Package assembly does not download
legal material. The archive contains one top-level directory with the
unversioned `devlegate` executable, Devlegate legal files,
`THIRD_PARTY_NOTICES.md`, `BUILD-PROVENANCE.json`, and the exact third-party
license snapshots used by the manifest. Its Linux x86_64 target is explicitly
the glibc target and records the GNU scie-jump asset identity.

`tools/validate_standalone_package.py` independently checks the archive root,
safe extraction, executable modes, artifact identity, manifest hashes, notice
references, and absence of transient build paths. This archive is not uploaded
or published for releases before `0.5.4`. It is required for releases
`0.5.4` and later.

## Builder

`tools/build_full_source.py` (also exposed as `tools/build-full-source`)
packages an explicitly selected ref or commit:

```sh
python3 tools/build_full_source.py \
  --repo /path/to/devlegate \
  --ref v0.5.0 \
  --version v0.5.0 \
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
`master` against the selected tag. The standalone path begins with release
`0.5.4`; it checks out the exact annotated-tag commit recursively and executes
that tag's own standalone builder, packager, validator, and compliance manifest.
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
