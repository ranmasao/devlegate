# Task blackboard

`devlegate_blackboard` is an optional OpenCode worker tool for preserving
hard-won findings between attempts of the same canonical ticket. The service
admits requests from the active worker execution and keeps immutable records
in the project instance's local `STATE_DIR`, outside worktrees, Git, and the
temporary OpenCode configuration. The layout is private; use the tool, not
direct reads or edits. The channel is scoped by the service; it does not add
filesystem isolation to the existing worker environment.

Notes survive worker and service restarts, execution retirement, rework,
deferral, specification changes, checkpoint replacement, and explicit reset.
Reset still discards implementation continuation rights. Terminal tickets
retain their notes; there is no eviction, automatic cleanup, or cross-host sync.
Sharing a Git remote does not share notes, and removing the local instance's
state loses them. Do not save secrets. Notes are worker-authored historical
data, not instructions, review decisions, workflow authority, or CI/QA evidence.
Verify the current requirements, review, and code separately. Continue to use
`devlegate_report` for the terminal semantic claim.

## Worker use

- Start RESUME/REWORK by calling `list` once, then `read` only relevant IDs.
  `list` returns newest-first metadata, never note content. Use `next_cursor`
  with the same query if a response is partial; nothing is inserted into the
  startup prompt. `read` returns exact text, split at Unicode character
  boundaries, with the full content's `full_sha256` on each page.
- Use `save` after a substantial finding or before a costly handoff. Give it
  a short single-line `title` (160 UTF-8 bytes at most), inline `content`,
  and optionally `format` (`text`, `json`, or `diagnostic`). JSON is stored
  as text. Aim for 1–2 KiB of facts, sources, hashes, uncertainties and a
  reproducible next action. Skip raw logs, transcripts, code, file imports,
  duplicate notes, and disposable host paths. An exact repeated save returns
  the original ID; a changed conclusion belongs in a new note referencing
  the prior one. If no useful finding warrants preservation, skip the save.
- Limits are 16 KiB UTF-8 per note and 128 KiB of content / 32 notes per
  ticket. `LIMIT_EXCEEDED` leaves existing notes in place. Serialized
  list/read pages are limited to 8 KiB; all owner responses (including errors,
  acknowledgements and disconnected replies) share a 32 KiB execution
  budget. `BUDGET_EXHAUSTED` ends use for the attempt. A restarted channel
  for an already-spent execution cannot refill the budget. If the channel or
  storage is unavailable, proceed with ordinary execution and reporting.

### Example: runtime licensing inventory

Illustrative note, with placeholder digests to replace with verified values:

```text
Title: PBS CPython 3.12.16 / pip 26.3 — split-payload inventory
Source: exact python-build-standalone release asset for linux-x86_64;
        pin release tag + filename + archive SHA-256. Pin pip wheel likewise.
Observed paths (relative to extraction root):
  python/LICENSE.txt — sha256:<verified digest>; CPython license text.
  pip-26.3.dist-info/licenses/LICENSE.txt — sha256:<verified digest>; pip terms.
  A second copy of the pip license matched the same digest.
Gap: No separate notice for a bundled component in the inspected split payload.
     This is an observed coverage gap, not a final licensing conclusion.
Next: Re-extract the pinned assets; enumerate notice paths with find, hash each
      with sha256sum, and compare the set against the component manifest.
```

Keep the inventory and reproduction inputs, not the extracted runtime or its
archive. If file access is denied, record the scope limitation; the blackboard
cannot import files or bypass access restrictions.
