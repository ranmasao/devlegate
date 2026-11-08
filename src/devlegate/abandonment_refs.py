# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Narrow independent receipts for exclusive, immutable execution code pins.

Old pins without receipts remain blocked. This is not an adoption mechanism.
Remote authority assumes cooperating writers; a Git lease cannot detect ABA.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from devlegate.abandonment_evidence import EvidenceError
from devlegate.git import push_refs, remote_ref, resolve_ref_commit
from devlegate.resource_binding import (
    ResourceBinding,
    _identity,
    _read,
    _safe_path,
    _sync_directory,
    _write_once,
)


def prefixes(target: str) -> tuple[str, ...]:
    return tuple(f"refs/devlegate/{kind}/{target}/" for kind in (
        "executions", "reconciliation", "recovery/workspace",
        "recovery/force-retry", "recovery/operator",
    ))


def inventory(repo: Path, remote: str, target: str, runner) -> list[dict]:
    result = []
    local = runner(repo, "for-each-ref", "--format=%(objectname) %(refname)",
                   *prefixes(target), check=False)
    upstream = runner(repo, "ls-remote", remote,
                      *(prefix + "*" for prefix in prefixes(target)), check=False)
    for location, observation in (("local", local), ("remote", upstream)):
        if observation.returncode:
            raise EvidenceError(f"cannot observe {location} code pins")
        for line in observation.stdout.splitlines():
            head, ref = line.split()
            if not any(ref.startswith(prefix) for prefix in prefixes(target)):
                raise EvidenceError("code pin observation escaped target namespace")
            result.append({"location": location, "ref": ref, "head": head})
    return sorted(result, key=lambda item: (item["location"], item["ref"]))


def _directory(binding: ResourceBinding, ref: str) -> Path:
    owned_prefixes = prefixes(binding.manager.ticket_id)
    if not any(ref.startswith(prefix) for prefix in owned_prefixes):
        raise EvidenceError("pin is outside the exclusive target namespace")
    # Only a canonical execution-specific ref is eligible, never a namespace sweep.
    suffix = ref.rsplit("/", 1)[-1]
    if suffix not in {binding.intent["execution_id"],
                      str(binding.intent["execution_id"]) + "-rewritten"}:
        raise EvidenceError("pin is not exclusive to the receipted execution")
    if not any(ref == prefix + suffix for prefix in owned_prefixes):
        raise EvidenceError("pin path is not canonical")
    return binding.directory / "pins" / hashlib.sha256(ref.encode()).hexdigest()


def _log_digest(path: Path) -> str:
    _safe_path(path)
    with path.open("rb") as stream:
        content = stream.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        raise EvidenceError("immutable pin reflog exceeds ownership proof limit")
    return hashlib.sha256(content).hexdigest()


def create_pin(binding: ResourceBinding, ref: str, head: str) -> None:
    """Create once with a reflog witness. Never receipt an existing foreign pin."""
    binding._validate_intent()
    directory = _directory(binding, ref)
    _safe_path(directory)
    runner, repo = binding.manager._runner, binding.manager.repo
    if (directory / "local.json").exists():
        require_pin(binding, {"location": "local", "ref": ref, "head": head})
        return
    if directory.exists() or resolve_ref_commit(repo, ref, runner=runner) is not None:
        raise EvidenceError("existing pin has no independent creation receipt")
    directory.mkdir(parents=True, mode=0o700)
    _sync_directory(directory.parent)
    _write_once(directory / "intent.json", {
        "generation": binding.intent["generation"], "ref": ref, "head": head,
    })
    created = runner(repo, "update-ref", "--create-reflog", ref, head, "0" * len(head),
                     check=False)
    if created.returncode or resolve_ref_commit(repo, ref, runner=runner) != head:
        raise EvidenceError("pin creation did not complete exactly")
    log = Path(str(binding.intent["common"])) / "logs" / ref
    _safe_path(log)
    os.link(log, directory / "witness", follow_symlinks=False)
    _sync_directory(directory)
    _write_once(directory / "local.json", {
        "generation": binding.intent["generation"], "ref": ref, "head": head,
        "log_sha256": _log_digest(log),
    })


def publish_pin(binding: ResourceBinding, ref: str, head: str) -> None:
    pin = {"location": "local", "ref": ref, "head": head}
    require_pin(binding, pin)
    directory = _directory(binding, ref)
    runner, repo, remote = (binding.manager._runner, binding.manager.repo,
                            str(binding.intent["remote"]))
    if (directory / "remote.json").exists():
        require_pin(binding, {**pin, "location": "remote"})
        if remote_ref(repo, remote, ref, runner=runner) != head:
            raise EvidenceError("published pin changed")
        return
    if (directory / "remote-intent.json").exists() or remote_ref(
        repo, remote, ref, runner=runner
    ) is not None:
        raise EvidenceError("remote pin creation is unowned or uncertain")
    intent = {"generation": binding.intent["generation"], "ref": ref,
              "before": None, "after": head}
    _write_once(directory / "remote-intent.json", intent)
    pushed = push_refs(repo, remote, f"{head}:{ref}",
                       options=(f"--force-with-lease={ref}:",), runner=runner)
    if pushed.returncode or remote_ref(repo, remote, ref, runner=runner) != head:
        raise EvidenceError("remote pin publication is uncertain")
    _write_once(directory / "remote.json", intent)


def require_pin(binding: ResourceBinding, pin: dict) -> dict:
    binding._validate_intent()
    directory = _directory(binding, pin["ref"])
    receipt = _read(directory / "local.json")
    if any(receipt.get(key) != value for key, value in {
        "generation": binding.intent["generation"], "ref": pin["ref"],
        "head": pin["head"],
    }.items()):
        raise EvidenceError("pin generation receipt differs")
    if pin["location"] == "remote":
        expected = {"generation": binding.intent["generation"], "ref": pin["ref"],
                    "before": None, "after": pin["head"]}
        if _read(directory / "remote-intent.json") != expected or _read(
            directory / "remote.json"
        ) != expected:
            raise EvidenceError("remote pin has no independent publication receipt")
    else:
        log = Path(str(binding.intent["common"])) / "logs" / pin["ref"]
        if _identity(log) != _identity(directory / "witness") or (
            _log_digest(log) != receipt["log_sha256"]
        ):
            raise EvidenceError("pin local generation witness changed")
    return receipt
