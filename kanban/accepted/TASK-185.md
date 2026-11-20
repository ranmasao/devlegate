---
"type": "devlegate.ticket"
"title": "Fix reset retirement of named submodules in linked worktrees"
---

## Scheduling and incident

**Urgent maintenance for Devlegate 0.5.8; independent of TASK-133.** The self-hosted instance on `archbig` is blocked while retiring the old TASK-133 generation during an already-admitted `devlegate reset TASK-133`:

```text
2026-11-20T18:28:29Z workflow blocked: ticket reset blocked: reset submodule Git directory is outside generation
```

Product release `479ff05917081848c5ca2af3c19ef9d7d62bdc5f` has a source submodule whose **name** differs from its **checkout path**:

```ini
[submodule "nanoyaml"]
    path = src/devlegate/_vendor/nanoyaml
    url = ../nanoyaml.git
```

In `src/devlegate/clean_reset.py`, `_require_discardable_generation()` recursively demands that `git rev-parse --absolute-git-dir` for each initialized submodule equals `parent_admin / "modules" / relative`, where `relative` is the checkout path. Git legitimately uses the configured submodule **name**, not necessarily that path. A disposable linked-worktree reproduction confirmed:

```text
parent admin: .../.git/worktrees/worker
actual submodule Git dir: .../.git/worktrees/worker/modules/nanoyaml
old check expects: .../.git/worktrees/worker/modules/src/devlegate/_vendor/nanoyaml
```

This is a reproducible **reset safety-validator false positive**; it does not by itself prove that the live filesystem is unmodified. No manual worktree, Git admin, ref or SQLite edits are authorized.

## Required change

1. Fix only the submodule admin ownership proof used by reset retirement. Resolve the unique submodule name corresponding to each Gitlink path from Git's authoritative tracked `.gitmodules` configuration at the appropriate pinned commit (including recursive nested modules), or use an equivalently strong native-Git identity proof. Do not assume the name equals the checkout path.
2. Validate the configured mapping and resolved Git directory fail-closed: reject absent/ambiguous names, unsafe name components, symlinked or relocated `.git` files/admin directories, foreign admin outside the owned generation, unproved nested repositories, changed checkout/head, unsafe locks and mount boundaries. Retain the existing fs identity/receipt and retirement journal safeguards. Do not blindly trust `rev-parse` or a modified working `.gitmodules`.
3. Preserve the existing durable `ticket_reset` transaction and its control-head/resource lineage binding. Fix the product code in an isolated worker checkout; **do not modify the live TASK-133 worktree, SQLite runtime state, refs, or control branch to work around the blocker**.
4. Extend the existing real-Git `tests/test_ticket_reset.py` fixture or add a focused regression to use an explicit submodule name `nanoyaml` with nested path `src/devlegate/_vendor/nanoyaml` in a linked worktree, exercise both clean and dirty reset retirement, and verify no owned resources remain and unrelated resources are intact. Keep existing nested submodule and malicious/foreign admin negative tests. Avoid full-scope refactoring or migrations.
5. Run focused reset/resource tests, `./dev lint` and `git diff --check`; verify full hosted CI on exact final SHA (including coverage and Ruff). Report the verified root cause and any remaining blockers.

## Acceptance and operational follow-up

- A legitimate named submodule whose name differs from its Gitlink path passes strict resource proof and allows reset retirement.
- Incorrect or foreign Git admin paths continue to fail **before any deletion**; no `--force`/manual cleanup shortcut that bypasses identity evidence.
- Integration with existing owner reset stages, restart/retry and safe fresh-generation semantics remains intact.
- After integrating and deploying the product fix, operator can **retry the existing** `devlegate reset TASK-133 --json` against its pinned control lineage. Do not create a new reset operation or edit/push the active control branch until that transaction is resolved; do not promise success without observing the actual instance.
- Do not make TASK-133 amendment work, TASK-184 blackboard work, runtime migrations or global Git abstractions part of this ticket.
