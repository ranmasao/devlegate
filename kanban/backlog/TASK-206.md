---
"type": "devlegate.ticket"
"title": "Separate committed and service inspection from mutation-owner execution"
---

## Milestone

Post-audit O1 — backlog until explicitly scheduled. Phase II/III narrowed the original TASK-206 to the first complete vertical slice. TASK-212 owns residual live observations and removal of the last necessary observer slots.

## Goal

Serve committed ticket/execution/prompt and service inspection independently of worker supervision and mutation admission, using one attributed immutable revision or detached published runtime snapshot.

## Context

TASK-194 enabled read-only inspection at owner safe points, with OBSERVATION_WAIT_SECONDS=1.75, WORKER_OBSERVATION_SECONDS=0.75 and OPERATOR_ADMISSION_TIMEOUT=1.0. On 2026-11-27 the first inspect ticket TASK-204 after FRESH admission timed out; immediate repeat succeeded. The expensive suboperation is unproved. TASK-195/196 already share committed graph projection; TASK-191 uses committed state for ticket log follow. Phase II/III recommend A+C (one mutation owner, independent read contexts), not another service. Baseline fb135883ef3ec584f456fddedc8daf7f723f2553.

## Required behavior

- Inventory and replace owner-bound ordinary committed ticket/execution/prompt and service inspection. Capture exact published control/product identity and detached snapshot with minimal synchronization; expensive Git history/blobs/response outside mutation owner. Reuse existing IPC and committed projections; a direct CLI or bounded independent handler is acceptable if proof and authentication permit.
- Correctly attributed older immutable revision remains valid during advancement. Preserve exact report/review/prompt attribution, privacy and cycle identity; no dependence on unavailable live workspace for independently available committed evidence. Live additions are honestly attributed/partial/unavailable, not an invented atomic sample.
- Thread-safe bounded cache and request concurrency, subprocess/output limits, independent query/connect/admission/cancellation policy, no global lock around expensive read and no timeout inflation. No fetch, hooks, filters, index refresh, ref/SQLite mutation or second authority. Keep machine schemas where sufficient and explain any necessary semantic change.
- Keep existing workspace ls/inspect/prune preview and mutation paths fully usable in this release. Remove replaced committed/service owner paths; retain only still-needed live observation slot consumers for TASK-212. Do not add a second complete observation implementation, new daemon/store/queue framework or broaden into Git reader redesign.
- Preserve exact mutation-time revalidation and IPC authentication; measure actual Git calls, owner busy time, query latency and reset/stop responsiveness before/after.

## Acceptance criteria

Ordinary committed and service inspection completes independently of the serial mutation loop, even during slow committed-history reads and active worker supervision. Each result is internally coherent at its declared identity. Old live paths remain operational and correctly guarded pending TASK-212; no redundant committed observer path survives without justification.

## Required regressions

1. Real CLI/IPC with held worker: current/historical ticket, exact committed execution and saved prompt, service/status; inject history read longer than old 750ms budget; query succeeds within independent policy while reset/stop remain responsive.
2. Concurrent reads across control advancement, graph cache miss/hit and same-ID reopening; no mixed revision or cycle; bounded concurrency and safe shared cache.
3. Missing live resource, prompt mismatch/privacy, client cancel/shutdown, unobservable committed data; truthful results and no worker disruption/read mutation.
4. Existing live inspection/preview and confirmation, stop/reset/retry/recovery remain usable; record before/after owner time, actual Git commands and latency.

## Operational end state and upgrade

The integrated runtime must remain usable for self-hosted development without a later ticket. Preserve unaffected workflows, immutable evidence and independent authority/effect proof. Document safe quiescent upgrade and readiness checks. No production migration, synthetic receipts or mixed-version protocol. An actual stopped local incompatible proven state may be converted once by an untracked script with exact preconditions, backup/dry-run and validation; do not fabricate authority or erase uncertain effects. Focused tests, lint, diff check and exact-checkpoint hosted CI are required for implementation.
