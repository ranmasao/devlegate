---
"type": "devlegate.ticket"
"title": "Collect operator-facing UX friction and actionable CLI diagnostics"
---

## Milestone

Deferred Devlegate operator and developer experience improvements. **Backlog collection only**: do not schedule, implement or automatically requeue this ticket while observations are still accumulating. When a coherent release-sized set is ready, explicitly refine and split it into bounded implementation tickets, retaining this register for future observations.

## Goal

Maintain one canonical, evidence-backed register of operator-facing UX problems discovered during self-hosted development and Arch dogfooding. Record the exact user action, observed behavior, likely boundary, desired behavior and regression idea. Prioritize actionable errors over cosmetic wording, without weakening runtime ownership or provenance checks.

## Context

An operator tried `./dev package arch --ticket TASK-089` first while the self-hosted Devlegate service was running and then after `devlegate stop`. Under integrated TASK-156, both attempts correctly refused unsafe/unproven source selection, but the *first* diagnostic gave no indication that a running owner was the reason. The *second* diagnostic correctly identified the immutable historical report `34878bd12a3844a7ab4057bffb635c8e` with missing `resource_generation` and suggested the clean exact-checkpoint `--repo` manual path.

TASK-156 already fixed the previous interactive-TTY silent `exit 1` and added fail-closed historical identity diagnostics. **Do not duplicate or regress those completed fixes.** A UX improvement must preserve the existing correct refusal semantics.

## Required behavior

1. Keep this ticket in backlog as a living issue register. For each new observation append a numbered entry with date/context, exact command or workflow, actual output, expected operator action, safety implications, and a reproducible acceptance/regression case when possible. Separate confirmed behavior from speculation; reference the ticket/commit that already fixed an observation.
2. Before scheduling implementation, triage observations by common boundary and scope into individually reviewable tasks. Do not make a single worker implement an ever-growing mixed-feature backlog. Preserve references back to this register and explicitly close or supersede addressed items instead of silently deleting historical observations.
3. Diagnostics should communicate *what was refused* and *what the operator can safely do next* when the reason is known. Do not leak secrets, private state paths, internal lock details or mutable resource identities; do not suggest stopping a service if the failure is not actually established to be owner contention.
4. Preserve nonzero exit codes, read-only source selection, the distinction between runtime/project authority and historical generation evidence, and the prohibition on inferred or fabricated provenance. No data migrations, compatibility layer or change to TASK-089 behavior merely for better UX.

## Observations

### UX-001 — Running owner incorrectly presented as generic provenance refusal

- **Status:** Open; first actionable UX issue.
- **Observed 2026-11-11** with installed Devlegate `0.5.7.dev0` sourced from `f6a61185ec4c08c8d4985509e663458ca1c36b63`.
- **Action:** From the Devlegate project checkout, execute `./dev package arch --ticket TASK-089` while the corresponding Devlegate service is running.
- **Actual stderr:**
  ```text
  dev: package: ticket source refused: independent execution/resource evidence could not be verified; check owner retirement and generation receipts
  ```
- **Observed control:** `devlegate stop` reports `service stopped`; a retry then produces the precise historical refusal:
  ```text
  dev: package: ticket source refused: latest execution 34878bd12a3844a7ab4057bffb635c8e lacks checkpoint identity (resource_generation); no independent generation binding remains; use --repo with an independently verified clean exact-checkpoint checkout for manual testing
  ```
- **Problem:** The running-owner refusal gives an unrelated-looking hint about generation receipts; the user cannot tell whether waiting, stopping the owner or repairing evidence is appropriate. The observed sequence strongly indicates authority contention, but the implementation must prove the actual typed reason before giving specific advice.
- **Desired:** When the *typed* failure specifically indicates the project runtime/owner is active, present a concise distinct explanation and safe next step (for this CLI, stop the corresponding service using `devlegate stop`, then retry). If the owner is absent and generation evidence is missing, preserve the current exact historical-provenance diagnostic. Other ambiguous/unverified resource errors must remain generic and fail closed.
- **Regression:** In disposable project/owner fixtures, simulate (a) a verified active owner and (b) a stopped owner with missing generation in the latest report. Invoke the real CLI through the intended dispatcher, assert distinct actionable stderr, no raw private path or secret, nonzero exit, and no mutation of owner state, worktrees, refs or execution reports. Include a third unrelated `UnprovenResource` refusal to prove it is **not** mislabeled as active-owner contention.
- **Relationship:** Follow-up to TASK-156's otherwise successful diagnostics; not a reason to reopen or roll back TASK-156.


### UX-002 — Reset progress misidentifies the operation and floods the normal service log

- **Status:** Open; operator-facing CLI and default journal-message defect, separate from reset scheduling semantics.
- **Observed 2026-11-23** on the self-hosted Arch service, invoking `devlegate reset TASK-056` for a ticket with prior executions (initially in review).
- **Actual CLI output immediately after the command:**
  ```text
  reset TASK-056: stopping-worker
  ```
  The operator has asked to *reset the ticket*, not merely stop its worker. This status presents an internal phase as if it were the principal result and does not itself confirm eventual reset success.
- **Actual service journal:** It logs an ordinary `no remote changes` synchronization line and, for this reset, the following stages (volatile identifiers redacted for this public issue register):
  ```text
  ticket reset ticket_id=TASK-056 operation_id=<redacted> stage=stopping-worker admitted
  ticket reset ticket_id=TASK-056 operation_id=<redacted> stage=retiring-old-generation worker retirement proven
  ticket reset ticket_id=TASK-056 operation_id=<redacted> stage=requeue old generation retirement proven
  control updated: <old-head> -> <new-head>
  ticket reset ticket_id=TASK-056 operation_id=<redacted> stage=queued requeue published and activated
  ```
  The reset-specific INFO messages expose implementation steps and duplicate the retirement proof without improving the ordinary operator's understanding.
- **Expected CLI:** Lead with the user-requested action, e.g. `reset TASK-056: requested` / `accepted` if asynchronous. Describe worker termination only as a subsidiary step *when an active execution is actually confirmed*. Do not report reset success until the authoritative requeue/disposition completes. If the CLI only acknowledges an asynchronous command, say so and provide an existing way to check completion or failure; do not imply that `stopping-worker` means the reset has finished.
- **Expected normal service log:** Keep a short, comprehensible progression: reset request received; ticket found or not found; whether an active execution exists and, only if it exists, whether worker retirement is required/was completed; reset completed successfully or failed/blocked with actionable reason. Avoid routine `operation_id`, generation/containment details, raw checkpoint hashes, stage-by-stage receipt narration and repeated proof messages at INFO level. Detailed transaction evidence may remain available at debug level and in durable internal records; **do not remove or weaken** durability, attribution, recovery checks or audit artifacts.
- **Regression:** Exercise the real command/IPC path with (a) an existing ticket without an active worker, (b) an existing ticket with an active worker, (c) an unknown ticket, and (d) rejected/failed or interrupted reset. Assert that command acknowledgement is not mistaken for completion, that worker-stop messaging appears only when justified, and that the default journal has clear request/identity/activity/result semantics without routine receipt spam. Existing reset evidence, status transitions and fail-closed semantics must remain intact.
- **Scope boundary:** The separately observed *automatic immediate execution after reset requeues a ticket to todo* is a reset workflow/dispatch policy question, **not part of UX-002**. Do not implicitly change requeue destination or admission behavior when implementing this logging/CLI issue.



### UX-003 — Implemented `inspect` subcommands are undiscoverable in CLI help

- **Status:** Confirmed in the `release/0.5.8` product source, observed by the operator on self-hosted Arch on 2026-11-23. **Presentation/argparse only**, not an unimplemented inspection feature.
- **Action and actual output:** Running `devlegate inspect` reports `a command is required`, suggesting `devlegate inspect --help`. That help prints `usage: devlegate inspect [-h] [--yaml | --json] COMMAND ...`, then an empty `Commands: COMMAND` section: it does not name any legal object type or show an example. The customized `devlegate --help` top-level command groups also omit the existing `inspect` and `workspace` commands.
- **Verified boundary:** `src/devlegate/cli.py` registers `ticket`, `execution`, `workspace`, `service` using `inspect_commands.add_parser(object_type)` **without `help=`**. `ConciseArgumentParser.add_subparsers()` intentionally uses `metavar='COMMAND'`, so there is no implicit list of parser choices. The product's top-level `DevlegateArgumentParser._top_level_groups` is manually maintained and incomplete. `src/devlegate/inspection.py`, `docs/inspect.md`, and inspection CLI/IPC tests already cover the four actual read-only operations (TASK-161 is done).
- **Desired:** `inspect --help` must enumerate and describe all four object types and indicate which require an exact ID (`service` takes none). The main `devlegate --help` must advertise `inspect` and `workspace` alongside other implemented commands. Preserve the current concise missing-command error and exit code 2; `--help` remains exit code 0. Do not create aliases, extra commands, new owner calls or change inspection semantics.
- **Regression:** Exercise actual parser/CLI help with `devlegate inspect`, `devlegate inspect --help`, `devlegate --help`, and four subcommand help views. Assert each registered `inspect` choice appears with useful `help=` text, `inspect` and `workspace` are visible at the top level, parser errors remain concise, and help needs no service/IPC access. Prefer a test protecting accidental drift between actual supported choices and their help.
- **Related:** TASK-161 (already implemented); no reason to reopen inspection-domain logic.

### UX-004 — Default `inspect ticket` floods the terminal with raw evidence and full ticket text

- **Status:** Confirmed operator UX observation on 2026-11-23. The correctness and availability of read-only inspection evidence are **not** in question; the issue is the default human-facing amount and hierarchy of information. **Interface decision deferred until triage**: do not assume a particular flag spelling.
- **Action:** `devlegate inspect ticket TASK-056` without `--json` or `--yaml`.
- **Actual output:** Begins with ticket ID/title/type/state/path/blob, expands committed dependency graph and blockers, prints the complete `Control HEAD` and a long `Ticket path history` (dozens of full 40-character commit IDs), then a verbose `Reset operation (runtime observation)` (operation ID, stage, historical fresh execution ID), and finally **the full canonical multi-page ticket body**. The operational question “what is the state of TASK-056 and are its dependencies blocked?” is buried between forensic metadata and the task specification.
- **Verified boundary:** In `release/0.5.8`, `src/devlegate/cli.py::_render_ticket_inspection()` unconditionally enumerates `observation.provenance.history`, emits all reset fields, and appends `observation.text`. It is a human renderer decision; the typed `inspection.py` observation intentionally carries exact committed evidence. Do not remove that evidence from the owner/IPC API.
- **Desired human default:** A **concise, current-state-first** summary, e.g. exact ticket ID, title, state, direct dependencies with their state, actual dependency blockers/uncertainties, and only other information with immediate operator utility. An empty/blocker-free result should be explicit, not confused with unproven evidence. Avoid claiming a current execution/result/reset state that the existing observation cannot prove. Omit the full ticket text, lengthy commit history, raw blob/control hashes and old reset transaction details from default TTY output. If an operator asks for a full specification or historical/provenance investigation, provide a deliberate accessible path to those **exact** data without losing their provenance distinctions.
- **Open UX choice:** Evaluate a minimal opt-in long view (`--verbose` or `--full`), optional targeted history/text flags, or a small separate explicit presentation command **only if justified by actual use**. Avoid multiplying flags prematurely, automated paging, truncation disguised as a complete history, and hard-coding a guessed “latest execution” when its attribution is not established. A simple human-summary/full-view split is the leading candidate but not yet binding.
- **Machine output contract:** `--json` and `--yaml` must continue emitting the **complete existing versioned inspection data**, including full ticket text, dependency graph, history, provenance, reset evidence, blockers, and informational/mutation flags. Do not silently alter schema, remove owner evidence, reduce auditability or introduce a new expensive query to render the summary.
- **Regression:** With a ticket having many committed path-history entries, large body, dependencies and reset observations, assert default terminal output remains bounded by **selection of sections** (not silently dropping data in the structured response) and presents accurate current state first. Confirm chosen explicit detailed mode exposes the exact existing evidence and distinguishes historical reset context from current execution. Test unknown/missing/ambiguous evidence without fabricating a clean state. Assert JSON/YAML payloads are byte/structure-equivalent to the pre-UX change and the CLI remains read-only; no extra service mutation, Git refresh or new admission.
- **Related:** TASK-161 inspection domain is complete. UX-003 concerns help/discovery; UX-004 concerns the default human-readable projection. May be grouped into one bounded CLI-presentation ticket at triage but neither authorizes an immediate runtime change.


### UX-005 — Healthy active worker presented as blocked startup reconciliation

- **Status:** Confirmed operator observation on self-hosted Arch, 2026-11-25, runtime 0.5.8.dev0, product 2e84d4b, control 03e4df1.
- **Action:** Run `devlegate status` during ordinary TASK-193 execution. The active execution row says `running / worker-running`; worker output was recently active (52.9s without output). Yet the next line says:
  ```text
  While the current execution continues, the observed scheduler decision: blocked — persisted worker ownership is matching-live; startup reconciliation is blocked
  ```
- **Verified boundary:** `runtime.py` derives a hypothetical persisted-state recovery plan from agent_running/worker-running and matching-live ownership; `cli.py` prefixes the same blocked reason during an active execution. Current-service ownership is separately observed. The display mixes normal ongoing execution and startup recovery semantics; the example does not establish an actual recovery blocker.
- **Desired:** Current-state-first human status that distinguishes an ordinarily owned active worker (serial scheduling waits normally) from genuinely blocked recovery or operator action required. Do not merely rename every blocked plan to running or hide actual faults. Use existing owner evidence rather than new checks. Keep real blockers prominent. Review redundant service running/version labels, State vs Stage, worker-output age phrasing, and clarify that canonical Todo includes the active ticket. Prefer concise normal status and detailed inspection on demand; exact layout deferred to triage.
- **Regression:** Real status CLI/owner IPC during proven current-service active execution must not imply startup reconciliation is blocked; after restart with surviving/indeterminate persisted ownership and no proven current ownership, report actual recovery uncertainty/blocker. Also cover idle genuine workflow blocker, stop/restart and finalization. Preserve structured evidence and all mutation/admission safeguards; distinguish projections without changing scheduling policy.
- **Scope:** UX register only; semantic projection correction may require a bounded runtime change at implementation time, not just text formatting. Do not extend active TASK-193.

Former entries UX-006 and UX-007 were reclassified as functional defects and transferred to the reopened TASK-204, with their diagnostic evidence preserved there and in control history. They are not outstanding UX issues; numbering is not reused.

## Acceptance criteria

- Each UX observation is independently reproducible or explicitly marked as unconfirmed; command/output evidence and anticipated safe operator path are preserved.
- When promoted to implementation tickets, each chosen improvement has a bounded scope, regression coverage and no reduction in provenance/ownership safety.
- UX-001, if implemented, distinguishes **confirmed active owner** from **unproven historical checkpoint** and never advises unsafe mutation or invents source identity.
- This accumulating backlog item does not become an automatically scheduled or unbounded implementation task.

## Required regressions

- Reproduce UX-001 in a disposable Git-backed project with active owner: diagnostic must name owner contention and suggest safe operator action; exit remains nonzero and no source/owner state changes occur.
- Stop the owner and retain a latest historical report lacking `resource_generation`: preserve a specific latest-execution refusal and the clean `--repo` manual testing guidance.
- Inject a distinct insufficient-receipt/resource fault without a confirmed active owner: diagnostic must **not** falsely say the service is running.
- For subsequent UX observations, attach separate scenario-specific regression expectations at triage rather than generalizing all failures into one message.

## Boundaries

This is a persistent backlog register, **not** an implementation authorization. No owner runtime, ticket worktree, release branch, immutable review/report, archival asset, Git ref or systemd resource is to be modified by adding or updating this document. Keep user observations and hypotheses distinguishable and avoid unnecessary historical-data migrations.
