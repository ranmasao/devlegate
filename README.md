# Devlegate

Devlegate is a minimal bootstrap orchestrator for a software-development workflow. Product code remains on the configured product branch and canonical workflow files live only on `CONTROL_BRANCH` (default `devlegate/control`) in a Devlegate-owned worktree outside the product checkout.

During this transitional phase, worker dispatch is deliberately gated. Devlegate now maintains three physically separate Git surfaces: the operator checkout, the canonical control worktree, and a per-ticket execution worktree. Later execution phases must define how workers report and mutate canonical control state.

## Usage

Install Devlegate, then run the `devlegate` command from the root of the target
project checkout. Copy `.env.example` to that project's `$PWD/.env` and
configure at least `OPENCODE_MODEL` and the managed workflow paths if they
differ from the defaults. The project must also have its normal Git remote and
authentication configured.

```sh
devlegate control init
devlegate run
devlegate run --once
devlegate check
devlegate status
devlegate status --json
devlegate plan
devlegate plan --json
```

`devlegate control init` explicitly fetches and attaches an already existing remote
control branch at the deterministic state location. It does not create a branch,
migrate tickets, commit, or push. Existing projects must explicitly migrate their
workflow files to the control branch first; the product checkout must not retain a
second managed workflow copy.

The default `run` mode is a foreground polling loop. Use `run --once` for one synchronization/execution pass. `check` validates setup without running workflow. `status` is read-only and reports what Devlegate observes. `plan` is read-only and reports what Devlegate would decide from one optimistic consistent snapshot; its output includes the branch, local HEAD, known remote ref, and known remote HEAD used for the decision. Neither command fetches or writes durable state. Retry if project state changes continuously while a snapshot is being collected. `--env FILE` selects a configuration file instead of `$PWD/.env`, and `--version` prints the installed version.

Use `check` for a side-effect-free configuration and checkout preflight.

`REMOTE_BRANCH` defaults to the currently checked-out branch when HEAD is attached. If set explicitly, the current branch must match it; detached HEAD is always blocked for planning and execution. See `.env.example` for the available runtime settings.

## Development Setup

For contributors working on the Devlegate repository, run:

```sh
./dev setup
```

This creates the local `.venv`, installs Devlegate in editable mode, and installs the pytest and Ruff development tools.

## Development Commands

All commands use the local `.venv`:

```text
./dev setup             Create the environment and install dependencies
./dev test              Run pytest
./dev lint              Run Ruff check
./dev check             Run tests and lint
./dev run [args...]     Run the checkout-local installed Devlegate CLI
./dev clean             Remove named caches and build artifacts
./dev purge             Also remove .venv and named development artifacts
```

`clean` and `purge` use an explicit allowlist. They do not use `git clean` and preserve source files, configuration, `.env`, and user files.

`./dev check` is the authoritative development validation entry point. GitHub Actions runs the same `./dev setup` and `./dev check` path on pushes and pull requests using Python 3.12.

## Bootstrap workflow

```text
external Architect/Reviewer
    -> pushes new or hardening tickets into todo
    -> Devlegate notices a new repository revision or todo generation
    -> fast-forward pull/sync
    -> Devlegate selects one runnable todo ticket
    -> worker dispatch remains gated during v0.4 construction
    -> later execution phases reconnect worker reporting
    -> external Architect/Reviewer inspects the result
```

A ticket that is not actually complete must remain in `todo`. If implementation is blocked on an architectural decision, the agent should leave the ticket there and report the blocker instead of moving it to `review`.

## Current safety rules

- The product checkout must be clean before Devlegate synchronizes it. The control worktree must also be clean before it is synchronized or used for scheduling.
- Managed tickets use the explicit `BACKLOG_PATH`, `TODO_PATH`, `REVIEW_PATH`, and `DONE_PATH` directories. Missing configured directories block planning and execution; only canonical `<ID>.md` entries are tickets, while other human/editor artifacts are ignored.
- `CONTROL_BRANCH` defaults to `devlegate/control`. The local control worktree is stored under `$STATE_DIR/worktrees/<repository-key>/control`; `status`, `plan`, `check`, and `run` never create it automatically.
- Each selected ticket gets the deterministic local branch `devlegate/work/<ticket-id>` and worktree `$STATE_DIR/worktrees/<repository-key>/work/<ticket-id>`. The branch is durable implementation history; the worktree is a disposable active workspace. Creation, resume, and safe recreation are Devlegate-owned and never use the operator checkout or control worktree.
- Execution branches start at the exact planned product code HEAD and retain the bound control revision in durable state. Control history is never merged or copied into an execution branch. Missing worktrees may be recreated from the existing branch; dirty or conflicting worktrees fail closed without reset, clean, force removal, or branch stealing.
- An execution lineage keeps its original base revision when the product branch advances independently. A valid prepared generation is not repeatedly materialized just because worker dispatch remains gated.
- The execution base identifies the ticket lineage, while the persisted control revision identifies the current scheduling attempt. A new generation may refresh control authority without creating a new branch; an interrupted attempt must retain and revalidate its original authority.
- Ticket frontmatter uses the vendored, restricted NanoYAML N0 implementation; Devlegate does not depend on a general YAML library or support free-form tickets.
- Ticket identity is the filename stem. Devlegate strictly validates NanoYAML N0 frontmatter, ticket metadata, globally unique IDs, dependency references, and the dependency DAG before launching a worker.
- Only `todo` tickets whose dependencies are in `done` are runnable. `review` does not satisfy dependencies. Devlegate sorts runnable IDs and selects exactly one ticket for a potential worker dispatch.
- Devlegate owns ticket discovery, parsing, graph validation, runnable determination, deterministic selection, and selected-ticket execution binding. Worker dispatch remains gated while the v0.4 execution and reporting boundaries are constructed.
- Devlegate owns worker prompt construction. The worker receives one exact implementation assignment, a small worker-only contract, and only relevant implementation context. Canonical workflow paths, ticket paths, kanban, and Git/execution topology are Devlegate details, not worker API.
- Fresh, resume, rework, and recovery prompts share one contract and differ only through a narrow work directive and relevant optional context. Ticket content is opaque assigned data; it cannot authorize canonical workflow mutation.
- Worker output is free-form unless it is exactly one strictly validated `devlegate_report` tool event. `WorkerClaim` is untrusted semantic egress with `completed`, `incomplete`, or `blocked` outcomes; missing, malformed, or duplicate reports are protocol failures and do not mutate workflow.
- Devlegate supplies an ephemeral reserved OpenCode tool configuration for each worker process. The worker runs in the validated execution workspace, while Devlegate keeps process status separate from the worker claim.
- `ExecutionResult` is the canonical Devlegate-owned interpretation of one execution. `ExecutionReport` is its durable structured record under `executions/<ticket-id>/<execution-id>.json` in the control worktree. Tickets remain specifications, not execution logs; `questions` and `remaining` remain structured execution data.
- Devlegate derives an immutable `ExecutionPlan` from one internally consistent observed snapshot. The plan carries repository observation identity and the exact selected ticket; runtime consumes that exact ticket decision rather than performing an independent selection.
- Execution plans and status snapshots expose separate nested `observation.code` and `observation.control` Git identities. A work generation combines code and control revision identity with a deterministic fingerprint of canonical ticket files under `TODO_PATH`.
- Devlegate persists the handled generation, so unchanged todo is not redispatched on every poll or after restart.
- Dirty and divergent Git states are diagnosed with paths and topology, but Devlegate never automatically destroys local changes or reconciles divergent history.
- Git synchronization and workflow validity are separate boundaries: `merge_pending` covers only an unproven fast-forward transaction and is cleared once the intended HEAD is verified. A later workflow blocker is derived from current control contents and does not reopen that Git transaction.
- Later execution phases will define worker failure and recovery semantics. Phase 1 does not start worker execution.
- Devlegate stores per-repository iteration state atomically under `$XDG_STATE_HOME/devlegate`, or `~/.local/state/devlegate` when XDG_STATE_HOME is unset. Set `STATE_DIR` to override it. Lock files are stored under its `locks` subdirectory.
- Worker dispatch is deliberately gated during this transitional phase because workers cannot safely mutate the separate canonical control worktree. Later execution architecture must reconnect this boundary without giving workers cross-plane Git access.
- A kernel-managed `flock` prevents concurrent Devlegate instances for the same checkout.
- Agent sessions are intentionally ephemeral for now.
- Workers remain gated and receive no control-worktree paths, control branch topology, or ticket source paths. A worker, when re-enabled in a later phase, will use only the execution worktree.
