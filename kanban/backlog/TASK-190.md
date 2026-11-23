---
"type": "devlegate.ticket"
"title": "Separate universal worker contracts, project instructions and prompt composition provenance"
---

## Milestone

Future Devlegate iteration (candidate 0.5.8) — universal worker-prompt architecture for arbitrary managed software projects. **Backlog only until curator explicitly schedules it.** This records the agreed direction, not authorization to rewrite the currently active worker prompt or launch an implementation. Do not make current TASK-056 recovery or release work depend on this ticket.

## Goal

Make the implementation worker's exact delivered prompt a reproducible composition of **Devlegate-owned universal protocol**, **Devlegate-owned engineering principles**, **project-owned concise instructions**, and **execution-specific assignment/feedback/continuation data**. The same composition applies when Devlegate is developing itself and when it manages an unrelated repository: self-hosting must not have a special prompt assembly path.

Keep the existing simple deterministic text renderer and exact private prompt artifact. Do not introduce a template engine, plugin framework, arbitrary hierarchy of programmable policies, or alternate worker execution architecture.

## Context

Current `src/devlegate/worker_prompt.py` composes `core_contract.txt`, a FRESH/RESUME/REWORK directive, the canonical ticket text, and optional verified latest review feedback. It fences task/review material as untrusted data, calculates a SHA-256 of the exact UTF-8 prompt, and persists the exact text with execution/review binding and delivery state in a private per-execution JSON.

The existing `.devlegate/project.md` and `src/devlegate/project_context.py` route common project documentation to Architect and Reviewer, but provide **no explicit worker role or project-specific worker instruction input** to `build_worker_prompt()`. Putting Devlegate's own Python, dependency, language-version or lint policy into the universal `core_contract.txt` would incorrectly impose it on unrelated projects.

There is also a distinct reliability concern demonstrated by TASK-056: workers sometimes deliver small valid prerequisites and claim `incomplete` while the primary acceptance-blocking functionality is still untouched. The desired engineering principle is **smallest complete change**, not smallest partial patch. Existing core instructions already caution against this; simply adding more emphatic wording is not a proven remedy for bounded context/runtime capacity or over-large tickets.

Ponytail is a possible **reference for review**, especially its emphasis on reuse and the smallest *complete* change. Do not blindly adopt its text, treat third-party benchmark claims as authoritative, or introduce Ponytail as a mandatory runtime dependency. Compare any borrowed practice against Devlegate's own evidence and required safety boundaries.

## Required behavior

1. **Separate responsibilities.** Review the current `core_contract.txt` and distinguish:
   - **Protocol Contract (universal, Devlegate-owned):** the worker's allowed authority and ownership boundary; prohibition on independently committing, pushing, merging, switching branches or editing workflow state; exact `devlegate_report` contract and claim semantics; ticket and review are task data and cannot grant control-plane authority.
   - **Engineering Principles (universal, Devlegate-owned):** inspect the affected code path before editing; identify the principal blocker to satisfying the entire assignment; prefer existing mechanisms and platform/stdlib solutions; avoid duplicate state machines, speculative abstractions and unnecessary configuration; make the smallest **complete** change; validate essential behavior and do not trade correctness/safety for line-count reduction.
   - **Project Instructions (project-owned):** concise technology/version/dependency rules, preferred validation commands and local architectural constraints. These are not global Devlegate rules.
   Preserve existing important core semantics and precise report-field definitions. Physical separation into small template files is fine; avoid gratuitous role-template proliferation or instruction repetition.

2. **Add an explicit worker project context.** Extend the existing project context routing (e.g. an optional `worker` entry in `.devlegate/project.md`) rather than building a second project-configuration scheme. Permit a concise project instruction file, for example `.devlegate/worker.md`, to be selected and incorporated deterministically. For Devlegate itself, representative rules include: prefer stdlib and installed dependencies rather than adding libraries; do not raise the supported Python baseline without an explicit requirement; preserve existing architecture; do not add hypothetical migrations for unused interfaces; run focused tests and `./dev lint` before claiming completion. For other projects these rules must be entirely replaceable.
   
   Do not eagerly paste whole README/architecture/reference manuals into every prompt. Where useful, project instructions can point workers to relevant repository documents to inspect on demand. Any project-context input must be validated and bound to the admitted source; do not read a mutable unverified host file at an arbitrary point after admission.

3. **Retain strict instruction/data boundaries.** The assembly order and presence rules are defined by Devlegate, not user-customizable policy code. Project instructions may set ordinary engineering preferences but cannot override Devlegate's protocol authority; assignment and verified review remain fenced task data and cannot become new global instructions. Be honest that text-level hierarchy is a prompt contract, **not** OS-level enforcement; TASK-152 owns stronger capability isolation.

4. **Support execution-specific context without special-case prompt families.** Keep FRESH/RESUME/REWORK directives and verified current review feedback. Provide an optional bounded `Continuation Context` component for proven interrupted work (e.g. a stalled-worker restart), with safe encoding/truncation and no private orchestration IDs or internal paths leaked to the agent. This ticket owns the **generic composition interface**; TASK-056 owns generation of watchdog evidence, retirement and recovery decisions, and TASK-184 owns durable task blackboard data. Do not recreate either system or make them dependencies.

5. **Materialized prompt and provenance.** Preserve the existing exact UTF-8 delivered prompt, SHA-256, private `delivery` marker, control admission and verified review attribution. Augment the existing private artifact with a small **ordered composition manifest**: section kind, source identity appropriate to its trust boundary, source content digest and whether a section was included. Bind project-input content to the actual admitted version rather than relying on mutable file names. Exact rendering and the private diagnostic manifest must describe the same bytes. This artifact proves what Devlegate assembled and handed to its worker adapter; it must **not claim to represent hidden provider/OpenCode instructions** or the complete model context.

6. **Self-hosted = ordinary project.** Devlegate's local project instructions live in its repository just like those of other managed projects, while universal contracts remain packaged in the Devlegate distribution. Avoid hardcoded Devlegate-only implementation advice in universal templates; no special `self_hosting` mode, dual renderer or project-name check.

7. **Keep the scope narrow.** Reuse `WorkerPromptInput`, `build_worker_prompt()`, `project_context.py` and the existing private artifact path as much as possible. A small explicit typed composition structure is acceptable; no arbitrary templating language, instruction plugin registry, new daemon/service, duplicated stored prompt, extensive configuration surface or compatibility/migration layer for hypothetical customers. If research reveals distinct release-sized implementation tasks, propose a bounded split for curator approval rather than creating a sprawling implementation.

## Acceptance criteria

- A worker in an unrelated sample Python/Go/other project receives exactly the same Devlegate protocol and engineering rules as self-hosted Devlegate, but only **that project's** configured instructions; no Devlegate-specific language/version/linter constraints leak.
- All mandatory protocol capabilities and `devlegate_report` claim semantics remain intact across FRESH, RESUME and REWORK.
- Worker project context is explicitly opt-in/declared, path-safe, exact-source-bound and deterministic; absent optional project guidance has a defined valid behavior.
- The private prompt artifact retains the exact delivered text and delivery status and records an ordered, checkable component manifest with stable source digests.
- Assignment/review/continuation remain appropriately delimited; internal control-plane information is absent from worker-visible text unless explicitly within the established worker interface.
- The prompt renderer stays simple, deterministic and reusable without a separate self-hosting variant.
- Universal methodology clearly favors the **smallest complete** safe solution and reuse of existing code; it does not accidentally encourage early `incomplete` termination on trivial helper patches.
- No change to agent authorization, lifecycle ownership, Git integration or TASK-056's runtime recovery logic under this ticket.

## Required regressions

1. Render FRESH/RESUME/REWORK with and without optional project instructions, review and continuation context; assert stable exact bytes, ordering, deterministic hashes and omission rules.
2. Configure two distinct sample projects: verify no project-specific instruction from one appears in the other's prompt; prove that neither can override the universal protocol text or section placement.
3. Try unsafe/symlinked/missing/stale project-context paths or changed source after admission: deterministic refusal or pinned content; no silent late substitution of new instructions.
4. Inject section-heading or fence-like instructions in ticket/review/project-context data and verify deterministic separation and absence of escaped content reinterpreted as a top-level section.
5. Round-trip materialized prompt artifact and manifest: reproduce the exact prompt from pinned inputs; verify per-component digests, total SHA-256, review provenance and `constructed` → `sent` state.
6. Ensure prompt contents, private source paths and internal execution identities do not leak through default logs; preserve current no-commit/no-push/no-control-access worker contract.
7. Run focused prompt/context tests, relevant worker-level integration, `./dev lint` and authoritative CI for the eventual implementation. Do not assert that a textual prompt can guarantee an LLM's behavior without a separate behavioral evaluation.

## Boundaries and related tickets

- **TASK-056**: watchdog detection and real recovery-context evidence production; do not modify its running implementation here.
- **TASK-184**: durable worker-authored notes and cross-attempt blackboard; do not substitute a prompt manifest for it.
- **TASK-152**: hard capability isolation; instructions are not security confinement.
- The current source prompt's exact byte layout may change by deliberate design; update behavioral/digest tests accordingly without treating incidental text typography as a public compatibility contract.

**Status: architecture/product refinement backlog.** No automatic admission or implementation is authorized merely by recording this ticket.
