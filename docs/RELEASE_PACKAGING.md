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
It obtains the packaging tool from `master`, checks out the selected annotated
tag separately, and uploads the full-source asset only when that selected tree
contains a gitlink. It never creates tags or releases. Normal release tags must
match `vX.Y.Z`.

The workflow also supports `workflow_dispatch` with an explicit `tag` input for
retry or historical recovery. The release and tag must already exist. Existing
asset names are replaced only by the workflow's explicit `--clobber` retry
policy after all tag checks pass.

Before upload, the read-only build job extracts the actual archive and checks
its root, SHA-256 sidecar, `SOURCE-MANIFEST`, selected tag target, absence of
Git metadata, and every materialized submodule path and pinned commit. It runs
`./dev setup && ./dev check` from the extracted tree when that source tree has
the current development entrypoints; early historical trees receive the
structural/provenance checks they support.

The validated archive and sidecar cross the job boundary as a workflow
artifact. Only the separate upload job has `contents: write`, and it uploads
to the exact existing release tag. The explicit `--clobber` behavior is the
documented idempotent retry policy for a requested release.
