---
"type": "devlegate.ticket"
"title": "Make inspect service a readable detailed service diagnostic"
"depends_on": ["TASK-176"]
---

## Problem

All `inspect` text renderers currently serialize nested JSON into unwrappable grid cells. `inspect service` also embeds the whole published status snapshot, obscuring the command's separate purpose.

## Goal

Make `inspect service` a coherent, detailed, human-readable view of the running service instance, deeper than the service summary in `status` but not a second project workflow status.

## Requirements

1. Human output groups service/owner identity (version, PID, instance ID, protocol), lifecycle/readiness/intent, worker count and service-specific diagnostics. An overlap such as running/ready is fine, but do not duplicate ticket lists, repository status and scheduler summaries by dumping `diagnostic_snapshot`.
2. Keep observed-at-request identity distinct from an older published diagnostic snapshot; mark unavailable/unproven/none correctly, never imply a stale snapshot is current. Do not leak owner secrets, raw private state, environment or sensitive worker identity.
3. Decide whether the existing `devlegate.inspect-service.v1` machine schema is adequate. Change schema/version only to correct a demonstrated semantic deficiency; avoid compatibility layers. Preserve `--json/--yaml` contract appropriately and read-only owner-serialized IPC.
4. Implement a purpose-built renderer and, if justified, tiny reusable output primitives usable by later inspect types. No recursive JSON-in-cell formatter. Include real shape fixtures, incomplete diagnostics, lifecycle transitions, 80/120 columns, non-TTY, Unicode/long tokens. Independent reviewer examines actual rendered samples, not only schema assertions.
5. Preserve explicit refusal when no service owner is available; inspection must never create one.

## Boundaries

Do not change scheduler, status presentation, mutation authority, unrelated inspection object schemas or worker behavior. Follow focused tests, Ruff, hosted CI.
