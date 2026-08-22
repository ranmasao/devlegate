# Devlegate

Devlegate is a minimal bootstrap orchestrator for a software-development workflow. It runs from the root of an already configured Git checkout, watches the configured remote branch for new commits, fast-forwards the local checkout, and starts a fresh OpenCode implementation agent when actionable tickets exist in `TODO_PATH`.

During this bootstrap phase, responsibilities that will later move into deterministic orchestration modules are intentionally delegated to the implementation agent. In particular, the agent currently owns ticket movement from `todo` to `review`, Git commits, and pushes.

## Python setup

Run the development helper from this repository's root:

```sh
./dev setup
```

This creates the local `.venv`, installs Devlegate in editable mode, and installs the pytest and Ruff development tools. The virtual environment is local to this checkout and is not used for project files that Devlegate manages.

Devlegate runs from the root of the target project checkout. Copy `.env.example` to that project's `$PWD/.env` and configure at least `OPENCODE_MODEL` and the ticket paths if they differ from the defaults. The project must also have its normal Git remote and authentication configured.

## Execution

From the target project's repository root, run the installed CLI through the helper in the Devlegate checkout:

```sh
/path/to/devlegate/dev run
```

The default mode is a foreground polling loop. Use `--once` for one synchronization/execution pass. `--env FILE` selects a configuration file instead of `$PWD/.env`, and `--version` prints the installed version.

The old `--watch` option has been removed. Polling is now the default, so no watch flag is needed.

`REMOTE_BRANCH` defaults to the currently checked-out branch. If set explicitly, the current branch must match it. See `.env.example` for the available runtime settings.

## Development commands

All commands use the local `.venv`:

```text
./dev setup             Create the environment and install dependencies
./dev test              Run pytest
./dev lint              Run Ruff check
./dev check             Run tests and lint
./dev run [args...]     Run devlegate with arguments
./dev clean             Remove named caches and build artifacts
./dev purge             Also remove .venv and named development artifacts
```

`clean` and `purge` use an explicit allowlist. They do not use `git clean` and preserve source files, configuration, `.env`, and user files.

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

## Current safety rules

- The project checkout must be clean before Devlegate pulls or starts OpenCode.
- Prompt files support `${TODO_PATH}`, `${REVIEW_PATH}`, `${TODO_DIRECTORY}`, `${REVIEW_DIRECTORY}`, `${REPO_ROOT}`, `${REMOTE_NAME}`, and `${REMOTE_BRANCH}` substitutions. The `*_PATH` values are configured paths; the `*_DIRECTORY` values are resolved filesystem paths.
- Devlegate only starts the agent after observing a new remote revision and finding ticket files in `TODO_PATH`.
- A subsequent poll with no new remote revision does not run the agent again merely because `todo` is non-empty.
- After an agent failure, the next poll can run one recovery attempt against its dirty working tree. The recovery prompt tells the agent to review unfinished changes before starting ticket work.
- Recovery instructions are loaded from `RECOVERY_PROMPT_FILE`, defaulting to Devlegate's `recovery-prompt.txt`.
- Devlegate stores per-repository iteration state atomically under `/var/lib/devlegate` by default. Set `STATE_DIR` when that location is not writable or a different state volume is required. Lock files are stored under its `locks` subdirectory.
- State-directory access is checked at startup. If the default is not writable, interactive Devlegate offers to use `~/.local/state/devlegate` for the current run, use it and persist `STATE_DIR` in `.env`, or exit. Non-interactive runs exit with these choices instead of prompting.
- Merge and agent-dispatch intent is persisted before each transition, so a restart after a completed merge resumes agent dispatch instead of treating the checkout as an ordinary no-change poll.
- The agent is expected to leave intended changes committed and pushed; after it exits, Devlegate verifies that the working tree is clean and the local and remote branch heads match.
- A kernel-managed `flock` prevents concurrent Devlegate instances for the same checkout.
- Agent sessions are intentionally ephemeral for now.

`devlegate.sh` remains as a legacy compatibility entry point. It is a thin wrapper around the installed Python CLI in this checkout, preserves arguments, and does not implement the workflow itself. Run it from the target project's repository root, or use the `dev` helper instead.
