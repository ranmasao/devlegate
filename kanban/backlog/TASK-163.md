---
"type": "devlegate.ticket"
"title": "Future Git read-path optimization and native local object reader architecture"
---

## Scheduling: deferred to future development iterations

**Architectural research backlog only — explicitly not authorized for current iterations.**
Keep this ticket in `kanban/backlog`; do not auto-promote, schedule, implement, or make TASK-162/TASK-132 or subsequent current reset/CLI work depend on it. A curator may revisit after the current `inspect`, `workspace prune`, and `reset` chain is complete and performance data exists. Any implementation should be split into small, separately reviewed tickets after a recorded architectural decision. No runtime or distribution changes are authorized here.

## Motivation and observations

TASK-161 introduced `devlegate inspect ticket|execution|workspace|service` as read-only, owner-serialized observation. The current implementation uses the Git CLI plumbing via repeated `subprocess.run` calls: for example, `inspect ticket` materializes the complete ticket graph and repeatedly invokes `cat-file` for individual blobs; `inspect execution` reuses per-artifact `log`, `ls-tree` and `cat-file` calls. This is a plausible scalability and process-startup cost, **not yet a measured defect**. Most immutable history is local: GitHub API and network access are unnecessary for ordinary ticket/execution evidence. Workspace inspection additionally needs authoritative remote observation where remote state matters.

The operator is interested not only in batching Git subprocess calls but also in exploring a **Devlegate-owned, stdlib-only local Git object reader** similar in broad capability to Dulwich, **designed independently rather than copying Dulwich's implementation**. Reading local objects and history is the near-term research target; a Git network client is explicitly deferred. If future remote transaction/transport needs arise, revisit the earlier **Transactional Git** idea as a separate architectural decision, not as an automatic extension of a read-only parser.

## Research questions and stages

### A. Instrument the existing read path before choosing implementation

1. Benchmark representative disposable copies or safely read-only real-world-sized control histories: e.g. `inspect ticket`, `inspect execution`, `inspect workspace` with growing ticket, report, review and Git commit counts, loose vs packed object stores, submodules and incomplete/unbound evidence.
2. Measure Git subprocess **count**, startup time, cumulative wall time, CPU, resident memory, network time (only where relevant), object bytes read and output size. Identify hot paths and distinguish repeated immutable-object reads from mandatory live authority/resource reobservations.
3. Record a reproducible baseline and explicit improvement targets. No asserted speedup without measurements. The read-only `inspect` boundary must stay read-only; do not add persistent caches or new provenance authority.

### B. Low-risk batching using existing Git plumbing

Explore replacing per-object `git cat-file blob <oid>` with `git cat-file --batch`/`--batch-command`, grouping `ls-tree` requests by exact control HEAD and collecting per-path history once where equivalent to existing immutable publication proofs. Consider a request-scoped `GitReadSnapshot` / `GitObjectReader` that pins **one immutable commit** and stores only ephemeral tree/blob/history indexes. Compare one-shot batch against a persistent subprocess, accounting for timeout/restart/error handling and Git process containment. Prefer stdlib only, with no new mandatory third-party dependency.

Maintain `_history`'s exact immutable-artifact chronology and ambiguity checks: one combined `git log` is not automatically semantically equivalent to a set of per-path publication proofs. Batch only where independent tests establish equivalent results and fail-closed errors.

### C. Longer-term native **local** Git object database reader

Evaluate a small independently designed Python parser/adapter for **read-only local object access**, not a copy or fork of Dulwich. Use documented Git formats and clean-room design/test cases; respect licensing and provenance for any external reference material. Evaluate minimally sufficient support for commit/tree/blob/tag objects, object-ID validation, loose objects, packed objects/pack indexes and delta resolution, and safe bounded reads. Inventory unsupported features (e.g., SHA-256 repositories, alternates, shallow/promisor/partial clones, reftable, corruption, malicious objects) and determine what should fail closed versus what is actually necessary for Devlegate's supported deployments.

Set hard size, recursion, allocation, decompression and object-integrity limits; avoid symlink/path traversal and untrusted object hazards. Treat Git CLI as a differential **test oracle**, not as an unwanted competitor to remove at all costs. Determine whether a hybrid approach (native local immutable object reads + Git CLI for authoritative refs/worktree observations) is simpler and safer than replacing Git completely.

**Do not implement the network client in this phase.** Transport/authentication, fetch/push negotiation and remote ref mutation are separate domains and security boundaries; revisit Transactional Git only when a concrete requirement and a transaction/lease model have been specified. No optimistic continuity claim for remote Git SHA/name ABA, and no replacement of existing explicit lease/CAS protections by a local parser.

## Safety invariants to preserve

- Devlegate service owner remains sole Git and workflow authority; no worker-facing raw Git/ref capability is introduced.
- `inspect` remains purely read-only, observing the requested exact immutable control HEAD without converting the observation into mutation authorization.
- Reset/cleanup and integrations must **reobserve current** runtime owner, worktree registration/locks/dirty/untracked/submodules, exact local ref states and remote states before effects. A cached/batched snapshot of immutable objects must never replace these checks or remote `--force-with-lease`/local CAS.
- Provenance must retain unique canonical paths, publication chronology, resource-generation independent bindings, explicit unknown/unproven distinctions, and rejection of historical unbound resource adoption.
- Zero-dependency/stdlib-only goals remain. No compatibility/migration scaffolding, no production reset, no destructive testing on managed user resources.

## Expected research deliverable (not implementation in this ticket)

A measured comparison of: (1) existing per-call Git plumbing; (2) batched Git CLI reader; (3) short-lived/persistent subprocess options; (4) native local Git object reader; and (5) hybrid design. Include performance, complexity, memory, correctness/safety, packaging/runtime implications, ownership boundaries, adversarial testing and maintenance cost. Propose a narrow first implementation ticket only **after** measurement and independent review. Retain a separate future question for the potential Transactional Git/network layer.

## Boundaries and cross-references

Related to TASK-161's universal read-only inspect, TASK-162's workspace prune, TASK-132's fresh execution reset and TASK-154's future worker-facing virtual change explorer. This backlog ticket does **not** block or change any of them. The earlier Transactional Git direction is recorded as an idea, not treated as an existing implemented contract or a verified prior decision in the repository. Preserve current Git CLI behavior while researching alternatives.
