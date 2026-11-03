---
"type": "devlegate.ticket"
"title": "Bootstrap recovery of interrupted Git metadata and managed checkout corruption"
---

## Milestone and scheduling

Devlegate self-hosting reliability. **Backlog until explicitly scheduled**; this is a generic host/startup integrity feature, not follow-up work to one ticket or a repair of one historical execution. No ticket-specific production paths, no migration framework and no separate always-on repair daemon.

## Goal

After abrupt host/VM/WSL termination or interrupted filesystem writes, the service must identify and, where it can prove an exact safe reconstruction, **automatically recover** interrupted Git objects, internal evidence refs and managed control checkout files, then enter the existing normal runtime/worker restart reconciliation. If proof or source data is unavailable, fail closed with a precise, actionable *bootstrap recovery* diagnostic while preserving all evidence and unfinished worker work. A corrupt local Git object must not permanently prevent the recovery machinery itself from starting.

The central requirement is **recovery before normal Git-dependent service initialization**: a service that cannot resolve `HEAD` cannot use ordinary runtime reconciliation, ticket validation or CLI/status as its sole repair entry point.

## Observed incident (motivating evidence, not a production special case)

On 2026-11-03, after an abrupt WSL restart during an execution, the installed `0.5.7.dev0` user systemd service failed **during its initial Git checkout observation**, before announcing normal service readiness and before worker startup/reconciliation. The immediate error was `cannot observe Git checkout: cannot resolve Git revision 'HEAD'`; an on-disk loose commit object reachable from the control branch was **zero bytes**. The service exited with status 1. The corruption affected the shared Git common directory, so the product checkout and linked control worktree both depended on the same damaged object database.

After manually reconstructing that commit from the authoritative remote, `git fsck --full` revealed **four additional zero-byte loose objects** (three trees and one blob), an internal `refs/devlegate/recovery/workspace/<ticket>/<execution>` ref represented by a zero-length file and reported as an all-zero invalid SHA pointer, and a linked-control index cache-tree pointing to a missing tree. The control worktree also had **two zero-length tracked files**: the canonical todo ticket and its immutable execution-attributed reviewer feedback. Their intended bytes were verified against the exact published control HEAD; restoring those bytes removed the ticket-frontmatter blocker.

Normal startup after object, ref and checkout repair saw persisted `agent_running` state, no live worker and a preserved execution worktree; the existing recovery path resumed the **same execution ID** with `directive=resume` and retransmitted its persisted prompt. This establishes that the post-repair runtime lifecycle can work, but not that the pre-runtime integrity recovery exists.

The precise cause of the interrupted writes is not proven; the contract must depend on **observed disk state**, not a WSL-specific explanation. Do not assume every dangling commit/tag is corrupt; the incident's many dangling objects were not the cause. The incident has already been manually repaired: **do not access or alter its real resources in tests or migration scripts**.

## Required implementation — ordered critical path

### 1. Independent preflight before any Git-dependent initialization

Trace the actual service and CLI initialization path. In `src/devlegate/runtime.py`, `ServiceEngine.__init__` currently calls `self._validate()` **before** `self._load_state()`; `_validate()` calls `_branch_runtime(self.repo)` and other Git observers. Other bootstrap/location helpers may resolve Git revisions even earlier. Move/add the smallest necessary **pre-initialization integrity preflight** before any step that requires readable `HEAD`, a valid control ticket, a registered worktree or normal state-loaded owner reconciliation. It must be able to locate configured repository/common Git directory, runtime state key and owner lock without successful Git revision resolution. Never assume the canonical control ticket or its feedback can already be parsed.

Distinguish bootstrap outcomes: clean / deterministic repairable / blocked-unproven / transient-unavailable. Acquire **exclusive per-repository owner authority before any repair effects**; never race another daemon, operator Git operation or possibly live worker. Read-only preflight/status may inspect and report but must not mutate. Ensure failure is discoverable from systemd logs (and from a restricted degraded status/diagnostic surface if feasible) even when full ServiceEngine cannot initialize. Avoid generic `cannot observe Git checkout` crash loops concealing the actual damaged path, object type and recovery stage.

### 2. Detect and inventory specific damage, without destructive Git maintenance

Read-only detection must handle zero-byte/truncated/invalid loose objects; missing expected commit/tree/blob objects in the **shared common object database**; empty/malformed Git refs including internal evidence refs; linked-worktree registration/index inconsistencies; and tracked control files whose contents diverge from exact committed blobs (including zero-length ticket and reviewer feedback). Distinguish absent objects from unreachable/dangling but valid objects, missing refs from a ref with unknown target, Git object corruption from dirty/staged/untracked work, and physical disk errors from missing remote evidence. Use targeted probes where possible; bounded `git fsck` is a diagnostic escalation, not a blanket prerequisite on every poll.

Build an immutable, auditable damage inventory: path/ref, expected object type and SHA when independently known, observed bytes/type/error, proof sources, action decision and durable stage. Do not log secrets or silently alter `HEAD`, product branch, control branch, execution branch, worktree registration, history or any live checkpoint.

### 3. Recover objects from independently verified sources

For missing/empty objects with known exact expected Git SHA and type, use a **separate temporary Git object store/repository** to retrieve source data from a configured authoritative remote or other *independently verified* retained copy. Verify reconstructed object type and the full Git object hash equals the expected SHA, plus relevant provenance/ancestry before installing it. Stage and atomically place only the intended object into the shared object store; preserve corrupt bytes/metadata in an instance-specific quarantine. Repeat after restart idempotently. Do not run `git reset --hard`, `git gc`, `git prune`, `git clean`, destructive `fetch` into a corrupt repository, blind ref reset, forced remote update or whole-repo/worktree recreation.

A remote can be absent/offline and some valid local-only worker commits may never have been pushed. In that case reconstruct only from exact independently verified local/remote evidence, or **block**; never discard unique local objects or invent content. Handle multiple dependent objects (commit -> tree -> blob) without assuming repairing the first `HEAD` object alone suffices.

### 4. Repair ref and checkout data only on exact positive provenance

Treat a zero-byte/malformed internal evidence ref as a **different problem from an object**: a ref name, a report filename, or a nearby SHA is not enough to supply its target. Recreate only a ref whose intended target is *uniquely proven* by immutable Git report/checkpoint provenance and relevant durable owner-published intent, after checking that this target is a valid commit, that the remote/local generation has not drifted and that no other owner has acquired the resource. Use create-if-absent/CAS, quarantine the original ref and preserve reflog. If target proof is ambiguous, keep it blocked and leave the ref untouched. Never infer a historical BOUND/worktree owner from topology.

For managed linked **control** worktree files that should match exact committed HEAD, require validated HEAD/branch, registered/common-dir topology, the exact tracked path/blob and evidence that overwriting the current bytes does not erase legitimate staged/dirty work; then restore only individually proven files using atomic temp-write, hash verification and quarantine. Preserve exact immutable ticket/review bytes and their publication provenance. Do not generalize this into auto-reset of dirty worker/product checkouts, user-modified files or untracked files. If a clean-before-crash assertion is unavailable or the index is staged/ambiguous, block rather than erase user/worker changes. Inspect index/cache-tree failures separately; never rebuild/drop the index without proof that no meaningful staged state will be lost.

### 5. Durable, crash-safe bootstrap state machine and handoff

Record a small per-instance **bootstrap-recovery journal independent of readable Git control and normal SQLite execution state**, durably before each external mutation: observed damage -> provenance/source verified -> candidate object pinned -> object installed -> ref recovered -> control file restored -> integrity revalidated -> bootstrap cleared/handoff. A compact stdlib implementation is preferred; no permanent old-data migration or extra schema for historical tickets. Restart at every boundary must inspect actual disk outcomes and reuse the same intended reconstruction, never perform a second conflicting rewrite, discard quarantine or manufacture a new worker.

After a clean integrity gate, enter existing normal `_validate`, `_load_state`, startup worker/process observation and execution reconciliation. **Bootstrap recovery repairs storage bytes only; it never marks an interrupted worker completed, creates retry authorization, clears execution metadata, picks a different generation or skips normal review/resource ownership checks.** If normal reconciliation can prove the prior worker generation, resume it with the persisted execution ID/prompt and safe retained workspace; otherwise surface its existing fail-closed blocker. Product/control fetch/adoption and scheduler must not run while bootstrap state is unresolved.

### 6. Operational behavior and diagnostics

Expose clear staged diagnostics to logs and available status/health surfaces: corruption category, affected object/ref/path, known expected SHA, remote/source availability, repair performed vs withheld, whether worker scheduling is disabled, and next safe operator action if automatic proof fails. Avoid a continuously restarting service with only a generic Git error; preserve the runtime store and quarantine for later inspection. A healthy startup and a rerun after already completed recovery must not introduce extra mutations or alter Git history. Support the same contract on WSL, Linux and other supported POSIX hosts; no platform-specific branch logic.

## Acceptance criteria / mandatory disposable regressions

1. **Earliest-startup fatal case:** inject a zero-byte commit object needed to resolve checkout/control `HEAD` *before ServiceEngine constructor validation*, with an interrupted `agent_running` state. Show the old entry point would exit before owner/state load; the new bootstrap repair path executes before that call, reconstructs the exact commit and continues. Assert actual service startup/ready behavior, not only a unit-tested helper.
2. **Multiple-object chain:** corrupt a reachable commit and several dependent trees/blobs in a disposable shared common-dir repo with linked control and execution worktrees. Verify all are restored by exact SHA/type from a separate source, existing branches/ref heads/worktree registrations unchanged, and a subsequent `git fsck --full` has no integrity errors. Dangling but valid objects must remain untouched.
3. **Malformed evidence ref:** create a zero-byte internal recovery ref with a genuinely unique source report plus durable expected binding; restore it once with exact target/CAS and preserve the original. Contrast nonexistent report, conflicting report, different committed target, missing checkpoint object and active/foreign owner: all block without mutation.
4. **Two zero-byte control files:** damage an otherwise provably clean canonical ticket and its co-published immutable curator review under a linked control worktree. Restore both from verified HEAD (not from a guessed template); verify byte/blob SHA and valid ticket/review ingestion. Contrast legitimately staged or dirty work, wrong index, symlink and foreign checkout: no auto-overwrite.
5. **Handoff to runtime:** with durable interrupted-worker identity and a preserved dirty or committed worker worktree, verify bootstrap recovery does **not** clear or duplicate execution state; ordinary startup reconciliation either safely resumes the same execution/prompt or blocks on genuine unresolved ownership. Worker launch is prohibited until full storage repair succeeds.
6. **Power-loss boundaries:** inject termination before/after quarantine, object placement, ref CAS, control-file atomic replacement and final handoff; restart multiple times. Verify idempotence, journal consistency, no lost local-only commits, no duplicate workers, no premature cleanup and no mutation of original valid refs.
7. **Unavailable/unsafe evidence:** remote offline, missing authoritative object, unexpected hash/type, conflicting remote/ref, invalid local-only history, malformed index or state journal, concurrent owner, worker still live, filesystem I/O failure. These cases report a specific durable blocker and preserve all evidence; no automatic reset or fictitious success.
8. **No regressions:** retain existing service/IPC read-only guarantees, control workflow provenance, retry/rework/reconciliation, normal startup, crash-recovery and worktree ownership tests. Add focused startup integration tests under disposable Git repositories and temporary runtime dirs, Ruff and exact-checkpoint CI.

## Boundaries

Do not repair the 2026-11-03 incident as the implementation, depend on TASK-147, hardcode any ticket/execution ID or assume WSL-specific behavior. Do not change ticket/review schemas, invent Git provenance or owner authorization, automatically delete dangling objects/refs/worktrees, auto-reset dirty Git trees, mutate historical worker reports, or add migration/compatibility scaffolding. No manual operator intervention should be required when **all necessary exact evidence is independently available**; otherwise an explicit, well-diagnosed fail-closed state is correct. This is bootstrap integrity recovery, not generalized filesystem repair.
