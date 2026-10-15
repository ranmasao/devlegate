---
"type": "devlegate.ticket"
"title": "Finish substantive consolidation of repeated Git operations in runtime"
"depends_on": ["TASK-069"]
---

## Context and correction

TASK-069 was accepted and integrated although its implementation reduced raw `rev-parse` sites only 78 -> 70 and `rev-list` 8 -> 7, with no consolidation of the repeated `merge-base` and `update-ref` command patterns. CI proved correctness, not sufficient progress toward the intended architectural simplification. Keep its merged changes and continue from current product HEAD; do not undo or recreate the completed ticket.

## Goal

Meaningfully reduce duplicated Git-only mechanisms and result interpretation in `runtime.py`, without compromising domain authorization, recovery evidence, locking, WAL or CAS. This is a continuation of the original TASK-069 architectural goal, not a cosmetic renaming exercise.

## Required work

1. Inventory **actual unique patterns** among remaining `rev-parse`, `merge-base`, `rev-list`, `update-ref`, `cat-file`, status and ancestry calls in `runtime.py` and compare with existing helpers in `git.py`, `execution_workspace.py`, `reconciliation.py`, and recovery modules. Record repeated command shapes, semantics, owners, and call-site counts before editing.
2. Prioritize repeated **read-only** primitives, especially ancestry checking via `merge-base --is-ancestor` and ref/commit resolution, where semantics genuinely match. A shared ancestry helper must distinguish exit 0 (yes), exit 1 (no), and exit >1 (Git error) rather than silently treating all failures as false. Consolidate repeat history and parent readers only when identical.
3. Evaluate repeated `update-ref` operations for shared expected-old CAS mechanics; preserve distinctions between create-if-absent, compare-and-swap, evidence pin and delete. A shared low-level command helper must not decide authorization, ordering or evidence policy. Do not extract if differing invariants make reuse unsafe; explain why.
4. Replace genuinely duplicated call sites, including result parsing, not merely their invocation spelling. Keep domain-specific fetch/push/rebase/reset and intentional repeated re-observation when needed for race protection.
5. KISS/DRY: simple functions in existing `git.py` where warranted, no new framework, Protocol hierarchy or third-party dependency. Preserve existing mocking seams as needed, without introducing dependency-injection machinery.
6. Report before/after per-command raw call sites **and** concrete duplicate patterns actually eliminated. Small numerical change alone is not proof of failure, but a trivial handful of replacements with most clear repetition untouched is not acceptable. If audit finds fewer safe opportunities than expected, document the specific semantic differences and remaining rationale in the report instead of claiming completion.

## Validation and acceptance

Use existing Git, recovery, reconciliation, worktree, history and publication regression tests; add only focused unit tests for new shared semantics. Full exact-checkpoint CI and Ruff must pass.

Acceptance requires **substantive, evidenced reduction of duplication** in `runtime.py`, especially read-only history/ancestry mechanisms, or a rigorous call-pattern inventory demonstrating why further consolidation would be unsafe or counterproductive. Passing tests without that result is insufficient. Keep worker report concise and quantitative.
