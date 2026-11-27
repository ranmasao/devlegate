# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-held preparation of a private candidate from an admitted A/B/H."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from subprocess import CompletedProcess

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
)
from devlegate.resource_binding import (
    _publish_once as _publish,
)

_OID = re.compile(r"[0-9a-f]{40}")
_TOKEN = re.compile(r"[0-9a-f]{64}")
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
    "-c", "maintenance.auto=false",
    "-c", "gc.auto=0",
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
            and all(_OID.fullmatch(oid) for oid in (
                self.original_base, self.checkpoint, self.target,
            ))
        ):
            raise ValueError("transfer requires admitted identity and full object IDs")


class TransferEffect(StrEnum):
    CHANGED = "changed"
    SOURCE_EMPTY = "source-empty"
    ALREADY_ANCESTOR = "already-ancestor"
    TARGET_TREE_UNCHANGED = "target-tree-unchanged"


@dataclass(frozen=True)
class TransferReady:
    identity: TransferIdentity
    path: Path | None
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
    source_digest: str | None = None


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
type _Inputs = tuple[tuple[str, ...], str, TransferEffect | None]
type _Progress = tuple[TransferReady | TransferConflict, Path | None, set[str]]


class _Unsafe(ExecutionWorkspaceError):
    pass


class _Interrupted(_Unsafe):
    pass


def source_resolution_digest(path: Path) -> str:
    """Bind operator consent to source bytes, modes and paths, not the Git index."""
    _safe_path(path)
    if not path.is_dir() or not (path / ".git").is_dir():
        raise _Unsafe("resolution path is not a candidate checkout")
    digest = hashlib.sha256()

    def visit(directory):
        _safe_path(directory)
        for entry in sorted(directory.iterdir()):
            if entry.name == ".git":
                if directory != path:
                    raise _Unsafe("nested Git administration in resolution source")
                continue
            before = entry.lstat()
            if stat.S_ISDIR(before.st_mode):
                visit(entry)
                continue
            name = os.fsencode(entry.relative_to(path))
            if stat.S_ISLNK(before.st_mode):
                content = hashlib.sha256(os.fsencode(entry.readlink()))
            elif stat.S_ISREG(before.st_mode):
                fd = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    if os.fstat(stream.fileno()) != before:
                        raise _Interrupted("resolution source changed while reading")
                    content = hashlib.file_digest(stream, "sha256")
            else:
                raise _Unsafe("resolution source contains a special file")
            after = entry.lstat()
            # Reading can advance atime; only inode, metadata and content matter.
            if (before.st_ino, before.st_dev, before.st_mode, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_ino, after.st_dev, after.st_mode, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns,
            ):
                raise _Interrupted("resolution source changed during observation")
            digest.update(name + b"\0" + str(before.st_mode).encode() + b"\0")
            digest.update(content.digest())

    visit(path)
    return digest.hexdigest()


def _receipt(path: Path) -> dict | None:
    try:
        return _read(path)
    except FileNotFoundError:
        return None


class ImplementationTransfer:
    """Runs inside the existing owner region; the caller retains source evidence.

    Only paths named by durable outcomes are ever reopened. An unpublished
    scratch checkout is not a resolver workspace and cannot supply a result.
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
        self.intent = self.root / f"{identity.attempt}.json"
        self.released = self.root / f"{identity.attempt}.released.json"
        self._require_owner = require_owner
        self._require_source = require_source
        self._runner = runner

    def prepare(self) -> TransferResult:
        return self._run(None)

    def observe(self) -> TransferResult:
        """Reobserve an existing outcome without creating another checkout."""
        return self._run(None, prepare=False)

    def checkpoint_resolution(
        self, authorization: ResolutionAuthorization,
    ) -> TransferResult:
        return self._run(authorization)

    def stage_resolution(
        self, conflict: TransferConflict, paths: tuple[str, ...],
    ) -> str:
        """Stage explicit editor paths for the owner to consider authorizing."""
        self._require_owner()
        self._check_conflict(conflict)
        if not paths or any(not p or Path(p).is_absolute() for p in paths):
            raise _Unsafe("resolution staging requires explicit relative paths")
        self._command(conflict.path, "--literal-pathspecs", "add", "-A", "--", *paths)
        return self._staged_tree(conflict.path)

    def resolution_tree(self, conflict: TransferConflict) -> str:
        self._require_owner()
        self._check_conflict(conflict)
        return self._staged_tree(conflict.path)

    def stage_source_resolution(self, conflict: TransferConflict) -> str:
        """Stage a source-only edit after deliberate owner admission."""
        self._require_owner()
        self._check_conflict(conflict)
        path = conflict.path
        unmerged = self._command(path, "ls-files", "--unmerged", "-z")
        for record in filter(None, unmerged.split("\0")):
            if record.startswith("160000 "):
                raise _Unsafe("source edits cannot select an unresolved gitlink")
        self._status(path)  # Reject hidden index entries before staging.
        self._command(path, "add", "-A", "--", ".")
        tree = self._staged_tree(path)
        changed = self._command(
            path, "diff-tree", "--no-renames", "--diff-filter=AMT", "--name-only",
            "-r", "-z", f"{conflict.parent}^{{tree}}", tree,
        ).split("\0")
        for name in filter(None, changed):
            markers = self._git(
                path, "--literal-pathspecs", "grep", "-I", "-l", "-E",
                r"^(<{3,}|>{3,}|\|{3,})( |$)", tree, "--", name,
            )
            if markers.returncode != 1:
                raise _Unsafe(f"cannot exclude unresolved conflict markers in {name}")
        return tree

    def _run(
        self, authorization: ResolutionAuthorization | None, *, prepare: bool = True,
    ) -> TransferResult:
        self._require_owner()
        try:
            if authorization is not None:
                conflict = authorization.conflict
                receipt = self._check_conflict(conflict)
                if (
                    not authorization.justification.strip()
                    or "\0" in authorization.justification
                    or self._staged_tree(conflict.path) != authorization.tree
                    or (authorization.source_digest is not None
                        and source_resolution_digest(conflict.path)
                        != authorization.source_digest)
                ):
                    raise _Unsafe("resolution authorization differs from staged tree")
                _publish(receipt, {
                    "tree": authorization.tree,
                    "justification": authorization.justification,
                    "source_digest": authorization.source_digest,
                })
            return self._observe(prepare=prepare)[0]
        except _Interrupted as error:
            return TransferInterrupted(self.identity, str(error))
        except (ExecutionWorkspaceError, GitObservationError, GitAncestryError, OSError,
                UnicodeError) as error:
            return TransferUnavailable(self.identity, str(error))

    def _git(
        self, cwd: Path, *args: str, check: bool = False, **kwargs,
    ) -> CompletedProcess[str]:
        private = () if cwd == self.source else (
            f"--git-dir={cwd / '.git'}", f"--work-tree={cwd}",
        )
        return self._runner(cwd, *_GIT_OPTIONS, *private, *args, check=check, **kwargs)

    def _command(self, cwd: Path, *args: str, **kwargs) -> str:
        result = self._git(cwd, *args, **kwargs)
        if result.returncode:
            raise _Unsafe(f"transfer Git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout

    def _tree(self, path: Path, commit: str) -> str:
        return self._command(
            path, "rev-parse", "--verify", f"{commit}^{{tree}}",
        ).strip()

    def _inputs(self) -> _Inputs:
        identity = self.identity
        a, h, b = identity.original_base, identity.checkpoint, identity.target
        for oid in {a, h, b}:
            if resolve_commit(self.source, oid, runner=self._git) != oid:
                raise _Unsafe("exact admitted input object is unavailable")
        commits = () if a == h else commit_range(
            self.source, f"{a}..{h}", reverse=True, runner=self._git,
        )
        if a != h:
            parents = {
                c: commit_parents(self.source, c, runner=self._git) or ()
                for c in commits
            }
            proof = prove_linear_lineage(a, commits, parents)
            if not proof.valid or not commits or commits[-1] != h:
                raise _Unsafe(f"invalid source lineage: {proof.reason}")
        if self._tree(self.source, a) == self._tree(self.source, h):
            return (), h if a == b else b, TransferEffect.SOURCE_EMPTY
        if a == b:
            return (), h, TransferEffect.CHANGED
        if is_ancestor(self.source, h, b, runner=self._git):
            return (), b, TransferEffect.ALREADY_ANCESTOR
        return tuple(commits), b, None

    def _observe(self, *, prepare: bool, verify_source: bool = True) -> _Progress:
        if _receipt(self.released) is not None:
            raise _Unsafe("candidate has been released")
        intent = _receipt(self.intent)
        if intent is None and not prepare:
            raise _Unsafe("transfer intent is unavailable")
        if verify_source:
            self._require_source(self.identity)
        common = common_dir(self.source, runner=self._git)
        if intent is None and any(self.root.is_relative_to(p) for p in (
            *worktree_paths(self.source, runner=self._git), common,
        )):
            raise _Unsafe("transfer storage overlaps a canonical repository path")
        objects = common / "objects"
        binding = {"identity": asdict(self.identity), "source": str(self.source),
                   "objects": str(objects), "store": _identity(objects)}
        if intent is not None and intent.get("binding") != binding:
            raise _Unsafe("transfer attempt identity or source object store changed")
        commits, start, effect = self._inputs()
        if intent is None:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            _sync_directory(self.root.parent)
            storage = None
            if commits:
                directory = Path(tempfile.mkdtemp(
                    dir=self.root, prefix=f"{self.identity.attempt}-",
                ))
                self._create_pool(directory, objects)
                _sync_directory(self.root)
                storage = [directory.name, _identity(directory),
                           _identity(directory / "objects")]
            intent = {"binding": binding, "storage": storage}
            _publish(self.intent, intent)
        if not commits:
            if intent.get("storage") is not None or effect is None:
                raise _Unsafe("no-transfer intent differs from admitted inputs")
            tree = self._tree(self.source, start)
            ready = TransferReady(self.identity, None, start, tree, effect, False)
            return ready, None, set()
        directory = self._directory(intent)
        return self._advance(directory, commits, start, objects, prepare=prepare)

    def _directory(self, intent: dict, *, released: bool = False) -> Path:
        storage = intent.get("storage")
        if not isinstance(storage, list) or len(storage) != 3:
            raise _Unsafe("invalid transfer storage evidence")
        name, witness, pool = storage
        if not isinstance(name, str) or not re.fullmatch(
            rf"{self.identity.attempt}-[a-z0-9_]+", name,
        ):
            raise _Unsafe("invalid transfer storage name")
        directory = self.root / (f"{name}.released" if released else name)
        if _identity(directory) != witness or not directory.is_dir():
            raise _Unsafe("transfer storage has been replaced")
        if not released and _identity(directory / "objects") != pool:
            raise _Unsafe("transfer object pool has been replaced")
        return directory

    def _create_pool(self, directory: Path, objects: Path) -> None:
        if b"\n" in os.fsencode(objects):
            raise _Unsafe("cannot bind candidate object alternate")
        pool = directory / "objects"
        alternate = pool / "info/alternates"
        alternate.parent.mkdir(parents=True, mode=0o700)
        (pool / "pack").mkdir(mode=0o700)
        with alternate.open("xb") as stream:
            stream.write(os.fsencode(objects) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(alternate.parent)
        _sync_directory(pool)
        _sync_directory(directory)

    def _registration(self, path: Path, objects: Path) -> dict:
        admin = path / ".git"
        config = admin / "config"
        pool = path.parent / "objects"
        alternate = pool / "info/alternates"
        for entry in (config, alternate):
            _safe_path(entry)
            if not entry.is_file():
                raise _Unsafe(f"candidate Git evidence missing: {entry.name}")
        if (
            (admin / "objects").readlink() != Path("../../objects")
            or alternate.read_bytes() != os.fsencode(objects) + b"\n"
        ):
            raise _Unsafe("candidate object alternate has changed")
        for name in (
            "commondir", "info/grafts", "shallow", "index.lock", "HEAD.lock",
            "sequencer", "rebase-merge", "rebase-apply", "MERGE_HEAD", "REVERT_HEAD",
        ):
            entry = admin / name
            _safe_path(entry)
            if entry.exists():
                raise _Unsafe(f"unexpected candidate Git state: {name}")
        for name in ("HEAD", "index", "logs/HEAD", "refs", "packed-refs",
                     "CHERRY_PICK_HEAD"):
            _safe_path(admin / name)
        return {"path": _identity(path), "admin": _identity(admin),
                "objects": _identity(pool),
                "config": hashlib.sha256(config.read_bytes()).hexdigest()}

    def _prove_checkout(self, path: Path, objects: Path, registration: object) -> None:
        if registration != self._registration(path, objects):
            raise _Unsafe("candidate repository identity has changed")
        if self._command(path, "for-each-ref", "--format=%(refname)"):
            raise _Unsafe("unexpected candidate refs")
        if self._git(path, "symbolic-ref", "-q", "HEAD").returncode != 1:
            raise _Unsafe("candidate HEAD is not detached")

    def _log(self, path: Path) -> bytes:
        return (path / ".git/logs/HEAD").read_bytes()

    def _marker(self, path: Path) -> str | None:
        marker = path / ".git/CHERRY_PICK_HEAD"
        return marker.read_text(encoding="ascii").strip() if marker.is_file() else None

    def _status(self, path: Path) -> str:
        flags = self._command(path, "ls-files", "-v", "-z").split("\0")
        if any(flag[:1].islower() or flag.startswith("S ") for flag in flags):
            raise _Unsafe("candidate index hides working tree changes")
        return self._command(
            path, "status", "--porcelain=v1", "--no-renames", "-z",
            "--untracked-files=all", "--ignored", "--ignore-submodules=none",
        )

    def _require_clean(self, path: Path) -> None:
        if self._marker(path) is not None or self._status(path):
            raise _Unsafe("candidate no longer matches its clean checkpoint")

    def _materialize(
        self, directory: Path, objects: Path, start: str, commits: tuple[str, ...],
    ) -> dict:
        # No fixed scratch path or Git effect is adopted on replay. Only the
        # outcome publication below makes a checkout eligible for later use.
        path = Path(tempfile.mkdtemp(dir=directory, prefix="candidate-"))
        _sync_directory(directory)
        witness = _identity(path)
        self._command(path, "init", "--template=", "--object-format=sha1")
        if _identity(path) != witness:
            raise _Unsafe("candidate reservation has changed")
        local_objects = path / ".git/objects"
        _safe_path(local_objects)
        for entry in (local_objects / "info", local_objects / "pack", local_objects):
            entry.rmdir()
        local_objects.symlink_to("../../objects", target_is_directory=True)
        _sync_directory(local_objects.parent)
        registration = self._registration(path, objects)
        self._command(path, "checkout", "--detach", start, "--")
        head = start
        stopped = None
        self._prove_checkout(path, objects, registration)
        self._require_clean(path)
        log = self._log(path)
        initial = f"{'0' * 40} {start} ".encode()
        if (resolve_commit(path, "HEAD", runner=self._git) != start
                or len(log.splitlines()) != 1 or not log.startswith(initial)):
            raise _Interrupted("candidate materialization is not attributable")
        for applied, commit in enumerate(commits):
            pick = self._git(
                path, "cherry-pick", "--strategy=ort", "--no-edit",
                "--allow-empty", "--keep-redundant-commits", commit,
            )
            self._prove_checkout(path, objects, registration)
            after = resolve_commit(path, "HEAD", runner=self._git)
            updated = self._log(path)
            if pick.returncode:
                if (after == head and updated == log and self._marker(path) == commit
                        and self._command(path, "ls-files", "--unmerged", "-z")):
                    stopped = applied
                    break
                raise _Interrupted(f"unproved cherry-pick: {pick.stderr.strip()}")
            effect = updated.removeprefix(log)
            if (
                after is None or not updated.startswith(log)
                or len(effect.splitlines()) != 1
                or not effect.startswith(f"{head} {after} ".encode())
                or not effect.partition(b"\t")[2].startswith(b"cherry-pick:")
                or commit_parents(path, after, runner=self._git) != (head,)
            ):
                raise _Interrupted("mechanical checkpoint is not attributable")
            self._require_clean(path)
            head, log = after, updated
        return {"path": path.name, "registration": registration, "start": start,
                "head": head, "log": hashlib.sha256(log).hexdigest(), "stop": stopped}

    def _advance(
        self, directory: Path, commits: tuple[str, ...], start: str,
        objects: Path, *, prepare: bool,
    ) -> _Progress:
        offset = 0
        expected = {"objects"}
        for segment in range(len(commits) + 1):
            outcome = directory / f"{segment}.outcome.json"
            approval = directory / f"{segment}.approval.json"
            result = _receipt(outcome)
            if result is None:
                if not prepare:
                    raise _Unsafe("candidate has no matching outcome evidence")
                result = self._materialize(directory, objects, start, commits[offset:])
                _publish(outcome, result)
            name = result.get("path")
            head = result.get("head")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"candidate-[a-z0-9_]+", name)
                or not isinstance(head, str) or not _OID.fullmatch(head)
                or result.get("start") != start or "stop" not in result
            ):
                raise _Unsafe("invalid candidate outcome evidence")
            path = directory / name
            expected.update((outcome.name, name))
            self._prove_checkout(path, objects, result.get("registration"))
            log_digest = hashlib.sha256(self._log(path)).hexdigest()
            if (resolve_commit(path, "HEAD", runner=self._git) != head
                    or log_digest != result.get("log")):
                raise _Unsafe("candidate no longer matches its recorded HEAD")
            stop = result["stop"]
            if stop is None:
                self._require_clean(path)
                tree = self._tree(path, head)
                target_tree = self._tree(self.source, self.identity.target)
                effect = (TransferEffect.TARGET_TREE_UNCHANGED if tree == target_tree
                          else TransferEffect.CHANGED)
                ready = TransferReady(
                    self.identity, path, head, tree, effect, segment != 0,
                )
                return ready, directory, expected
            if type(stop) is not int or not 0 <= stop < len(commits) - offset:
                raise _Unsafe("invalid candidate conflict step")
            step = offset + stop
            conflict = TransferConflict(self.identity, path, step, head, commits[step])
            if self._marker(path) != conflict.source_commit:
                raise _Unsafe("candidate no longer matches the recorded conflict")
            plan = _receipt(approval)
            if plan is None:
                return conflict, approval, expected
            expected.add(approval.name)
            offset = step + 1
            # Once the next segment is published it already retains the parent.
            write = prepare and not (directory / f"{segment + 1}.outcome.json").exists()
            start = self._resolution_commit(conflict, plan, write=write)
        raise _Unsafe("candidate progress exceeds the admitted series")

    def _check_conflict(self, conflict: TransferConflict) -> Path:
        current, approval, _ = self._observe(prepare=False)
        if current != conflict or approval is None:
            raise _Unsafe("resolution authorization is stale or for another attempt")
        return approval

    def _staged_tree(self, path: Path) -> str:
        if self._command(path, "ls-files", "--unmerged", "-z"):
            raise _Unsafe("candidate index still has unresolved entries")
        if any(record[1:2] != " " or record[:1] in {"?", "!"}
               for record in self._status(path).split("\0") if record):
            raise _Unsafe("candidate contains unstaged, untracked or ignored edits")
        return self._command(path, "write-tree").strip()

    def _resolution_commit(
        self, conflict: TransferConflict, plan: dict, *, write: bool,
    ) -> str:
        tree, justification = plan.get("tree"), plan.get("justification")
        if (not isinstance(tree, str) or not _OID.fullmatch(tree)
                or not isinstance(justification, str) or not justification.strip()):
            raise _Unsafe("invalid resolution authorization evidence")
        self._require_authorized_tree(conflict.path, tree)
        source = plan.get("source_digest")
        if source is not None and source_resolution_digest(conflict.path) != source:
            raise _Interrupted("authorized source changed; candidate retained")
        content = (
            f"tree {tree}\nparent {conflict.parent}\n"
            "author Devlegate Transfer <transfer@devlegate.invalid> 0 +0000\n"
            "committer Devlegate Transfer <transfer@devlegate.invalid> 0 +0000\n\n"
            f"Resolve transfer of {conflict.source_commit}\n\n"
            f"{justification}\n\nTransfer-Attempt: {self.identity.attempt}\n"
        )
        # No ref, HEAD, index or sequencer update: the next scratch checkout
        # takes this exact object as parent. A lost write can repeat safely.
        head = self._command(
            conflict.path, "hash-object", *(("-w",) if write else ()),
            "-t", "commit", "--stdin", input_text=content,
        ).strip()
        if write:
            self._require_authorized_tree(conflict.path, tree)
        return head

    def _require_authorized_tree(self, path: Path, tree: str) -> None:
        try:
            current = self._staged_tree(path)
        except _Unsafe as error:
            raise _Interrupted(f"authorized resolution changed: {error}") from error
        if current != tree:
            raise _Interrupted("authorized resolution tree changed; candidate retained")

    def release(
        self, ready: TransferReady, *,
        require_consumed: Callable[[TransferReady], None],
    ) -> None:
        """Seal downstream custody; remove only fully attributed, unchanged storage."""
        self._require_owner()
        evidence = _receipt(self.released)
        claim = {"identity": asdict(ready.identity), "path": str(ready.path),
                 "head": ready.head, "tree": ready.tree, "effect": ready.effect,
                 "resolved": ready.externally_resolved}
        if evidence is None:
            result, directory, expected = self._observe(
                prepare=False, verify_source=False,
            )
            if result != ready:
                raise _Unsafe("candidate has no matching final checkpoint evidence")
            require_consumed(ready)
            # Unknown scratch content may contain edits. Keep the entire volume
            # (including alternates dependencies) instead of trying to adopt it.
            storage = _read(self.intent)["storage"] if directory and {
                p.name for p in directory.iterdir()
            } == expected else None
            evidence = {"claim": claim, "storage": storage, "entries": sorted(expected)}
            _publish(self.released, evidence)
        if evidence.get("claim") != claim or ready.identity != self.identity:
            raise _Unsafe("release targets a different checkpoint")
        if evidence["storage"] is not None:
            name = evidence["storage"][0]
            directory, grave = self.root / name, self.root / f"{name}.released"
            _safe_path(directory)
            _safe_path(grave)
            if directory.exists():
                # Re-prove on retries before moving a still-visible checkout.
                bound = self._directory(evidence)
                objects = Path(_read(self.intent)["binding"]["objects"])
                commits, start, _ = self._inputs()
                result, _, _ = self._advance(
                    bound, commits, start, objects, prepare=False,
                )
                entries = sorted(p.name for p in bound.iterdir())
                if (result != ready or entries != evidence["entries"]
                        or grave.exists()):
                    raise _Unsafe("release storage has changed; candidate retained")
                bound.rename(grave)
                _sync_directory(self.root)
            if grave.exists():
                shutil.rmtree(self._directory(evidence, released=True))
                _sync_directory(self.root)
