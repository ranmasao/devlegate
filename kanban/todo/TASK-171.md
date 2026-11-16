---
"type": "devlegate.ticket"
"title": "Edit 0.5.7 release notes to match established CHANGELOG style"
"depends_on": ["TASK-170"]
---

## Milestone

Final editorial release preparation for Devlegate 0.5.7. All implementation and
version-freeze tickets are integrated and the integration-triggered GitHub CI for
the current product HEAD is green. This ticket addresses release-note readability
only, not release authorization or new product work.

## Goal

Refine **only** the `## 0.5.7 (Unreleased)` section in `CHANGELOG.md` so its
tone, granularity and concise product-facing descriptions align with the existing
0.5.6, 0.5.5 and 0.5.3–0.5.0 entries, while accurately representing all material
verified changes actually integrated in 0.5.7.

## Context

- The current 0.5.7 section has approximately 93 lines and 21 bullets, while
  0.5.6 has 21 lines / 5 bullets and 0.5.5 has 29 lines / 7 bullets. A larger
  0.5.7 entry is justified by greater release scope, but the current version
  reads too much like an internal provenance and safety audit.
- The historical format is English past tense, a short release summary, and
  product-visible `Added`, `Changed`, `Fixed`, optionally `Removed`.
  Historical entries summarize user or operator outcomes rather than individual
  implementation stages and low-level proof details.
- The 0.5.7 section currently places `Added the non-runnable abandoned workflow
  state` under `Changed`. It also repeats resource generation, recovery and
  cleanup across many bullets at overlapping levels of detail.
- Previous editor TASK-166 established factual release coverage. Preserve that
  coverage: this ticket is a tighter editorial revision, **not** a reconsideration
  of implementation readiness or a new feature ticket.

## Required behavior

1. Independently compare the existing 0.5.7 section with adjacent earlier
   releases and the actual integrated product, maintained docs and tests.
   Rewrite the 0.5.7 prose in the established changelog voice: concise,
   past-tense, product/operator-facing, and organized into sensible
   `Added`/`Changed`/`Fixed`/`Removed` categories. Keep the short overview.
2. Consolidate implementation details that describe one delivered capability
   or fix; prefer the observable outcome to internal machinery. Reduce
   excess generation-receipt, ref-lease, handoff and journal terminology
   unless a constraint is material to safe operator use. The intent is
   a substantially shorter, more readable release section, not adherence
   to an arbitrary exact line or bullet count.
3. Retain clear coverage of the significant delivered outcomes:
   `inspect`, one-argument `reset`, workspace list/prune and done-resource
   cleanup, the reviewed `abandoned` workflow and recovery, safer generation/
   worker/rework recovery and Git authority, review/worker priority contracts,
   useful status/log/diagnostic improvements, `upgrade-templates`,
   `./dev package --ticket`, submodule-aware `./dev setup`, and native
   packaging/build/validation support. Group closely related changes without
   obscuring substantive capabilities.
4. Put genuinely new commands/states in `Added`, not `Changed`; reserve
   `Changed` for altered existing behavior and `Fixed` for defects.
   Do not force a `Removed` heading unless the documented removed
   legacy review fallbacks are important to understanding the public
   release behavior; if retained, state the removal once and plainly.
5. Verify each nontrivial product claim against current integrated source,
   docs or tests. Explicitly avoid claims that Change Explorer, Landlock
   kernel isolation, or future repair/reconciliation UX are shipped.
   Do not invent guarantees stronger than the implementation proves.
6. Keep `## 0.5.7 (Unreleased)` and do not invent a release date.
   Preserve all older release entries **byte for byte**. Do not touch other
   files unless an actual direct reference defect blocks the changelog
   edit; prefer a pure one-file documentation diff.
7. No ticket IDs, developer session IDs, unpublished artifacts or internal
   ticket chronology in the release section. No release tag, GitHub Release,
   artifact publication, pyproject/version change, runtime edits, tests edits,
   migration/compatibility logic, CI workflow changes or branching.

## Acceptance criteria

- The 0.5.7 notes read like the nearby release notes rather than an internal
  technical audit: each bullet states a user-visible result, capability or
  meaningful behavioral change in a reasonably compact form.
- Material 0.5.7 features and fixes remain accurately represented, with no
  misleading novelty claims, category errors or duplicated explanations.
- The previous sections, heading `Unreleased`, stable product version `0.5.7`
  and existing full-source GitHub asset policy remain unchanged.
- `git diff --check`, Markdown/reference checks and hosted CI pass for the
  exact Devlegate-published checkpoint. This purely editorial change must
  not require altering runtime tests or weakening release gates.
- Worker gives a short release-operator handoff: actual date is added when
  authorizing publication; final clean/tagged source package proof and
  post-integration CI are separate operator gates.

## Required regressions

1. Compare before/after 0.5.7 coverage against the integrated source/docs,
   paying attention to abandonment, inspect/reset, workspace cleanup, rework
   recovery, distribution support and diagnostic changes.
2. Verify no file other than `CHANGELOG.md` changed and the entire history
   starting at `## 0.5.6 -- 2026-10-13` is byte-identical.
3. Confirm the release heading still says `0.5.7 (Unreleased)`, no obsolete
   `.dev0` is reintroduced, and no future/unshipped capability is claimed.

## Boundaries

No new features, backward compatibility, migration path, platform policy
changes, package uploads or release tag. Devlegate owns Git lifecycle; the
curator separately owns any release-date normalization and publication.
