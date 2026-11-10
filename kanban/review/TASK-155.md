---
"type": "devlegate.ticket"
"title": "Package a ticket's latest verified worktree or checkpoint through ./dev package --ticket"
"depends_on": ["TASK-096"]
---

## Milestone

Devlegate 0.5.7 developer tooling and dogfooding. Provide a small, source-identity-safe way to build native packages from a ticket execution **before product integration**. Do not change installed runtime behavior or add a general revision-selection framework. Complete integrated TASK-096 first so a source checkout's pinned NanoYAML submodule can be prepared reliably.

## Goal and CLI

Add exactly one user-facing source selector to the existing development packaging interface:

```sh
./dev package arch --ticket TASK-089
```

It should also work for existing package targets using the same source-selection plumbing. Do not introduce `--sha`, `--revision`, `--dirty`, `--snapshot`, `--worktree`, or other selector flags. Preserve existing `./dev package arch`, `--repo PATH`, `--output-dir`, `--keep-work` and their clean-source semantics. Explicit `--repo` and `--ticket` are mutually exclusive; the normal implicit invoking-repository default is not a conflict. The selected ticket belongs to the invoking Devlegate project, not an arbitrary similarly named repository.

## Source selection and identity

1. Resolve the specified ticket from the current project's committed control/execution evidence and locally registered Git resources. Use authoritative repo/worktree ownership and ticket-generation identity, not fabricated worktree paths, Git branch-name substring matches, or a stale status string. Fail with a diagnostic on missing/ambiguous/foreign identities, active or uncertain writers, missing Git objects or unprovable source lineage. Resolution is read-only: never retry the ticket, admit a worker, update branches, fetch, or change runtime SQLite.
2. When a unique verified, quiescent ticket worktree exists, prefer its **current contents**, including staged, unstaged and newly created non-ignored source files. If it has no relevant modifications, its exact current ticket HEAD is the source. This command is for dogfooding unfinished work, not a request to commit it.
3. If no suitable live worktree remains (including after accepted/done integration and resource retirement), fall back to the **latest independently attributable ticket code checkpoint/commit**, from its verified branch/ref or immutable execution history. Preserve the ticket's checkpoint identity and verify that all needed objects are locally available; never silently substitute the current product HEAD, another execution, a named branch without ownership evidence or an older checkpoint for a newer unavailable one. An unresolved ambiguity must fail closed.
4. The supported simple case is a clean ticket worktree; it must remain simple. For a worktree with uncommitted changes, make a **private immutable build snapshot** outside the execution/control worktrees and all project branches/indexes. Preserve exactly the selected tracked and non-ignored untracked source files, their applicable executable modes and pinned submodule contents; do not package ignored caches, secrets from unrelated paths or transient runtime state. Detect unsafe symlinks, missing/stale submodule content, changing files during capture and other inconsistent snapshots. Do not overwrite, stage, commit, clean, stash or reset the original worktree. Do not assume a dirty snapshot is an authentic committed ticket checkpoint.
5. The resulting build tree must satisfy the existing clean-source and submodule preflight contracts; keep the normal deterministic wheel -> standalone -> Arch/Debian packaging/validation graph, rather than bypassing them to accept dirty input. If a private temporary Git commit is necessary to represent a snapshot, mark it explicitly as a synthetic **build-only** identity with the verified origin ticket, execution/generation, underlying committed HEAD and a digest of captured WIP bytes. Never present the synthetic SHA as the original worker checkpoint, silently publish it as product history or grant owner/rebase/retry authority from it.
6. **Toolchain consistency is essential:** the packaging scripts, dependency inventory, templates, compliance material and version used to build a selected ticket must be the scripts and files from the *selected source snapshot*, not the invoking checkout's potentially older `tools/build_distribution.py` / `tools/package_arch.py` modules mixed with newer ticket source. The invoking `./dev` may act as a small dispatcher, but the selected source's actual packaging implementation must execute for the package build, even when that older implementation itself does not recognize `--ticket`. Reuse the existing development Python/venv where valid; never install or modify the selected worktree merely to dispatch. Give an actionable refusal if the selected source lacks a compatible package graph.
7. Preserve readable source provenance in the retained build report/log: ticket ID, verified source HEAD/checkpoint, captured WIP digest (if any), snapshot versus committed source, selected toolchain identity, and generated package digest. Distinguish WIP development packages from release-tag build evidence. Keep their default output separate from ordinary release artifacts to avoid accidental replacement or publication. No GitHub Release upload, deployment, target-host installation or privileged service mutation is part of this command.

## Acceptance regressions

- In disposable real-Git fixtures, build a ticket-selected package from a clean, locally registered worktree. Verify exact code/tooling provenance and package contents; the original worktree/index/ref remain unchanged.
- A modified tracked source file plus a new non-ignored source file is captured in a stable, isolated snapshot; ignored caches are not copied. A changing source or unsafe submodule fails safely. The WIP snapshot is distinguishable from a committed checkpoint and cannot be used to claim a product integration.
- If the ticket worktree is gone after integration, use the exact latest verified accessible ticket checkpoint. If it is missing or provenance cannot identify the latest, refuse instead of falling back to unrelated product code.
- Reject wrong project/ticket, ambiguous or foreign worktree/ref, active/uncertain worker generation, incomplete source and unverified or unavailable Git objects; no source or owner-state mutation occurs in these rejection paths.
- Exercise the motivating case in a disposable fixture: the invoking checkout's builder predates a ticket's changes in `tools/package_arch.py`; the resulting package must use the **ticket version of the builder**, not the caller's version.
- Existing no-selector `./dev package` retains its clean-tree behavior, deterministic output, package validations and CI. Explicit `--repo` remains supported, but conflicts with `--ticket`. No additional CLI selector options or runtime dependencies. Focused tests, Ruff and exact-checkpoint CI pass.

## Sequencing and boundaries

Schedule only **after TASK-096 is durably done**. This ticket implements developer-side source selection/snapshot and packaged-artifact provenance, **not** TASK-089's native dependency fix and **not** a rebase tool. Do not mutate or test destructively against actual TASK-089 worktrees, branches, runtime SQLite, review history or control resources. Once TASK-155 has been accepted and integrated, the curator may requeue the same deferred TASK-089 for an owner-guarded rebase/rework generation onto the then-current product HEAD, followed by a real Arch dogfooding-host acceptance run. No historical state migration, generic VCS abstraction, new packaging formats or compatibility framework.
