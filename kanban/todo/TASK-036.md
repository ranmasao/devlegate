---
"type": "devlegate.ticket"
"title": "Bootstrap Devlegate 0.5.6 development line"
"depends_on": ["TASK-035"]
---

## Milestone

Devlegate 0.5.6 iteration bootstrap.

## Goal

Transition the newly opened `release/0.5.6` product line from the completed
0.5.5 release identity to the explicit development identity:

```text
0.5.6.dev0
```

without changing release topology or rewriting the published 0.5.5 release.

## Context

The human-owned release/topology transition has already been completed:

```text
master         -> 47c08f117b6da939793d9b6c8f1b314f2bee3aeb
release/0.5.5  -> 47c08f117b6da939793d9b6c8f1b314f2bee3aeb
release/0.5.6  -> 47c08f117b6da939793d9b6c8f1b314f2bee3aeb
v0.5.5         -> 22cc635a0d042a9be469f88b77533c3d110bb696
```

The `v0.5.5` annotated tag and published GitHub Release are complete and must
remain immutable historical release identity.

The product source currently still reports `0.5.5` in canonical version
metadata because `release/0.5.6` was created directly from the post-release
0.5.5 integration point.

This ticket establishes the development-version identity before functional 0.5.6
work begins.

## Required behavior

- Change the canonical Devlegate development version from `0.5.5` to
  `0.5.6.dev0`.
- Keep all canonical runtime/build metadata consistent. At minimum audit and update:
  - `pyproject.toml`;
  - `src/devlegate/__init__.py`;
  - current-version-sensitive tests and packaging fixtures.
- Distinguish current-version assertions from historical release fixtures. Do not
  rewrite references whose purpose is specifically to test or document released
  version `0.5.5`.
- Ensure Python-package, source-distribution, full-source, standalone, and Debian
  build/proof code can represent the PEP 440 development version where those paths
  consume the current project version.
- Do not weaken stable release-tag validation. Published release tags remain
  `vX.Y.Z`; this ticket does not introduce a `v0.5.6.dev0` release tag.
- Do not create, move, delete, or force-update any Git tag.
- Do not create, edit, delete, or republish a GitHub Release.
- Do not merge or move `master`, `release/0.5.5`, or `release/0.5.6`; product
  branch topology is human-owned and already established.
- Do not change the repository's release publication threshold or the existing
  `>=0.6.0` standalone/Debian publication policy.
- Do not add functional 0.5.6 behavior as part of the version bootstrap.
- Do not invent a release date or describe 0.5.6 as released.
- Preserve the completed `0.5.5` changelog section as historical release history.
  If an unreleased/development changelog marker is needed by existing project
  convention, it must clearly remain unreleased and must not masquerade as a final
  0.5.6 release section.

## Acceptance criteria

- Canonical package/runtime version is exactly `0.5.6.dev0`.
- A source-tree runtime version probe reports `0.5.6.dev0` through the currently
  supported version command/API.
- Built Python metadata identifies the distribution as `0.5.6.dev0`.
- Current-version packaging and validation tests expect `0.5.6.dev0` where
  appropriate.
- Historical `v0.5.5` release identity, changelog data, and historical fixtures
  remain semantically intact.
- Stable release-tag parsing/selection still rejects development-version tags where
  the release policy requires `vX.Y.Z`.
- No Git topology, release object, or publication mutation is performed by this
  ticket.
- Full tests, Ruff, coverage, and relevant packaging/version consistency checks
  remain green.

## Required regressions

- `pyproject.toml` and `devlegate.__version__` agree on `0.5.6.dev0`.
- Python build metadata carries `Version: 0.5.6.dev0`.
- Current-version distribution fixtures derive or assert the development version
  correctly rather than retaining accidental `0.5.5` literals.
- Historical tests/docs that intentionally describe released `0.5.5` remain
  unchanged in meaning.
- Release tag validation continues to accept normal stable tags and does not treat
  `v0.5.6.dev0` as a normal published-release tag.
- No functional CLI/runtime behavior changes beyond reported version identity.
