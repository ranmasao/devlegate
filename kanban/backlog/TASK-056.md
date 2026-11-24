---
"type": "devlegate.ticket"
"title": "Detect worker output inactivity and reuse shared interrupted-worker recovery"
"depends_on": ["TASK-055", "TASK-061", "TASK-154", "TASK-184", "TASK-192"]
---

## Milestone

Devlegate 0.5.8 — dogfooding reliability. Complete the deferred watchdog without duplicating the integrated context-transfer and retirement mechanisms.

## Goal

Detect a live worker that has stopped producing observable output and has no active owned child/tool, warn the operator, and automatically continue through the existing interrupted-worker recovery once exact retirement is proven. Preserve current repository edits and attribution. Use the weakest sufficient guarantee: at most one active owned worker, not exactly-once execution of arbitrary agent effects.

## Context

LAB-169 remained alive in do_epoll_wait with an established HTTPS socket but no meaningful output or session progress for approximately 95 minutes after the last tool/edit activity. Process liveness is not progress; output silence is only a heuristic, not a diagnosis of the provider or network.

The curator deferred TASK-056 at checkpoint ca29dc301e09993a033b00fd6467b36eb375385f (execution 2fa4da42eb3246e283247fc4174946f1) to integrate TASK-154 and TASK-184 first. Retain the existing useful watchdog work and all historical evidence. The current product also includes TASK-192's bounded post-leader-exit I/O and exact containment retirement, and TASK-191's log-follow continuity. The earlier recovery-context requirements are superseded by this specification, not cumulative with it. Historical review feedback about output excerpts is not a current requirement.

Change Explorer can inspect current changes; Blackboard can preserve optional worker-authored findings across attempts; the execution log is operator diagnostics. None is mandatory recovery evidence. Do not reproduce their responsibilities inside the watchdog.

## Required behavior

### Activity and policy

- Track the current attempt's observed stdout/stderr bytes using a monotonic clock. Every nonempty observed chunk resets inactivity, including partial, malformed, repeated or non-rendered data. Observe at the existing raw read boundary before framing, decoding, protocol validation or rendering. Integrate with the current cancellable pipe reader; avoid another competing reader, redundant framing or output capture pipeline.
- Provide machine-local configurable warning/recovery thresholds, initially 300/600 seconds, using the retained policy names WORKER_IDLE_WARNING_SECONDS and WORKER_IDLE_RECOVERY_SECONDS. Require finite positive ordered thresholds. Warning alone does not mutate execution state. Report recent activity, idle age/warning and stalled/recovering truthfully through existing status/log paths.
- At recovery threshold, classify only the still-live exact current worker, with no active owned child/tool and no higher-priority operator stop/reset or completion. Do not infer activity or ownership from arbitrary host processes. An indeterminate child/ownership observation is not proof of eligibility. No separate tool timeout policy.
- Make the final classification race-safe against newly observed bytes, leader completion, changed ownership and operator commands, using existing supervision/owner synchronization. No stale timer may retire a later attempt. Normal leader exit cancels the watchdog and follows TASK-192.

### Retirement and shared recovery

- Durably attribute worker_stalled to the exact interrupted attempt before destructive retirement, using existing execution/resource-generation authority and worker operation evidence. Retain only the evidence needed for safe recovery and concise diagnosis; no output excerpts or changed-path lists. If required persistence or ownership proof fails, do not guess or signal an unproven boundary.
- Retire only the exact execution containment through the current containment capability. Reuse TASK-192's bounded I/O/retirement, directory identity binding and empty proof, including failed/indeterminate retirement behavior. Do not restore older blocking joins, unsafe group signals, duplicate directory-identity mechanisms or alternate termination pipelines. Keep raw activity observation independent of log rendering. Preserve existing evidence/revision/blackboard owner polling and authorization.
- After proven retirement, converge on recover_stranded_execution / _normalize_stranded_execution and shared automatic_resume, preserving current modified tracked and eligible untracked files, checkpoint and provenance. Distinguish worker_stalled from ordinary process loss, shutdown, operator abort and reset. Do not use zero-delta automatic_retry. No watchdog-specific successor scheduler, durable successor-ID protocol or parallel admission transaction.
- Proven-safe live and restarted cases continue automatically through that same shared boundary. Missing or contradictory required evidence fails closed; no overlap or speculative second worker. A completed worker must not be revived by stale stall state. If a demonstrated generic recovery defect prevents safe continuation, fix it at its owning shared boundary, not via a stall-only workaround.
- Keep necessary existing generation/retirement checks and consolidate redundant checks where the same trusted boundary already establishes them. Do not weaken independent ownership or restart proof merely to reduce LOC.

### Continuation context

- Use the existing RESUME directive, preserving normal assignment and verified review feedback. Add at most a short deterministic neutral reason that the previous attempt was interrupted after extended output silence, with a reminder to inspect current changes and not assume completion. No new directive, LLM summary or generic prompt-composition framework.
- Remove the deferred checkpoint's WorkerOutputTail, rendered-output callback used solely for excerpts, render_stall_context path/tail projection, recovery-context Git status collection, persisted prompt_output and associated tests/data plumbing. Do not deliberately inject raw logs, tool responses, previous output or private control-plane metadata into the prompt. No separate transcript or log reader.
- Rely on existing optional Change Explorer and Blackboard guidance when those tools are available; do not demand calls or notes, duplicate their guidance, or make their availability an admission condition. Ordinary RESUME remains usable without either tool. TASK-190 owns general prompt restructuring.
- Keep old committed execution/review evidence unchanged. No runtime migration or new compatibility layer for the deferred implementation; it was never integrated.

## Acceptance criteria

- Live quiet eligible worker reaches warning and recovery at configured thresholds; any observed new bytes reset the timer. Active owned child/tool or unproven eligibility prevents an inactivity-triggered retirement.
- Exact retirement is bounded and proven before shared continuation; a valid normal exit uses current normal-exit retirement and publication without being classified as stalled. Failed/unproven retirement blocks overlapping execution and gives actionable diagnostics.
- A new attempt automatically resumes preserved work in proven-safe cases with distinct durable worker_stalled attribution, including restart; no watchdog-specific successor authority.
- Continuation receives the ordinary RESUME context and a concise deterministic stall reason, not copied output or changed-path projections. Optional tool/evidence unavailability does not block safe continuation.
- Obsolete context machinery is removed from the retained implementation, and current TASK-154/184/192 behavior and existing interruption/recovery contracts are preserved. No unrelated rewrite of runtime.py, new framework or migration. Report the resulting change scope and any necessary shared corrections.
- Exact checkpoint passes focused tests, diff check, Ruff and authoritative hosted CI.

## Required regressions

1. Actual raw stdout/stderr bytes (partial line, malformed JSON, repetitive output, and small stderr chunks) reset activity before rendering; idle thresholds and a just-in-time byte prevent stale classification. Normal completion, stop/reset, and changed execution identity win over stale watchdog observations.
2. Quiet live worker warns and stalls at configured thresholds; owned child/tool prevents stall while active; indeterminate membership or ownership does not trigger destructive action. Unrelated sentinel survives.
3. Live stall persists its exact attribution before retirement, retires the old boundary and reaches shared automatic_resume; preserved tracked/untracked changes are available to the successor. Verify no overlap, no fabricated successful claim, no unnecessary reset or separate successor protocol.
4. Failed persistence, failed/indeterminate retirement, mismatched generation or containment and restart across stall/retirement boundaries cannot launch a speculative successor. Proven-safe restart reaches shared recovery. Ordinary process-loss/stop/reset and completed-worker behavior remain correct.
5. Actual delivered RESUME prompt retains applicable assignment/review and concise stall reason, with no duplicated output excerpt or changed-path list; both optional tools continue to function when available, and their absence does not block recovery.
6. Retain TASK-192 regressions for normal leader exit, inherited pipes, exact retirement and intact claim, as well as TASK-154/184 authorization and tool behavior. Real subprocess/service regressions must have independent bounded cleanup even on assertion/startup failure; do not rely on the watchdog under test for teardown.
7. Run focused watchdog, containment, worker supervision, prompt, recovery and service lifecycle tests, lint and diff check; obtain authoritative exact-checkpoint hosted CI.

## Non-goals

Provider/network diagnosis, semantic progress detection, tool timeout framework, new recovery scheduler, exactly-once agent effects, new context-transfer subsystem, prompt redesign, private OpenCode log/database inspection, or migration. Do not reset, abandon, rewrite history or discard the retained checkpoint to avoid reconciling it with the integrated product.
