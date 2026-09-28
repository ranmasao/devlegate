---
"type": "devlegate.ticket"
"title": "Exclude repository self-hosting integration from distribution artifacts"
---

## Milestone

Devlegate 0.5.5 distribution-boundary hardening.

## Goal

Keep Devlegate's repository-specific self-hosting integration out of every
repository-owned distribution artifact.

A contributor clone may contain Devlegate project integration because that clone is
the Devlegate project itself. A Devlegate package must contain the product, not the
configuration used by the Devlegate repository to orchestrate its own development.

The only intentional exception is GitHub's automatic `Source code.zip` /
`Source code.tar.gz`, whose contents mirror the tagged Git tree and are outside
Devlegate's packaging control.

## Context

The Devlegate repository currently self-hosts using tracked project-integration
artifacts including:

```text
.devlegate/project.md
.devlegate/templates/**
skills/architect/SKILL.md
skills/reviewer/SKILL.md
```

The root `skills/...` files are generated project-side targets declared by
`.devlegate/templates/artifacts.toml`.

They are not the product templates shipped to users. The canonical packaged
bootstrap templates live separately under:

```text
src/devlegate/default_templates/**
```

and must remain part of the Devlegate product.

The Python wheel already derives packages from `src/`, so the repository-root
self-hosting integration does not normally enter the wheel. Standalone and Debian
artifacts are derived from the wheel/standalone packaging boundary and likewise
should not contain repository integration.

The custom full-source builder is different. `tools/build_full_source.py`
currently clones the selected commit, materializes submodules, removes Git metadata,
and archives essentially the entire checkout. As a result, tracked self-hosting
integration currently crosses the custom full-source distribution boundary.

That is not the desired model.

The distinction is:

- **repository checkout / contributor clone** — may contain tracked Devlegate
  self-hosting integration;
- **Devlegate distribution artifact** — contains product source/runtime material,
  product bootstrap defaults, required documentation/legal/build metadata, but not
  the integration configuration of the Devlegate repository itself.

## Distribution invariant

Repository-specific self-hosting integration MUST NOT cross any distribution
boundary controlled by Devlegate.

At minimum, controlled distribution artifacts must not contain:

```text
.env
.devlegate/**
skills/architect/SKILL.md
skills/reviewer/SKILL.md
```

The implementation should avoid relying only on today's two generated target
filenames if a stronger safe mechanism can derive or validate the repository-side
generated artifact set from the project integration manifest. Any such mechanism
must remain fail closed and must not allow arbitrary project configuration to
silently exclude unrelated product source.

The following product material is explicitly **not** part of this exclusion and
must remain packaged:

```text
src/devlegate/default_templates/**
```

including the packaged project bootstrap templates and generated-marker material.

## Required behavior

- The custom full-source archive excludes repository self-hosting integration.
- Python wheel/sdist packaging, if built through supported repository tooling,
  excludes repository self-hosting integration.
- Standalone archives exclude repository self-hosting integration.
- Debian packages exclude repository self-hosting integration.
- Package validators fail if a controlled artifact unexpectedly contains prohibited
  repository-integration paths.
- Product bootstrap/default templates under `src/devlegate/default_templates/**`
  remain present and validated in the appropriate Python/standalone product payload.
- Exclusion is based on an explicit distribution boundary, not incidental current
  setuptools behavior alone.
- The custom full-source archive remains deterministic and self-contained with its
  pinned submodule content.
- `SOURCE-MANIFEST` continues to bind the archive to the exact release/commit and
  materialized submodule identities.
- GitHub automatic source archives are documented as repository snapshots and are
  explicitly outside this curated exclusion guarantee.
- Do not rewrite Git history or remove the tracked self-hosting integration from the
  repository itself. Contributor clones must continue to receive it normally.

## Full-source requirements

Update the custom full-source builder so that repository-integration material is
removed or omitted before deterministic archive assembly.

The builder must preserve all material needed to build, inspect, license, and
validate Devlegate from the curated full-source archive, including the product
templates under `src/devlegate/default_templates/**`.

The exclusion must happen deterministically from the selected exact Git tree.
Local ignored/untracked files must never influence archive membership.

A source archive produced from the same selected commit and inputs must remain
byte-reproducible under the existing reproducibility contract.

## Documentation requirements

Update the relevant distribution/release documentation to make the distinction
explicit:

- GitHub automatic source archives are literal repository snapshots and may contain
  repository self-hosting integration;
- `devlegate-X.Y.Z-full-source.tar.gz` is a curated product source distribution
  and excludes repository-specific self-hosting integration;
- wheel/standalone/Debian distribution units contain product bootstrap defaults,
  not the Devlegate repository's own project configuration.

Do not describe GitHub automatic source archives as violating the Devlegate
distribution guarantee; they are explicitly outside the set of Devlegate-built
packages.

## Acceptance criteria

- A custom full-source archive built from the current self-hosting repository does
  not contain `.devlegate/**`, `.env`, or the repository-generated Architect /
  Reviewer target files.
- The same archive still contains
  `src/devlegate/default_templates/**` and all required product/build/legal source
  material.
- Wheel contents contain no repository self-hosting integration and retain the
  packaged default templates.
- Standalone archive contents contain no repository self-hosting integration.
- Debian package contents contain no repository self-hosting integration.
- Validators explicitly test the negative boundary rather than merely relying on
  current packaging layout.
- Full-source reproducibility, submodule materialization, manifest verification, and
  SHA-256 sidecar validation remain green.
- Normal contributor `git clone` behavior is unchanged: tracked project integration
  remains in the repository.
- Release documentation clearly states the GitHub-generated source-archive
  exception.
- Full tests, lint, distribution graph checks, and relevant package validators are
  green.

## Required regressions

- Build the custom full-source archive from a tree containing tracked
  `.devlegate/**` and generated project-side skills; assert those paths are absent.
- Assert `src/devlegate/default_templates/**` remains present in the full-source
  archive.
- Assert wheel/sdist member lists reject or omit root self-hosting integration.
- Assert standalone package validation rejects injected repository-integration paths.
- Assert Debian validation rejects injected repository-integration paths.
- Rebuild the same full-source candidate twice and preserve byte equality.
- Preserve exact recursive submodule identities after applying the curated source
  boundary.


## Review continuation after execution 26f6bc0d604f4a25b47cd0e3338dd488

Checkpoint `c3bbfc97821a3482fdbedc700972642077d9a70c` is structurally
sound and should be continued rather than redesigned.

Confirmed good work:

- the curated full-source builder now removes repository integration before archive
  assembly;
- wheel/sdist/standalone/Debian validation all have a shared negative-boundary hook;
- the explicit fixed prohibited paths (`.env`, `.devlegate/**`,
  `skills/architect/SKILL.md`, `skills/reviewer/SKILL.md`) are protected;
- `src/devlegate/default_templates/**` remains outside the exclusion boundary;
- the GitHub automatic source-snapshot exception is documented clearly;
- existing full-source reproducibility/submodule/SOURCE-MANIFEST behavior remains
  covered;
- exact-head CI is fully green: `1009 passed, 1 skipped`, coverage completed, and
  Ruff reports `All checks passed!`.

The task is not ready for acceptance for three narrow reasons.

### 1. Manifest-derived exclusion currently trusts arbitrary project targets too much

`distribution_boundary.generated_targets()` accepts any target whose string starts
with `skills/`. The manifest `source` is parsed but not used to prove that the
target is actually Devlegate-generated repository integration.

As written, a tracked project manifest could declare an unrelated product file such
as:

```toml
[[artifact]]
source = "something.tmpl"
target = "skills/product-data/README.md"
```

and the curated full-source builder would silently remove that tracked file. This
violates TASK-023's explicit requirement that a stronger manifest-derived mechanism
must remain fail closed and must not let arbitrary project configuration silently
exclude unrelated product source.

Keep the manifest-derived mechanism, but require positive ownership proof before a
declared target becomes excludable. Reuse or mirror the project renderer's ownership
semantics where practical: validate the complete manifest entry, constrain the
template source to the project template boundary, and/or prove the existing target
is Devlegate-generated (for example through the generated marker). The exact design
is flexible, but a manifest declaration alone must not authorize source removal.

Add a regression in which a manifest points at an unrelated tracked `skills/**`
file and prove the distribution boundary refuses to delete/omit it.

### 2. Full-source validation does not enforce the same dynamic boundary as the builder

`build_full_source.remove_integration()` derives additional generated targets from
the selected source manifest. But `validate_full_source.validate_archive()` checks
archive members using:

```python
is_prohibited(name)
```

without the selected source/boundary root. Therefore that direct negative check sees
only the fixed default targets plus `.env/.devlegate`, not non-default generated
targets declared by the selected source manifest.

`verify_tree_content(..., boundary_root=selected_source)` skips expected dynamic
targets while comparing source content, but it does not reject an *extra/injected*
dynamic target in the archive. Thus builder and validator can disagree about the
curated boundary.

Make the validator enforce the exact same resolved prohibited-member set as the
builder, using the selected source tree/validated manifest ownership proof. Add a
focused regression with a non-default, positively proven generated target and show:

- the builder excludes it;
- the validator rejects an archive in which that target is injected back;
- an unrelated manifest-declared target without ownership proof fails closed rather
  than being silently excluded.

### 3. Required wheel/sdist boundary regressions are missing

TASK-023 explicitly requires wheel/sdist member-list coverage. The checkpoint wires
`validate_members()` into `validate_wheel()` and `validate_sdist()`, but the
maintained tests added in this execution cover only:

- full-source builder exclusion;
- standalone validator rejection;
- Debian validator rejection.

Add focused wheel and sdist tests that construct/inject repository-integration
members and prove the supported validators reject them. At minimum exercise one
fixed prohibited root integration path; where practical also exercise the
manifest-derived target boundary.

Do not broaden this into packaging redesign. The current shared-boundary structure
is appropriate; finish its ownership proof and validation/regression symmetry, then
rerun the exact-head full suite and Ruff.


## Review continuation after execution d1f2ab1528f84a6c928c6af0d2ed767d

Checkpoint `a8cabc480eac78187c7ad832c0c6a84fbbd8f469` closes the three
previous review findings in substance:

- manifest-derived targets now require a template plus the Devlegate generated
  marker rather than trusting the manifest declaration alone;
- full-source validation now resolves dynamic prohibited targets against the same
  selected source tree used by the builder;
- explicit wheel and sdist injection regressions now exercise the supported
  validators.

The focused distribution work is good and should be retained. Two narrow
distribution-boundary safety issues remain.

### 1. Reject symlinked path components before manifest-derived inspection or removal

The new ownership proof checks only the final `template` / `generated` path with
`is_symlink()`. It does not reject symlinks in parent components.

That is unsafe for a destructive source-boundary operation. A selected Git tree can
contain, for example, a tracked `skills` symlink. Then a manifest target such as:

```text
skills/custom/SKILL.md
```

can resolve through that symlink. If the external file happens to carry the generated
marker, `generated_targets()` can authorize it and `remove_integration()` can
subsequently `unlink()` outside the temporary checkout.

The same fail-closed rule should apply while locating
`.devlegate/templates/artifacts.toml`, template sources, and generated targets:
do not follow symlinked repository path components to establish ownership or perform
removal.

Reuse a component-wise containment/symlink check analogous to the project renderer's
safe-path handling rather than checking only the final leaf.

Add a regression with a symlinked parent component and an external sentinel proving:

- the boundary fails closed;
- the external file/directory is untouched;
- no curated archive is produced from that unsafe ownership topology.

### 2. The full-source builder must apply the fixed prohibited targets even if the manifest is absent

`is_prohibited()` and the package validators always include:

```text
skills/architect/SKILL.md
skills/reviewer/SKILL.md
```

through `DEFAULT_GENERATED_TARGETS`.

But `remove_integration()` currently removes only:

```python
(".env", ".devlegate", *sorted(generated_targets(root)))
```

so if the project manifest is absent, those explicitly prohibited fixed targets are
not removed by the builder. A later validator may reject the resulting archive, but
the builder itself has already produced a distribution artifact that violates the
declared boundary.

Make builder and validator resolve the same fixed-plus-proven-dynamic prohibited set.
The two fixed Architect/Reviewer targets are part of TASK-023's unconditional
distribution invariant and must not depend on the manifest being present.

Add a focused regression that builds from a source tree with a fixed prohibited
target but no project manifest and proves the builder either excludes it or fails
closed before producing the artifact.

### Validation state

The first exact-head GitHub run for this checkpoint reached
`1012 passed, 1 skipped` and failed only in the already-known intermittent
`test_real_service_drop_retire_old_lineage_and_runs_fresh[T-2]` Git commit race.
That failure is unrelated to TASK-023. A same-SHA failed-job rerun was requested
during review; regardless of that rerun, acceptance still requires a fresh green
exact-head validation after the two boundary fixes above.

Do not redesign the shared boundary. Keep the current ownership-marker, dynamic
validator, wheel/sdist, standalone, Debian, reproducibility, and documentation work;
finish these fail-closed edge cases and rerun the full suite plus Ruff.


## Review continuation after execution 4cfb51700101498da90a35d9e49547cb

Checkpoint `06d665b2fab557d6dd0cdd4ec840f25925bdfc72` closes the two
remaining distribution-boundary findings from the prior review.

Confirmed:

- manifest/template/generated-target ownership inspection now rejects symlinked path
  components before following or removing repository integration material;
- the focused external-sentinel regression proves a symlinked generated-target
  parent fails closed without modifying the external file and without producing a
  curated archive;
- `remove_integration()` now resolves the unconditional fixed Architect/Reviewer
  targets independently of whether the project manifest exists;
- the no-manifest regression proves the fixed prohibited Architect target is
  excluded by the builder itself;
- all prior ownership-marker, dynamic-validator, wheel/sdist, standalone, Debian,
  default-template, reproducibility, documentation, and source-manifest work remains
  intact.

Exact-head GitHub pytest/coverage is green:

```text
1015 passed, 1 skipped
```

One mechanical validation blocker remains. Ruff fails with exactly one `I001`:

```text
tests/test_licensing.py:5:1
Import block is un-sorted or un-formatted
```

This import block was introduced by the preceding TASK-023 continuation. Fix only
the import ordering/formatting required by Ruff, then rerun the exact-head validation.

No additional TASK-023 semantic blocker is known after this checkpoint. If the next
checkpoint changes only that lint issue and exact-head tests plus Ruff are green, the
task should be ready for acceptance.


## Review acceptance after execution e26feae75cad44d7b89007681019e225

Accepted checkpoint: `73831989ce2e962f3a64d8fce689260438028f73`.

Final review confirms that TASK-023's controlled distribution boundary is complete:

- curated full-source packaging excludes `.env`, `.devlegate/**`, the fixed
  Architect/Reviewer repository-integration targets, and positively proven dynamic
  generated targets;
- manifest-derived exclusions fail closed unless ownership is proven by a valid
  template plus Devlegate-generated marker;
- symlinked repository path components are rejected before ownership inspection or
  destructive cleanup, preventing escape from the selected source tree;
- full-source builder and validator resolve the same fixed-plus-dynamic prohibited
  boundary;
- wheel, sdist, standalone, and Debian validators explicitly reject injected
  repository-integration members;
- packaged defaults under `src/devlegate/default_templates/**` remain product
  material and are preserved;
- custom full-source reproducibility, submodule materialization, SOURCE-MANIFEST,
  documentation, and the GitHub automatic source-snapshot exception remain intact.

Exact-head GitHub validation is green:

```text
1015 passed, 1 skipped
Ruff: All checks passed!
```

The exact reviewed result is approved for Devlegate-owned accepted finalization.
