---
"type": "devlegate.ticket"
"title": "Audit and remove temporary analysis artifacts before release 0.5.7"
---

## Goal
Before the final 0.5.7 release, identify and remove temporary design/audit/research artifacts added since the release branch began, while retaining user-facing documentation, tests, templates, and required release evidence. This is release hygiene, not permission to delete all non-Python files.

## Timing
Execute **after** implementation/decomposition and Git story review, but **before** release packaging/tag. Do not queue early while other tickets still use these artifacts. Coordinate with release checklist.

## Inventory
Use `release/0.5.6` as the verified direct ancestry baseline of `release/0.5.7` (merge-base `d8420085dedb9b58a282d504fe39e905c84bdc14`). Use `git diff --name-status --diff-filter=A BASE...release/0.5.7` and a separate inventory of modified non-Python files. Include Markdown, JSON, YAML, templates, text and config, not just `*.md`; distinguish added from modified and moved files.

Initial **candidates for review, not automatic deletion** (only files added since 0.5.6):
- `docs/architecture/runtime-decomposition-audit.md`
- `docs/git-semantic-api-audit.md`
- `docs/GIT_COMMAND_INVENTORY.md`
- `docs/RECOVERY_CONTRACT.md` (may instead be useful permanent documentation).
- `.devlegate/templates/.devlegate-defaults.json` (likely required configuration; inventory, do not remove blindly).

`docs/EXECUTION_OWNERSHIP.md`, `tests/FOCUSED_TEST_AUDIT.md`, and `tests/TEST_PLACEMENT.md` already existed in `release/0.5.6`; assess them only for temporary *changes since 0.5.6*, not as newly added 0.5.7 files.

## Required behavior
1. Classify every newly added non-Python file as permanent product documentation/test support/template/config, temporary research/audit artifact, or ambiguous needing curator decision. Modified files must be checked for embedded temporary sections as well; do not revert unrelated legitimate changes.
2. For proposed removals, check all repository references (README, docs links, CI, tests, packaging manifests, architecture notes, and other tickets). Eliminate dangling references and preserve any necessary durable knowledge in existing maintained docs, without carrying over transient investigation logs.
3. Produce a precise proposed deletion/retention table **for curator approval before destructive cleanup**. Do not delete ambiguously categorized files; if approvals are unavailable, claim incomplete with the specific decision required. Maintain license/release documentation and all files consumed by tests/build.
4. After approval, apply the bounded deletions, run relevant docs/reference/package tests, Ruff and exact checkpoint CI. Record release artifact content so deleted files do not enter wheel/sdist. Do not change production behavior.

## Acceptance
No known temporary analysis artifacts are shipped in 0.5.7; no required maintained docs, templates or test inputs are lost; references and packaging are valid; human curator signs off on removal decisions.
