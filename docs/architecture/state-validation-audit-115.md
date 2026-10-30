# Runtime state validation audit

Baseline: `e48a44b688d20a58eff22e376925dbfd45d44bd3`. This is an
extraction investigation, not final architecture approval.

## Callers and supported inputs

The production calls are `ServiceEngine._load_state`, `_save_state_locked`,
and `_status_state_observation` in `src/devlegate/runtime.py`. The load and
observation paths call `SQLiteRuntimeStore.load` and `observe`; their `_read`
decodes the stored payload with `json.loads` in `runtime_store.py:141-188`.
That yields plain JSON containers and scalars. An absent state yields a new
idle dict. Load's existing pre-worker normalization and `replace` happen
before validation, not inside it. Status validates without that normalization.
Save constructs a plain outer dict from `self._state` and keyword fields, then
clears or adjusts fields before validation; nested values can be shared with
the prior state or caller. The store serializes only after validation. The
validator must therefore leave nested aliases untouched. Test callers also
invoke the static method directly. No production call to the validator passes a
callback as its state; store-derived values are JSON data. This does not prove
that all future runtime save callers will supply only built-in containers.

## Dependency and effects inventory

The static validator at `runtime.py:3335-3791` does not access `self`, a store,
Git, a clock, or a lock. It reads state and constructs local sets, tuples and
temporary parsed values; the retry helper mutates only its newly allocated
`required` set. Its free non-builtin names and called dependencies are:

* `DevlegateError`, the runtime's user-facing exception class. The validator
  also propagates Python exceptions for some malformed JSON-shaped inputs, so
  preserving only the explicitly raised errors would not preserve behavior.
* `_is_git_identity` (`runtime.py:220-225`), a string/length/hex check.
* `_worker_identity_from_value` (`runtime.py:577-637`), which checks fields and
  constructs the frozen `WorkerProcessIdentity`; it neither changes the input
  nor observes the process. Both helpers are used elsewhere in runtime.
* `ExecutionReport.from_dict` and `ExecutionReportError`
  (`execution_result.py:103-309`). Parsing constructs frozen report, result,
  and claim objects and tuples; its helpers validate, hash in memory, and map
  signal numbers to names. The file also contains I/O code, but that code is
  not on this parsing path.
* `restore_review_binding` and `ReviewProvenanceError`
  (`review_provenance.py:62-130`): field and regex checks, in-memory UTF-8
  encoding and SHA-256, and construction of a frozen binding. Its Git-reading
  functions are separate and are not called by this path.

On ordinary JSON inputs these paths do not write the input or perform I/O.
Custom mapping/list subclasses could execute arbitrary methods during access;
that is distinct from the normal SQLite JSON domain and is not by itself an
extraction blocker. `tests/test_state_validation_purity.py` supplies limited
runtime evidence: selected accepted and rejected inputs retain their original
contents while common file, process and SQLite entry points are forbidden, and
the existing error class and exact text are checked. It is not an exhaustive
proof of every branch or every possible I/O primitive.

## Extraction result

`runtime_state_validation.py` now owns the complete validator, its shared error,
and both pure identity helpers. `runtime.py` imports those definitions and keeps
the static entrypoint used by existing callers; state loading and saving remain
there. The frozen test baseline's validator was checked by AST against the exact
baseline SHA above. `tests/test_state_validation_purity.py` compares results,
error identity and text, and input preservation, including malformed nested
report and review bindings, legacy and current states, and both import orders.
It also checks save and reload through a new engine instance. Existing recovery
tests exercise replay paths. Instrumentation covers common I/O entry points, not
every possible effect or arbitrary custom container behavior. This audit does
not establish final architecture approval or cover the separate recovery audit.
