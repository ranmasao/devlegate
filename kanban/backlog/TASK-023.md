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
