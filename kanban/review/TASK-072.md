---
"type": "devlegate.ticket"
"title": "Extract Git mutation and publication mechanics from runtime"
"depends_on": ["TASK-071", "TASK-077"]
---

## Goal

Finish the layered Git-specific extraction started by TASK-070/071: `ServiceEngine` orchestrates workflow policy, state and ownership, while Git command construction and output decoding live in cohesive Git infrastructure or domain workflow modules. Do not design a hypothetical multi-VCS interface.

## Work

1. Inventory all remaining direct Git mutation/transport calls in `runtime.py`: `update-ref`, fetch/push, branch/worktree management, commit/add/reset/merge, evidence pinning and publication/recovery sequences. Document each call's preconditions, owner lock, WAL stage, lease/expected-old, re-observation and error semantics.
2. Consolidate actual common Git mutations as *explicit* typed operations, especially create-if-absent ref vs compare-and-swap vs unconditional update. Do not hide an unguarded update behind an innocuous generic name. Keep distinct domain validation and policy outside low-level Git helpers.
3. Extract cohesive control/product synchronization, publication, evidence, or lifecycle Git command sequences into existing specialized modules or minimal new modules only where doing so clarifies responsibility. Preserve serialized state mutation in ServiceEngine and owner admission, provenance/evidence-before-publish, interruption handling, crash replay and CAS boundaries.
4. Remove raw Git subcommand construction from `runtime.py`; callers should use semantic Git operations or domain functions. Do not just create one-line forwarding methods, broaden Protocols or increase indirection to meet a textual target. A minimal owner-bound execution seam may remain solely for owner/shutdown enforcement, but it must not host scattered Git command construction.
5. Reuse existing regression coverage; add only focused tests for newly factored primitives and previously untested invariants. Validate full pytest and Ruff on the exact checkpoint.

## Explicit effects and subprocess accounting

- Each Git mutation helper must declare its actual effects and the Git subprocesses it executes. In particular, an apparently read-only method must never conceal `fetch`, `update-ref`, `push`, branch changes, cleanup, or worktree mutation.
- Compare command traces for representative flows (control/product sync, accepted integration, evidence pinning, retry and recovery) before and after. Keep intentional re-observation and CAS checks. Document and cost-classify every extra Git invocation. Modest justified extra local-only reads may be acceptable; additional remote reads require clear latency/round-trip rationale; any new state-changing command requires exact safety justification and curator review. No hidden `N+1` Git calls.
- Route Git CLI launches through the established transport seam (including owner/shutdown-aware variants); do not introduce scattered new direct `subprocess` Git invocations, hidden shell commands or background launches. Use small focused spies/tests instead of a large instrumentation framework.

## Git execution cost and effect classes

Track newly introduced *executed commands* by **actual effects**, not just by Git subcommand name or number of helper calls:

- **Tier 1 — local-only read:** no remote access, no persistent repository/worktree mutation (e.g. `rev-parse`, `rev-list`, `merge-base`, `cat-file` in read-only modes). Lowest priority for elimination relative to network and mutation; nevertheless, an extra invocation is not made acceptable simply by being local. Avoid per-object `N+1` loops and expensive history scans.
- **Tier 2 — remote read:** accesses the remote but does not mutate repository/worktree state, e.g. `ls-remote`. Higher priority for elimination than local reads; extra network round trips are not an acceptable trade-off merely because a helper is cleaner.
- **Tier 3 — state-changing operation:** mutates local or remote Git state, including refs/index/worktree/object store; e.g. `update-ref`, `add`, `commit`, `reset`, `merge`, `push` and ordinary `fetch` (remote read **plus local object/ref writes**). Highest priority for eliminating newly introduced calls due to side effects, durability, and recovery/authorization implications. Identify *local versus remote* writes separately.

A command can have mixed effects; classify by the **highest applicable tier**, not its apparent name. Some observations such as `status` may refresh the index depending on how invoked; check actual options/side effects. **These tiers are an order for eliminating unwanted new calls: state-changing first, remote read second, local-only read third. They are not a cost budget, an allowance to add calls, or numeric weights.** An added invocation of any tier is a potential regression requiring a before/after trace and concrete rationale. Do not accept more local reads simply because they are cheap. If a genuinely indispensable safety verification introduces a command, make it visible for explicit curator review; never hide new network operations or mutations in read helpers. Preserve intentional race/CAS checks; do not sacrifice safety simply to reduce process counts.

## Acceptance

Every original raw Git call in `runtime.py` is accounted for in a before/after inventory. Zero scattered raw Git command calls remain in the main runtime; if one owner-bound executor remains, justify that safety boundary explicitly. Report actual removed duplication, changed module edges, LOC and direct-call counts without mistaking renamed call sites for fewer subprocess executions. No workflow behavior or ownership weakening; per-tier before/after executed-command traces, no hidden effects and no automatic acceptance of extra calls at any tier; exact-checkpoint CI green.

## Reviewer decision — 2026-10-16: REWORK REQUIRED (same-ticket hardening)

**Reviewed execution:** `64453551b2974730b0124841bb3038c8`; exact feature checkpoint `fd0765ad2e792c8373577eb29bbe5d666557d120`; product base `dbff0d65fbf27ead55a6acd7fb17bea4abc8622c`. Worker claimed `completed` with no remaining items; this does not confer review acceptance. The atomic lifecycle evidence was published at control `150cfd7f5f8841ab60e5187b99baa1a817089ef9`.

### Verified work to preserve

- Direct raw Git subcommand construction has been removed from ordinary `runtime.py` call sites; `_git_runtime` remains the owner/shutdown-aware Git transport boundary.
- `git.py` now provides named, explicit ref create-if-absent, compare-and-swap, and unconditional update operations, along with named transport/worktree helpers. Preserve their distinct semantics.
- `docs/GIT_COMMAND_INVENTORY.md` lists effectful operations and existing WAL/admission checkpoints; the added `test_git.py` spies assert argument-level command construction.

### Blocking gap A — actual domain decomposition, not renaming Git invocations

Current `runtime.py` still owns the Git-specific *sequences* for control/product sync, accepted integration, publication, lifecycle replay, evidence pinning, drop, and reconciliation. Most of the diff replaces `_git(repo, "command", ...)` with one-line `git.some_command(repo, ..., runner=_git)` while leaving complete Git choreography at the original call site. `push_refs(remote, *specs, options=...)`, `add_worktree(*arguments)`, `fetch_ref`, `merge_fast_forward`, etc. are low-level typed command wrappers, **not proof that Git-domain workflows have been extracted**.

The existing ticket explicitly requires meaningful responsibility boundaries and prohibits *cosmetic forwarding layers*. Identify real cohesive Git command sequences that should move into domain workflow modules (existing modules first, small dedicated modules only where justified) while keeping **ServiceEngine** responsible for workflow policy, owner locks, WAL/state transitions, authorization, retries and operator-visible errors. Extract these cohesive responsibilities rather than merely changing method names. Reuse established `git.py` primitives; do not invent a generic VCS interface, broad Protocol expansion, or a parallel owner/transaction framework.

In the worker report, map *every* original runtime mutation site and remaining semantic operation to its final owner and explicitly distinguish cases where keeping orchestration in ServiceEngine is necessary. Demonstrate a **substantive** module boundary: source LOC, dependencies, eliminated duplication, and a before/after map of Git choreography remaining inside `runtime.py`. A zero count of literal raw Git commands alone does not satisfy acceptance.

### Blocking gap B — missing executed-command/effect accounting

The 41-line `docs/GIT_COMMAND_INVENTORY.md` is a useful qualitative catalog, but contains no exact before/after **executed Git command traces**, counts by Tier 1/2/3, changed calls, or quantitative validation of effects and duplicated invocations for representative workflows. The new tests in `tests/test_git.py` check wrapper command strings only; they do not verify whole-flow call parity, ordering, durability boundaries or hidden subprocess effects.

Provide focused trace tests (small spies or existing harnesses, not a new instrumentation framework) and an audit table for control/product synchronization, checkpoint publication, accepted integration, lifecycle replay, evidence pins, and recovery/drop where applicable. Count *executed subprocess invocations*, including nested helpers. Classify actual effects (ordinary fetch is Tier 3), differentiate local/remote mutations, explain every added command (including Tier 1), and prove no new `N+1`, hidden network calls, unintended writes, or loss of necessary re-observation/CAS. The preferred outcome is equal or fewer commands, but do not remove required safety checks just to lower counts.

### Required verification and boundaries

- Regression coverage must exercise representative real workflow or cross-layer paths **before/after extraction**, including lease/CAS races, failed pushes, interrupted recovery, evidence-before-publication and owner/shutdown semantics. Preserve existing full test coverage; add only focused tests for genuinely new boundaries.
- Exact-checkpoint full pytest with coverage and Ruff must be green. [CI #38952185189](https://github.com/ranmasao/devlegate/actions/runs/38952185189) was still running during this review and must not be represented as successful before it finishes.
- Avoid expanding unrelated `runtime.py` logic; preserve `TASK-073` as the separate final architecture acceptance gate. Do not change execution reports, rewrite published Git history, integrate product manually or create a replacement ticket.

**Reviewer disposition:** move the **same** `TASK-072` from `kanban/review` back to `kanban/todo` with this hardening specification. Existing implementation and evidence remain intact for the next worker attempt.

## Reviewer decision — 2026-10-16, execution cbbc2c5b: REWORK REQUIRED

Reviewed exact feature checkpoint `d11380674977e246209353b22c8b627ae02c1dda`, execution `cbbc2c5b6dd7493980920fc66dbabdda` (worker conclusion: `incomplete`). The lifecycle was successfully published in control commit `7d9161396151` after the curator corrected an inadvertent **concurrent control edit** using compensating commit `eb6347443dc8`. The earlier `control descendant changed the active ticket` block came from the curator changing the ticket during execution; **do not attribute it to worker code, discard the checkpoint, or rewrite the control/feature history**.

**Retain:** typed Git primitives and the newly extracted `control_git_workflow.sync_control()`, including owner-bound runner and preservation of fetch/ancestry/merge/re-observation order. Do not reimplement these.

### Required hardening

1. **Fix exact-checkpoint CI.** [GitHub Actions #38953457384](https://github.com/ranmasao/devlegate/actions/runs/38953457384) ran 1229 successful tests, 1 skipped, 1 failing. The deterministic failure is `tests/test_licensing.py::test_devlegate_owned_source_has_exact_eupl_header`: `src/devlegate/control_git_workflow.py` has `# SPDX-License-Identifier: EUPL-1.2.` (trailing period) instead of the required `EUPL-1.2`. Correct this header, obtain fully green exact-SHA pytest+coverage and Ruff; Ruff was skipped after this failure. The worker's missing local pytest/Ruff executables are not a reason to waive authoritative CI.
2. **Resolve duplicated/unreachable control Git workflows.** The new `control_git_workflow.py` defines `attach_existing_control()` and `bootstrap_control()`, but `runtime.py` imports only `sync_control` and still contains and calls its original `_attach_existing_control()` and `_bootstrap_control()` implementations. The new attach/bootstrap functions are not exercised by their new tests. Either integrate these cohesive transactions through a thin ServiceEngine policy/exception seam **while preserving exact authorization, owner, failure, worktree topology and publishing invariants**, or remove the unused duplicated implementations with a precise scope justification. Never leave two independent versions of effectful Git control initialization. Add real integration/trace tests for whichever implementation remains authoritative.
3. **Substantiate transaction-level effect accounting.** `docs/GIT_COMMAND_INVENTORY.md` now provides before/after Tier 1/2/3 tables, but only control sync has a command trace assertion; many other rows are descriptive claims, not independently instrumented observations. Reuse existing cross-layer harness/spies for representative publication, accepted integration, and recovery/evidence flows, recording executed Git commands (including nested helper calls), before/after counts, and any permitted unchanged operation traces. Validate lease/CAS, interrupted/retry and error paths; do not add a generic instrumentation framework or remove safety observations to reduce counts. Keep already-existing ownership boundaries and do not demand unnecessary wholesale extraction from ServiceEngine.

The original requirements of TASK-072 remain authoritative. Maintain `TASK-073` as the independent final architecture gate. Preserve all historical execution reports and source checkpoints. Perform the next implementation via ordinary Devlegate worker execution and atomic `todo → review` lifecycle publication.

**Disposition:** return this **same** ticket `review → todo` for focused hardening; no acceptance and no manual product integration.
