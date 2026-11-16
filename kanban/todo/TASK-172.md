---
"type": "devlegate.ticket"
"title": "Remove nondeterministic Git filter stderr comparison from ticket-source fail-closed test"
"depends_on": ["TASK-171"]
---

## Milestone

Devlegate 0.5.7 release-blocking hosted CI flake. The release is technically
frozen and no post-release branch bootstrap may begin until a stable
release-branch CI gate passes. This is a narrow test defect, not a feature
or migration.

## Evidence: same SHA passed and failed

At product SHA `0c97ca80d0efa6160f563acecd3124b5c6587a9d`:

- Worker-branch exact-SHA CI run `43951407879` completed success.
- Integration-triggered `release/0.5.7` CI run `43958106708` completed
  failure with 1 failed, 2278 passed, 3 skipped.
- Exact failure:
  `tests/test_ticket_source.py::test_project_filter_policy_still_fails_closed[process]`,
  line 224, `assert unchanged(binding) == before`.
- Failure diff is at tuple index 1, the `git status --porcelain=v1`
  result from `unchanged()`: both observations return `0` and empty
  stdout, but their stderr text differs. Git's deliberately broken
  `filter.project.process=false` emits repeated and variably ordered/
  counted subprocess diagnostics such as
  `could not read greeting from subprocess 'false'`,
  `initialization for subprocess 'false' failed`, and sometimes
  `packet write with format failed`.
  There is no evidence in this failure diff of changed refs, index,
  worktree files or selected artifact state.
- `unchanged()` currently groups actual invariants (refs, index bytes,
  binding-directory files and Git status result) with volatile
  stderr text. Exact equality of diagnostics emitted by the intentionally
  failing process filter is not a resource immutability invariant.

## Goal

Keep `test_project_filter_policy_still_fails_closed` a genuine
no-mutation and policy-refusal test, while eliminating its exact
comparison of nondeterministic Git filter stderr. Preserve independently
proven refusal of an *active project-local* clean/process filter; do not
silence/ignore the filter policy, weaken resource proofs or change
production Git invocation.

## Required correction

1. Independently inspect the test fixture and `unchanged()` in
   `tests/test_ticket_source.py`, and `tools/ticket_source.py` / actual
   Git calls. Confirm the observed mismatch is attributable to the
   stderr side-channel and not a mutation hidden by the snapshot.
2. Make the smallest **test-only** change: the failing negative-path
   assertion must continue to prove Git refs, `status` exit code and
   stdout, Git index bytes, and durable resource binding records are
   identical before/after; it must not demand byte-identical stderr
   from a failed `filter.process`. Preserve the existing
   `UnprovenResource` refusal assertion for `clean` and `process`.
   Avoid weakening the shared `unchanged()` helper for unrelated
   tests if a narrowly scoped comparison suffices.
3. Retain a useful diagnostic when the stable state differs; do not
   simply remove the no-mutation assertion, xfail/skip the test, add
   retries until green, suppress all stderr globally, disable
   `filter.process` in the test, or replace it with a fake that no
   longer covers actual Git failure behavior.
4. Ideally provide deterministic evidence that otherwise-identical
   before/after snapshots with different filter stderr are accepted,
   whereas changed refs, index bytes, durable binding files, `status`
   return code or stdout are still rejected. Distinguish this from
   production fail-closed enforcement.
5. Preserve all runtime behavior, Python metadata `0.5.7`,
   `CHANGELOG.md`, `ROADMAP.md`, packaging tools, release workflow,
   and release/tag/publication state. No migrations or extra
   compatibility paths.

## Acceptance criteria

- The failing test passes repeatedly under `pytest -n 4 --dist=worksteal`
  and proves the genuine negative-path refusal and repository immutability.
- Focused ticket-source and Git security tests plus `git diff --check`
  and Ruff pass. No unrelated files change.
- Hosted GitHub Actions CI succeeds on the **published exact worker SHA**,
  and a separate **post-integration push** CI succeeds on
  `release/0.5.7` before any release tag/publication.
- Worker supplies a clear explanation of the original failure and why
  volatile subprocess stderr is not an immutable resource artifact.

## Boundary

Do not re-run the release script on red CI in order to bypass the gate;
do not use CI reruns as a substitute for the deterministic test fix.
Worker owns implementation only. Devlegate handles Git lifecycle.
Independent curator reviews the checkpoint and exact-SHA CI. Release
operator separately handles changelog date, final tag and source proof.
