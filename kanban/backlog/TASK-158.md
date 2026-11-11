---
"type": "devlegate.ticket"
"title": "Bootstrap architecture planning: state-aware project initialization and reconnection"
---

## Milestone and scheduling

**Deferred architecture planning, not implementation.** Keep in `kanban/backlog`. Do not promote, auto-schedule, execute, or consume current-iteration capacity. Eligible for curator consideration **no earlier than the next development iteration**, after the ongoing Arch packaging, fresh-host initialization and release validation work. This ticket authorizes an architectural study and implementation plan only; any product implementation needs separately approved, bounded follow-up tickets.

## Context and observed evidence

During physical Arch x86_64 dogfooding of installed Devlegate `0.5.7.dev0`, the operator encountered a sequence that existing commands handled individually but did not present as one coherent first-use workflow:

- `devlegate init <alias>` generated project-owned `.env`, `.devlegate/`, role templates and alias registration, even when the Git product branch had an unborn `HEAD` and no initial commit.
- `devlegate control init` then failed when `origin` was not configured/reachable, or could not proceed until a real initial product commit was made and the product branch published.
- On an independently initialized disposable repository with a valid bare `origin`, first-time `control init` successfully created and published `devlegate/control`; repeat initialization was idempotent. Preserve this already-working boundary, including TASK-088's absence-versus-observation-error semantics.
- `devlegate check` simultaneously reported unrelated worker readiness gaps (model, OpenCode, project-context routing), obscuring which bootstrap stage had been completed.
- A per-user host installation (`systemd`) can legitimately exist before there are any projects or per-project units. Installation, project onboarding, control-plane setup, worker readiness and service startup have different lifecycles.

The existing `project remove @ALIAS` decommissions a registered project while preserving its repository and runtime evidence; `project alias` can register it again at its existing path. Existing control Git code distinguishes new bootstrap from attaching an existing remote control branch and verifies worktree ownership. Do not reimplement these operations.

Current `RuntimeLocator.state_key` is derived from the resolved absolute repository path, while alias does not enter runtime identity. Therefore same-path reconnect, moving/renaming a checkout, new-host adoption and copying/forking a checkout are distinct; the latter three have no verified general identity-transfer contract yet. Do not claim relocation is already supported.

## Goal

Design a **single state-aware, optionally interactive `devlegate init` onboarding/reconnection experience** backed by a reusable deterministic, UI-independent bootstrap API. The architectural separation of product plane, control plane, project registration, durable runtime and host policy must remain intact. An operator should not have to invoke `control init` as a separate mandatory step of ordinary first-use onboarding, though a specialized operator recovery/attach surface may remain if independently justified.

The design must support a future terminal wizard, non-interactive CLI callers, tests and nonterminal frontends **without creating any new public command** or coupling business decisions to `input()`, TTY state or formatted stdout.

## Required architecture planning

### 1. Source-grounded ownership and lifecycle inventory

Inspect the current integrated implementation and tests of `init`, `control init`, `check`, `start`, `project alias/remove/rename`, local registry, host installation, runtime locator/state, control Git workflow and systemd owner boundaries. Identify precisely which operations are reusable as-is, which observations or preflights are missing, which effects are safe to compose, and which responsibilities must remain with their current owners. Account for related tasks TASK-088 (control first-use), TASK-089 (native runtime dependencies), TASK-148 (pre-runtime Git corruption recovery, a separate scope) and TASK-157 (accumulating UX observations); do not silently absorb those tickets.

### 2. Orthogonal bootstrap observations

Specify a typed, factual observation model that distinguishes at least:

- target directory missing / empty / populated / unsafe;
- no Git repository / unborn `HEAD` / branch with commits / detached `HEAD` / linked worktree;
- local product branch vs remote tracking: unpublished, synchronized, ahead/behind, divergent or unobservable;
- remote absent, present with missing ref, reachable, unavailable, or conflicting;
- Devlegate project configuration absent, partial, valid, customized or conflicting;
- project alias absent, same target, different alias on same repository, or foreign/conflicting registration;
- control ref absent, local-only, remote-only, attached, recoverable partially initialized, divergent, or owner-ambiguous;
- local runtime evidence absent, preserved after decommission, active/owned, or recovery-required;
- per-user host policy present/absent and per-project unit absent/owned/foreign;
- worker/backend readiness separately from Git/bootstrap readiness.

Model absent, negative, unknown and observation-error conditions distinctly; never infer safety from translated Git stderr.

### 3. Supported onboarding and reconnection scenarios

Describe deterministic decisions and allowed next actions for:
1. Devlegate installed before any project exists;
2. creation from a missing/empty directory (including opt-in `git init`);
3. nonempty existing directory not yet in Git;
4. unborn Git repository with an accessible empty bare remote;
5. existing local and published project not yet managed by Devlegate;
6. partial/interrupted `init` or control bootstrap with safe resumption;
7. project decommissioned via `project remove`, then reconnected on the same host/path without losing retained history/evidence;
8. reattachment to existing remote control history on a new host;
9. relocated/renamed repository path, requiring a separately reasoned identity choice;
10. copied/cloned/forked project with potentially simultaneous instances sharing product/control history.

Specify when a situation is a safe continuation, an explicit decision, or a fail-closed conflict. Preserve unfinished executions and ambiguous workers; do not resume, discard, overwrite or reset them as a side effect of onboarding.

### 4. Relocation and stable local identity design decision

Evaluate current path-derived `state_key`, alias-independent identity, effects on runtime SQLite, managed worktrees, internal Git refs, locks, sockets, logs, supervision ownership, and systemd unit names. Compare retaining a path-bound identity with an explicit offline relocation operation against a stable **local installation/project-instance identity** independent of the directory path. Address instance uniqueness and copy/fork/new-host cases; do not blindly store a single universal identifier in tracked project files and assume two clones are the same running instance. Propose a minimal authority and provenance model and explicit safety invariants, including how to differentiate reconnect from a foreign instance.

**Planning only:** no identity change, data conversion, in-runtime legacy/migration paths or one-off repair performed in this ticket. For self-hosted existing state, any later approved incompatibility may be handled with an explicitly scoped one-shot untracked script, not permanent production compatibility.

### 5. UI-independent backend contracts

Propose concise data types and APIs for `inspect -> plan -> apply -> re-observe` (names provisional), with immutable typed snapshots, executable actions, user decisions, risk classifications, effect/authority boundaries, expected identity/revision guards and structured results. Plans are not indefinite mutation authority: revalidate the necessary filesystem/Git/owner preconditions under the proper locks immediately before each effect; make safe retries idempotent.

Separate:
- pure read-only observation and transition planning;
- backend effect execution delegated to existing Git/project/host owners;
- CLI frontend: presentation, prompts, confirmation and actionable diagnostics;
- nonterminal use: explicit decisions and machine-readable results without surprise prompts.

No independent persistent bootstrap database or global all-or-nothing transaction is assumed; analyze whether any narrowly durable receipt is actually necessary for crash-safe effects before proposing it. Existing execution/reconciliation WAL must remain authoritative.

### 6. Operator control and CLI behavior

Design the normal `devlegate init [alias]` flow around observed state, including repeated invocation and optional interaction. No new CLI command is authorized. Explain proposed behavior for no TTY, explicit `--json`/noninteractive operation, missing required answers, cancellation, partial completion, and return codes. Do not auto-commit or auto-push product history, overwrite project-owned files/configuration, reset branches, replace configured remotes, repair corrupt Git or start services/workers without explicitly granted authority. A full Devlegate host install with zero projects is already a valid completed state.

Define how `check` reports bootstrap readiness separately from worker readiness; `start` must not silently bootstrap an unready project. Decide whether `control init` remains an advanced recovery entry point or is hidden from normal onboarding, using real operator scenarios rather than aesthetic preference.

## Deliverables and acceptance

- One focused architecture/design report committed to the product repository, with exact reviewed source revision, ownership map and a matrix of observed Git/filesystem/registry/control/runtime states and transitions.
- Explicit frontend/backend interface proposal, authorization model, idempotency/crash/re-entry semantics, and machine-readable error/decision semantics.
- A documented decision on stable instance identity versus path-bound relocation, including the distinction between move, reconnect, clone and new host, plus a do-nothing comparison and risks.
- An implementation sequence of small independently reviewable follow-ups with testable acceptance criteria: (a) observation/planning backend, (b) safe application/reconnection and identity where approved, (c) optional interactive CLI frontend. Proposals only; no follow-up tickets or code changes without curator approval.
- Proposed real-Git and systemd-fixture acceptance matrix, including unborn HEAD, empty/reachable remote, remote observation error, control attach, partial bootstrap, alias conflict, preserved state, linked worktree, copy/fork and non-TTY behavior.
- Preserve product/control provenance, no reinitialization of existing control history, no fabricated recovery authority and no unrelated runtime redesign.

## Scope boundary

**Backlog only; not eligible in the current iteration.** Do not implement this wizard, add commands, rework packaging, introduce persistent migration support, alter live runtime/project artifacts or modify control/review/execution history under this planning task. Continue current Arch dogfooding and existing release tasks first.
