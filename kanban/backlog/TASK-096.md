---
"type": "devlegate.ticket"
"title": "Initialize and synchronize pinned Git submodules during dev setup"
---

## Context

A fresh detached checkout made with `git worktree add` leaves `src/devlegate/_vendor/nanoyaml` uninitialized. Running `./dev setup` successfully creates a virtual environment and editable install but later `./dev test` fails at import time (`cannot import name 'NanoYAMLError'`). Manually running `git submodule update --init --recursive` resolves this.

A similar problem arises when a developer manually fast-forwards, pulls, checks out, or otherwise updates the main repository to a commit with a *different recorded submodule gitlink*, but does not update the submodule checkout. Setup must repair this drift as well, not merely initialize missing submodules.

## Required work

1. Make source-tree-only `./dev setup` synchronize required Git submodules to the **exact commits pinned by the currently checked-out superproject**, e.g. `git -C "$ROOT" submodule update --init --recursive`, before editable install. This includes missing, uninitialized, and clean out-of-date submodule checkouts. Do not use `--remote` or follow submodule branch tips.
2. Define safe handling of **locally modified or otherwise non-disposable submodule content**: do not silently discard local work or force reset. Fail clearly and describe how to resolve the condition. Do not allow `setup` to return success while the required vendored module is absent or at an unexpected commit.
3. Handle a superproject updated manually without submodule update: subsequent `./dev setup` must restore the pinned state. Ensure the procedure also works for linked/detached worktrees and when the repository's source submodule is already at the expected SHA.
4. If setup cannot fetch/access a pinned submodule commit, report a clear failure, with no misleading successfully prepared environment. Keep repeated setup idempotent.
5. Add real-Git tests for a fresh worktree with uninitialized submodule, a clean stale submodule after superproject gitlink advance, already-correct checkout, and dirty/divergent submodule safety and fetch failures. Add concise documentation explaining `./dev setup` synchronization behavior and that after manual superproject updates developers can rerun setup.
6. Scope changes to development/source-checkout tooling. Do not add Git fetching, submodule initialization, Python environment setup, or other dependencies to the installed Devlegate runtime, package entrypoint, or wheel/standalone payload. Preserve clean-source distribution invariants.

## Acceptance

From a newly created checkout of a commit with the NanoYAML submodule, `./dev setup` makes `from devlegate._vendor.nanoyaml import NanoYAMLError` and the tests usable without a separate manual submodule command. After a superproject update changing the submodule gitlink, rerunning setup deterministically synchronizes the checkout to the newly pinned SHA without destroying local modifications.

Priority: **normal**. Independent of TASK-088; do not preempt its packaging smoke test and CI triage.
