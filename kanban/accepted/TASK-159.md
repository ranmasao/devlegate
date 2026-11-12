---
"type": "devlegate.ticket"
"title": "Support Git before 2.42 when bootstrapping a new orphan control worktree"
---

## Scheduling and relationship to TASK-089

**Release 0.5.7 blocker, targeted implementation.** Schedule and integrate this fix before the final acceptance of TASK-089, whose packaging metadata currently declares Git >=2.31 but whose initial control bootstrap calls `git worktree add --orphan` (Git 2.42+). Do not change the existing TASK-089 ticket bytes, previously attributed review feedback, execution reports or worker checkpoint merely to express this dependency. Keep this task bounded to control-plane first bootstrap and its compatibility proof. TASK-088 already addressed absent-vs-error control ref observation; preserve it.

The Arch x86_64 package has now been tested on a real host through native installation, Git/control init, systemd service, execution, review, accepted integration, and worktree/ref retirement. Those results are valid on the newer host Git; they do not prove first bootstrap on an older Git.

## Goal

Make first-time control-plane initialization work using the existing declared Git >=2.31 floor, without testing Git versions on the ordinary execution path or adding a permanent compatibility framework. Keep native `git worktree add --orphan -b <branch> <path>` on Git versions that support it. A narrowly isolated fallback for Git without that option may later be removed independently.

Do not infer compatibility with every untested distribution. Prove at least the affected stock Debian 12 Git 2.39 case; retain family-independent capability semantics.

## Current code and independently reproduced behavior

`src/devlegate/control_git_workflow.py::bootstrap_control` calls `devlegate.git.add_worktree(repo, "--orphan", "-b", control_branch, str(control_worktree), runner=runner)` only when the control checkout does not yet exist and both local and remote control refs are absent. Ordinary execution worktree creation and attaching an existing control branch do not use this option.

An isolated *modern* Git 2.47.3 repository demonstrated this fallback sequence:

```sh
git -C "$repo" worktree add --detach --no-checkout "$control" HEAD
git -C "$control" symbolic-ref HEAD refs/heads/devlegate/control
git -C "$control" read-tree --empty
# Create and stage the existing workflow skeleton, then make one initial root commit.
```

The resulting control commit had no parents, contained only the created workflow skeleton, the product checkout was unchanged, and the control worktree became a registered worktree on `devlegate/control`. This is **not yet a Git 2.39 test**. Before `symbolic-ref`, the intermediate worktree is registered and detached; an interrupted operation must handle that state explicitly and safely.

## Required implementation

1. Preserve the existing native `worktree add --orphan` attempt for modern Git. On failure, distinguish genuinely unavailable option support from unrelated Git failures using a narrow capability observation (for example the actual `git worktree -h` usage options), without parsing localized error strings or making assumptions from numeric version alone. Never use fallback merely because a Git mutation returned nonzero.
2. Before beginning the fallback, re-observe the exact target path and registered worktrees; do not reuse a path or rewrite a partial native operation without positive ownership and absence-of-effects proof. Unavailable or ambiguous observations must fail closed.
3. When unsupported and safe, use the minimal `worktree add --detach --no-checkout`, `symbolic-ref HEAD <exact control ref>`, `read-tree --empty` sequence, delegating all effects to the existing owner-bound Git runner. Do not use a checkout that populates the product tree, a hard reset, deletion of user work, or blind removal/pruning of worktree registrations.
4. Preserve the existing shared skeleton initialization, initial root-commit verification, lease-guarded remote publish, refresh, idempotent repeated initialization, and remote attach paths. Product HEAD, product files and remote product branch must remain unchanged. Root commit must have no parents and its tree must contain only the canonical skeleton.
5. Classify interruption after each fallback effect. When the state is exactly proven safe, resume without changing generation or confusing detached intermediate state with a foreign worktree. When owner registration, local index, symbolic HEAD, worktree contents, remote ref, or process authority is ambiguous, refuse with an actionable diagnostic and preserve all existing evidence. No new permanent migration path, global bootstrap state machine, or unsafely inferred recovery authorization.
6. Do not probe the Git version or `--orphan` capability during ordinary worker execution, service polls or existing control attach. No persistent capability cache, new runtime dependency, public command, distro-specific conditional or package-version bump unless independent evidence requires it.
7. Keep the native dependency inventory and Debian/Arch mappings unchanged at `git>=2.31` / `git (>= 1:2.31)` for this targeted correction; update documentation narrowly to explain actual first-bootstrap capabilities and the fallback. If a different, independently required Git capability breaks the declared floor, report that separately with exact evidence rather than silently claiming broad support.

## Required regression evidence

- Real-Git disposable fixture: product with an initial commit, configured bare remote, both control refs absent; native branch creates and publishes a root control commit, a repeat call is idempotent, the product HEAD/branch/index stay identical, and the remote control ref points to the created commit.
- Actual Git 2.39 (Debian 12 or equivalent pinned tool): same full first-time control bootstrap, no unsupported-option failure, and re-attach from remote with the same data. Prefer executing the **installed native Debian package** (no system Python/venv needed); if installation CI remains container-only, clearly distinguish that from real-host acceptance.
- Native Git supporting `--orphan`: prove that fallback is not invoked and keep the existing ordered Git-effect trace.
- Fault injection: native command fails for an ordinary access/ownership/invalid-path reason while `--orphan` is supported -> no fallback; failed native command leaves unknown partial effects -> no fallback; old Git lacking support but with foreign/nonempty target -> refuse; interruption after `--detach --no-checkout`, after `symbolic-ref`, after `read-tree --empty`, after local commit and after remote publish -> deterministic safe resume or explicit fail-closed preservation.
- Existing TASK-088 ref-existence error cases, remote transport failure, partial native orphan checkout and nonempty/foreign worktree regressions remain green. Run focused tests, Ruff, and exact-checkpoint CI.

## Boundaries and acceptance

The acceptance criterion is **functional control-plane first bootstrap and lifecycle safety**, not just `git worktree add -h` reporting available commands. Keep this change small and removable when the minimum Git feature set is raised. No universal Git capability registry, platform/version matrix speculation, ref rewriting, historical report modification, package redesign or migration machinery.

Completion requires proving both the modern native path and the older compatible path and updating the TASK-089 final review with this prerequisite satisfied. Do not mark TASK-089 accepted merely because the Arch machine with new Git worked.
