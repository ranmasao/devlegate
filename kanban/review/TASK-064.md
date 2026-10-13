---
"type": "devlegate.ticket"
"title": "Audit and finalize documentation for Devlegate 0.5.6"
"depends_on": ["TASK-057", "TASK-059"]
---

## Milestone

Devlegate 0.5.6 — documentation readiness before tagging and publication.

## Goal

Review the complete tracked product-facing and maintainer documentation against
the exact release/0.5.6 implementation, correct inaccuracies and omissions,
and leave an internally consistent set of documents ready for release.

This is documentation-only release preparation, not permission to extend the
0.5.6 implementation or publish a release.

## Scope

Review README.md, CHANGELOG.md, ROADMAP.md, CONTRIBUTING.md, LICENSING.md,
all relevant docs/*.md, the default templates and packaged skill instructions,
plus test documentation where it describes current runtime or distribution
contracts. Include links and examples. Distinguish current product behavior,
maintainer-only tooling, future roadmap items, and historical release notes.

## Known issues to verify

- CHANGELOG.md ends with 0.5.5: draft a factual 0.5.6 section derived from
  integrated product changes, not an invented release date.
- Operations and architecture docs must accurately describe the TASK-057
  policy: only an explicitly admitted `devlegate stop --force` persists an
  execution-scoped manual-retry barrier. Restart, service shutdown, external
  systemd/WSL shutdown and process loss are not equivalent to that command.
  Safe automatic continuation is still subject to ownership, provenance,
  generation and workspace proofs; normal worker failure is not auto-retried.
- Distribution and packaging docs must match TASK-054/TASK-058/TASK-059:
  Arch native package, source/gitlink preflight before distribution builds,
  direct standalone build, deterministic proofs, runtime Python requirements,
  and the difference between full-source and GitHub-generated snapshots.
- Verify README installation and host-operation instructions for wheel, source,
  standalone, Debian and Arch where supported, and ensure all CLI command and
  option examples agree with the current parser.
- Search for obsolete assertions that standalone/binary publication is
  categorically deferred to 0.6.0, or that 0.5.6 features are still future
  work; resolve against actual release workflow/policy rather than guessing.
- Verify URLs, internal relative links, named files, paths, version references,
  runtime/development dependency boundaries, and licensing statements.

## Requirements

1. Inspect the actual code, tests, packaging scripts, project metadata and
   release workflow before modifying claims. Prefer the released behavior
   contract over incidental implementation mechanics.
2. Preserve accurate historical CHANGELOG entries; add 0.5.6 without rewriting
   historical releases. Do not invent its publication date. Avoid duplicating
   every internal ticket or implementation step in user-facing release notes.
3. Keep documents DRY: each authoritative contract has a canonical explanation,
   with concise links/summaries elsewhere rather than contradictory copies.
4. Keep developer-only tooling (`./dev`, source install), runtime dependencies,
   externally managed host installation, and standalone runtime distinct.
5. Fix only documentation and documentation examples. If a genuine code/doc
   mismatch cannot be resolved without code changes, report it explicitly in
   the worker result rather than quietly editing code or weakening claims.
6. Do not create release tags, GitHub Releases, publish binaries, or modify
   execution/control machinery beyond the normal ticket lifecycle.

## Acceptance criteria

- All scoped documentation has been reviewed against release/0.5.6.
- No stale version/publication promises, contradictory runtime lifecycle
  semantics, or unsupported installation/packaging instructions remain.
- CHANGELOG has a concise factual 0.5.6 section without a fabricated release date.
- Relevant cross-document links, commands and examples validate.
- Documentation audit findings and the actual changes are summarized in the
  worker report, including any unresolved release blockers.
- Full authoritative CI, lint/Ruff, and relevant docs/build/link validation
  are green on the exact checkpoint.

## Non-goals

Do not perform the release, rearchitect recovery/packaging, implement new
installation methods, or pull 0.5.7 work into 0.5.6.

## Review decision — 2026-10-13, checkpoint 5bde69a642be (REQUEST CHANGES)

Execution `356b151ebfbe4bc794f244fa716ac847` completed at
`5bde69a642be6bb1818f4cdbd8bfcdeaa1235ec2`. The documentation
audit updates CHANGELOG, README, roadmap, architecture, operations and
packaging/distribution documentation, with no product-code changes.

### Blockers

1. Authoritative [GitHub Actions run 38439395741](https://github.com/ranmasao/devlegate/actions/runs/38439395741)
   on the exact checkpoint **failed**: 1190 passed, 1 failed, 1 skipped;
   Ruff was not reached. The failing test is
   `tests/test_cli_topology.py::test_real_service_auto_resume_survives_review_barrier_and_reschedules`.
   Diagnostics show `worker identity persistence failed: [Errno 3] No such process`
   and a service-condition timeout. Similar real-service races have been
   seen in previous CI runs; nondeterminism is plausible but not yet proven.
   Investigate or rerun CI on this same SHA, documenting the outcome;
   require successful coverage and Ruff before acceptance.
2. `docs/DEVELOPMENT.md` says `all` selects wheel, sdist, standalone,
   Debian and full-source artifacts, accidentally omitting **Arch**, although
   `tools/build_distribution.py` explicitly includes `arch` in
   `ALL_TARGETS` and handles `all` for the Arch package. Correct the list
   and ensure the surrounding artifact/installed-runtime list reflects Arch.

### Required follow-up

Fix the small concrete documentation omission, verify docs examples and
internal links, and rerun authoritative full CI on the **new exact SHA**.
Keep changes documentation-only. Preserve the accurate 0.5.6 changelog,
manual-retry semantics, and packaging publication policy; do not create a
release tag, publish assets, or expand product scope.

**Disposition: REQUEST CHANGES; move `review` -> `todo`.** Resume from
`5bde69a642be6bb1818f4cdbd8bfcdeaa1235ec2`.
