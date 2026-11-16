---
"type": "devlegate.ticket"
"title": "Freeze Devlegate 0.5.7 version metadata and release-facing examples"
"depends_on": ["TASK-166"]
---

## Milestone

Devlegate 0.5.7 — final version freeze after all implementation and changelog
work is integrated. This is a bounded source/metadata preparation ticket, not
an authorization to create a Git tag, GitHub Release, install packages or
publish artifacts.

## Goal

Make the integrated `release/0.5.7` source declare the final stable
`0.5.7` version rather than `0.5.7.dev0`, and remove misleading
development-version filenames from current release-facing package examples.
Preserve the already-frozen 0.5.7 product behavior and curator-owned release
approval boundary.

## Context

- `TASK-166` is `done`; the comprehensive `CHANGELOG.md` section is still
  correctly titled `0.5.7 (Unreleased)`.
- `pyproject.toml` currently declares `version = "0.5.7.dev0"`.
- `docs/RELEASE_PACKAGING.md` currently uses
  `devlegate-0.5.7.dev0-1-x86_64.pkg.tar.zst` and
  `devlegate_0.5.7.dev0_amd64.deb` in native-container smoke examples.
- The current `.github/workflows/release.yaml` publishes full-source
  material for tagged trees with gitlinks; for versions below 0.6.0 it
  intentionally does not publish standalone/Debian assets, and it never
  uploads Arch packages. Any desired GitHub asset additions for 0.5.7
  are separate curator/operator release actions, not changes authorized here.
- The actual release date belongs to the curator's final tag/publication
  operation. Do not invent it or use the present calendar date as a proxy.

## Required behavior

1. Set `[project].version` in `pyproject.toml` to exactly `0.5.7`.
   Confirm that the normal build metadata and installed CLI version derive
   from this canonical project declaration; do not add a second version
   authority, runtime migration, compatibility alias or packaging dependency.
2. Replace the stale `.dev0` native-package example filenames in
   `docs/RELEASE_PACKAGING.md` with usable final-version examples or
   version-neutral, correctly quoted shell variables. Keep Debian/Arch
   validation instructions as validation instructions, not a claim that
   those packages will be uploaded to GitHub.
3. Inspect current release-facing maintained documents, templates and
   packaging/test configuration for other *active* `0.5.7.dev0` references
   or assumptions about an unreleased development build. Correct only
   genuinely misleading live release examples or version assertions.
   Preserve historical facts and independent pinned dependency versions.
4. Leave the `CHANGELOG.md` heading `0.5.7 (Unreleased)` intact.
   A release owner will set the **actual** release date and rerun the
   final exact-SHA gate before creating the annotated tag. Do not reopen
   feature scope, rewrite older release entries, or claim the release
   has already been published.
5. Do not change production behavior, release asset policy, runtime code,
   Git integration/recovery, release workflows, test infrastructure
   merely for convenience, or unrelated documents. Never tag, release,
   merge product branches, install host packages, or push commits;
   those remain Devlegate/curator operations.

## Acceptance criteria

- The integrated source, after Devlegate-owned checkpoint publication, reports
  stable `0.5.7` through the canonical project metadata and normal version
  derivation, with no accidental `.dev0` in current release instructions.
- Existing native-package smoke instructions remain executable when supplied
  with appropriately named `0.5.7` artifacts; Debian/Arch proof remains
  distinct from publication.
- The comprehensive 0.5.7 changelog, historical releases, product behavior,
  packaging provenance and supported runtime constraints are unchanged.
- The worker provides a concise handoff for separate release-owner finalization:
  choose actual date, prove the final clean/tagged source, build/validate intended
  artifacts including native validation, publish the authorized GitHub assets,
  and merge/open the next development branch only after release acceptance.
- Focused checks, Ruff, whitespace checks and exact-checkpoint hosted CI pass.
  Final release packaging and host-installation evidence is a subsequent
  release-operator gate, not an uncommitted-worker build claim.

## Required regressions

1. Parse `pyproject.toml` with Python 3.12 stdlib `tomllib` and assert
   `project.version == "0.5.7"` and `requires-python >=3.12` remains
   unchanged; verify any existing version/metadata tests against the
   stable value.
2. Inspect the revised native-package smoke examples for shell correctness
   and final-version filenames; check current maintained release documentation
   for misleading development-version references without altering historical
   snapshots solely to suppress a search result.
3. Run the relevant existing version/metadata, distribution-boundary and
   documentation checks, `git diff --check`, Ruff, and obtain hosted CI
   for the exact Devlegate-published checkpoint. Do not evade the clean-source
   precondition of `./dev package` by committing/pushing from the worker.

## Boundaries

This is the last bounded product-source edit before operator release gates.
Do not create new distribution formats, change the `0.6.0` workflow threshold,
add release-specific compatibility logic, or introduce any migration paths.
