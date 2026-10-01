---
"type": "devlegate.ticket"
"title": "Make persistent service startup an explicit start command"
"depends_on": ["TASK-022"]
---

## Milestone

Next iteration after Devlegate 0.5.5.

## Goal

Remove the asymmetric top-level CLI behavior where invoking `devlegate` with no
command starts the persistent service.

Make startup explicit through:

```text
devlegate start
```

and make a commandless invocation side-effect-free.

## Context

The current CLI uses the absence of a subcommand as the persistent-service start
operation. This makes the command grammar asymmetric:

```text
devlegate
devlegate stop
devlegate restart
devlegate status
```

and makes an accidental bare invocation operational rather than informational.

The existing startup behavior itself is valid; this task changes how it is selected,
not the hosting/reconciliation semantics behind it.

## Required behavior

### Explicit start command

Add a top-level `start` command that owns the current bare-start behavior.

`start` must preserve the existing startup semantics, including:

- project resolution from the current working directory;
- explicit `--env FILE` project selection;
- registered `@ALIAS` project selection;
- current host-mode selection and managed-systemd reconciliation;
- existing service-health/version checks;
- idempotent behavior when the service is already running;
- current startup/restart authority and readiness semantics.

Do not duplicate the startup implementation merely to add the command. Route
`start` through the existing authoritative startup path.

### Commandless invocation

A plain:

```text
devlegate
```

must have no service, repository, registry, systemd, IPC, or runtime-state side
effects.

Render the top-level help/command summary and exit successfully.

If a project selector is supplied without an operation, for example:

```text
devlegate @project
devlegate --env /path/to/.env
```

fail clearly as an incomplete invocation and point the operator toward an explicit
command such as `start`; do not preserve selector-only startup as a hidden alias.

### Help and documentation

Update top-level usage/help so startup is visibly symmetric with `stop` and
`restart`.

Remove wording that says a commandless invocation starts the service.

Keep `foreground` and `once` as their current explicit attached/single-pass
operations.

## Acceptance criteria

- `devlegate start` performs the same supported persistent startup behavior that
  bare `devlegate` performed before this task.
- Plain `devlegate` prints top-level help and performs no operational mutation.
- Selector-only invocations do not start a service.
- `start`, `stop`, and `restart` are discoverable together in top-level help.
- Existing host/systemd reconciliation and service identity behavior is unchanged.
- Existing explicit `foreground` and `once` behavior is unchanged.
- Machine/project selection semantics are not broadened or guessed.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Bare `devlegate` renders help and does not resolve/start a project.
- Bare invocation does not touch service IPC, host integration, systemd, or runtime
  state.
- `devlegate start` starts/resumes the same target as the previous default path.
- `devlegate @alias start` and the supported `--env` form select the exact
  requested project.
- Selector without command fails without side effects.
- Starting an already-running service remains idempotent.
- Existing `foreground`, `once`, `stop`, and `restart` regressions stay green.


## Review finding: stale startup guidance

Execution `323c891970e644c3a5d351e48e5c7d93` / checkpoint
`6fcc839eba3bffebc28cfa27bdd0837af7967ca2` correctly implements the
explicit `devlegate start` command and the commandless side-effect-free path.

The production routing and tests show:

- bare `devlegate` prints help without project/service resolution;
- selector-only invocation fails before startup;
- `devlegate start` reuses the previous authoritative startup path;
- alias and `--env` selection remain exact;
- existing foreground/once/platform behavior remains intact;
- GitHub CI run `36829628324` is green:
  `1069 passed, 1 skipped`, coverage 79%, Ruff green.

One small operator-UX inconsistency remains.

Several daemon-dependent mutation paths in `src/devlegate/cli.py` still emit:

```text
service is not running for this checkout; start `devlegate`
```

That advice is now incorrect because bare `devlegate` only prints help.

Update every current user-facing occurrence to point to the explicit command, e.g.:

```text
service is not running for this checkout; run `devlegate start`
```

or equivalent concise wording.

The affected paths include retry, drop, recover, and reconciliation commands. Update
their regression expectations as well so the old hidden-start guidance cannot return.

Do not change startup semantics or broaden the ticket further. Return to review with
full CI and Ruff green.
