---
"type": "devlegate.ticket"
"title": "Prepare Devlegate 0.5.5 for manual release"
"depends_on": ["TASK-019", "TASK-020", "TASK-021", "TASK-023", "TASK-024", "TASK-031"]
---

## Milestone

Devlegate 0.5.5 release preparation.

## Goal

Bring the current 0.5.5 release branch to a release-ready product state and produce
a clear final readiness result for the human-managed release.

This task prepares the product tree and validates everything required to release
0.5.5. It does **not** perform the release itself.

The actual release, all release-topology Git branch operations, and any change of
the configured working/product branch are explicitly human-owned operations and
must remain outside this ticket.

## Context

The current product version is still:

```text
0.5.5.dev0
```

in both `pyproject.toml` and `src/devlegate/__init__.py`.

The current `CHANGELOG.md` section for 0.5.5.dev0 describes only the early
zero-delta product-drift retry work. The release has since accumulated substantial
additional self-hosting, review/finalization, observability, packaging,
distribution, recovery, and operational hardening.

By the time this task runs, all product work intended to block the 0.5.5 release
must already be complete through TASK-019, TASK-020, TASK-021, TASK-023, TASK-024,
and TASK-031. Post-0.6
architecture and infrastructure backlog items TASK-012 through TASK-018 are not
0.5.5 release blockers.

The current release workflow is deliberately conservative:

- it reacts to an already published GitHub Release or explicit
  `workflow_dispatch` for an already existing annotated tag;
- normal release tags must be `vX.Y.Z`;
- the release and tag must already exist before the workflow packages them;
- standalone/Debian asset publication is policy-gated to versions `>=0.6.0`.

Therefore 0.5.5 release preparation must not change publication policy merely
because standalone/systemd code was hardened during this development cycle.

## Required work

### Finalize product version metadata

- Change the product version from `0.5.5.dev0` to final `0.5.5` everywhere
  that constitutes canonical shipped version metadata.
- Keep all version declarations internally consistent.
- Do not introduce a new version-schema mechanism as part of this release task;
  post-0.6 version modeling is tracked separately.

### Rewrite the 0.5.5 changelog entry

Replace the provisional 0.5.5.dev0 changelog section with a concise but complete
0.5.5 release entry using the actual release date at execution time.

Describe shipped product behavior rather than ticket-by-ticket implementation
history. At minimum, audit and accurately summarize the final integrated behavior
covering:

- safe automatic retry for zero-delta execution after descendant product drift;
- human-readable Devlegate-generated commit history while retaining machine
  provenance;
- worker/service-host environment isolation and correct externally supervised
  readiness/hosting diagnostics;
- coherent live status and active execution-log access;
- accurate scheduler blocking diagnostics;
- detailed semantic package-build progress;
- Debian/package metadata hardening relevant to 0.5.5;
- exclusion of vendored NanoYAML from Devlegate-owned coverage metrics;
- universal worker handoff through review and accepted finalization, including
  zero-product-delta accepted completion and durable long-form reports;
- bound pre-worker execution-workspace recovery hardening from TASK-019;
- durable standalone launch identity from TASK-020;
- bidirectional managed-systemd/host representation reconciliation from TASK-021;
- end-to-end pre-worker operator-recovery continuation and coherent recovery status
  from TASK-031.

Do not claim behavior that is not present in the final integrated product.
Do not enumerate superseded failed implementation attempts.

### Release-readiness validation

Run the repository's normal full validation from the exact final candidate tree,
including at least:

- full pytest suite;
- Ruff/lint;
- coverage validation under the repository's current coverage policy;
- version consistency checks;
- licensing/compliance checks already owned by the repository;
- source/distribution graph validation;
- full-source release packaging and validation appropriate to a 0.5.5 release;
- any repository-defined snapshot/namespace/platform/package validation that is part
  of the normal release test suite.

Because 0.5.5 changed standalone/systemd behavior, also exercise the current
standalone build/proof and relevant Debian/systemd validation sufficiently to prove
that TASK-020/TASK-021 did not leave the release broken. This is a product
readiness check, not a request to publish standalone/Debian 0.5.5 assets.

Respect the existing release policy that standalone/Debian publication is deferred
until 0.6.0 unless a separate human decision changes that policy.

### Release workflow and documentation audit

Check the final candidate against the current release documentation and workflow:

- `docs/RELEASE_PACKAGING.md`;
- `docs/DISTRIBUTION.md`;
- `.github/workflows/release.yaml`;
- relevant README/release-facing documentation.

Verify that documented commands, filenames, version assumptions, release-policy
gates, licensing boundaries, and source/archive expectations remain truthful for
0.5.5.

Make only product-tree corrections needed for release correctness. Do not expand
this task into post-0.6 release automation or branch-policy redesign.

### Final readiness report

Return a concise durable report stating:

- exact candidate product HEAD;
- final version observed;
- changelog state;
- tests/lint/coverage result;
- release-package/full-source validation result;
- standalone/systemd proof result performed for 0.5.5 readiness;
- any intentionally non-published artifact classes under current policy;
- any remaining manual release steps.

If any required release gate fails, report the task incomplete rather than
declaring 0.5.5 release-ready.

## Explicitly human-owned operations

This ticket must **not** perform any of the following:

- create, move, merge, rename, delete, or otherwise manipulate release/product
  branches as part of release topology;
- switch the human working checkout to another product branch;
- change Devlegate project/runtime configuration to point the working product from
  `release/0.5.5` to another branch;
- create or push the `v0.5.5` tag;
- create, publish, edit, or delete the GitHub Release;
- upload release assets to GitHub;
- change repository default-branch settings;
- perform the final human release announcement or post-release branch/configuration
  switch.

Normal Devlegate-owned temporary execution/checkpoint branch mechanics used to
carry out this ticket are not release operations and remain governed by the normal
execution lifecycle.

The human operator will perform the actual release and all branch/configuration
transitions after this ticket is reviewed and finalized.

## Acceptance criteria

- Canonical shipped version metadata reports exactly `0.5.5`, not
  `0.5.5.dev0`.
- `CHANGELOG.md` contains a final 0.5.5 entry that accurately summarizes the
  complete integrated release rather than only the initial development change.
- All 0.5.5-blocking tickets are already finalized before this task can complete.
- Exact-candidate full tests and lint are green.
- Coverage generation/validation is green under the current vendor exclusion policy.
- Required licensing and package/source-distribution checks are green.
- A valid full-source 0.5.5 release artifact can be built and independently
  validated according to current release documentation.
- Standalone/systemd readiness checks relevant to TASK-020/TASK-021 are green,
  without enabling 0.5.5 binary publication contrary to current policy.
- Release documentation and workflow assumptions remain consistent with the final
  product candidate.
- The final report explicitly lists the remaining human release operations.
- No tag, GitHub Release, release-asset upload, release/product branch topology
  operation, configured working-branch change, or default-branch change occurs as
  part of this ticket.

## Required regressions / safeguards

- Existing automated version-consistency tests reject disagreement between shipped
  version declarations.
- Release/package validation uses the final `0.5.5` identity and does not silently
  accept `0.5.5.dev0`.
- The release workflow continues to require an existing annotated tag and existing
  release rather than gaining release-creation authority.
- The `>=0.6.0` standalone-publication policy gate is preserved unless explicitly
  changed by a separate human-authorized task.
- Running release preparation twice on the same final candidate is idempotent apart
  from already-finalized textual/version state and generated ignored build outputs.


## Review continuation after execution c454925057c047ed9dbe20ac12dc58d6

Checkpoint `22cc635a0d042a9be469f88b77533c3d110bb696` contains the intended
0.5.5 release-candidate changes:

- canonical shipped version metadata is finalized to `0.5.5`;
- the 0.5.5 changelog entry is finalized for 2026-09-28 and summarizes the
  integrated release behavior rather than superseded attempts;
- version-sensitive distribution/package fixtures were updated consistently;
- the existing release workflow still requires an already existing annotated tag
  and GitHub Release;
- the existing policy gate still keeps standalone/Debian publication disabled for
  versions below 0.6.0.

No additional release-preparation design or product-code change is requested unless
the clean-candidate validation below exposes a real defect.

The execution correctly reported itself `incomplete` because the guarded
distribution proof could not be completed while the release-candidate edits were
still uncheckpointed. A temporary-clone workaround also lost the repository-relative
NanoYAML submodule resolution context. Do not work around this by changing submodule
policy or packaging semantics.

### Exact candidate identity

The previous worker summary states that the candidate is
`ea76996e296233b241dcfb98dafd0b78de1ab4d1`. That SHA is the execution's
`code_base_head`, before the 0.5.5 release-preparation changes.

The exact release-candidate checkpoint to validate is:

```text
22cc635a0d042a9be469f88b77533c3d110bb696
```

The next execution should preserve this checkpoint unchanged unless validation
reveals an actual release defect.

### Remaining work: clean exact-candidate validation

Run validation from the normal execution workspace bound to the exact checkpoint
above, now that it is a clean committed tree. Use the repository's canonical
development environment and commands.

At minimum complete and record concrete results for:

```sh
./dev test-parallel
./dev coverage -n 4 --dist=worksteal
./dev lint
./dev package all
```

`./dev package all` is the canonical distribution graph and must complete its
wheel, sdist, standalone, Debian proof, and full-source paths from the clean exact
candidate. It must not create or require the final release tag or GitHub Release.

If the repository-local development environment is absent or incomplete, use
`./dev setup` first.

The full-source path must be exercised through the normal repository/workspace
context so the relative `../nanoyaml.git` submodule URL resolves through the
repository's real origin. Do not substitute an ad-hoc temporary clone whose origin
changes that resolution context.

### Final readiness report

On successful validation, return a durable report that explicitly states:

- exact candidate product/checkpoint HEAD:
  `22cc635a0d042a9be469f88b77533c3d110bb696`;
- observed final version: `0.5.5`;
- final changelog state/date;
- full tests, lint, and coverage results;
- `./dev package all` result, including full-source, standalone, and Debian proof;
- that 0.5.5 standalone/Debian artifacts are readiness proofs only and remain
  intentionally non-published under the current `>=0.6.0` policy;
- remaining human-owned release steps: review/integration, annotated `v0.5.5`
  tag creation/push, GitHub Release creation/publication, workflow-driven upload of
  policy-eligible assets, announcement, and any later branch/configuration
  transition.

Do not create or push a tag, create/edit/publish a GitHub Release, upload assets,
move release/product branches, change the configured product branch, or change
repository default-branch settings.

If the clean exact-candidate validation is green and no real release defect is
found, TASK-022 should return to review without further product changes.
