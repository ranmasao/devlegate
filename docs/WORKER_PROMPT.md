# Implementation worker prompt

The renderer orders protocol, engineering principles, project instructions,
Change Explorer, Blackboard, directive, assignment and review. Unavailable or
unused sections are omitted. Each project document, assignment and review is
fenced with more tildes than occur anywhere in its content. Project preferences
operate within the protocol; the layout does not provide OS confinement.

The private prompt journal records the UTF-8 text, its SHA-256 and the delivery
state (`constructed` or `sent`). Its ordered `components` list includes omitted
slots. For each included slot, slice the prompt's UTF-8 bytes using `offset` and
`bytes` and hash the slice to verify `sha256`. Slices include section headings;
two LF bytes separate consecutive slices and one LF terminates the prompt. The
project slot pins the admitted product commit, manifest blob and ordered worker
document paths, blob IDs and SHA-256 digests. Its documents can be recovered from
that commit; the assignment and review use the journal's admission and review
bindings. `sent` covers delivery to the adapter's stdin, not provider-side
instructions or the full model context.

## Representative comparison

Compare `build_worker_prompt` at `f836ad74fe92f6848ca56e2ddd8bdfe69e595468`
with this renderer, in stalled RESUME with current review, Blackboard and
Change Explorer available. The old adapter exposed Change Explorer but its
prompt had no section for it. Use `.devlegate/worker.md` for the new project
slot. Pass these same inputs to both renderers (`\n` is LF):

- Assignment: `Preserve recovery of interrupted ticket executions.\nVerify that the original checkpoint remains attributable.`
- Feedback: `Keep the process identity check before reusing the checkpoint.\nDo not advance an unproven generation.`
- Review source: `verified curator review`

Count body words with `len(body.split())` and bytes with
`len(body.encode('utf-8'))`. Count fences as body text; omit headings and
inter-section separators for body counts and include them in the total.

| Section body | Old words / bytes | New words / bytes |
| --- | ---: | ---: |
| Core Worker Contract (old includes Blackboard) | 820 / 5700 | 169 / 1209 |
| Engineering Principles | — | 66 / 501 |
| Project Worker Instructions | — | 131 / 909 |
| Change Explorer | — | 74 / 538 |
| Blackboard | included above | 30 / 230 |
| Work Directive (with stall reason) | 53 / 377 | 35 / 259 |
| Assigned Work | 15 / 121 | 15 / 121 |
| Current Review Feedback | 21 / 146 | 21 / 146 |
| **Full prompt** | **923 / 6437** | **567 / 4104** |

This removes repeated whole-ticket stopping rules, repeated retained-work
instructions and mandatory-looking Blackboard steps. The Python baseline,
dependency constraints, focused pytest, lint, diff check, and hosted CI and
coverage policy live in Devlegate's project instructions. The render still
defines authority, exactly one semantic report, honest completion and external
blockers, critical-path validation, review attribution and the stall reason.
