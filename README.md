# Devlegate

Devlegate is a minimal bootstrap orchestrator for a software-development workflow. It runs from the root of an already configured Git checkout, synchronizes the configured remote branch, validates the managed ticket graph, and starts a fresh OpenCode implementation agent for one deterministic runnable ticket.

During this bootstrap phase, responsibilities that will later move into deterministic orchestration modules are intentionally delegated to the implementation agent. In particular, the agent currently owns ticket movement from `todo` to `review`, Git commits, and pushes.

## Python setup

Run the development helper from this repository's root:

```sh
./dev setup
```

This creates the local `.venv`, installs Devlegate in editable mode, and installs the pytest and Ruff development tools. The virtual environment is local to this checkout and is not used for project files that Devlegate manages.

Devlegate runs from the root of the target project checkout. Copy `.env.example` to that project's `$PWD/.env` and configure at least `OPENCODE_MODEL` and the managed workflow paths if they differ from the defaults. The project must also have its normal Git remote and authentication configured.

## Execution

From the target project's repository root, run the installed CLI through the helper in the Devlegate checkout:

```sh
/path/to/devlegate/dev run
/path/to/devlegate/dev run --once
/path/to/devlegate/dev check
/path/to/devlegate/dev status
/path/to/devlegate/dev status --json
```

The default `run` mode is a foreground polling loop. Use `run --once` for one synchronization/execution pass. `check` validates setup without running workflow. `status` is read-only, does not fetch, and reports one optimistic consistent snapshot; retry if project state changes continuously while it is being collected. `--env FILE` selects a configuration file instead of `$PWD/.env`, and `--version` prints the installed version.

For 0.2.x compatibility, bare `devlegate` runs the normal workflow, and legacy `devlegate --once` and `devlegate --check` forms remain accepted. The command forms are canonical.

The old `--watch` option has been removed. Polling is now the default, so no watch flag is needed. Use `check` for a side-effect-free configuration and checkout preflight.

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
    -> Devlegate notices a new repository revision or todo generation
    -> fast-forward pull/sync
    -> Devlegate selects one runnable todo ticket
    -> OpenCode implements only that selected ticket
    -> completed tickets move todo -> review
    -> agent runs relevant checks, commits, and pushes
    -> external Architect/Reviewer inspects the result
```

A ticket that is not actually complete must remain in `todo`. If implementation is blocked on an architectural decision, the agent should leave the ticket there and report the blocker instead of moving it to `review`.

## Current safety rules

- The project checkout must be clean before Devlegate pulls or starts OpenCode.
- Managed tickets use the explicit `BACKLOG_PATH`, `TODO_PATH`, `REVIEW_PATH`, and `DONE_PATH` directories. Missing directories are empty; only canonical `<ID>.md` entries are tickets, while other human/editor artifacts are ignored.
- Ticket frontmatter uses the vendored, restricted NanoYAML N0 implementation; Devlegate does not depend on a general YAML library or support legacy free-form tickets.
- Ticket identity is the filename stem. Devlegate strictly validates NanoYAML N0 frontmatter, ticket metadata, globally unique IDs, dependency references, and the dependency DAG before launching a worker.
- Only `todo` tickets whose dependencies are in `done` are runnable. `review` does not satisfy dependencies. Devlegate sorts runnable IDs and dispatches exactly one ticket per OpenCode execution.
- Devlegate owns ticket discovery, parsing, graph validation, runnable determination, deterministic selection, and selected-ticket execution binding. The implementation agent temporarily owns implementation, the Executor report, the exact `todo` to `review` move, commit, and push.
- Prompt files support `${BACKLOG_PATH}`, `${TODO_PATH}`, `${REVIEW_PATH}`, `${DONE_PATH}`, `${TODO_DIRECTORY}`, `${REVIEW_DIRECTORY}`, `${REPO_ROOT}`, `${REMOTE_NAME}`, and `${REMOTE_BRANCH}` substitutions. The `*_PATH` values are configured paths; the `*_DIRECTORY` values are resolved filesystem paths.
- A work generation combines the known remote HEAD with a deterministic fingerprint of regular files under `TODO_PATH`; remote HEAD is no longer the sole work trigger.
- Devlegate persists the handled generation, so unchanged todo is not redispatched on every poll or after restart.
- Dirty and divergent Git states are diagnosed with paths and topology, but Devlegate never automatically destroys local changes or reconciles divergent history.
- After an agent failure, Devlegate may run one automatic recovery attempt. If recovery fails or is interrupted, Devlegate enters `recovery_failed` and requires manual intervention before automatic work resumes.
- Recovery instructions are loaded from `RECOVERY_PROMPT_FILE`, defaulting to Devlegate's `recovery-prompt.txt`.
- Devlegate stores per-repository iteration state atomically under `$XDG_STATE_HOME/devlegate`, or `~/.local/state/devlegate` when XDG_STATE_HOME is unset. Set `STATE_DIR` to override it. Lock files are stored under its `locks` subdirectory.
- Merge and agent-dispatch intent is persisted before each transition. A merge interrupted before agent execution can be superseded by a newer descendant revision; once agent execution has started, recovery handles the original work instead.
- The agent is expected to leave intended changes committed and pushed; after it exits, Devlegate verifies that the working tree is clean and the local and remote branch heads match.
- A kernel-managed `flock` prevents concurrent Devlegate instances for the same checkout.
- Agent sessions are intentionally ephemeral for now.
- The worker receives one selected ticket body and exact ticket/review coordinates. It must not scan kanban or select additional work. Recovery is bound to the persisted selected ticket identity and cannot switch tickets.
- Worker output is untrusted data and is never raw-forwarded to the operator
  terminal. OpenCode runs headlessly with stdin disconnected, structured stdout
  decoding, captured stderr, and a safe text renderer for both output streams.
  TTY-dependent automated tests must eventually create and own their own PTY
  rather than relying on the operator terminal.

`devlegate.sh` remains as a legacy compatibility entry point. It is a thin wrapper around the installed Python CLI in this checkout, preserves arguments, and does not implement the workflow itself. Run it from the target project's repository root, or use the `dev` helper instead.
