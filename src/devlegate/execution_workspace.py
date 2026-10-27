# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Devlegate-owned per-ticket Git execution workspaces."""

from __future__ import annotations

import dataclasses
import re
import subprocess
from pathlib import Path

from devlegate.commit_messages import checkpoint_message
from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    GitRunner,
    is_ancestor,
    patch_equivalence,
    ref_exists,
    resolve_commit,
    run_git,
)
from devlegate.tickets import is_canonical_ticket_name


class ExecutionWorkspaceError(Exception):
    """Raised when execution workspace topology is unsafe or invalid."""


class ExecutionWorkspaceConflict(ExecutionWorkspaceError):
    """Raised when operator action is required to resolve workspace topology."""


@dataclasses.dataclass(frozen=True)
class ExecutionWorkspace:
    ticket_id: str
    branch: str
    path: Path
    head: str
    base_head: str
    dirty: bool


@dataclasses.dataclass(frozen=True)
class WorkspaceInspection:
    """Read-only classification of a persisted execution workspace binding."""

    classification: str
    reason: str
    workspace: ExecutionWorkspace | None = None


@dataclasses.dataclass(frozen=True)
class ExecutionCheckpoint:
    before_head: str
    after_head: str
    commit_created: bool


type WorktreeRegistration = dict[str, str | None]


_git = run_git


def _default_git_runner(
    cwd: Path, *args: str, **kwargs: object
) -> subprocess.CompletedProcess[str]:
    """Preserve the injectable module seam for standalone callers and tests."""
    return _git(cwd, *args, **kwargs)


class ExecutionWorkspaceManager:
    """Materialize one deterministic worktree without owning worker changes."""

    def __init__(
        self, repo: Path, root: Path, ticket_id: str, *, runner: GitRunner | None = None
    ) -> None:
        if not is_canonical_ticket_name(f"{ticket_id}.md"):
            raise ExecutionWorkspaceError(
                f"invalid ticket ID for execution workspace: {ticket_id}"
            )
        self.repo = repo.resolve()
        self.ticket_id = ticket_id
        self.branch = f"devlegate/work/{ticket_id}"
        # Keep managed components lexical so symlink substitution is observable.
        self.root = root.absolute()
        self.path = self.root / "work" / ticket_id
        # Standalone inspection remains available, while service callers inject
        # their owner- and shutdown-aware Git capability.
        self._runner = _default_git_runner if runner is None else runner

    def prepare(self, base_head: str) -> ExecutionWorkspace:
        self._validate_base(base_head)
        if self.root.is_symlink() or (self.root / "work").is_symlink():
            raise self._path_conflict("has a symlinked workspace root")
        self._ensure_branch(base_head)
        self._validate_branch(base_head)
        registrations = self._registrations()
        branch_path = next(
            (
                path
                for path, item in registrations.items()
                if item.get("branch") == self.branch
            ),
            None,
        )
        expected_path = self.path.resolve()
        if branch_path is not None and branch_path != expected_path:
            raise ExecutionWorkspaceConflict(
                f"execution branch {self.branch} is already attached to unexpected "
                f"worktree {branch_path}"
            )

        if self.path.is_symlink():
            raise self._path_conflict("is a symlink")
        registration = registrations.get(expected_path)
        if registration is not None:
            if not self.path.is_dir():
                self._prune_expected_stale_registration()
            else:
                workspace = self._validate_existing(registration, base_head)
                self._prepare_submodules(workspace.path)
                return workspace
        elif self.path.exists():
            if (self.path / ".git").exists() or (self.path / "HEAD").exists():
                raise self._path_conflict("belongs to another repository")
            raise self._path_conflict(
                "exists but is not a registered execution worktree"
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        created = self._runner(
            self.repo, "worktree", "add", str(self.path), self.branch, check=False
        )
        if created.returncode:
            registrations = self._registrations()
            if registrations.get(expected_path) is not None:
                workspace = self._validate_existing(
                    registrations[expected_path], base_head
                )
                self._prepare_submodules(workspace.path)
                return workspace
            raise ExecutionWorkspaceError(
                f"cannot create execution worktree {self.path}: "
                f"{created.stderr.strip() or 'unknown git error'}"
            )
        registration = self._registrations().get(expected_path)
        if registration is None:
            raise ExecutionWorkspaceError(
                f"created execution worktree {self.path} but Git did not register it"
            )
        workspace = self._validate_existing(registration, base_head)
        self._prepare_submodules(workspace.path)
        return workspace

    def prepare_rework(
        self, base_head: str, previous_base: str, previous_checkpoint: str
    ) -> ExecutionWorkspace:
        """Port a proven linear worker delta onto a newly admitted product base.

        A rework transition can be interrupted after the ticket branch has
        moved but before the cherry-pick completes.  Replay accepts only the
        empty target base or an exact-size, patch-equivalent linear port; an
        arbitrary descendant is never evidence that the old code survived.
        """
        self._validate_base(base_head)
        self._validate_base(previous_base)
        self._validate_base(previous_checkpoint)
        branch_ref = f"refs/heads/{self.branch}"
        current = self._runner(
            self.repo,
            "for-each-ref",
            "--format=%(objectname)",
            branch_ref,
            check=False,
        )
        if current.returncode == 0 and not current.stdout.strip():
            if previous_checkpoint != previous_base:
                raise ExecutionWorkspaceConflict("previous execution branch is missing")
            # A completed no-op may not have published a ticket branch. There
            # is no implementation delta to carry, so create this generation's
            # ordinary branch from its admitted base.
            return self.prepare(base_head)
        if current.returncode or len(current.stdout.splitlines()) != 1:
            raise ExecutionWorkspaceError(
                "cannot observe previous execution branch: "
                f"{current.stderr.strip() or 'unknown git error'}"
            )
        current_head = current.stdout.strip()
        try:
            predecessor = is_ancestor(
                self.repo, previous_base, previous_checkpoint, runner=self._runner
            )
        except GitAncestryError as error:
            raise ExecutionWorkspaceError(
                f"cannot prove previous execution ancestry: {error}"
            ) from error
        if not predecessor:
            raise ExecutionWorkspaceConflict(
                "rework preparation conflict: previous checkpoint is not descended "
                "from its reported base"
            )
        commits = self._runner(
            self.repo,
            "rev-list",
            "--reverse",
            f"{previous_base}..{previous_checkpoint}",
            check=False,
        )
        if commits.returncode:
            raise ExecutionWorkspaceError("cannot inspect previous execution delta")
        delta = [commit for commit in commits.stdout.splitlines() if commit]
        merges = self._runner(
            self.repo,
            "rev-list",
            "--merges",
            f"{previous_base}..{previous_checkpoint}",
            check=False,
        )
        if merges.returncode or merges.stdout.strip():
            raise ExecutionWorkspaceConflict(
                "rework preparation conflict: previous implementation is not a "
                "linear worker delta"
            )
        try:
            already_integrated = is_ancestor(
                self.repo, previous_checkpoint, base_head, runner=self._runner
            )
        except GitAncestryError as error:
            raise ExecutionWorkspaceError(
                "cannot prove whether the previous implementation is integrated: "
                f"{error}"
            ) from error
        if current_head == previous_checkpoint:
            registrations = self._registrations()
            registration = registrations.get(self.path.resolve())
            if registration is None:
                raise ExecutionWorkspaceConflict(
                    "previous execution worktree is missing"
                )
            workspace = self._validate_existing(registration, previous_base)
            if workspace.head != previous_checkpoint or workspace.dirty:
                raise ExecutionWorkspaceConflict(
                    "previous execution worktree is not clean at its reported "
                    "checkpoint"
                )
            self.retire(workspace, previous_checkpoint)
            moved = self._runner(
                self.repo,
                "update-ref",
                branch_ref,
                base_head,
                previous_checkpoint,
                check=False,
            )
            if moved.returncode:
                raise ExecutionWorkspaceConflict(
                    "execution branch changed during rework preparation"
                )
        elif current_head != base_head:
            # A completed replay must contain exactly the old patch series and
            # nothing else.  This rejects a foreign descendant after a crash.
            try:
                replay_ancestry = is_ancestor(
                    self.repo, base_head, current_head, runner=self._runner
                )
            except GitAncestryError as error:
                raise ExecutionWorkspaceError(
                    f"cannot prove replay ancestry: {error}"
                ) from error
            if not replay_ancestry:
                raise ExecutionWorkspaceConflict(
                    "execution branch changed during rework preparation"
                )
            replay = self._runner(
                self.repo,
                "rev-list",
                "--reverse",
                f"{base_head}..{current_head}",
                check=False,
            )
            replay_delta = [commit for commit in replay.stdout.splitlines() if commit]
            try:
                equivalent = patch_equivalence(
                    self.repo,
                    previous_checkpoint,
                    current_head,
                    base_head,
                    runner=self._runner,
                )
            except GitObservationError as error:
                raise ExecutionWorkspaceError(
                    f"cannot prove replay patch equivalence: {error}"
                ) from error
            if (
                replay.returncode
                or len(replay_delta) != len(delta)
                or equivalent != ("-",) * len(delta)
            ):
                raise ExecutionWorkspaceConflict(
                    "rework preparation conflict: execution branch is not the exact "
                    "proven transplanted implementation"
                )
        prepared = self.prepare(base_head)
        if delta and not already_integrated and prepared.head == base_head:
            transplanted = self._runner(
                prepared.path, "cherry-pick", "--no-edit", *delta, check=False
            )
            if transplanted.returncode:
                self._runner(prepared.path, "cherry-pick", "--abort", check=False)
                raise ExecutionWorkspaceConflict(
                    "rework preparation conflict: previous checkpoint cannot be "
                    "applied to the new product base"
                )
            registration = self._registrations().get(self.path.resolve())
            prepared = self._validate_existing(registration, base_head)
        if delta and not already_integrated and prepared.head == base_head:
            raise ExecutionWorkspaceConflict(
                "rework preparation conflict: previous implementation was not ported"
            )
        return prepared

    def inspect(
        self, base_head: str, expected_head: str | None = None
    ) -> WorkspaceInspection:
        """Inspect resume topology without creating, removing, or moving Git state."""
        try:
            self._validate_base(base_head)
            registrations = self._registrations()
            if not self._branch_exists():
                return WorkspaceInspection("RECOVERABLE", "execution branch is missing")
            try:
                self._validate_branch(base_head)
            except ExecutionWorkspaceError as error:
                return WorkspaceInspection("RECOVERABLE", str(error))
            expected_path = self.path.resolve()
            branch_path = next(
                (
                    path
                    for path, item in registrations.items()
                    if item.get("branch") == self.branch
                ),
                None,
            )
            if branch_path is not None and branch_path != expected_path:
                return WorkspaceInspection(
                    "UNSAFE", f"execution branch is attached to {branch_path}"
                )
            registration = registrations.get(expected_path)
            if registration is None or not self.path.is_dir():
                if self.path.exists():
                    return WorkspaceInspection(
                        "UNSAFE", "execution worktree path is not a registered worktree"
                    )
                return WorkspaceInspection(
                    "RECOVERABLE", "execution worktree is missing"
                )
            workspace = self._validate_existing(registration, base_head)
            if expected_head is not None and workspace.head != expected_head:
                return WorkspaceInspection(
                    "UNSAFE",
                    "execution worktree HEAD does not match persisted start HEAD",
                )
            self.verify_submodules(workspace)
            if workspace.dirty:
                return WorkspaceInspection("UNSAFE", "execution worktree is dirty")
            return WorkspaceInspection(
                "REUSABLE", "persisted execution workspace is reusable", workspace
            )
        except (ExecutionWorkspaceError, OSError, subprocess.SubprocessError) as error:
            return WorkspaceInspection("UNSAFE", str(error))

    def verify_submodules(self, workspace: ExecutionWorkspace) -> None:
        """Verify immutable submodules after worker execution."""
        # A worker normally leaves intentional superproject edits. Validate its
        # identity here without duplicating the caller's checkpoint cleanliness
        # decision; submodule verification itself is still strict.
        self._validate_workspace(workspace, verify_status=False)
        self._verify_submodules(workspace.path)

    def retire(self, workspace: ExecutionWorkspace, expected_head: str) -> None:
        """Remove one validated, clean execution worktree registration."""
        if workspace.ticket_id != self.ticket_id or workspace.branch != self.branch:
            raise ExecutionWorkspaceError("execution workspace identity is invalid")
        if workspace.path != self.path or workspace.head != expected_head:
            raise ExecutionWorkspaceError("execution workspace checkpoint is invalid")
        if workspace.dirty:
            raise ExecutionWorkspaceError("execution worktree is dirty")
        if self.root.is_symlink() or (self.root / "work").is_symlink():
            raise self._path_conflict("has a symlinked workspace root")
        if self.path.is_symlink():
            raise self._path_conflict("is a symlink")
        registrations = self._registrations()
        branch_path = next(
            (
                path
                for path, item in registrations.items()
                if item.get("branch") == self.branch
            ),
            None,
        )
        if branch_path is not None and branch_path != self.path.resolve():
            raise self._path_conflict(
                f"branch is attached to unexpected worktree {branch_path}"
            )
        registration = registrations.get(self.path.resolve())
        if registration is None:
            if self.path.exists():
                raise self._path_conflict("is not a registered execution worktree")
            return
        validated = self._validate_existing(registration, workspace.base_head)
        if validated.head != expected_head or validated.dirty:
            raise ExecutionWorkspaceError("execution worktree changed before removal")
        self._validate_submodule_paths(self.path)
        self._verify_submodules(self.path)
        removed = self._runner(
            self.repo, "worktree", "remove", "--force", str(self.path), check=False
        )
        if removed.returncode:
            raise ExecutionWorkspaceError(
                f"cannot remove execution worktree {self.path}: "
                f"{removed.stderr.strip() or 'unknown git error'}"
            )
        if self.path.exists() or self.path.resolve() in self._registrations():
            raise ExecutionWorkspaceError(
                f"execution worktree {self.path} remains after removal"
            )

    def _prepare_submodules(self, path: Path) -> None:
        try:
            self._validate_submodule_paths(path)
            self._observe_initialized_submodules(path)
            updated = self._runner(
                path, "submodule", "update", "--init", "--recursive", check=False
            )
            if updated.returncode:
                raise ExecutionWorkspaceError(
                    "submodule materialization failed: "
                    f"{updated.stderr.strip() or 'unknown git error'}"
                )
            self._verify_submodules(path)
        except ExecutionWorkspaceError:
            raise
        except (OSError, subprocess.SubprocessError) as error:
            raise ExecutionWorkspaceError(
                f"cannot prepare execution submodules: {error}"
            ) from error

    def _validate_submodule_paths(self, repo: Path) -> None:
        for path in self._gitlink_paths(repo):
            candidate = repo / path
            current = repo
            for component in path.parts:
                current /= component
                if current.is_symlink():
                    raise ExecutionWorkspaceError(
                        "submodule path is unsafe because it contains a symlink: "
                        f"{path}"
                    )
            if candidate.exists() and not candidate.is_dir():
                raise ExecutionWorkspaceError(
                    f"submodule path is not a directory: {path}"
                )

    def _observe_initialized_submodules(self, repo: Path) -> None:
        result = self._runner(repo, "submodule", "status", "--recursive", check=False)
        if result.returncode:
            # No .gitmodules is a valid repository without submodules. Other
            # failures are observations that cannot safely be treated as empty.
            if not (repo / ".gitmodules").exists() and not self._gitlink_paths(repo):
                return
            raise ExecutionWorkspaceError(
                f"cannot observe submodule state: "
                f"{result.stderr.strip() or 'unknown git error'}"
            )
        for line in result.stdout.splitlines():
            if len(line) < 43 or line[0] not in " +-U":
                raise ExecutionWorkspaceError("malformed submodule status")
            fields = line[1:].split(" ", 1)
            if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
                raise ExecutionWorkspaceError("malformed submodule status")
            if line[0] in "+U":
                raise ExecutionWorkspaceError(
                    f"submodule {fields[1]} is initialized at an unexpected state"
                )
        for path in self._gitlink_paths(repo):
            candidate = repo / path
            if not candidate.is_dir():
                continue
            initialized = self._runner(candidate, "rev-parse", "--git-dir", check=False)
            initialized_root = self._runner(
                candidate, "rev-parse", "--show-toplevel", check=False
            )
            if (
                initialized.returncode
                or initialized_root.returncode
                or Path(initialized_root.stdout.strip()).resolve()
                != candidate.resolve()
            ):
                if any(candidate.iterdir()):
                    raise ExecutionWorkspaceError(
                        f"submodule {path} has an unsafe existing checkout"
                    )
                continue
            head = self._runner(candidate, "rev-parse", "HEAD", check=False)
            status = self._runner(candidate, "status", "--porcelain", check=False)
            expected = self._expected_gitlink(repo, path)
            if head.returncode or status.returncode:
                raise ExecutionWorkspaceError(
                    f"cannot observe submodule {path}; dependency integrity cannot "
                    "be proven"
                )
            if head.stdout.strip() != expected or status.stdout:
                raise ExecutionWorkspaceError(
                    f"submodule {path} is dirty or has unexpected HEAD "
                    f"(head={head.stdout.strip()}, expected={expected}, "
                    f"status={status.stdout!r}); preserving evidence"
                )
            self._observe_initialized_submodules(candidate)

    def _gitlink_paths(self, repo: Path) -> list[Path]:
        result = self._runner(repo, "ls-tree", "-r", "-z", "HEAD")
        paths: list[Path] = []
        for entry in result.stdout.split("\0"):
            if not entry:
                continue
            metadata, separator, name = entry.partition("\t")
            fields = metadata.split()
            if separator and len(fields) == 3 and fields[0] == "160000":
                path = Path(name)
                if path.is_absolute() or ".." in path.parts or "." in path.parts:
                    raise ExecutionWorkspaceError(
                        f"unsafe submodule path in execution superproject: {name}"
                    )
                paths.append(path)
        return paths

    def _verify_submodules(self, repo: Path) -> None:
        for path in self._gitlink_paths(repo):
            candidate = repo / path
            if candidate.is_symlink() or not candidate.is_dir():
                raise ExecutionWorkspaceError(
                    f"submodule {path} is not initialized safely"
                )
            expected_sha = self._expected_gitlink(repo, path)
            head = self._runner(candidate, "rev-parse", "HEAD", check=False)
            root = self._runner(candidate, "rev-parse", "--show-toplevel", check=False)
            status = self._runner(candidate, "status", "--porcelain", check=False)
            if (
                head.returncode
                or root.returncode
                or Path(root.stdout.strip()).resolve() != candidate.resolve()
                or status.returncode
            ):
                raise ExecutionWorkspaceError(
                    f"cannot verify submodule {path}; dependency integrity cannot "
                    "be proven"
                )
            if head.stdout.strip() != expected_sha:
                raise ExecutionWorkspaceError(
                    f"submodule {path} has unexpected HEAD; expected {expected_sha}"
                )
            if status.stdout:
                raise ExecutionWorkspaceError(
                    f"submodule {path} is dirty; dependency integrity cannot be proven"
                )
            self._verify_submodules(candidate)

    def _expected_gitlink(self, repo: Path, path: Path) -> str:
        output = self._runner(repo, "ls-tree", "-r", "-z", "HEAD").stdout
        for entry in output.split("\0"):
            metadata, separator, name = entry.partition("\t")
            fields = metadata.split()
            if separator and name == str(path) and len(fields) == 3:
                if fields[0] != "160000" or not re.fullmatch(
                    r"[0-9a-f]{40}", fields[2]
                ):
                    break
                return fields[2]
        raise ExecutionWorkspaceError(f"cannot observe gitlink for submodule {path}")

    def checkpoint(
        self,
        workspace: ExecutionWorkspace,
        execution_id: str,
        *,
        title: str = "",
        summary: str = "",
    ) -> ExecutionCheckpoint:
        self._validate_workspace(workspace, verify_status=False)
        before_head = self._runner(workspace.path, "rev-parse", "HEAD").stdout.strip()
        if before_head != workspace.head:
            raise ExecutionWorkspaceError(
                "execution Git history changed while worker was running"
            )
        self._runner(workspace.path, "add", "-A")
        staged = self._runner(
            workspace.path, "diff", "--cached", "--quiet", check=False
        )
        if staged.returncode not in {0, 1}:
            raise ExecutionWorkspaceError(
                f"cannot inspect staged execution changes: {staged.stderr.strip()}"
            )
        commit_created = staged.returncode == 1
        if commit_created:
            committed = self._runner(
                workspace.path,
                "commit",
                "-m",
                checkpoint_message(self.ticket_id, title, execution_id, summary),
                check=False,
            )
            if committed.returncode:
                raise ExecutionWorkspaceError(
                    f"cannot create execution checkpoint: "
                    f"{committed.stderr.strip() or 'unknown git error'}"
                )
        after_head = self._runner(workspace.path, "rev-parse", "HEAD").stdout.strip()
        return ExecutionCheckpoint(before_head, after_head, commit_created)

    def _validate_workspace(
        self, workspace: ExecutionWorkspace, *, verify_status: bool = True
    ) -> ExecutionWorkspace:
        if not isinstance(workspace, ExecutionWorkspace):
            raise TypeError("execution workspace must be ExecutionWorkspace")
        if (
            workspace.ticket_id != self.ticket_id
            or workspace.branch != self.branch
            or workspace.path != self.path
        ):
            raise ExecutionWorkspaceError("execution workspace binding is invalid")
        if (
            self.root.is_symlink()
            or (self.root / "work").is_symlink()
            or self.path.is_symlink()
        ):
            raise self._path_conflict("is a symlink")
        registrations = self._registrations()
        registration = registrations.get(self.path.resolve())
        return self._validate_existing(
            registration, workspace.base_head, verify_status=verify_status
        )

    def _validate_base(self, base_head: str) -> None:
        try:
            resolved = resolve_commit(
                self.repo, base_head, check=False, runner=self._runner
            )
        except GitObservationError as error:
            raise ExecutionWorkspaceError(
                f"cannot observe planned code revision {base_head}: {error}"
            ) from error
        if resolved != base_head:
            raise ExecutionWorkspaceError(
                f"planned code revision {base_head} is not available in the "
                "product repository"
            )

    def _ensure_branch(self, base_head: str) -> None:
        if self._branch_exists():
            return
        created = self._runner(self.repo, "branch", self.branch, base_head, check=False)
        if created.returncode:
            if not self._branch_exists():
                raise ExecutionWorkspaceError(
                    f"cannot create execution branch {self.branch}: "
                    f"{created.stderr.strip() or 'unknown git error'}"
                )

    def _branch_exists(self) -> bool:
        try:
            return ref_exists(
                self.repo, f"refs/heads/{self.branch}", runner=self._runner
            )
        except GitObservationError as error:
            raise ExecutionWorkspaceError(
                f"cannot observe execution branch {self.branch}: {error}"
            ) from error

    def _validate_branch(self, base_head: str) -> None:
        try:
            ancestor = is_ancestor(
                self.repo, base_head, self.branch, runner=self._runner
            )
        except GitAncestryError as error:
            raise ExecutionWorkspaceError(
                f"cannot prove execution branch ancestry: {error}"
            ) from error
        if not ancestor:
            raise ExecutionWorkspaceConflict(
                f"execution branch {self.branch} is unrelated to planned code "
                f"revision {base_head}"
            )

    @staticmethod
    def _parse_worktree_porcelain(output: str) -> dict[Path, WorktreeRegistration]:
        entries: dict[Path, WorktreeRegistration] = {}
        current: WorktreeRegistration = {}
        seen: set[str] = set()

        def finish() -> None:
            if not current:
                return
            if "path" not in current or "head" not in current:
                raise ExecutionWorkspaceError("unexpected Git worktree metadata")
            if (
                "branch" not in current
                and "detached" not in current
                and "bare" not in current
            ):
                raise ExecutionWorkspaceError("unexpected Git worktree metadata")
            if "branch" in current and "detached" in current:
                raise ExecutionWorkspaceError("unexpected Git worktree metadata")
            path = Path(current["path"] or "").resolve()
            if path in entries:
                raise ExecutionWorkspaceError(
                    f"duplicate Git worktree registration: {path}"
                )
            entries[path] = current.copy()

        for line in output.splitlines() + [""]:
            if not line:
                finish()
                current = {}
                seen = set()
                continue
            key, separator, value = line.partition(" ")
            if key in seen:
                raise ExecutionWorkspaceError(
                    f"duplicate Git worktree metadata field: {key}"
                )
            seen.add(key)
            if key in {"detached", "bare", "locked"} and not separator:
                current[key] = ""
                continue
            if not separator or key not in {
                "worktree",
                "HEAD",
                "branch",
                "locked",
                "prunable",
            }:
                raise ExecutionWorkspaceError(
                    f"unexpected Git worktree metadata: {line}"
                )
            if key == "worktree":
                if not value:
                    raise ExecutionWorkspaceError("unexpected Git worktree metadata")
                current["path"] = value
            elif key == "HEAD":
                if not value:
                    raise ExecutionWorkspaceError("unexpected Git worktree metadata")
                current["head"] = value
            elif key == "branch":
                if not value.startswith("refs/heads/") or not value[11:]:
                    raise ExecutionWorkspaceError(
                        f"unexpected Git worktree branch metadata: {line}"
                    )
                current["branch"] = value[11:]
        return entries

    def _registrations(self) -> dict[Path, WorktreeRegistration]:
        result = self._runner(self.repo, "worktree", "list", "--porcelain", check=False)
        if result.returncode:
            raise ExecutionWorkspaceError(
                "cannot observe execution worktree registrations: "
                f"{result.stderr.strip() or 'unknown git error'}"
            )
        return self._parse_worktree_porcelain(result.stdout)

    def _validate_existing(
        self,
        registration: dict[str, str | None] | None,
        base_head: str,
        *,
        verify_status: bool = True,
    ) -> ExecutionWorkspace:
        if registration is None or not self.path.is_dir():
            raise ExecutionWorkspaceError(
                f"expected execution worktree {self.path} is not registered and "
                "cannot be reused"
            )
        common = self._runner(self.path, "rev-parse", "--git-common-dir", check=False)
        root = self._runner(self.path, "rev-parse", "--show-toplevel", check=False)
        expected_common = self._runner(
            self.repo, "rev-parse", "--git-common-dir"
        ).stdout.strip()

        def resolve_git_path(directory: Path, value: str) -> Path:
            path = Path(value)
            if not path.is_absolute():
                return (directory / path).resolve()
            return path.resolve()

        if (
            common.returncode
            or resolve_git_path(self.path, common.stdout.strip())
            != resolve_git_path(self.repo, expected_common)
            or root.returncode
            or resolve_git_path(self.path, root.stdout.strip()) != self.path.resolve()
        ):
            raise self._path_conflict("belongs to another repository")
        branch = self._runner(
            self.path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        if branch.returncode or branch.stdout.strip() != self.branch:
            raise self._path_conflict(
                f"is on branch {branch.stdout.strip() or '<detached>'}"
            )
        head = self._runner(self.path, "rev-parse", "HEAD").stdout.strip()
        if head != registration["head"]:
            raise ExecutionWorkspaceError(
                f"execution worktree {self.path} registration HEAD does not "
                "match its checkout"
            )
        if registration["branch"] != self.branch:
            raise self._path_conflict("has the wrong registered branch")
        status = ""
        if verify_status:
            observed_status = self._runner(
                self.path, "status", "--porcelain", check=False
            )
            if observed_status.returncode:
                raise ExecutionWorkspaceError(
                    "cannot observe execution worktree status: "
                    f"{observed_status.stderr.strip() or 'unknown git error'}"
                )
            status = observed_status.stdout
        return ExecutionWorkspace(
            self.ticket_id,
            self.branch,
            self.path,
            head,
            base_head,
            bool(status),
        )

    def _prune_expected_stale_registration(self) -> None:
        # `worktree remove` removes only this missing registration, unlike prune.
        removed = self._runner(
            self.repo, "worktree", "remove", str(self.path), check=False
        )
        if removed.returncode:
            raise ExecutionWorkspaceError(
                f"cannot remove stale execution worktree registration {self.path}: "
                f"{removed.stderr.strip() or 'unknown git error'}"
            )

    def _path_conflict(self, observed: str) -> ExecutionWorkspaceConflict:
        return ExecutionWorkspaceConflict(
            f"expected execution worktree {self.path} for ticket {self.ticket_id} "
            f"and branch {self.branch} {observed}"
        )


__all__ = [
    "ExecutionCheckpoint",
    "ExecutionWorkspaceConflict",
    "ExecutionWorkspace",
    "ExecutionWorkspaceError",
    "ExecutionWorkspaceManager",
    "WorkspaceInspection",
    "WorktreeRegistration",
    "parse_worktree_porcelain",
]


def parse_worktree_porcelain(output: str) -> dict[Path, WorktreeRegistration]:
    """Parse Git's stable worktree list format, rejecting ambiguous records."""
    return ExecutionWorkspaceManager._parse_worktree_porcelain(output)
