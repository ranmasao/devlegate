---
"type": "devlegate.ticket"
"title": "Make extracted full-source release checks compatible with Git-metadata-free archives"
"depends_on": ["TASK-022"]
---

## Milestone

Devlegate 0.5.5 release recovery.

## Goal

Recover full-source asset publication for the already published `v0.5.5` release
without moving or recreating the tag, while making the release verification harness
compatible with the documented full-source contract that archives contain no Git
metadata.

## Context

The published `v0.5.5` tag is an annotated tag whose commit target is exactly:

```text
22cc635a0d042a9be469f88b77533c3d110bb696
```

The GitHub Release `Devlegate 0.5.5` is already published.

Release workflow run `36482640731` successfully:

- validated the existing release and annotated tag;
- built `devlegate-0.5.5-full-source.tar.gz`;
- independently validated the archive and sidecar;
- confirmed the standalone/Debian publication path is skipped for versions below
  0.6.0.

It then failed only at `Run extracted source checks when supported`.

The extracted archive intentionally has no `.git` metadata. The full source
contract requires Git metadata to be removed before archiving.

The failure was:

```text
2 failed, 1026 passed, 1 skipped
```

Both failures were in:

```text
tests/test_licensing.py::test_wheel_validator_rejects_injected_repository_integration
tests/test_licensing.py::test_sdist_validator_rejects_injected_repository_integration
```

Their helper `_distribution_source()` executes:

```text
git rev-parse HEAD
```

and therefore fails with exit status 128 in the extracted Git-metadata-free source
tree. The distribution validators under test do not require repository history for
the behavior those tests are proving.

The validated archive itself did not fail structural, manifest, submodule, checksum,
or safe-extraction validation.

The release workflow already supports `workflow_dispatch` for recovery against an
existing annotated tag and existing GitHub Release. A previous 0.5.4 release
recovery used that path successfully.

## Required behavior

- Preserve the existing `v0.5.5` annotated tag and its exact commit target.
  Do not retag, move, delete, recreate, or force-update the tag.
- Preserve the already published GitHub Release. Do not delete/recreate it merely
  to recover asset publication.
- Preserve the full-source artifact contract: the produced archive itself must
  contain no Git metadata.
- Preserve the existing independent full-source archive validation before any
  repository-oriented developer checks are run.
- Make the extracted-source verification step compatible with a source archive that
  legitimately lacks `.git`.
- If repository-oriented tests require a Git HEAD, any compatibility Git context
  must be created only in the disposable extracted verification tree after archive
  validation. It must not alter archive bytes, sidecar hashes, `SOURCE-MANIFEST`,
  uploaded artifacts, or the selected release commit.
- A disposable synthetic Git repository in the extracted verification tree is an
  acceptable compatibility mechanism if it leaves a clean working tree and is used
  only for the post-validation developer-check phase.
- Remove unnecessary direct dependence on live repository Git metadata from current
  tests/helpers where the value is not semantically required by the behavior being
  tested, so future source archives do not acquire the same accidental assumption.
- Keep `./dev setup` / `./dev check` or an equivalent complete modern-source
  verification for extracted current source. Do not solve the incident by silently
  dropping the two failing tests from release verification.
- Preserve the existing release trust boundary: selected tag/release validation,
  trusted release tooling, full-source archive validation, separate upload job, and
  no write permission in read-only build jobs.
- Preserve the current `>=0.6.0` gate for standalone/Debian publication.
- Preserve `workflow_dispatch` recovery for an already existing release/tag.
- The recovery workflow must be runnable against `v0.5.5` without modifying that
  tag or its release notes.

## Acceptance criteria

- A no-`.git` extracted full-source tree can complete the modern extracted-source
  verification path.
- The two licensing/distribution-boundary tests no longer fail merely because the
  extracted source tree itself has no repository metadata.
- Full-source archive validation still proves that uploaded archive contents contain
  no Git metadata before any disposable compatibility context is created.
- The validated archive and sidecar remain byte/hash identities produced from the
  exact tagged commit `22cc635a0d042a9be469f88b77533c3d110bb696`.
- Normal repository CI remains green.
- A release recovery run can be started through the existing
  `workflow_dispatch(tag=v0.5.5)` path using the corrected workflow generation.
- In that recovery run, full-source build, archive validation, extracted-source
  checks, artifact publication, and upload-release-assets all succeed.
- The 0.5.5 GitHub Release receives only the policy-eligible full-source archive and
  checksum sidecar; standalone and Debian assets remain absent.
- No release/tag topology mutation is required to recover the release.

## Required regressions

- Given a validated extracted current full-source archive with no `.git`, running
  the release's extracted-source verification completes successfully.
- Given the wheel validator rejection test, absence of a real repository HEAD does
  not prevent the test from reaching and asserting the intended repository-
  integration rejection behavior.
- Given the sdist validator rejection test, absence of a real repository HEAD does
  not prevent the test from reaching and asserting the intended repository-
  integration rejection behavior.
- Given a full-source archive containing Git metadata, independent archive
  validation still rejects it before post-validation developer checks.
- Given `v0.5.5 < 0.6.0`, a recovery run continues to skip standalone/Debian
  publication.
- Given the existing annotated `v0.5.5` and GitHub Release, workflow-dispatch
  recovery uses the existing release identity and does not create a new release or
  move the tag.
