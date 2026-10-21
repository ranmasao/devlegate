---
"type": "devlegate.ticket"
"title": "Remove review-protocol legacy compatibility from the production runtime"
"depends_on": ["TASK-094"]
---

## Context

TASK-094 introduced durable current-review feedback binding but also shipped an embedded `control upgrade-review-protocol` migration and ongoing backward-compatibility branches for historical review dispositions. Devlegate's architecture intentionally does **not** retain old data/protocol readers or migration code in the production runtime. Migrations are one-shot, external to the main codebase. Both currently deployed projects have already run the review-protocol upgrade, so the adopted marker is historical data, not a reason to retain dual protocols.

## Objective

Make the main Devlegate runtime and shipped CLI understand **one** review contract: every rejected current execution has an immutable, execution-scoped review document, correctly attributed and bound to its disposition. Remove ongoing pre-v1 compatibility and migration infrastructure from the distributed runtime.

## Requirements

1. Remove `devlegate control upgrade-review-protocol` from the primary CLI, runtime entry points, packaging and active operational instructions. Do not replace it with another bundled migration command. If a standalone historical migration artifact is genuinely necessary for archival use, keep it outside production package/code paths; do not require or execute it at runtime.
2. Remove `legacy`, `legacy_boundary`, review-protocol marker history scans, feedback-free REWORK, pre-v1 headerless-review acceptance, and any corresponding fallback from review resolver, persisted binding, prompt renderer and execution admission. Treat absent, mutable, mismatched or unattributed current review as an explicit fail-closed workflow error before launching a worker.
3. Preserve the strict positive path introduced by TASK-094: exact ExecutionReport provenance, unchanged canonical ticket, atomic current-review artifact and disposition, immutable review content, content hash, prompt provenance, replay-safe resume, exact delivered prompt diagnostics, robust Git/error boundaries, safe template upgrades, and zero-delta support. No unrelated scheduler or adapter refactor.
4. Decide explicitly what to do about existing review-protocol marker files: they may remain in Git history as inert data. Their presence, absence, modification, or ancestry MUST NOT select alternate production runtime behavior. Do not rewrite control history or retroactively fabricate review documents.
5. If a pre-v1 pending REWORK cannot satisfy the current protocol, block it clearly and require explicit operator disposition outside runtime rather than pretending that nonexistent feedback exists. Persisted old-format pending state must not silently become FRESH or start without feedback; reject safely, documenting an external recovery route if needed.
6. Update packaged architect/reviewer/worker roles, project documentation and changelog so they prescribe only the current protocol and make no promises about built-in migration or legacy support. Maintain safe upgrade of managed templates separately from review-protocol backward compatibility.
7. Replace old-compatibility regression cases with real-Git contract tests: missing artifact (including after backlog deferral), legacy headerless artifact, malformed header and mismatched attribution all block; valid co-published current review enters REWORK with precise stdin feedback; restart/resume retains the exact accepted binding and prompt; zero-delta reviewed results remain supported. Test upgraded control history containing an inert `reviews/.devlegate-review-v1` marker.
8. CI, packaging checks and all existing applicable regressions must pass. Do not modify canonical ticket specifications to inject feedback, do not rewrite historical execution/review data, do not introduce new long-lived format-version branches.

## Architectural boundary

The production code supports only the current schema. One-shot migrations, when needed, are explicitly version-bound tools outside the shipped runtime, not permanent compatibility branches. This ticket removes the deviation introduced by TASK-094; it does not revert the review feedback feature.
