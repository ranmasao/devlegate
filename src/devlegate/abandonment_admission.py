# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-only durable bindings for an exclusive abandonment disposition.

Admission is deliberately distinct from analysis, review and disposal authority.
The source blobs and independent generation observations survive service restart;
neither a new ticket body nor a matching branch SHA can replace that binding.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from devlegate.abandonment_evidence import EvidenceError, OwnerGitEvidence
from devlegate.abandonment_refs import inventory, prefixes, require_pin
from devlegate.execution_result import ExecutionReportStore
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.git import remote_ref, resolve_ref_commit
from devlegate.resource_binding import ResourceBinding
from devlegate.tickets import Ticket, TicketStore, parse_ticket_text

_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def require_quiescence(state: dict[str, object], active_count: int) -> None:
    """Do not cancel, normalize or adopt uncertain execution ownership."""
    if state.get("phase") != "idle" or active_count:
        raise EvidenceError("abandonment requires an idle owner with no active worker")
    if state.get("worker_identity") is not None:
        raise EvidenceError("abandonment worker ownership is not proven absent")
    # Even resolved recovery records can pin resources for a later replay. The
    # operator must finish/clear that lifecycle using its existing owner command.
    for field in (
        "accepted_integration", "reconciliation", "operator_recovery",
        "automatic_retry", "force_retry_authorization", "resume_required",
        "execution_ref_retirement", "workspace_cleanup_pending",
    ):
        if state.get(field) is not None:
            raise EvidenceError(
                f"abandonment requires completion of pending {field} ownership"
            )


def _path(value: object) -> str:
    if not isinstance(value, str):
        raise EvidenceError("invalid admitted ticket path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(
        part in {"", ".", "..", ".git"} for part in path.parts
    ):
        raise EvidenceError("invalid admitted ticket path")
    return value


def specification(source: OwnerGitEvidence, head: str, ticket: Ticket,
                  path: str) -> dict[str, str]:
    """Read a regular committed blob, never a symlink or mutable working file."""
    _path(path)
    entry = source.small("ls-tree", head, "--", path).decode("utf-8").rstrip("\n")
    fields = entry.split("\t")
    if len(fields) != 2 or fields[1] != path:
        raise EvidenceError(f"admitted specification is missing: {path}")
    identity = fields[0].split()
    if len(identity) != 3 or identity[:2] != ["100644", "blob"]:
        raise EvidenceError(f"admitted specification is not a regular blob: {path}")
    raw = source.small("cat-file", "blob", identity[2])
    try:
        parsed = parse_ticket_text(ticket.path, ticket.state, raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise EvidenceError(f"invalid admitted specification: {path}") from error
    if parsed != ticket:
        raise EvidenceError(f"working specification differs from admitted blob: {path}")
    return {"path": path, "blob": identity[2],
            "sha256": hashlib.sha256(raw).hexdigest()}


def observe_resources(*, repo: Path, root: Path, remote: str, target: Ticket,
                      control: Path, control_head: str,
                      source: OwnerGitEvidence, runner) -> dict[str, object]:
    """Capture independent local ownership; label upstream continuity honestly.

    An exact upstream observation is recorded for later lease comparison, never
    promoted to proof of uninterrupted ref identity. No resource is modified.
    """
    manager = ExecutionWorkspaceManager(repo, root, target.id, runner=runner)
    registrations = manager._registrations()
    registration = registrations.get(manager.path.resolve())
    local = resolve_ref_commit(repo, f"refs/heads/{manager.branch}", runner=runner)
    upstream = remote_ref(repo, remote, f"refs/heads/{manager.branch}", runner=runner)
    if manager.path.is_symlink() or any(
        path != manager.path.resolve() and item.get("branch") == manager.branch
        for path, item in registrations.items()
    ):
        raise EvidenceError("abandonment target has foreign or ambiguous worktree")
    if registration is None and manager.path.exists():
        raise EvidenceError(
            "abandonment target path is not a registered owned worktree"
        )
    if registration is not None and local is None:
        raise EvidenceError("abandonment registered worktree has no local branch")
    result: dict[str, object] = {
        "path": str(manager.path), "branch": manager.branch,
        "registration": registration, "local_head": local,
        "upstream_head": upstream, "upstream_continuity": "unproven",
        "generation": None, "execution_id": None, "dirty": None,
        "code_pins": [],
    }
    reports = ExecutionReportStore(control).list(target.id)
    for pin in inventory(repo, remote, target.id, runner):
        pin = {**pin, "generation": None, "execution_id": None,
               "gap": "no independently receipted execution pin"}
        for report in reports:
            if report.resource_generation is None:
                continue
            try:
                binding = ResourceBinding.for_execution(manager, report.execution_id)
                if binding.intent["generation"] != report.resource_generation:
                    continue
                require_pin(binding, pin)
            except (EvidenceError, ExecutionWorkspaceError, OSError, ValueError):
                continue
            report_path = f"executions/{target.id}/{report.execution_id}.json"
            if source.small("show", f"{control_head}:{report_path}") != (
                report.to_json().encode("utf-8")
            ):
                raise EvidenceError("pin report differs from admitted control history")
            pin.update(generation=report.resource_generation,
                       execution_id=report.execution_id, gap=None)
            break
        result["code_pins"].append(pin)
    if registration is None and local is None and upstream is None:
        return result
    # Ref-only leftovers still require the independent ref-generation witness.
    matches = []
    for report in reports:
        if report.resource_generation is None or report.workspace_head != (
            local if local is not None else upstream
        ):
            continue
        report_path = f"executions/{target.id}/{report.execution_id}.json"
        entry = source.small("ls-tree", control_head, "--", report_path).decode()
        fields = entry.rstrip("\n").split("\t")
        if len(fields) != 2 or fields[1] != report_path or not fields[0].startswith(
            "100644 blob "
        ):
            raise EvidenceError(
                "resource report is not an admitted regular control blob"
            )
        raw = source.small("cat-file", "blob", fields[0].split()[2])
        if raw != report.to_json().encode("utf-8"):
            raise EvidenceError("resource report differs from admitted control history")
        binding = ResourceBinding.for_execution(manager, report.execution_id)
        if binding.intent["generation"] != report.resource_generation:
            raise EvidenceError("abandonment report resource generation conflicts")
        try:
            if registration is not None:
                workspace = binding.require_local(clean=False, read_only=True)
                dirty = workspace.dirty
            else:
                if local is not None:
                    binding.require_ref(local)
                elif binding.abandonment_remote_authority() != upstream:
                    raise EvidenceError("upstream-only publication generation differs")
                dirty = None
            binding.require_worker_retirement()
        except (OSError, ValueError, ExecutionWorkspaceError) as error:
            raise EvidenceError(
                f"abandonment resource ownership unproven: {error}"
            ) from error
        matches.append((report, binding, dirty))
    if len(matches) != 1:
        raise EvidenceError(
            ("upstream-only " if local is None else "abandonment ")
            + "resource has no unique independently verified generation; "
            "do not adopt an old or replacement checkout"
        )
    report, binding, dirty = matches[0]
    if upstream is not None and upstream != binding.latest_remote(
        binding.intent["prior_remote"]
    ):
        raise EvidenceError("abandonment upstream differs from generation publication")
    result.update(generation=report.resource_generation,
                  execution_id=report.execution_id, dirty=dirty)
    return result


@dataclass(frozen=True)
class AbandonmentAdmission:
    """Canonical JSON is the persisted intent and the manifest's source binding."""

    encoded: str

    def as_dict(self) -> dict[str, Any]:
        return json.loads(self.encoded)

    @classmethod
    def restore(cls, value: object) -> AbandonmentAdmission:
        required = {"schema", "request_id", "target_id", "execution_id", "control_head",
                    "product_head", "start_head", "reason_sha256", "request", "target",
                    "resources"}
        if not isinstance(value, dict) or set(value) != required or (
            value.get("schema") != "devlegate.abandonment-admission.v1"
        ):
            raise EvidenceError("invalid durable abandonment admission")
        for key in ("request_id", "target_id"):
            if not isinstance(value[key], str) or not _ID.fullmatch(value[key]):
                raise EvidenceError("invalid admitted abandonment identity")
        if value["request_id"] == value["target_id"] or not isinstance(
            value["execution_id"], str
        ) or not re.fullmatch(r"[0-9a-f]{32}", value["execution_id"]):
            raise EvidenceError("invalid admitted abandonment execution")
        for key in ("control_head", "product_head", "start_head"):
            if not isinstance(value[key], str) or not _OID.fullmatch(value[key]):
                raise EvidenceError("invalid admitted abandonment HEAD")
        if not isinstance(value["reason_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", value["reason_sha256"]
        ):
            raise EvidenceError("invalid admitted abandonment reason digest")
        for key in ("request", "target"):
            spec = value[key]
            if not isinstance(spec, dict) or set(spec) != {"path", "blob", "sha256"}:
                raise EvidenceError("invalid admitted abandonment specification")
            _path(spec["path"])
            if (
                not isinstance(spec["blob"], str) or not _OID.fullmatch(spec["blob"])
            ) or (
                not isinstance(spec["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"])
            ):
                raise EvidenceError("invalid admitted abandonment blob identity")
        resources = value["resources"]
        if not isinstance(resources, dict) or set(resources) != {
            "path", "branch", "registration", "local_head", "upstream_head",
            "upstream_continuity", "generation", "execution_id", "dirty",
            "code_pins",
        } or resources["branch"] != f"devlegate/work/{value['target_id']}":
            raise EvidenceError("invalid admitted abandonment resources")
        if resources["upstream_continuity"] != "unproven":
            raise EvidenceError("abandonment cannot assert plain Git remote continuity")
        pins = resources["code_pins"]
        if not isinstance(pins, list) or len(pins) > 4096 or any(
            not isinstance(pin, dict) or set(pin) != {
                "location", "ref", "head", "generation", "execution_id", "gap",
            } or pin["location"] not in {"local", "remote"}
            or not isinstance(pin["ref"], str)
            or not isinstance(pin["head"], str) or not _OID.fullmatch(pin["head"])
            for pin in pins
        ):
            raise EvidenceError("invalid admitted code pin inventory")
        identities = set()
        for pin in pins:
            identity = (pin["location"], pin["ref"])
            if identity in identities or not any(
                pin["ref"].startswith(prefix) for prefix in prefixes(value["target_id"])
            ):
                raise EvidenceError("duplicate or foreign admitted code pin")
            identities.add(identity)
            if pin["generation"] is None:
                if pin["execution_id"] is not None or not isinstance(pin["gap"], str):
                    raise EvidenceError("unproven admitted code pin lacks a gap")
            elif not isinstance(pin["generation"], str) or not re.fullmatch(
                r"[0-9a-f]{64}", pin["generation"]
            ) or not isinstance(pin["execution_id"], str) or not re.fullmatch(
                r"[0-9a-f]{32}", pin["execution_id"]
            ) or pin["gap"] is not None:
                raise EvidenceError("invalid admitted code pin generation")
        if not isinstance(resources["path"], str) or not Path(
            resources["path"]
        ).is_absolute() or (
            resources["dirty"] is not None and type(resources["dirty"]) is not bool
        ):
            raise EvidenceError("invalid admitted abandonment resource path/state")
        for key in ("local_head", "upstream_head"):
            if resources[key] is not None and (
                not isinstance(resources[key], str)
                or not _OID.fullmatch(resources[key])
            ):
                raise EvidenceError("invalid admitted abandonment resource HEAD")
        if resources["registration"] is not None and not isinstance(
            resources["registration"], dict
        ):
            raise EvidenceError("invalid admitted abandonment resource registration")
        generation = resources["generation"]
        execution = resources["execution_id"]
        if generation is None:
            if any(resources[key] is not None for key in (
                "execution_id", "registration", "local_head", "upstream_head", "dirty",
            )):
                raise EvidenceError("unbound abandonment resources are not absent")
        elif not isinstance(generation, str) or not re.fullmatch(
            r"[0-9a-f]{64}", generation
        ) or not isinstance(execution, str) or not re.fullmatch(
            r"[0-9a-f]{32}", execution
        ):
            raise EvidenceError("invalid admitted abandonment resource generation")
        return cls(json.dumps(value, ensure_ascii=True, sort_keys=True))

    @classmethod
    def create(cls, *, state: dict[str, object], active_count: int,
               store: TicketStore, request: Ticket, control: OwnerGitEvidence,
               control_head: str, product_head: str, execution_id: str,
               resources: dict[str, object], control_root: Path,
               ) -> AbandonmentAdmission:
        require_quiescence(state, active_count)
        if request.type != "devlegate.abandonment" or request.state != "todo" or (
            request not in store.runnable
        ):
            raise EvidenceError("abandonment request is not runnable")
        target = store.by_id.get(request.target or "")
        if target is None or target.state in {"done", "abandoned"}:
            raise EvidenceError("abandonment target is missing or terminal")
        if any(
            item.state == "accepted" or (
                item.state == "review" and item.id != target.id
            ) for item in store.tickets
        ):
            raise EvidenceError("finish the existing review/accepted serial boundary")
        if request.abandonment_reason is None:
            raise EvidenceError("abandonment request has no validated reason")
        return cls.restore({
            "schema": "devlegate.abandonment-admission.v1",
            "request_id": request.id, "target_id": target.id,
            "execution_id": execution_id, "control_head": control_head,
            "product_head": product_head, "start_head": request.start_head,
            "reason_sha256": hashlib.sha256(
                request.abandonment_reason.encode("utf-8")
            ).hexdigest(),
            "request": specification(control, control_head, request,
                                     request.path.relative_to(control_root).as_posix()),
            "target": specification(control, control_head, target,
                                    target.path.relative_to(control_root).as_posix()),
            "resources": resources,
        })

    def verify(self, *, store: TicketStore, control: OwnerGitEvidence,
               control_head: str, control_root: Path) -> None:
        """Refuse all amendments, including reason-only and source replacements."""
        value = self.as_dict()
        if control.small("merge-base", "--is-ancestor", value["control_head"],
                         control_head) != b"":
            raise EvidenceError("abandonment control history is not a descendant")
        if control.small(
            "log", "--format=%H", "--max-count=1",
            f"{value['control_head']}..{control_head}",
            "--", f"executions/{value['target_id']}", f"reviews/{value['target_id']}",
        ):
            raise EvidenceError(
                "target execution/review evidence changed after abandonment admission; "
                "the admitted manifest must not silently omit new target history"
            )
        for key in ("request", "target"):
            ticket = store.by_id.get(value[f"{key}_id"])
            if ticket is None:
                raise EvidenceError(f"admitted abandonment {key} is missing")
            if key == "request" and ticket.state not in {"todo", "review", "accepted"}:
                raise EvidenceError("admitted abandonment request is no longer active")
            if key == "target" and ticket.state in {"done", "abandoned"}:
                raise EvidenceError("admitted abandonment target is already terminal")
            observed = specification(control, control_head, ticket,
                                     ticket.path.relative_to(control_root).as_posix())
            if observed["blob"] != value[key]["blob"] or (
                observed["sha256"] != value[key]["sha256"]
            ):
                raise EvidenceError(
                    f"admitted abandonment {key} blob changed; restore exact source "
                    "before resuming this disposition"
                )
