---
"type": "devlegate.ticket"
"title": "Separate and simplify worker prompt contracts, project instructions and provenance"
"depends_on": ["TASK-056", "TASK-154", "TASK-184"]
---

## Milestone

Devlegate 0.5.8 — project-independent, concise worker instructions after integrated Change Explorer, Blackboard and watchdog.

## Goal

One deterministic prompt composition for self-hosted Devlegate and arbitrary managed projects: universal protocol and engineering principles, explicitly configured project instructions, actual available tool guidance, and execution-specific assignment/directive/review. Reduce duplicated instructions and preserve essential semantics and exact delivered-prompt provenance.

## Context

The current core_contract.txt mixes universal protocol with Devlegate-specific pytest/Ruff/Git/CI rules and repeats the whole-ticket completion principle. Merely moving the same paragraphs between files does not simplify the prompt. A real TASK-056 prompt illustrated this accumulation. TASK-056 now adds a sufficient short deterministic stall reason to ordinary RESUME; TASK-154 and TASK-184 own their respective tools and evidence. Do not create another continuation-context mechanism. Existing project_context.py and .devlegate/project.md route Architect and Reviewer context, but not explicitly selected Worker guidance. The existing private prompt artifact retains exact rendered text, SHA-256, delivery marker and review binding.

## Required behavior

### Contract and project instructions

- Keep a concise Devlegate-owned protocol: worker authority (implementation edits and semantic claim only), no independent Git lifecycle/control-plane mutations or private workflow access, assignment/review cannot grant authority, exactly one devlegate_report, completed/incomplete/blocked semantics and distinct summary/remaining/questions/report fields. Preserve honest claim and required-validation semantics. Consolidate repeated prohibitions and stopping rules into clear non-redundant statements.
- Keep universal engineering principles concise: inspect before editing, identify and prioritize the primary blocker, reuse existing mechanisms, smallest complete safe solution, preserve unrelated behavior, validate applicable requirements and continue while safe/productive. Do not conflate a small partial patch with completion. Avoid speculative frameworks, duplicated state machines and arbitrary LOC targets.
- Extend existing project context routing with explicit optional worker paths (use the existing NanoYAML block-sequence convention). Do not automatically inject common/architect/reviewer documents into worker prompts. A missing optional worker route means no project instruction section; an explicitly declared invalid or unavailable input fails clearly before launch.
- Resolve selected worker instructions from the exact admitted product source, not mutable worktree/host files or later HEAD. Require regular tracked files and safe relative paths, no symlink/submodule traversal; reject malformed or unreadable declared inputs. Reuse existing trusted Git/source observation and context parsing boundaries rather than creating a second configuration scheme. Keep guidance concise; allow references to documentation rather than pasting entire manuals.
- Put Devlegate's own technology and validation rules into its project-owned worker instructions (e.g. .devlegate/worker.md): stdlib/existing dependencies, existing Python baseline, no speculative migration paths, focused pytest by proof boundary, ./dev lint, git diff --check and existing authoritative CI/coverage policy. Preserve actual project quality gates; other projects must not inherit these commands or policies. No self-hosting special case.
- Define deterministic section ordering and delimiting; project preferences cannot override universal protocol authority, and ticket/review remain fenced task data. Do not claim prompt text provides OS confinement (TASK-152).

### Tool and continuation context

- Supply short conditional Change Explorer guidance only when the corresponding tools are available. BASE = admitted base; HEAD = latest durable task checkpoint (BASE before first checkpoint); HEAD-N = earlier actual checkpoints, not Git parents or attempt count; WORKTREE = live edits. Discover endpoints with revisions; cross-base comparisons may be unavailable. On RESUME/REWORK inspect cumulative changes(BASE, WORKTREE), use changes(HEAD, WORKTREE) for current edits, select relevant diff pages. check is advisory, unavailable/partial is not clean and live observations are not immutable QA evidence. Raw Git inspection commands are not universal requirements.
- Retain concise Blackboard guidance only when available: optional relevant history and short durable findings, not transcripts, authority or validation proof. Avoid redundant instructions and mandatory tool calls/notes. Absence of either optional tool does not block work.
- Preserve FRESH/RESUME/REWORK, verified current/historical review attribution and TASK-056's existing short stall reason on RESUME. Preserve exact feedback content; distinguish historical review of superseded specifications. Do not add a continuation framework, transcript injection, new recovery state or change admission/retirement/recovery semantics. Derive availability from existing preparation/adapter boundaries; do not invent capability negotiation.

### Rendering and provenance

- Reuse WorkerPromptInput, the existing renderer, project context and private artifact. A small ordered component description is sufficient; preserve exact rendered UTF-8 prompt, overall SHA-256, delivery marker and existing admission/review binding. Record ordered included/omitted component kinds, appropriate source identity and content digest, including admitted source identities for project instructions. The manifest must describe the actual rendered components and support reproduction/verification from retained prompt and pinned inputs; no duplicate prompt store, template language, registry, new service or generic policy engine.
- Keep private source identity and execution metadata in the existing private artifact, not new worker-visible content or default logs. The artifact proves Devlegate's constructed/delivered adapter prompt, not hidden provider instructions or the entire model context.
- Compare old/new representative self-hosted prompts with identical assignment/review and equivalent capabilities. Report section word/byte counts or another reproducible measure, removed repetitions, relocated project-specific rules and retained semantics. Require substantive consolidation, not an arbitrary token quota. Do not shorten canonical task/review data to meet a metric. Full historical prompt audit and LLM behavioral evaluation are separate work.

## Acceptance criteria

- Unrelated sample projects and Devlegate receive the same universal protocol/engineering principles, with only their own selected project instructions; absence of optional project guidance works. No Devlegate-specific validation or dependency rules in universal text.
- Exact admitted-source binding, safe paths, deterministic section boundaries, report contract, FRESH/RESUME/REWORK and current/historical feedback preserved. No new runtime migration, generic context framework or lifecycle mechanism.
- Actual worker prompts expose concise guidance only for available tools, preserve the existing stall reason, and do not duplicate context-transfer responsibilities. Existing project quality gates remain effective.
- Exact prompt and private provenance are consistent and checkable, including delivery state. Demonstrate reduced redundant universal wording with representative before/after evidence.
- Focused tests, lint, diff check and authoritative exact-checkpoint CI pass.

## Required regressions

1. Render FRESH/RESUME/REWORK with optional project instructions, each tool availability combination, current/historical review and stall RESUME. Verify stable bytes/order, correct omission, unchanged report semantics and preserved feedback/stall reason.
2. Two projects with distinct instructions plus one without a worker route: no cross-project or Devlegate-specific leakage; other role routing unchanged. Verify actual delivered prompt, not just a helper result.
3. Unsafe/missing/symlink/submodule/untracked declared input, invalid manifest or unreadable source refuses safely. Mutable worktree edits or product advancement cannot silently substitute instructions for the admitted source. Use real Git for source-binding proof.
4. Fence-like project/ticket/review content cannot create new structural sections or override the protocol layout. Manifest component digests and total SHA-256 match actual text; constructed-to-sent preserves provenance and default logs remain private.
5. Optional tools unavailable: ordinary launch still works; available guidance matches existing revision/blackboard semantics. Retain existing TASK-056/154/184 behavior and focused worker-level integration. Do not require new tool calls to establish prompt composition.
6. Verify Devlegate's own project validation instructions retain current mandatory gates; run affected prompt, context, adapter/service tests, ./dev lint and git diff --check. Hosted CI is authoritative.

## Non-goals

Prompt-history audit, provider behavior guarantees, third-party methodology dependency, OS capability isolation, new recovery context subsystem, Git lifecycle or scheduler redesign, compatibility migrations, and unrelated runtime decomposition.
