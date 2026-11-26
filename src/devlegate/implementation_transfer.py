# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-held preparation of a private candidate from an admitted A/B/H."""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    GitRunner,
    commit_parents,
    commit_range,
    common_dir,
    is_ancestor,
    resolve_commit,
    worktree_paths,
)
from devlegate.reconciliation import prove_linear_lineage
from devlegate.resource_binding import (
    _identity,
    _read,
    _safe_path,
    _sync_directory,
    _write_once,
)

_OID = re.compile(r"[0-9a-f]{40}")
_TOKEN = re.compile(r"[0-9a-f]{64}")
_REFS = "refs/devlegate/transfer/"
_UNBORN = "refs/heads/transfer-unborn"
_GIT_OPTIONS = (
    "--no-replace-objects",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "core.autocrlf=false",
    "-c", "core.filemode=true",
    "-c", "core.fsync=all",
    "-c", "core.logAllRefUpdates=always",
    "-c", "init.defaultRefFormat=files",
    "-c", "protocol.allow=never",
    "-c", "protocol.file.allow=always",
    "-c", "maintenance.auto=false",
    "-c", "submodule.recurse=false",
    "-c", "rerere.enabled=false",
    "-c", "commit.gpgSign=false",
    "-c", "user.name=Devlegate Transfer",
    "-c", "user.email=transfer@devlegate.invalid",
)


@dataclass(frozen=True)
class TransferIdentity:
    owner: str
    ticket: str
    source_generation: str
    attempt: str
    original_base: str
    checkpoint: str
    target: str

    def __post_init__(self) -> None:
        if not (
            self.owner and self.ticket
            and _TOKEN.fullmatch(self.source_generation)
            and _TOKEN.fullmatch(self.attempt)
            and all(_OID.fullmatch(oid) for oid in self.inputs.values())
        ):
            raise ValueError("transfer requires admitted identity and full object IDs")

    @property
    def inputs(self) -> dict[str, str]:
        return {"a": self.original_base, "h": self.checkpoint, "b": self.target}


class TransferEffect(StrEnum):
    CHANGED = "changed"
    SOURCE_EMPTY = "source-empty"
    ALREADY_ANCESTOR = "already-ancestor"
    TARGET_TREE_UNCHANGED = "target-tree-unchanged"


@dataclass(frozen=True)
class TransferReady:
    identity: TransferIdentity
    path: Path
    head: str
    tree: str
    effect: TransferEffect
    externally_resolved: bool


@dataclass(frozen=True)
class TransferConflict:
    identity: TransferIdentity
    path: Path
    step: int
    parent: str
    source_commit: str


@dataclass(frozen=True)
class ResolutionAuthorization:
    conflict: TransferConflict
    tree: str
    justification: str


@dataclass(frozen=True)
class TransferUnavailable:
    identity: TransferIdentity
    reason: str


@dataclass(frozen=True)
class TransferInterrupted:
    identity: TransferIdentity
    reason: str


type TransferResult = (
    TransferReady | TransferConflict | TransferUnavailable | TransferInterrupted
)


class _Unsafe(ExecutionWorkspaceError):
    pass


class _Interrupted(_Unsafe):
    pass


class ImplementationTransfer:
    """Use the existing owner's serialized repository region and Git runner.

    The caller admits the attempt token and independently proves its source
    before import. It excludes editors while invoking this service, and admits
    and authorizes any resolution outside it. This class grants no authority.
    """

    def __init__(
        self, repo: Path, workspace_root: Path, identity: TransferIdentity, *,
        require_owner: Callable[[], None],
        require_source: Callable[[TransferIdentity], None],
        runner: GitRunner,
    ) -> None:
        self.source = repo.absolute()
        self.root = workspace_root.absolute() / "transfers"
        self.identity = identity
        self.directory = self.root / identity.attempt
        self.path = self.directory / "candidate"
        self.admin = self.path / ".git"
        self._require_owner = require_owner
        self._require_source = require_source
        self._runner = runner

    def prepare(self) -> TransferResult:
        """Resume durable progress, or prepare the admitted attempt."""
        return self._run(None)

    def checkpoint_resolution(
        self, authorization: ResolutionAuthorization,
    ) -> TransferResult:
        """Checkpoint exactly the fully staged tree authorized for this conflict."""
        return self._run(authorization)

    def resolution_tree(self, conflict: TransferConflict) -> str:
        """Observe a fully staged resolution for the caller's authorization."""
        self._require_owner()
        self._check_conflict(conflict)
        return self._staged_tree()

    def _run(self, authorization: ResolutionAuthorization | None) -> TransferResult:
        self._require_owner()
        try:
            if authorization is not None:
                self._check_conflict(authorization.conflict)
                if (
                    not authorization.justification.strip()
                    or "\0" in authorization.justification
                    or self._staged_tree() != authorization.tree
                ):
                    raise _Unsafe("resolution authorization does not match staged tree")
                step = authorization.conflict.step
                self._put(f"{step}-resolution", {
                    "tree": authorization.tree,
                    "justification": authorization.justification,
                })
            self._materialize()
            return self._advance()
        except _Interrupted as error:
            return TransferInterrupted(self.identity, str(error))
        except (ExecutionWorkspaceError, GitObservationError, GitAncestryError, OSError,
                UnicodeError) as error:
            return TransferUnavailable(self.identity, str(error))

    def _receipt(self, name: str) -> dict[str, object] | None:
        path = self.directory / f"{name}.json"
        _safe_path(path)
        try:
            return _read(path)
        except FileNotFoundError:
            return None

    def _put(self, name: str, data: dict[str, object]) -> None:
        _write_once(self.directory / f"{name}.json", data)

    def _intent(self) -> dict[str, object]:
        return {
            "version": 1, "identity": asdict(self.identity), "source": str(self.source),
        }

    def _prove_source(self) -> None:
        self._require_source(self.identity)
        common = common_dir(self.source, runner=self._git)
        checkouts = worktree_paths(self.source, runner=self._git)
        if any(self.root.is_relative_to(path) for path in (*checkouts, common)):
            raise _Unsafe("transfer storage overlaps a canonical repository path")

    def _bind_directory(self, *, create: bool) -> None:
        intent = self.root / f"{self.identity.attempt}.json"
        reservation = self.root / f"{self.identity.attempt}.directory.json"
        _safe_path(self.directory)
        if not intent.exists() and create:
            if self.directory.exists() or reservation.exists():
                raise _Unsafe("foreign transfer attempt path")
            self._prove_source()
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            _sync_directory(self.root.parent)
            _write_once(intent, self._intent())
        if _read(intent) != self._intent():
            raise _Unsafe("transfer attempt identity has changed")
        _safe_path(reservation)
        if not reservation.exists() and create:
            if self.directory.exists():
                raise _Interrupted("transfer directory lacks a reservation receipt")
            self.directory.mkdir(mode=0o700)
            _sync_directory(self.root)
            _write_once(reservation, {"directory": _identity(self.directory)})
        if _read(reservation) != {"directory": _identity(self.directory)}:
            raise _Unsafe("transfer receipt directory has been replaced")

    def _git(self, cwd: Path, *args: str, check: bool = False, **kwargs):
        return self._runner(cwd, *_GIT_OPTIONS, *args, check=check, **kwargs)

    def _command(self, *args: str, **kwargs) -> str:
        result = self._git(self.path, *args, **kwargs)
        if result.returncode:
            raise _Unsafe(f"candidate Git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout

    def _registration(self) -> dict[str, object]:
        for path in (self.path, self.admin, self.admin / "objects"):
            _safe_path(path)
            if not path.is_dir():
                raise _Unsafe(f"candidate repository directory unavailable: {path}")
        config = self.admin / "config"
        _safe_path(config)
        if not config.is_file():
            raise _Unsafe("candidate repository config unavailable")
        for extra in ("objects/info/alternates", "info/grafts", "commondir", "shallow"):
            self._absent_admin(extra)
        return {
            "path": _identity(self.path), "admin": _identity(self.admin),
            "objects": _identity(self.admin / "objects"),
            "config": hashlib.sha256(config.read_bytes()).hexdigest(),
        }

    def _absent_admin(self, name: str) -> None:
        path = self.admin / name
        _safe_path(path)
        if path.exists():
            raise _Unsafe(f"unexpected candidate Git state: {name}")

    def _prove_repo(self, *, pins: bool = True) -> None:
        if self._receipt("repository") != self._registration():
            raise _Unsafe("candidate repository identity has changed")
        roots = self._command(
            "rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir",
        ).splitlines()
        if len(roots) != 3 or [(self.path / root).resolve() for root in roots] != [
            self.path, self.admin, self.admin,
        ]:
            raise _Unsafe("candidate Git root or administration directory changed")
        refs = self._command(
            "for-each-ref", "--format=%(refname) %(objectname) %(symref)",
        )
        expected = {
            f"{_REFS}{name} {oid} " for name, oid in self.identity.inputs.items()
        }
        if set(refs.splitlines()) != (expected if pins else set()):
            raise _Unsafe("candidate refs differ from the admitted private pins")
        for name in ("index.lock", "HEAD.lock", "sequencer", "rebase-merge",
                     "rebase-apply", "MERGE_HEAD", "REVERT_HEAD"):
            self._absent_admin(name)
        for name in ("HEAD", "index", "logs/HEAD", "refs", "packed-refs"):
            _safe_path(self.admin / name)

    def _head(self) -> str:
        if self._git(self.path, "symbolic-ref", "-q", "HEAD").returncode != 1:
            raise _Unsafe("candidate HEAD is not detached")
        head = resolve_commit(self.path, "HEAD", runner=self._git)
        if head is None or not _OID.fullmatch(head):
            raise _Unsafe("candidate HEAD is unavailable")
        return head

    def _pick(self) -> str | None:
        path = self.admin / "CHERRY_PICK_HEAD"
        _safe_path(path)
        if not path.exists():
            return None
        if not path.is_file():
            raise _Unsafe("invalid candidate cherry-pick marker")
        pick = path.read_text(encoding="ascii").removesuffix("\n")
        if not _OID.fullmatch(pick):
            raise _Unsafe("invalid candidate cherry-pick marker")
        return pick

    def _status(self) -> str:
        self._require_visible_index()
        return self._command(
            "status", "--porcelain=v1", "--untracked-files=all", "--ignored",
            "--ignore-submodules=none", "-z",
        )

    def _require_visible_index(self) -> None:
        flags = self._command("ls-files", "-v", "-z").split("\0")
        if any(flag[:1].islower() or flag.startswith("S ") for flag in flags):
            raise _Unsafe("candidate index hides working tree changes")

    def _head_log(self) -> bytes:
        path = self.admin / "logs" / "HEAD"
        _safe_path(path)
        if not path.is_file():
            raise _Unsafe("candidate HEAD reflog is unavailable")
        return path.read_bytes()

    def _head_log_digest(self) -> str:
        return hashlib.sha256(self._head_log()).hexdigest()

    def _prove_head_advance(
        self, previous_log: object, old: str, new: str, action_prefix: bytes,
    ) -> None:
        log = self._head_log()
        prior, _, last = log.rstrip(b"\n").rpartition(b"\n")
        if (hashlib.sha256(prior + b"\n").hexdigest() != previous_log
                or not last.startswith(f"{old} {new} ".encode("ascii"))
                or not last.partition(b"\t")[2].startswith(action_prefix)):
            raise _Interrupted("candidate HEAD advance is not attributable; retained")

    def _tree(self, commit: str) -> str:
        tree = self._command("rev-parse", "--verify", f"{commit}^{{tree}}").strip()
        if not _OID.fullmatch(tree):
            raise _Unsafe("candidate tree ID is unavailable")
        return tree

    def _inputs(self) -> tuple[tuple[str, ...], str, TransferEffect | None]:
        a, h, b = (self.identity.inputs[key] for key in ("a", "h", "b"))
        for oid in (a, h, b):
            if resolve_commit(self.path, oid, runner=self._git) != oid:
                raise _Unsafe("imported input is not the exact admitted commit")
        commits = commit_range(self.path, f"{a}..{h}", reverse=True, runner=self._git)
        if a != h:
            parents = {
                commit: commit_parents(self.path, commit, runner=self._git) or ()
                for commit in commits
            }
            proof = prove_linear_lineage(a, commits, parents)
            if not proof.valid or commits[-1] != h:
                raise _Unsafe(f"invalid source lineage: {proof.reason}")
        if self._tree(a) == self._tree(h):
            return (), h if a == b else b, TransferEffect.SOURCE_EMPTY
        if a == b:
            return (), h, None
        if is_ancestor(self.path, h, b, runner=self._git):
            return (), b, TransferEffect.ALREADY_ANCESTOR
        return commits, b, None

    def _materialize(self) -> None:
        self._bind_directory(create=True)
        if self._receipt("release") is not None:
            raise _Unsafe("candidate has been released")
        if self._receipt("repository") is None:
            if self.path.exists() or self.path.is_symlink():
                raise _Interrupted("candidate path exists without a repository receipt")
            self.path.mkdir(mode=0o700)
            _sync_directory(self.directory)
            self._command("init", "--template=", "--object-format=sha1",
                          "--initial-branch=transfer-unborn")
            self._put("repository", self._registration())
        if self._receipt("materialized") is None:
            importing = self._receipt("import")
            if importing is not None and importing != {"inputs": self.identity.inputs}:
                raise _Unsafe("candidate import intent differs from admitted inputs")
            try:
                self._prove_repo(pins=False)
                self._require_pristine_unborn()
            except _Unsafe as error:
                if importing is not None:
                    raise _Interrupted(f"unreceipted import: {error}") from error
                raise
            self._prove_source()
            if importing is None:
                self._put("import", {"inputs": self.identity.inputs})
            self._command(
                "fetch", "--no-tags", "--no-write-fetch-head",
                "--no-auto-maintenance",
                "--no-recurse-submodules", "--", self.source.as_uri(),
                *dict.fromkeys(self.identity.inputs.values()),
            )
            self._prove_repo(pins=False)
            self._require_pristine_unborn()
            self._command(
                "update-ref", "--stdin",
                input_text="".join(
                    f"create {_REFS}{name} {oid}\n"
                    for name, oid in self.identity.inputs.items()
                ),
            )
            self._prove_repo()
            _, start, _ = self._inputs()
            self._command("checkout", "--detach", start, "--")
            if self._head() != start or self._status() or self._pick() is not None:
                raise _Interrupted("candidate checkout did not materialize cleanly")
            self._put("materialized", {"head": start})
        self._prove_repo()

    def _require_pristine_unborn(self) -> None:
        if (self._command("symbolic-ref", "HEAD").strip() != _UNBORN
                or self._status() or self._pick() is not None):
            raise _Unsafe("candidate repository is not the original empty checkout")
        self._absent_admin("index")

    def _advance(
        self, *, allow_effects: bool = True,
    ) -> TransferReady | TransferConflict:
        commits, start, initial_effect = self._inputs()
        if self._receipt("materialized") != {"head": start}:
            raise _Unsafe("candidate materialization receipt does not match inputs")
        parent = start
        resolved = False
        for step, commit in enumerate(commits):
            intent = {"parent": parent, "source_commit": commit}
            conflict = TransferConflict(self.identity, self.path, step, parent, commit)
            before = self._receipt(f"{step}-intent")
            done = self._receipt(f"{step}-done")
            stopped = self._receipt(f"{step}-conflict")
            plan = self._receipt(f"{step}-resolution")
            if before is not None and (
                any(before.get(key) != value for key, value in intent.items())
                or not isinstance(before.get("head_log"), str)
            ):
                raise _Unsafe("candidate step intent differs from the proved lineage")
            if done is not None:
                if before is None or done.get("resolved") is not (plan is not None):
                    raise _Unsafe("candidate step has incomplete outcome evidence")
                after = done.get("head")
                if not isinstance(after, str):
                    raise _Unsafe("candidate step is missing its checkpoint")
                self._prove_step(parent, after)
                parent = after
                resolved |= plan is not None
                continue
            if not allow_effects:
                raise _Unsafe("candidate has no matching final checkpoint evidence")
            if stopped is not None:
                if stopped != before:
                    raise _Unsafe("candidate conflict receipt does not match the step")
                if plan is None:
                    self._match_stop(conflict, stopped)
                    return conflict
                parent = self._finish_resolution(conflict, stopped, plan)
                resolved = True
                continue
            if plan is not None:
                raise _Unsafe("resolution has no attributed conflict")
            if self._head() != parent or self._pick() is not None or self._status():
                if before is not None:
                    raise _Interrupted("unreceipted cherry-pick; candidate retained")
                raise _Unsafe("candidate changed outside the recorded transfer step")
            current = {**intent, "head_log": self._head_log_digest()}
            if before is None:
                self._put(f"{step}-intent", current)
            elif before != current:
                raise _Interrupted("candidate HEAD changed after cherry-pick intent")
            pick = self._git(
                self.path, "cherry-pick", "--strategy=ort", "--no-edit",
                "--allow-empty", "--keep-redundant-commits", commit,
            )
            self._prove_repo()
            if pick.returncode:
                if (self._head() == parent and self._pick() == commit
                        and self._head_log_digest() == current["head_log"]
                        and self._command("ls-files", "--unmerged", "-z")):
                    self._put(f"{step}-conflict", current)
                    return conflict
                raise _Interrupted(f"unproved cherry-pick: {pick.stderr.strip()}")
            after = self._head()
            self._prove_step(parent, after)
            self._require_clean_head(after)
            self._prove_head_advance(
                current["head_log"], parent, after, b"cherry-pick:",
            )
            self._put(f"{step}-done", {"head": after, "resolved": False})
            parent = after
        self._require_clean_head(parent)
        tree = self._tree(parent)
        effect = initial_effect or (
            TransferEffect.TARGET_TREE_UNCHANGED
            if tree == self._tree(self.identity.target) else TransferEffect.CHANGED
        )
        return TransferReady(self.identity, self.path, parent, tree, effect, resolved)

    def _prove_step(self, parent: str, head: str) -> None:
        if (not _OID.fullmatch(head)
                or commit_parents(self.path, head, runner=self._git) != (parent,)):
            raise _Unsafe("candidate step is not the recorded single-parent advance")

    def _require_clean_head(self, head: str) -> None:
        self._prove_repo()
        if self._head() != head or self._pick() is not None or self._status():
            raise _Unsafe("candidate no longer matches its recorded clean checkpoint")

    def _match_stop(
        self, conflict: TransferConflict, receipt: dict[str, object],
    ) -> None:
        if (self._head() != conflict.parent or self._pick() != conflict.source_commit
                or self._head_log_digest() != receipt.get("head_log")):
            raise _Unsafe("candidate checkout no longer matches the recorded conflict")

    def _check_conflict(self, conflict: TransferConflict) -> None:
        if (conflict.identity != self.identity or conflict.path != self.path
                or conflict.step < 0):
            raise _Unsafe("resolution authorization targets a different attempt")
        self._bind_directory(create=False)
        if self._receipt("release") or self._receipt("materialized") is None:
            raise _Unsafe("candidate is not available for resolution")
        self._prove_repo()
        before = self._receipt(f"{conflict.step}-intent")
        if (before is None or before.get("parent") != conflict.parent
                or before.get("source_commit") != conflict.source_commit
                or self._receipt(f"{conflict.step}-conflict") != before
                or self._receipt(f"{conflict.step}-done") is not None
                or self._receipt(f"{conflict.step}-resolution") is not None):
            raise _Unsafe("resolution authorization is stale or conflict is unproved")
        self._match_stop(conflict, before)

    def _staged_tree(self) -> str:
        self._require_visible_index()
        if self._command("ls-files", "--unmerged", "-z"):
            raise _Unsafe("candidate index still has unresolved entries")
        # Porcelain XY: only changes fully represented by the index are allowed.
        # -z renames have a second path record, so disable status rename detection.
        status = self._command("status", "--porcelain=v1", "--no-renames", "-z",
                               "--untracked-files=all", "--ignored",
                               "--ignore-submodules=none")
        if any(record[1:2] != " " or record[:1] in {"?", "!"}
               for record in status.split("\0") if record):
            raise _Unsafe("candidate contains unstaged, untracked or ignored edits")
        tree = self._command("write-tree").strip()
        if not _OID.fullmatch(tree):
            raise _Unsafe("cannot identify the exact staged resolution tree")
        return tree

    def _finish_resolution(
        self, conflict: TransferConflict,
        stop: dict[str, object], plan: dict[str, object],
    ) -> str:
        tree, justification = plan.get("tree"), plan.get("justification")
        if (not isinstance(tree, str) or not _OID.fullmatch(tree)
                or not isinstance(justification, str) or not justification.strip()):
            raise _Unsafe("invalid resolution checkpoint intent")
        # Deterministic bytes: the receipt authorizes this tree, parent and
        # source commit, not an arbitrary commit a resolver happened to create.
        content = (
            f"tree {tree}\nparent {conflict.parent}\n"
            "author Devlegate Transfer <transfer@devlegate.invalid> 0 +0000\n"
            "committer Devlegate Transfer <transfer@devlegate.invalid> 0 +0000\n\n"
            f"Resolve transfer of {conflict.source_commit}\n\n"
            f"{justification}\n\nTransfer-Attempt: {self.identity.attempt}\n"
        )
        head = self._head()
        marker = self._pick()
        if self._staged_tree() != tree:
            raise _Interrupted("authorized resolution tree changed; candidate retained")
        expected = self._command(
            "hash-object", "-t", "commit", "--stdin", input_text=content,
        ).strip()
        action = f"transfer resolution {self.identity.attempt}/{conflict.step}"
        if head == conflict.parent and marker == conflict.source_commit:
            self._match_stop(conflict, stop)
            written = self._command("hash-object", "-w", "-t", "commit", "--stdin",
                                    input_text=content).strip()
            if written != expected or self._staged_tree() != tree:
                raise _Interrupted("resolution changed before checkpointing")
            self._command(
                "update-ref", "--no-deref", "-m", action,
                "HEAD", expected, conflict.parent,
            )
        elif head != expected or marker not in (None, conflict.source_commit):
            raise _Interrupted("unrecognized resolution checkpoint; candidate retained")
        self._prove_step(conflict.parent, expected)
        self._prove_head_advance(
            stop.get("head_log"), conflict.parent, expected, action.encode("ascii"),
        )
        if self._tree(expected) != tree:
            raise _Unsafe("resolution checkpoint does not contain the authorized tree")
        if self._pick() == conflict.source_commit:
            self._command("cherry-pick", "--quit")
        self._require_clean_head(expected)
        self._put(f"{conflict.step}-done", {"head": expected, "resolved": True})
        return expected

    def release(
        self, ready: TransferReady, *,
        require_consumed: Callable[[TransferReady], None],
    ) -> None:
        """Release only a proved ready candidate after caller proves downstream custody.

        The tombstone and receipts remain. Ordinary workspace/ref cleanup cannot
        see this repo; an uncertain or conflicted attempt has no release path here.
        """
        self._require_owner()
        self._bind_directory(create=False)
        if ready.identity != self.identity or ready.path != self.path:
            raise _Unsafe("release targets a different transfer attempt")
        tombstone = self._receipt("release")
        if tombstone is None:
            self._prove_repo()
            if self._advance(allow_effects=False) != ready:
                raise _Unsafe("candidate has no matching final checkpoint evidence")
            require_consumed(ready)
            self._require_clean_head(ready.head)
            tombstone = {"head": ready.head, "path": _identity(self.path)}
            self._put("release", tombstone)
        if tombstone.get("head") != ready.head:
            raise _Unsafe("release receipt targets a different checkpoint")
        grave = self.directory / "released-candidate"
        _safe_path(grave)
        _safe_path(self.path)
        if self.path.exists():
            self._require_clean_head(ready.head)
            if grave.exists() or tombstone.get("path") != _identity(self.path):
                raise _Unsafe("release path identity has changed")
            self.path.rename(grave)
            _sync_directory(self.directory)
        if grave.exists():
            if tombstone.get("path") != _identity(grave):
                raise _Unsafe("released candidate directory has been replaced")
            shutil.rmtree(grave)
            _sync_directory(self.directory)
