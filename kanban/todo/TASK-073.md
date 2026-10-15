---
"type": "devlegate.ticket"
"title": "STORY acceptance gate: isolate Git implementation from ServiceEngine"
"depends_on": ["TASK-071", "TASK-072"]
---

## Purpose: parent story and final curator acceptance

This is the **parent story and final acceptance gate** for the Git-layer refactor. It is intentionally represented as an ordinary Devlegate ticket: the current workflow does not support native Epic/Story metadata. The completed milestones TASK-061 and TASK-067 through TASK-070, plus the two active implementation tasks TASK-071 and TASK-072, are child work toward **one architectural outcome**. Passing individual ticket tests or reducing a command counter is not proof this story is done.

This ticket runs only after TASK-071 and TASK-072 reach `done`. It is an **audit/report task**, not permission to silently redesign the system. Return any remaining gaps for focused follow-up work. **Final acceptance requires an explicit human curator decision, not an automated self-approval or merely green CI.**

## Curator's intended architecture (normative)

Git is Devlegate's supported first-class VCS backend. **Do not build a generic VersionControlSystemInterface**, plugin framework, or speculative backend portability layer.

Separate concerns without increasing indirection gratuitously:

1. **CLI transport**: the installed Git command runner, process lifecycle, captured output and OS/execution failures.
2. **Typed Git semantics**: commit/ref/object resolution; ancestry and history; worktree/branch inspection; blob/tree reads; ref creation/CAS and other command-level effects. Explicitly distinguish missing/negative results from Git observation errors.
3. **Domain Git workflows**: control/product synchronization, checkpoint publication, evidence pinning, recovery, reconciliation and lifecycle effects under their distinct constraints.
4. **ServiceEngine**: admission, serialized ownership/state transitions, scheduling, WAL orchestration and policy decisions. It uses semantic operations, not embedded raw Git command strings.

## Strict story-level acceptance criteria

All conditions must be verified on the **integrated product HEAD**, not inferred from individual worker reports:

1. **No scattered raw Git subcommand calls in `src/devlegate/runtime.py`.** No direct `_git(repo, "rev-parse", ...)`, `_git(..., "update-ref", ...)`, `self._git_runtime(..., "fetch", ...)`, `subprocess` invocation of Git, or equivalents constructed indirectly throughout ServiceEngine. At most a single *owner/shutdown-aware command transport seam* may remain, with no command-specific business logic. Any other exception is a **failure pending explicit curator approval**; workers cannot exempt their own calls.
2. **No fake decomposition.** Replacing raw commands by equally low-level one-line forwarding methods does not qualify. Git command construction and result parsing belong in cohesive infrastructure/domain modules; `runtime.py` must express business operations. Demonstrate real responsibility boundaries and no avoidable circular imports or Protocol inflation.
3. **Errors are meaningful and fail closed.** Distinguish absent ref, valid negative ancestry, unreachable/corrupt repository, malformed Git output and execution/OS errors. `run_git()` transport behavior must be explicit and stable; semantic helpers must not silently turn arbitrary Git failures into absence or permission to repair/create. Handle root commits correctly. Map typed Git failures to appropriate Devlegate domain errors at entrypoints; retain owner-bound shutdown interruption semantics.
4. **Transactional invariants preserved.** Owner lock/authorization and mutation admission; WAL ordering; evidence pin before publication; expected-old ref CAS; remote lease checks; re-observation before effects; crash/retry/reconciliation continuation; control/product/workspace provenance and isolation. Do not weaken these to achieve a counter target.
5. **Behavior and compatibility unchanged** except narrowly justified correctness fixes. Existing focused and full CI proof must be green on the final exact SHA, including pytest and Ruff. Add only focused tests for newly factored semantics or discovered gaps; do not duplicate broad E2E suites.
6. **Quantitative, traceable audit.** Inventory *all* prior direct Git call sites, grouped into read/mutation/transport, and map them to new semantic operations or domain modules. Count remaining raw command construction in `runtime.py` (target **zero** outside the one narrowly justified transport seam), direct subprocess executions, source LOC and module dependency edges before/after. Explain unchanged operations and any extra layers. Do not misrepresent fewer source call sites as fewer Git processes.
7. **No premature closure.** The story cannot be accepted merely because TASK-071/072 are accepted, because CI is green, or because individual subprocess patterns were deduplicated. If ANY criterion is unmet, report precisely which, cite relevant paths/functions, mark worker result `incomplete` with actionable remaining work, and leave final acceptance to the curator.

## Acceptance audit deliverable

Prepare a concise evidence-based table for criteria 1–7: PASS/FAIL, source references and numeric before/after inventory. Include a short remaining-risk section, especially around `_git_runtime`, `resolve_commit`, CAS and publication. Audit only; do not self-authorize acceptance or introduce broad code changes.

**Human curator's explicit APPROVE is the only completion signal for this parent story.**
