# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Generation evidence; observations are information, never mutation authority.

See docs/resource-identity.md for the trusted-filesystem boundary and the
deliberately unproved remote continuity claim. All Git IO uses the supplied
capability. This module never owns an owner lock or a runtime store.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path
from typing import TypedDict

from devlegate.execution_workspace import (
    ExecutionWorkspace,
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.git import GitObservationError, GitRunner, remote_ref, resolve_ref_commit

_OPERATIONS = (
    "worker", "checkpoint", "publication", "rewrite", "rollback", "rewrite-publication",
    "predecessor-publication",
    "force-checkpoint",
)


class UnprovenResource(ExecutionWorkspaceError):
    """Available independent evidence cannot prove an observation or effect."""

    def __init__(self, reason: str, *, local_proved: bool = False) -> None:
        super().__init__(reason)
        self.local_proved = local_proved


@dataclasses.dataclass(frozen=True)
class ResourceProof:
    ticket_id: str
    execution_id: str
    generation: str
    state: str
    reason: str
    local_state: str = "unproven"
    upstream_state: str = "unproven"
    upstream_observed: str | None = None
    upstream_exact: bool = False
    path: str = ""
    common_dir: str = ""
    local_ref: str = ""
    upstream_ref: str = ""


class _ProofIdentity(TypedDict):
    ticket_id: str
    execution_id: str
    generation: str
    path: str
    common_dir: str
    local_ref: str
    upstream_ref: str


def _safe_path(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ExecutionWorkspaceError(f"symlink in resource evidence path: {path}")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_once(path: Path, value: dict[str, object]) -> None:
    """An interrupted exclusive write remains evidence, never a retry target."""
    _safe_path(path)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        _sync_directory(path.parent)


def _read(path: Path) -> dict[str, object]:
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, encoding="ascii") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ExecutionWorkspaceError(f"not a regular evidence file: {path}")
        try:
            value = json.load(stream)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ExecutionWorkspaceError(
                f"invalid independent resource evidence at {path}: {error}"
            ) from error
    if not isinstance(value, dict):
        raise ExecutionWorkspaceError(f"invalid resource evidence: {path}")
    return value


def _identity(path: Path) -> list[int]:
    _safe_path(path)
    metadata = path.stat()
    return [metadata.st_dev, metadata.st_ino]


class ResourceBinding:
    """One immutable intent and independent creation receipt per generation."""

    def __init__(
        self, manager: ExecutionWorkspaceManager, intent: dict[str, object]
    ) -> None:
        self.manager = manager
        self.intent = intent
        token = intent.get("generation")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ExecutionWorkspaceError("invalid resource generation token")
        strings = (
            "ticket_id", "execution_id", "base", "control", "ticket_blob", "common",
            "path", "ref", "remote", "remote_ref",
        )
        if (
            type(intent.get("version")) is not int
            or intent["version"] != 1
            or any(
                not isinstance(intent.get(key), str) or not intent[key]
                for key in strings
            )
            or not isinstance(intent.get("prior_path_exists"), bool)
            or "prior_registration" not in intent
            or not isinstance(intent["prior_registration"], (dict, type(None)))
        ):
            raise ExecutionWorkspaceError(
                "resource creation intent is incomplete or invalid"
            )
        for key in ("base", "control", "ticket_blob", "prior_local", "prior_remote"):
            value = intent.get(key)
            if key not in intent or (
                value is not None and (
                    not isinstance(value, str)
                    or not re.fullmatch(r"[0-9a-f]{40}", value)
                )
            ):
                raise ExecutionWorkspaceError(f"resource intent has invalid {key}")
        for key in ("remote_urls", "push_urls"):
            urls = intent.get(key)
            if (
                not isinstance(urls, list) or len(urls) != 1
                or not isinstance(urls[0], str) or not urls[0]
            ):
                raise ExecutionWorkspaceError(
                    "resource intent lacks an exact upstream endpoint"
                )
        if intent["remote_urls"] != intent["push_urls"]:
            raise ExecutionWorkspaceError(
                "resource intent has different upstream endpoints"
            )
        if not all(Path(str(intent[key])).is_absolute() for key in ("path", "common")):
            raise ExecutionWorkspaceError("resource intent paths must be absolute")
        self.directory = manager.root / "bindings" / token

    @classmethod
    def for_execution(
        cls, manager: ExecutionWorkspaceManager, execution_id: str
    ) -> ResourceBinding:
        """Locate independently recorded evidence, without inventing old bindings."""
        root = manager.root / "bindings"
        _safe_path(root)
        matches = []
        for path in root.glob("*/intent.json"):
            intent = _read(path)
            if (
                intent.get("execution_id") == execution_id
                and intent.get("ticket_id") == manager.ticket_id
            ):
                binding = cls(manager, intent)
                if path.parent != binding.directory:
                    raise ExecutionWorkspaceError(
                        "generation evidence stored at wrong path"
                    )
                matches.append(binding)
        if len(matches) != 1:
            raise ExecutionWorkspaceError(
                "execution has no unique independent generation intent"
            )
        return matches[0]

    @classmethod
    def plan(
        cls,
        manager: ExecutionWorkspaceManager,
        *,
        execution_id: str,
        base: str,
        control: str,
        ticket_blob: str,
        remote: str,
        remote_head: str | None,
    ) -> ResourceBinding:
        """Read preconditions only; caller must commit intent before materialize."""
        _safe_path(manager.path)
        common = manager._runner(
            manager.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
        ancestor = manager.root
        while not ancestor.exists():
            ancestor = ancestor.parent
        if ancestor.stat().st_dev != Path(common).stat().st_dev:
            raise ExecutionWorkspaceError(
                "generation witnesses require the workspace and Git common "
                "directory on the same hard-link-capable filesystem"
            )
        local = resolve_ref_commit(
            manager.repo, f"refs/heads/{manager.branch}", runner=manager._runner
        )
        registrations = manager._registrations()
        prior_registration = registrations.get(manager.path.resolve())
        remote_urls = manager._runner(
            manager.repo, "remote", "get-url", "--all", remote
        ).stdout.splitlines()
        push_urls = manager._runner(
            manager.repo, "remote", "get-url", "--push", "--all", remote
        ).stdout.splitlines()
        if len(remote_urls) != 1 or push_urls != remote_urls:
            raise ExecutionWorkspaceError(
                "resource identity requires one exactly observed fetch/push endpoint"
            )
        # Exact prior state is recorded, but it is not permission to adopt it.
        return cls(manager, {
            "version": 1,
            "generation": secrets.token_hex(32),
            "ticket_id": manager.ticket_id,
            "execution_id": execution_id,
            "base": base,
            "control": control,
            "ticket_blob": ticket_blob,
            "common": common,
            "path": str(manager.path),
            "ref": f"refs/heads/{manager.branch}",
            "remote": remote,
            "remote_urls": remote_urls,
            "push_urls": push_urls,
            "remote_ref": f"refs/heads/{manager.branch}",
            "prior_local": local,
            "prior_remote": remote_head,
            "prior_path_exists": manager.path.exists(),
            "prior_registration": prior_registration,
        })

    def record_intent(self) -> None:
        """Called only after SQLite admission commits, under the service lock."""
        _safe_path(self.directory)
        self.directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _sync_directory(self.directory.parent.parent)
        self.directory.mkdir(mode=0o700)
        _sync_directory(self.directory.parent)
        _write_once(self.directory / "intent.json", self.intent)

    def _validate_intent(self) -> None:
        if _read(self.directory / "intent.json") != self.intent:
            raise ExecutionWorkspaceError(
                "independent creation intent differs from admission"
            )
        manager = self.manager
        if (
            self.intent.get("ticket_id") != manager.ticket_id
            or self.intent.get("path") != str(manager.path)
            or self.intent.get("ref") != f"refs/heads/{manager.branch}"
        ):
            raise ExecutionWorkspaceError(
                "resource intent has mismatched ticket/path/ref"
            )
        common = manager._runner(
            manager.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
        if common != self.intent.get("common"):
            raise ExecutionWorkspaceError(
                "resource intent has mismatched Git common directory"
            )
        for field, options in (
            ("remote_urls", ("--all",)), ("push_urls", ("--push", "--all")),
        ):
            result = manager._runner(
                manager.repo, "remote", "get-url", *options,
                str(self.intent["remote"]), check=False,
            )
            if (
                result.returncode
                or result.stdout.splitlines() != self.intent.get(field)
            ):
                raise ExecutionWorkspaceError(
                    "bound upstream endpoint changed or is missing"
                )

    def creation_pending(self) -> bool:
        """An untouched admission may prepare; an interrupted effect may not."""
        if self.intent_pending():
            return True
        self._validate_intent()
        if (self.directory / "receipt.json").exists():
            return False
        if any(path.name != "intent.json" for path in self.directory.iterdir()):
            raise UnprovenResource("creation effect is uncertain: receipt missing")
        if (
            self.manager.path.exists() != self.intent["prior_path_exists"]
            or resolve_ref_commit(
                self.manager.repo, str(self.intent["ref"]), runner=self.manager._runner
            ) != self.intent["prior_local"]
            or self.manager._registrations().get(self.manager.path.resolve())
            != self.intent["prior_registration"]
        ):
            raise ExecutionWorkspaceError(
                "creation preconditions changed since admission"
            )
        return True

    def intent_pending(self) -> bool:
        """SQLite intent can precede its independent WAL, never any Git effect.

        Only an absent evidence directory and wholly absent resources permit
        finishing that ordering step. A partial directory or any resource blocks.
        """
        _safe_path(self.directory)
        _safe_path(self.manager.path)
        return (
            not self.directory.exists()
            and self.intent["prior_local"] is None
            and self.intent["prior_remote"] is None
            and self.intent["prior_registration"] is None
            and not self.intent["prior_path_exists"]
            and not self.manager.path.exists()
            and not self.manager._branch_exists()
            and not any(
                item.get("branch") == self.manager.branch
                or path == self.manager.path.resolve()
                for path, item in self.manager._registrations().items()
            )
        )

    def _attempts(self) -> list[tuple[int, str, Path]]:
        """Read the immutable, globally ordered operation journal."""
        attempts = []
        for path in self.directory.glob("*-intent.json"):
            operation = path.name.removesuffix("-intent.json").split(".")[0]
            if operation not in _OPERATIONS:
                continue
            intent = _read(path)
            sequence = intent.get("sequence")
            if (
                type(sequence) is not int or sequence < 1
                or intent.get("generation") != self.intent["generation"]
                or intent.get("operation") != operation
            ):
                raise ExecutionWorkspaceError("invalid operation journal identity")
            attempts.append((sequence, operation, path))
        attempts.sort()
        if [item[0] for item in attempts] != list(range(1, len(attempts) + 1)):
            raise ExecutionWorkspaceError("operation journal has a gap or duplicate")
        seen = set()
        for sequence, operation, path in attempts:
            name = operation if operation not in seen else f"{operation}.{sequence:08d}"
            if path.name != f"{name}-intent.json":
                raise ExecutionWorkspaceError(
                    "operation journal path differs from stage"
                )
            seen.add(operation)
        for path in self.directory.glob("*-receipt.json"):
            operation = path.name.removesuffix("-receipt.json").split(".")[0]
            if operation in _OPERATIONS and not path.with_name(
                path.name.replace("-receipt", "-intent")
            ).exists():
                raise UnprovenResource(
                    f"{operation} outcome is unknown: independent intent missing"
                )
        return attempts

    def _receipt(self, path: Path) -> dict[str, object]:
        intent = _read(path)
        receipt = _read(path.with_name(path.name.replace("-intent", "-receipt")))
        if (
            receipt.get("generation") != self.intent["generation"]
            or any(intent[key] != receipt.get(key) for key in intent)
        ):
            raise ExecutionWorkspaceError("operation evidence has mismatched identity")
        return receipt

    def _require_settled(self) -> None:
        for _sequence, operation, path in self._attempts():
            try:
                self._receipt(path)
            except FileNotFoundError as error:
                raise UnprovenResource(
                    f"{operation} effect is uncertain: independent receipt missing",
                    local_proved=True,
                ) from error

    def begin_operation(self, operation: str, details: dict[str, object]) -> None:
        """Reserve a unique stage; never reuse or overwrite an uncertain attempt."""
        if operation not in _OPERATIONS:
            raise ValueError("invalid resource operation")
        self._validate_intent()
        self._require_settled()
        attempts = self._attempts()
        sequence = len(attempts) + 1
        name = operation if not any(item[1] == operation for item in attempts) else (
            f"{operation}.{sequence:08d}"
        )
        _write_once(self.directory / f"{name}-intent.json", {
            **details, "generation": self.intent["generation"],
            "sequence": sequence, "operation": operation,
        })

    def finish_operation(self, operation: str, details: dict[str, object]) -> None:
        if operation not in _OPERATIONS:
            raise ValueError("invalid resource operation")
        try:
            path = [item[2] for item in self._attempts() if item[1] == operation][-1]
            intent = _read(path)
        except IndexError as error:
            raise UnprovenResource(
                f"{operation} outcome is unknown: independent intent missing"
            ) from error
        identity = details.get("identity")
        if operation == "worker" and isinstance(identity, dict):
            if identity.get("execution_id") != self.intent["execution_id"]:
                raise ExecutionWorkspaceError(
                    "worker receipt has wrong execution identity"
                )
        if operation == "rewrite" and details.get("returncode", 0) != 0:
            self._validate_local_identity()
            if details.get("after") != intent.get("before"):
                raise ExecutionWorkspaceError(
                    "failed rebase changed the bound branch; outcome is uncertain"
                )
            if details.get("after") != resolve_ref_commit(
                self.manager.repo, str(self.intent["ref"]), runner=self.manager._runner
            ):
                raise ExecutionWorkspaceError("rebase branch changed before receipt")
        elif operation in {"checkpoint", "rewrite", "rollback", "force-checkpoint"}:
            workspace = self._observe_local(clean=True)
            if workspace.head != details.get("after"):
                raise ExecutionWorkspaceError(
                    "local ref changed before operation receipt"
                )
        if any(details[key] != intent[key] for key in details.keys() & intent.keys()):
            raise ExecutionWorkspaceError("operation result differs from its intent")
        _write_once(path.with_name(path.name.replace("-intent", "-receipt")), {
            **intent, **details,
        })

    def operation_receipt(self, operation: str) -> dict[str, object]:
        if operation not in _OPERATIONS:
            raise ValueError("invalid resource operation")
        try:
            path = [item[2] for item in self._attempts() if item[1] == operation][-1]
            return self._receipt(path)
        except (FileNotFoundError, IndexError) as error:
            raise UnprovenResource(
                f"{operation} outcome is unknown: independent intent or receipt missing"
            ) from error

    def operation_pending(self, operation: str) -> bool:
        attempts = [item[2] for item in self._attempts() if item[1] == operation]
        return bool(attempts) and not attempts[-1].with_name(
            attempts[-1].name.replace("-intent", "-receipt")
        ).exists()

    def record_worker_retirement(self) -> None:
        """Record the supervisor's observed group retirement before post-worker."""
        receipt = self.operation_receipt("worker")
        _write_once(
            self.directory / f"worker-retired.{receipt['sequence']:08d}.json",
            {"generation": self.intent["generation"], "worker": receipt},
        )

    def require_worker_retirement(self) -> None:
        receipt = self.operation_receipt("worker")
        try:
            observed = _read(
                self.directory / f"worker-retired.{receipt['sequence']:08d}.json"
            )
        except FileNotFoundError as error:
            raise UnprovenResource(
                "worker group retirement is unproven: independent receipt missing",
                local_proved=True,
            ) from error
        if observed != {"generation": self.intent["generation"], "worker": receipt}:
            raise ExecutionWorkspaceError(
                "worker retirement receipt differs from handoff"
            )

    def latest_remote(self, initial: str | None) -> str | None:
        self._require_settled()
        for _sequence, operation, path in reversed(self._attempts()):
            if operation in {
                "rewrite-publication", "publication", "predecessor-publication",
            }:
                value = self._receipt(path).get("after")
                if not isinstance(value, str):
                    raise ExecutionWorkspaceError(
                        "invalid independent publication receipt"
                    )
                return value
        return initial

    def abandonment_remote_authority(self, seen: set[str] | None = None) -> str | None:
        """Prove operational authority, not impossible remote ABA continuity.

        Walk independent generation handoffs back to a receipted create. A
        matching initial remote observation alone is never creation authority.
        This deliberately does not change normal integration cleanup policy.
        """
        self._validate_intent()
        self._require_settled()
        self.require_worker_retirement()
        seen = set() if seen is None else set(seen)
        token = str(self.intent["generation"])
        if token in seen:
            raise ExecutionWorkspaceError("remote generation handoff cycle")
        if len(seen) >= 128:
            raise ExecutionWorkspaceError(
                "remote generation handoff exceeds 128 links; manual authority required"
            )
        seen.add(token)
        current = self.intent["prior_remote"]
        if current is not None:
            predecessor = None
            for name in ("rework-started.json", "handoff-intent.json"):
                path = self.directory / name
                if path.exists():
                    predecessor = _read(path).get("predecessor")
            if not isinstance(predecessor, str) or not re.fullmatch(
                r"[0-9a-f]{64}", predecessor
            ):
                raise ExecutionWorkspaceError(
                    "remote creation has no independent generation handoff receipt"
                )
            prior = ResourceBinding(
                self.manager, _read(self.directory.parent / predecessor / "intent.json")
            )
            if prior.intent["generation"] != predecessor:
                raise ExecutionWorkspaceError("remote predecessor generation differs")
            if prior.abandonment_remote_authority(seen) != current:
                raise ExecutionWorkspaceError("remote predecessor receipt differs")
        for _sequence, operation, path in self._attempts():
            if operation in {
                "publication", "rewrite-publication", "predecessor-publication",
            }:
                receipt = self._receipt(path)
                if receipt.get("before") != current or not isinstance(
                    receipt.get("after"), str
                ):
                    raise ExecutionWorkspaceError("remote publication chain differs")
                current = receipt["after"]
        return current

    def prepare(self, *, remote_head: str | None) -> ExecutionWorkspace:
        """Fresh creation or exact receipt replay; never SHA-based adoption."""
        self._validate_intent()
        manager = self.manager
        if (self.directory / "receipt.json").exists():
            return self.require_local(clean=True)
        if remote_head != self.intent.get("prior_remote"):
            raise ExecutionWorkspaceError(
                "upstream ref changed before resource creation"
            )
        if (
            manager.path.exists()
            and manager.path.resolve() not in manager._registrations()
        ):
            raise ExecutionWorkspaceError(
                "creation is ambiguous: execution path is not a registered "
                "execution worktree; "
                "preserving the unowned path without a generation receipt"
            )
        if (
            self.intent.get("prior_local") is not None
            or self.intent.get("prior_remote") is not None
            or self.intent.get("prior_path_exists")
            or self.intent.get("prior_registration") is not None
            or manager._branch_exists()
            or manager.path.exists()
            or manager.path.resolve() in manager._registrations()
            or any(
                item.get("branch") == manager.branch
                for item in manager._registrations().values()
            )
            or any(self.directory.glob("witness-*"))
        ):
            raise ExecutionWorkspaceError(
                "creation is ambiguous or resources are foreign; exact generation "
                "receipt required, matching SHA is insufficient"
            )
        manager.path.parent.mkdir(parents=True, exist_ok=True)
        manager.path.mkdir(mode=0o700)
        _sync_directory(manager.path.parent)
        descriptor = os.open(
            manager.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            reserved = os.fstat(descriptor)
            identity = [reserved.st_dev, reserved.st_ino]
            _write_once(self.directory / "reservation.json", {"identity": identity})
            created = manager._runner(
                manager.repo, "update-ref", "--create-reflog", "-m",
                f"devlegate generation {self.intent['generation']}",
                str(self.intent["ref"]), str(self.intent["base"]), "", check=False,
            )
            if created.returncode:
                raise ExecutionWorkspaceError(
                    "execution branch creation CAS failed; no existing ref is adopted"
                )
            reflog = Path(str(self.intent["common"])) / "logs" / str(self.intent["ref"])
            _safe_path(reflog)
            creation_log = reflog.read_text()
            if (
                len(creation_log.splitlines()) != 1
                or not creation_log.endswith(
                    f"\tdevlegate generation {self.intent['generation']}\n"
                )
            ):
                raise ExecutionWorkspaceError(
                    "branch creation generation anchor differs"
                )
            os.link(reflog, self.directory / "witness-reflog", follow_symlinks=False)
            _sync_directory(self.directory)
            workspace = manager.create_reserved(str(self.intent["base"]))
            if _identity(manager.path) != identity:
                raise ExecutionWorkspaceError(
                    "reserved worktree directory was replaced"
                )
            self._record_creation(workspace, expected_identity=identity)
            if _identity(manager.path) != identity:
                raise ExecutionWorkspaceError(
                    "worktree replaced before creation receipt"
                )
            return workspace
        finally:
            os.close(descriptor)

    def _record_creation(
        self, workspace: ExecutionWorkspace, *,
        expected_identity: list[int] | None = None,
        clean: bool = True,
    ) -> None:
        manager = self.manager
        if expected_identity is None:
            expected_identity = _identity(manager.path)
        if _identity(manager.path) != expected_identity:
            raise ExecutionWorkspaceError("reserved worktree directory was replaced")
        admin = self._admin()
        marker = admin / "devlegate-generation.json"
        _write_once(marker, self.intent)
        targets = self._targets(admin)
        for name, target in targets.items():
            _safe_path(target)
            witness = self.directory / f"witness-{name}"
            if witness.exists():
                if name != "reflog" or _identity(target) != _identity(witness):
                    raise ExecutionWorkspaceError(
                        "branch generation witness was replaced"
                    )
            else:
                os.link(target, witness, follow_symlinks=False)
            with target.open("rb") as stream:
                os.fsync(stream.fileno())
        _sync_directory(admin)
        _sync_directory(manager.path)
        _sync_directory(self.directory)
        reflog = targets["reflog"].read_bytes()
        _write_once(self.directory / "receipt.json", {
            "generation": self.intent["generation"],
            "head": workspace.head,
            "path_identity": expected_identity,
            "admin": str(admin),
            "admin_identity": _identity(admin),
            "common_identity": _identity(Path(str(self.intent["common"]))),
            "reflog_size": len(reflog),
            "reflog_prefix_sha256": hashlib.sha256(reflog).hexdigest(),
        })
        self.require_local(clean=clean)

    def prepare_rework(
        self, predecessor: ResourceBinding, previous_base: str, checkpoint: str,
        *, remote_head: str | None,
    ) -> ExecutionWorkspace:
        """An explicit reviewed handoff, never an implicit generation transfer."""
        self._validate_intent()
        if (self.directory / "receipt.json").exists():
            return self.prepare(remote_head=remote_head)
        if (self.directory / "rework-started.json").exists():
            raise ExecutionWorkspaceError(
                "interrupted generation handoff has no creation receipt"
            )
        if (
            predecessor.intent["execution_id"] == self.intent["execution_id"]
            or predecessor.manager.path != self.manager.path
            or predecessor.intent["common"] != self.intent["common"]
        ):
            raise ExecutionWorkspaceError("reviewed generation identity differs")
        if predecessor.rework_source_absent(previous_base, checkpoint):
            # An ordinary zero-delta retirement left no resource to hand off.
            # This admission must create fresh resources, not adopt the old SHA.
            return self.prepare(remote_head=remote_head)
        predecessor.require_local(clean=True)
        if (
            predecessor.intent["execution_id"] == self.intent["execution_id"]
            or predecessor.manager.path != self.manager.path
            or remote_head != self.intent["prior_remote"]
            or self.intent["prior_local"] != checkpoint
        ):
            raise ExecutionWorkspaceError(
                "reviewed generation handoff precondition differs"
            )
        _write_once(self.directory / "rework-started.json", {
            "predecessor": predecessor.intent["generation"], "checkpoint": checkpoint,
        })
        reflog = predecessor._targets(predecessor._admin())["reflog"]
        os.link(reflog, self.directory / "witness-reflog", follow_symlinks=False)
        _sync_directory(self.directory)
        descriptors: list[int] = []

        def create(base: str) -> ExecutionWorkspace:
            self.manager.path.mkdir(mode=0o700)
            _sync_directory(self.manager.path.parent)
            descriptor = os.open(
                self.manager.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            descriptors.append(descriptor)
            reserved = os.fstat(descriptor)
            _write_once(self.directory / "reservation.json", {
                "identity": [reserved.st_dev, reserved.st_ino],
            })
            return self.manager.create_reserved(base)

        try:
            workspace = self.manager.prepare_rework(
                str(self.intent["base"]), previous_base, checkpoint, creator=create,
            )
            if len(descriptors) != 1:
                raise ExecutionWorkspaceError(
                    "rework did not create one reserved worktree"
                )
            reserved = os.fstat(descriptors[0])
            self._record_creation(
                workspace, expected_identity=[reserved.st_dev, reserved.st_ino],
            )
            return workspace
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    def rework_source_absent(self, previous_base: str, checkpoint: str) -> bool:
        """Recognize only a proven, ordinarily retired zero-delta predecessor."""
        self._validate_intent()
        self._require_settled()
        if _read(self.directory / "receipt.json").get("generation") != (
            self.intent["generation"]
        ):
            raise ExecutionWorkspaceError("predecessor creation receipt differs")
        if checkpoint != self._expected_head():
            raise ExecutionWorkspaceError("predecessor checkpoint receipt differs")
        absent = (
            checkpoint == previous_base
            and not self.manager.path.exists()
            and not self.manager._branch_exists()
            and self.manager.path.resolve() not in self.manager._registrations()
            and not any(
                item.get("branch") == self.manager.branch
                for item in self.manager._registrations().values()
            )
        )
        if not absent:
            self.require_local(clean=True)
        return absent

    def prepare_handoff(
        self, predecessor: ResourceBinding, *, remote_head: str | None
    ) -> ExecutionWorkspace:
        """Rebind proven resources only after the owner admits an explicit retry."""
        self._validate_intent()
        if (self.directory / "receipt.json").exists():
            return self.require_local(clean=False)
        if (self.directory / "handoff-intent.json").exists():
            raise ExecutionWorkspaceError("interrupted retry handoff has no receipt")
        descriptor = os.open(
            self.manager.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            workspace = predecessor.require_local(clean=False)
            if (
                self.intent.get("predecessor_generation")
                != predecessor.intent["generation"]
                or self.intent.get("predecessor_execution")
                != predecessor.intent["execution_id"]
                or workspace.head != self.intent["prior_local"]
                or remote_head != self.intent["prior_remote"]
                or predecessor.manager.path != self.manager.path
            ):
                raise ExecutionWorkspaceError(
                    "explicit retry generation precondition differs"
                )
            receipt = _read(predecessor.directory / "receipt.json")
            reserved = os.fstat(descriptor)
            identity = [reserved.st_dev, reserved.st_ino]
            if receipt.get("path_identity") != identity:
                raise ExecutionWorkspaceError("predecessor directory was replaced")
            _write_once(self.directory / "handoff-intent.json", {
                "generation": self.intent["generation"],
                "predecessor": predecessor.intent["generation"],
            })
            predecessor.require_local(clean=False)
            (self._admin() / "devlegate-generation.json").unlink()
            _sync_directory(self._admin())
            self._record_creation(workspace, expected_identity=identity, clean=False)
            return dataclasses.replace(workspace, base_head=str(self.intent["base"]))
        finally:
            os.close(descriptor)

    def _admin(self) -> Path:
        result = self.manager._runner(
            self.manager.path, "rev-parse", "--absolute-git-dir", check=False
        )
        if result.returncode or not result.stdout.strip():
            raise UnprovenResource(
                "cannot observe execution Git administrative directory"
            )
        admin = Path(result.stdout.strip())
        _safe_path(admin)
        return admin

    def _targets(self, admin: Path) -> dict[str, Path]:
        return {
            "checkout": self.manager.path / ".git",
            "registration": admin / "gitdir",
            "common": admin / "commondir",
            "marker": admin / "devlegate-generation.json",
            "reflog": (
                Path(str(self.intent["common"])) / "logs" / str(self.intent["ref"])
            ),
        }

    def require_local(
        self, *, clean: bool, read_only: bool = False
    ) -> ExecutionWorkspace:
        workspace = self._observe_local(clean=clean, read_only=read_only)
        self._require_settled()
        expected = self._expected_head()
        if workspace.head != expected:
            raise ExecutionWorkspaceError(
                "local execution ref differs from independent generation receipt"
            )
        return workspace

    def _expected_head(self) -> object:
        expected = _read(self.directory / "receipt.json").get("head")
        for _sequence, operation, path in self._attempts():
            if operation in {"checkpoint", "rewrite", "rollback", "force-checkpoint"}:
                expected = self._receipt(path).get("after")
        return expected

    def require_ref(self, expected: str) -> None:
        """Reobserve the local ref independently when ordinary cleanup lost a path."""
        self._validate_intent()
        self._require_settled()
        current = resolve_ref_commit(
            self.manager.repo, str(self.intent["ref"]), runner=self.manager._runner
        )
        if current is None:
            return
        if current != expected or expected != self._expected_head():
            raise ExecutionWorkspaceError(
                "local execution ref differs from independent generation receipt"
            )
        symbolic = self.manager._runner(
            self.manager.repo, "symbolic-ref", "--quiet", str(self.intent["ref"]),
            check=False,
        )
        if symbolic.returncode != 1:
            raise ExecutionWorkspaceError(
                "bound local execution ref is not a direct ref"
            )
        reflog = Path(str(self.intent["common"])) / "logs" / str(self.intent["ref"])
        if _identity(reflog) != _identity(self.directory / "witness-reflog"):
            raise ExecutionWorkspaceError(
                "local execution ref generation witness was lost or replaced"
            )
        receipt = _read(self.directory / "receipt.json")
        size = receipt.get("reflog_size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ExecutionWorkspaceError("invalid creation reflog receipt")
        with reflog.open("rb") as stream:
            digest = hashlib.sha256(stream.read(size)).hexdigest()
        if digest != receipt.get("reflog_prefix_sha256"):
            raise ExecutionWorkspaceError("local ref generation reflog prefix changed")

    def _validate_local_identity(self) -> Path:
        """Validate pinned filesystem evidence even during a detached rebase."""
        self._validate_intent()
        receipt = _read(self.directory / "receipt.json")
        admin = self._admin()
        if (
            receipt.get("generation") != self.intent["generation"]
            or receipt.get("path_identity") != _identity(self.manager.path)
            or receipt.get("admin") != str(admin)
            or receipt.get("admin_identity") != _identity(admin)
            or receipt.get("common_identity")
            != _identity(Path(str(self.intent["common"])))
        ):
            raise ExecutionWorkspaceError(
                "resource directory or registration was replaced"
            )
        for name, target in self._targets(admin).items():
            witness = self.directory / f"witness-{name}"
            if _identity(target) != _identity(witness) or not target.is_file():
                raise ExecutionWorkspaceError(
                    f"lost or replaced generation witness: {name}"
                )
        size = receipt.get("reflog_size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ExecutionWorkspaceError("invalid creation reflog receipt")
        with self._targets(admin)["reflog"].open("rb") as stream:
            digest = hashlib.sha256(stream.read(size)).hexdigest()
        if digest != receipt.get("reflog_prefix_sha256"):
            raise ExecutionWorkspaceError("local ref generation reflog prefix changed")
        if _read(admin / "devlegate-generation.json") != self.intent:
            raise ExecutionWorkspaceError("changed resource generation marker")
        common_target = Path((admin / "commondir").read_text().strip())
        if not common_target.is_absolute():
            common_target = admin / common_target
        _safe_path(common_target)
        if common_target.resolve() != Path(str(self.intent["common"])):
            raise ExecutionWorkspaceError("worktree Git common directory differs")
        common = Path(str(self.intent["common"]))
        for lock in (
            admin / "index.lock", admin / "HEAD.lock", common / "packed-refs.lock",
            common / "config.lock", common / f"{self.intent['ref']}.lock",
        ):
            _safe_path(lock)
            if lock.exists():
                raise ExecutionWorkspaceError(
                    f"execution Git resource is locked: {lock}"
                )
        return admin

    def rebase_state(self) -> dict[str, str]:
        admin = self._validate_local_identity()
        for name in ("rebase-merge", "rebase-apply"):
            directory = admin / name
            _safe_path(directory)
            if directory.exists():
                return {
                    field: (directory / field).read_text().strip()
                    for field in ("head-name", "orig-head", "onto")
                }
        return {}

    def require_rebase_stage(self) -> None:
        """Only an observed, receipted native rebase permits continue/abort."""
        self._require_settled()
        receipt = self.operation_receipt("rewrite")
        observed = self.rebase_state()
        if (
            not observed or observed != receipt.get("rebase_state")
            or observed["head-name"] != self.intent["ref"]
            or resolve_ref_commit(
                self.manager.repo, str(self.intent["ref"]), runner=self.manager._runner
            ) != self._expected_head()
        ):
            raise ExecutionWorkspaceError("native rebase stage has no exact receipt")

    def _observe_local(
        self, *, clean: bool, read_only: bool = False
    ) -> ExecutionWorkspace:
        """Identity gate in addition to existing operation-specific authority."""
        self._validate_local_identity()
        def observe(repo: Path, *args: str, **kwargs):
            if read_only and args[0] == "status":
                filters = self.manager._runner(
                    repo, "--no-optional-locks", "config", "--get-regexp",
                    r"^filter\..*\.(clean|process)$", check=False,
                )
                if filters.returncode not in {0, 1}:
                    raise UnprovenResource(
                        "cannot observe Git conversion configuration"
                    )
                if filters.returncode == 0:
                    raise UnprovenResource(
                        "read-only cleanliness proof unsupported with configured "
                        "Git clean/process filters"
                    )
                # Inspect each submodule through this same conversion gate
                # before asking Git to read its worktree contents.
                args = (*args, "--ignore-submodules=all")
            result = self.manager._runner(
                repo, "--no-optional-locks", "-c", "core.fsmonitor=false",
                "-c", "core.untrackedCache=false", *args, **kwargs,
            )
            if result.returncode and args[0] in {
                "worktree", "rev-parse", "status", "ls-tree", "submodule",
            }:
                raise UnprovenResource(
                    f"Git observation unavailable ({args[0]}): {result.stderr.strip()}"
                )
            return result

        manager = ExecutionWorkspaceManager(
            self.manager.repo, self.manager.root, self.manager.ticket_id,
            runner=observe,
        )
        _safe_path(manager.path)
        registration = manager._registrations().get(manager.path.resolve())
        if (
            registration is None
            or "locked" in registration
            or "prunable" in registration
        ):
            raise ExecutionWorkspaceError(
                "execution registration is missing, locked or prunable"
            )
        if registration.get("path") != str(manager.path):
            raise ExecutionWorkspaceError(
                "registered worktree path differs from binding"
            )
        workspace = manager._validate_existing(registration, str(self.intent["base"]))
        manager.verify_submodules(workspace)
        if clean and workspace.dirty:
            raise ExecutionWorkspaceError("execution worktree is dirty")
        return workspace

    def prove(
        self, *, runner: GitRunner, expected_remote: str | None, clean: bool = True,
        read_only: bool = True, worker_retired: bool = False,
    ) -> ResourceProof:
        """Strictly read-only snapshot for reset previews; no fetch or state writes."""
        values: _ProofIdentity = {
            "ticket_id": str(self.intent.get("ticket_id", "")),
            "execution_id": str(self.intent.get("execution_id", "")),
            "generation": str(self.intent["generation"]),
            "path": str(self.intent["path"]),
            "common_dir": str(self.intent["common"]),
            "local_ref": str(self.intent["ref"]),
            "upstream_ref": f"{self.intent['remote']}:{self.intent['remote_ref']}",
        }
        try:
            self.require_local(clean=clean, read_only=read_only)
            if worker_retired:
                self.require_worker_retirement()
            expected_remote = self.latest_remote(expected_remote)
        except UnprovenResource as error:
            return ResourceProof(
                **values, state="unproven",
                local_state="proved" if error.local_proved else "unproven",
                reason=str(error),
            )
        except FileNotFoundError as error:
            return ResourceProof(
                **values, state="unproven",
                reason=f"missing evidence: {error.filename}",
            )
        except (OSError, subprocess.SubprocessError) as error:
            return ResourceProof(
                **values, state="unproven",
                reason=f"local observation unavailable: {error}",
            )
        except (ExecutionWorkspaceError, ValueError) as error:
            return ResourceProof(
                **values, state="conflict", local_state="conflict", reason=str(error),
            )
        try:
            observed = remote_ref(
                self.manager.repo, str(self.intent["remote"]),
                str(self.intent["remote_ref"]), runner=runner,
            )
        except (GitObservationError, OSError, subprocess.SubprocessError) as error:
            return ResourceProof(
                **values, state="unproven", local_state="proved",
                reason=f"upstream observation unavailable: {error}",
            )
        if observed != expected_remote:
            return ResourceProof(
                **values, state="conflict", local_state="proved",
                upstream_observed=observed,
                reason=(
                    "upstream remote changed: ref differs from exact "
                    "expected observation"
                ),
            )
        return ResourceProof(
            **values, state="unproven", local_state="proved",
            upstream_observed=observed, upstream_exact=True,
            reason=(
                "local generation proved; upstream continuity unknown: plain Git "
                "cannot detect same-name, same-SHA recreation"
            ),
        )
