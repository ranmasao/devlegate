---
"type": "devlegate.ticket"
"title": "Materialize and cache canonical workflow DAG by immutable Git tree identity"
---

## Milestone

Devlegate 0.5.8 — shared workflow projection and read-path performance.

## Goal

Provide one reusable, immutable, version-bound representation of the committed ticket graph, with a bounded process-local cache. Build it from the canonical Git control tree and avoid rereading/reparsing unchanged ticket blobs. This ticket establishes the projection and its tests, not the wholesale migration of its consumers.

## Context

TicketStore in tickets.py already indexes parsed tickets and computes runnable tickets. The owner repeatedly loads a working-tree TicketStore; inspection.py separately reads each committed blob and constructs a graph for each ticket inspection. TASK-194 (deferred) introduces a 750 ms observation budget during worker supervision, making repeated Git subprocess startup a plausible bottleneck on the approximately 200-ticket self-hosted control history. No production timeout has yet been measured. TASK-163 is separate longer-term Git read-path research; this bounded implementation must not implement a native Git reader or general Git-object cache.

## Required behavior

- Define an immutable committed-workflow projection that retains exact canonical paths, ticket IDs, blob identities, parsed ticket metadata/text as needed, states, dependencies, graph-wide diagnostics, and existing runnable/selection semantics for valid graphs. Reuse the existing ticket parser, validation and TicketStore semantics rather than create a parallel scheduling policy. Invalid graphs must remain diagnostically inspectable where the existing contract allows it and must not become runnable through the cache.
- Resolve input from one explicit immutable local control commit and configured workflow paths. Identify equivalent workflow versions by the relevant committed workflow subtree identities (including absence and path mapping); a kanban root tree alone is sufficient only when configuration makes it equivalent. A new control commit with identical workflow trees may reuse the graph but must not misattribute its requested control HEAD. A tree change, ticket move, addition, deletion or edit must yield the corresponding projection. Do not key by mtime, HEAD name, ticket ID or blob alone for the complete graph.
- Reuse parsed content for unchanged blobs with correct path/state/ID attribution. The same content at another ID or in another state must never inherit stale identity or scheduling metadata. Account for path-dependent parsing and errors. Prefer bounded in-memory storage with a simple eviction policy; use existing read-only Git plumbing, grouping reads when useful. No persistent SQLite cache in this first implementation: startup cost is to be measured, not assumed. Keep the cache disposable, scoped to the configured project/owner, with no durable authority, migrations, third-party dependencies, background threads, or new Git transport.
- A cache miss/rebuild, malformed input, unavailable Git object or changed canonical revision must not silently return a mismatched graph. Publish only a coherent completed projection; report observation errors explicitly. Bind a historical projection to its own input, not a live ref. Separate committed facts from working-tree edits, runtime ownership, remote observations and mutation authority. Do not use cache membership as proof of local or remote ref continuity, workspace safety, generation identity or report/review provenance.
- Supply a narrow reusable interface for consumers in TASK-196, including exact requested revision and shared immutable graph data. Keep current public CLI/IPC schemas and scheduler behavior unchanged in this ticket. Do not implement TASK-194 admission or merge its deferred code; integration of consumers is the next task.
- Measure cold and warm materialization and small changes on a representative disposable control history with at least 200 tickets, and report elapsed time, Git subprocess counts and cache behavior. Include a realistic packed/loose object setup or document the tested conditions. Prefer deterministic call-count/reuse assertions over flaky wall-clock thresholds. The aim is to avoid per-ticket Git reads and parsing on warm unchanged graph observations, not to assert unmeasured speedups.

## Acceptance criteria

- One coherent reusable projection represents the same valid ticket graph and runnable ordering as existing canonical semantics for equivalent committed inputs; invalid states are diagnosed without scheduling unsafe work.
- Repeated same-tree observations reuse parsed content and graph materialization, including when only non-workflow files change. Changed workflow inputs are correctly represented without stale states; unchanged blobs can be reused without losing exact attribution.
- Cache lifetime and capacity are bounded; a fresh process or evicted cache reconstructs from Git without historical cache data. No SQLite schema changes or additional mutation authority.
- Document the cache identity, failure behavior, measured baseline and results, and how consumers will request it. Focused tests, lint, diff check and exact-checkpoint hosted CI pass.

## Required regressions

1. Real Git fixtures: cold/warm graph, unrelated control commits, moves of unchanged blobs, new/edited/deleted tickets, same blob at distinct IDs, changed workflow path mapping and empty/missing trees. Assert exact states, IDs, text/blob bindings, dependency edges and cache reuse/miss behavior.
2. Duplicate canonical ticket identity, missing dependencies, cycles, invalid metadata, abandonment reservation semantics and terminal dependency satisfaction. Compare valid selection with existing TicketStore; invalid input cannot schedule work, and diagnostic information is not silently discarded.
3. Different revisions/branches and project configuration, missing or unreadable object during build, changed ref after resolving explicit commit, eviction/reconstruction. Never present another revision as requested; preserve explicit errors and no mutation.
4. Representative >=200-ticket fixture: demonstrate warm graph lookup without a per-ticket Git subprocess or repeated full parsing; change a bounded subset and verify reuse of unchanged content. Report measured cold/warm/update results and subprocess counts; retain all existing workflow tests.
