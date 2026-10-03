---
"type": "devlegate.ticket"
"title": "Add forced restart for stuck active workers"
"depends_on": ["TASK-037"]
---

## Milestone

Devlegate 0.5.6 lifecycle and operator UX.

## Goal

Add:

```text
devlegate restart --force
```

as the explicit bounded-recovery path for restarting a service when an active
worker cannot reach the normal checkpoint boundary.

The command must compose the already-proven forced-stop semantics with the existing
restart/recovery model. It must not introduce a second worker-kill mechanism or
silently discard execution lineage.

## Context

Dogfooding exposed the missing operator path after a network loss left an active
worker unable to make progress.

Plain:

```text
devlegate restart
```

correctly entered checkpoint-aware draining. Under managed systemd,
`TimeoutStopSec=infinity` then left the restart job waiting indefinitely for the
worker to reach its safe boundary.

The installed CLI already supported:

```text
devlegate stop --force
```

but the still-running older service instance rejected the force payload. The
operator therefore had to retire the exact managed unit externally with:

```text
systemctl --user kill --kill-who=all --signal=SIGKILL <unit>
```

Because a systemd restart job was already pending, killing the old service completed
the stop phase and systemd immediately started the replacement service. Recovery
then resumed the retained execution workspace and created a new execution attempt.

This demonstrated the desired high-level behavior, but only through an external
systemd intervention. Devlegate needs a first-class operator command for it.

## Required behavior

### CLI surface

Add:

```text
devlegate restart --force
```

- Plain `devlegate restart` remains checkpoint-aware and unchanged.
- `restart --force` explicitly means that the operator does not want to wait for
  the active worker to reach the graceful checkpoint boundary.
- Keep restart output concise and human-readable.
- Do not reintroduce `--json` or `--yaml` to the top-level restart action.

### Forced restart semantics

`restart --force` must be a composition of existing proven mechanisms:

```text
force-retire current owned worker
-> persist interruption/lifecycle evidence
-> preserve execution/workspace/branch lineage
-> release old service authority
-> establish replacement service authority
-> recover through the existing interrupted-execution model
```

In particular:

- close new worker admission before retirement;
- interrupt only the exact owned worker/process group using the existing
  WorkerSupervisor bounded termination discipline;
- preserve the same interruption provenance used by `stop --force`;
- preserve the execution workspace, execution branch, ticket identity, checkpoint
  lineage, and recovery evidence;
- do not treat forced restart as `drop`, purge, acceptance, integration, or a clean
  execution completion;
- do not fabricate a fresh execution lineage merely because the service process was
  replaced;
- after replacement startup, recover the retained interrupted execution through the
  normal recovery path.

### Lifecycle identity and escalation

A graceful restart already in progress must be safely promotable to forced restart.

For:

```text
restart request A -> draining
restart --force request B
```

the system must preserve one coherent authoritative lifecycle operation rather than
inventing a conflicting second restart.

If the original lifecycle request identity remains authoritative, force escalation
and completion proof must bind to that identity, following the same invariant
already established for graceful-stop -> `stop --force`.

### Managed systemd hosting

For an externally/systemd-hosted service:

- operate only on the exact proven managed unit;
- do not use blind kill-by-name or process-name matching;
- do not rely on the operator manually invoking `systemctl kill`;
- the replacement start must be part of the same proven restart operation;
- `Restart=no` remains valid: this feature is an explicit lifecycle restart, not a
  crash restart policy;
- a force restart must not leave an indefinitely pending systemd stop/restart job.

### Version-skew behavior

A new CLI talking to an older running service may encounter a daemon that does not
understand the forced-restart payload.

- Fail closed with a clear version/protocol incompatibility error.
- Do not silently fall back to an untracked systemd kill.
- Preserve existing protocol/version diagnostics so the operator can determine that
  the installed CLI and running daemon differ.

## Acceptance criteria

- `devlegate restart --force` is exposed in CLI help.
- Plain restart remains graceful/checkpoint-aware.
- Active-worker forced restart retires the exact owned process group through the
  existing force-stop machinery.
- Interruption evidence is durable before the old authority is considered retired.
- Execution/workspace/branch/checkpoint lineage survives the restart.
- Replacement service authority is established and the interrupted execution is
  recovered through the existing recovery model.
- Graceful restart followed by force escalation preserves one coherent lifecycle
  identity and completes without waiting for a phantom request receipt.
- Managed-systemd force restart does not require an external `systemctl kill`.
- Foreign or unproven systemd authority cannot be killed or restarted through this
  path.
- Older-daemon protocol mismatch fails closed and does not trigger a fallback kill.
- Full tests, coverage, and Ruff remain green.

## Required regressions

1. CLI parser:
   - `restart --force` is accepted;
   - `restart --json` and `restart --yaml` remain rejected.

2. Active worker, live service:
   - start a real service with a deliberately long-running owned worker;
   - invoke the real `devlegate restart --force` surface;
   - prove exact worker-group retirement, old authority release, replacement
     authority startup, and successful command completion.

3. Recovery:
   - prove durable state records the forced interruption;
   - prove the same ticket/execution/workspace/branch lineage is retained;
   - prove replacement startup enters the existing interrupted/recoverable path
     rather than treating the execution as clean, dropped, or unrelated.

4. Escalation:
   - graceful restart A enters draining;
   - `restart --force` request B promotes the same lifecycle;
   - completion is proved through the authoritative lifecycle identity rather than
     a phantom second receipt.

5. Uncooperative worker:
   - reuse the existing WorkerSupervisor process-group harness to prove bounded
     termination escalation is reachable from the public forced-restart path.

6. Managed systemd:
   - exact managed unit only;
   - no blind process-name fallback;
   - explicit restart succeeds with `Restart=no`;
   - no external `systemctl kill` is necessary.

7. Version skew:
   - newer CLI + older daemon that rejects the force payload fails closed with a
     clear incompatibility diagnostic and performs no external kill fallback.


### First review: CLI/IPC plumbing exists, but CI is red and forced-restart lifecycle proof is missing

Execution `164e75354b974936b00907826523aee8` / checkpoint
`8b597af805cdb483bddef579c9ed167a8a158e8b` adds the requested CLI and protocol
surface:

- `restart --force` parses;
- restart IPC now accepts a boolean `force` payload;
- daemon lifecycle admission accepts forced restart for external/systemd hosting;
- force escalation reuses the existing lifecycle request identity returned by the
  daemon;
- the implementation routes forced interruption through the existing
  `operator_abort`/WorkerSupervisor path rather than adding another kill mechanism;
- unsupported forced lifecycle payloads are intended to fail closed rather than
  falling back to an external kill.

That is the correct implementation direction, but the ticket is not ready for
acceptance.

#### Blocker 1: full CI is red and plain managed restart behavior regressed

GitHub Actions run `37107644104` fails:

```text
1 failed, 1094 passed, 1 skipped
```

The failing regression is:

```text
tests/test_project_registry.py::
test_top_level_lifecycle_systemd_failure_is_concise_cli_error[restart]
```

Expected:

```text
devlegate: systemctl restart failed
```

Actual:

```text
devlegate: service IPC unavailable: [Errno 2] No such file or directory
```

The candidate changed the managed-systemd restart path so that it now sends a
`restart` IPC request before invoking `systemctl restart`. That changes plain
`devlegate restart` semantics in a case the existing suite explicitly protects:
proven managed systemd authority exists, but the service IPC endpoint is unavailable.

The ticket explicitly requires plain restart to remain unchanged and
checkpoint-aware.

Resolve this intentionally rather than merely updating the old assertion.

For the managed path, define and test the distinction between:

- ordinary `restart`, whose existing behavior must remain compatible;
- `restart --force`, which needs the new explicit force admission/retirement
  handshake.

If ordinary managed restart genuinely must acquire an IPC lifecycle receipt after
the current architecture changes, then the ticket/specification and all recovery
semantics must justify that behavioral change. Otherwise keep the new IPC handshake
specific to the forced path and preserve ordinary restart behavior.

Return only with full CI green, including Ruff.

#### Blocker 2: required public-path forced-restart regressions are absent

The candidate adds only:

- parser coverage for `restart --force`;
- rejection of `restart --json/--yaml`;
- an IPC dispatch unit test that forwards `force=True`.

Those tests prove argument/protocol plumbing. They do not prove the feature this
ticket exists to add.

The required regressions in the ticket remain unimplemented:

1. **Real active worker / public CLI path**
   - start a real hosted service;
   - start a deliberately long-running owned worker/process group;
   - invoke the real `devlegate restart --force` CLI;
   - prove the exact owned worker group is retired;
   - prove the old service authority exits;
   - prove replacement authority becomes ready;
   - prove the command completes.

2. **Recovery and lineage preservation**
   - inspect durable state/evidence after forced retirement;
   - prove the ticket, execution identity/binding, worktree, branch and checkpoint
     lineage are preserved;
   - prove replacement startup uses the existing interrupted-execution recovery
     model rather than clean completion/drop/fresh unrelated work.

3. **Graceful -> forced restart escalation**
   - request graceful restart A and hold it in draining on a live worker;
   - issue `restart --force` B;
   - prove force promotes the same authoritative lifecycle identity;
   - prove CLI completion waits on that authoritative identity and does not wait for
     a phantom request B receipt.

4. **Uncooperative worker**
   - drive the public forced-restart path through the existing WorkerSupervisor
     process-group harness;
   - prove bounded escalation reaches the existing hard termination step when the
     worker ignores the initial signal.

5. **Managed systemd**
   - exercise the exact proven unit path, not only a mocked IPC dispatcher;
   - prove `Restart=no` is compatible with explicit forced restart;
   - prove no manual/external `systemctl kill` is needed;
   - prove foreign/unproven unit authority is not operated on.

6. **Version skew**
   - emulate a new CLI talking to an older daemon that rejects the `force` field;
   - prove the command fails closed with a clear protocol/version diagnostic;
   - prove no systemd/process kill fallback occurs.

These can reuse the existing real-service, WorkerSupervisor, and systemd-supervisor
harnesses. Do not duplicate process-management machinery just to make tests easier.

#### Blocker 3: forced protocol-skew classification is currently too broad

The CLI currently does, for forced stop/restart:

```python
except IPCClientError as error:
    if force and error.application:
        raise DevlegateError(
            "forced restart is unsupported by the running service; "
            "the installed CLI and daemon protocol versions differ"
        )
```

`error.application` means an application-level daemon error; it does not by itself
prove protocol/version skew.

A current daemon may legitimately reject a forced restart for reasons such as a
conflicting lifecycle intent or another lifecycle precondition. Those errors must
not be rewritten as "CLI and daemon protocol versions differ".

Detect the old-daemon/unsupported-payload case narrowly from the protocol/application
error identity that actually denotes an unrecognized/invalid force field (or add an
explicit capability/protocol signal). Preserve other daemon application errors as
their real diagnostics.

Add regressions for both:

- genuine older-daemon force-payload rejection -> clear version/protocol mismatch;
- current-daemon semantic lifecycle rejection -> original semantic error, not
  mislabeled as version skew.

### Acceptance boundary

Return to review only when all of these hold:

```text
plain restart compatibility: pass
restart --force real active worker: pass
forced recovery/lineage preservation: pass
graceful -> force escalation identity: pass
uncooperative worker bounded retirement: pass
managed systemd exact-unit path: pass
version skew + semantic error distinction: pass
full pytest/coverage: green
Ruff: green
```

The existing production direction can remain; this review does not request a
redesign unless the required real lifecycle tests expose one.
