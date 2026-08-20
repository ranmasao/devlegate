# Devlegate

Devlegate is currently a minimal bootstrap orchestrator for a software-development workflow. It runs from the root of an already configured Git checkout, watches the configured remote branch for new commits, fast-forwards the local checkout, and starts a fresh OpenCode implementation agent when actionable tickets exist in `TODO_PATH`.

During this bootstrap phase, responsibilities that will later move into deterministic orchestration modules are intentionally delegated to the implementation agent. In particular, the agent currently owns ticket movement from `todo` to `review`, Git commits, and pushes.

## Bootstrap workflow

```text
external Architect/Reviewer
    -> pushes new or hardening tickets into todo
    -> Devlegate notices a new remote revision
    -> fast-forward pull/sync
    -> OpenCode processes actionable todo tickets
    -> completed tickets move todo -> review
    -> agent runs relevant checks, commits, and pushes
    -> external Architect/Reviewer inspects the result
```

A ticket that is not actually complete must remain in `todo`. If implementation is blocked on an architectural decision, the agent should leave the ticket there and report the blocker instead of moving it to `review`.

## Setup

1. From the project root, copy `/path/to/devlegate/.env.example` to `$PWD/.env` and configure at least `OPENCODE_MODEL` and the ticket paths if they differ from the defaults. The `.env` file belongs to the managed project, not to the Devlegate checkout, and should be ignored by that project's Git configuration.
2. Configure the project's normal Git remote/authentication outside Devlegate. The default remote is `origin`.
3. Run Devlegate from the root of the project checkout (`$PWD` is the workspace and the default configuration file is `$PWD/.env`):

```sh
/path/to/devlegate/devlegate.sh --once
/path/to/devlegate/devlegate.sh --watch
```

`REMOTE_BRANCH` defaults to the currently checked-out branch. If it is set explicitly, the current branch must match it. `--env FILE` can override the project-local configuration path when needed.

## Current safety rules

- The project checkout must be clean before Devlegate pulls or starts OpenCode.
- Devlegate only starts the agent after observing a new remote revision and finding ticket files in `TODO_PATH`.
- A subsequent poll with no new remote revision does not run the agent again merely because `todo` is non-empty.
- The agent is expected to leave intended changes committed and pushed; after it exits, Devlegate verifies that the working tree is clean and the local and remote branch heads match.
- A kernel-managed `flock` prevents concurrent Devlegate instances for the same checkout.
- Agent sessions are intentionally ephemeral for now.

This is deliberately not yet the final authority model. Later versions may move ticket transitions, commit/push ownership, attempts, review dispatch, and failure handling into deterministic modules.
