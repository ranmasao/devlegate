---
"type": "devlegate.ticket"
"title": "Expose execution log and saved prompt evidence through existing inspect"
"depends_on": ["TASK-161", "TASK-190"]
---

## Milestone

Devlegate 0.5.8 — complete the existing read-only execution inspection surface.

## Goal

Extend the existing exact-ID devlegate inspect execution command with useful local log and saved worker-prompt observations, including explicit opt-in access to the exact saved prompt. Do not recreate the inspect domain.

## Context

The original TASK-104 proposed designing a unified read-only CLI for project, ticket and execution objects. TASK-161 has already implemented the unified inspect domain with ticket, execution, workspace and service views, exact committed report/checkpoint/review resolution and owner-serialized observation. TASK-051 owns future resolved project inspection after TASK-014; it should extend this existing domain, not create a parallel CLI. Neither is a prerequisite for the remaining TASK-104 work.

The current execution view exposes committed report, checkpoint, review, history and generation observations, but does not expose its local execution log or saved prompt journal. TASK-190 adds the ordered prompt component manifest to the existing private exact-prompt artifact. These local artifacts are diagnostic evidence, not immutable control-plane history or proof of model behavior.

## Required behavior

- Extend the existing execution inspection implementation and human/JSON/YAML views, using the already resolved full execution ID and current project identity. Preserve exact-object resolution, committed history, blockers and read-only owner boundary. Do not broaden resolution to ticket IDs, SHA prefixes, latest runtime cursors or reportless executions under this ticket.
- Report the location and observation status of the execution-scoped local log through the existing log locator/resolution mechanism. No new log reader, follow implementation, transcript injection or parallel log store. Distinguish missing, unavailable/unproven and present local evidence; do not imply that a local path proves immutable history or remote availability.
- Report useful non-content prompt metadata by default (availability, digest, directive, delivery state and concise component/provenance information where available). Never include prompt text, sensitive source details or raw artifact content in default inspect output or service logs. Do not search other projects or guess an artifact from timestamps or names.
- Add an explicit execution-inspection opt-in such as --prompt to retrieve the exact retained prompt through the existing authorized local operator read-only interface. Validate exact execution/ticket/project association against available authoritative evidence, prompt SHA-256 and applicable admission/review bindings; validate TASK-190 component metadata against the saved bytes when present. Report missing or malformed evidence honestly; do not reconstruct a supposed delivered prompt from current templates, report constructed as sent, or claim provider receipt/full model context. Older existing prompt artifacts without a component manifest may be described as lacking it; no migration or fabricated provenance.
- Keep machine output lossless for explicitly requested prompt text, and escape unsafe terminal control characters in human output. Read only a regular local artifact at the canonical path, without following unsafe symlinks or reading arbitrary operator-supplied paths. Keep reads bounded using existing relevant artifact/IPC limits; report an actionable refusal rather than silently truncating a prompt and calling it exact. Preserve existing permissions, no hidden fetch, service startup, runtime state mutation, Git writes or artifact rewrites.
- Document the explicit opt-in and the difference between local diagnostics and committed execution evidence. Reuse existing inspect/IPC, operational log and prompt-journal boundaries; no new service, generic artifact registry or independent provenance store.

## Acceptance criteria

- An operator can inspect an exact committed execution and locate its local log and see its saved prompt availability, digest and delivery state without reading private files manually.
- Exact prompt text is available only via an explicit request and only when its required identity/integrity evidence is valid. Missing, foreign or malformed artifacts are diagnosed without substitution, private content leakage or loss of the committed report view.
- Existing inspect commands, log-follow behavior, current/historical execution distinction and read-only constraints remain intact. No dependency on future project configuration architecture.
- Focused tests, lint, diff check and authoritative exact-checkpoint hosted CI pass.

## Required regressions

1. Inspect a committed exact execution with real fixture log and TASK-190 prompt journal; assert correct execution attribution, default metadata without prompt content, and opt-in exact UTF-8 text/digest and delivery semantics in supported machine output.
2. Missing log or prompt, constructed-only prompt, historical prompt without component manifest, corrupted digest/manifest, wrong execution/ticket/control identity, foreign project artifact and symlink/non-regular file do not expose substituted prompt contents or manufacture proof.
3. Explicit prompt request is rejected for unrelated inspect object types; hostile terminal bytes are safe in human output, and oversized/unavailable evidence is explicit. Default logs and normal inspect output do not expose the prompt.
4. Preserve committed execution report/review inspection even when local artifacts are absent; exercise owner IPC and read-only/no-mutation behavior and existing log resolution regressions.
5. Run focused inspection, prompt journal, log and relevant CLI/IPC tests, ./dev lint and git diff --check; obtain authoritative hosted CI.

## Non-goals

Project configuration inspection (TASK-051), new inspection architecture, direct control-plane access for workers, reportless execution discovery, log reader/follow redesign, prompt composition changes (TASK-190), new evidence storage, migrations or unrelated runtime work.
