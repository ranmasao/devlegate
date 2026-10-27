# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Serialized product-lineage reconciliation transactions.

This module owns the admission and proof boundary for an update-base
transaction.  Git, state, and workspace mutation remain capabilities of the
single ``ServiceEngine`` owner; this module only sequences the proofs needed
before those capabilities may be used.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any, Protocol

from devlegate.git import (
    GitObservationError,
    commit_parents,
    commit_range,
    reset_hard,
    resolve_commit,
    resolve_ref_commit,
    run_git,
    working_tree_status,
)
from devlegate.reconciliation import (
    LineageProof,
    prove_linear_lineage,
    rewrite_is_authorized,
    rewritten_evidence_ref,
)


class ReconciliationTransactionError(ValueError):
    """A reconciliation request cannot be proven safe."""


@dataclasses.dataclass(frozen=True)
class UpdateBaseAdmission:
    """Immutable observations captured before a reconciliation side effect."""

    ticket_id: str
    target: str
    original_base: str
    control_head: str
    checkpoint: str
    execution_branch: str
    execution_path: str
    evidence_ref: str
    execution_remote_head: str | None


class UpdateBaseCapabilities(Protocol):
    """Narrow owner observations used by the admission transaction."""

    def reconciliation_pending(self) -> dict[str, object]: ...

    def reconciliation_target(self, value: str) -> str: ...

    def reconciliation_product(self, base: str) -> dict[str, object]: ...

    def reconciliation_target_is_descendant(self, base: str, target: str) -> bool: ...

    def reconciliation_same_parent_replacement(
        self, base: str, target: str
    ) -> bool: ...

    def reconciliation_evidence_matches(
        self, reference: str, checkpoint: str
    ) -> bool: ...

    def reconciliation_workspace_binding(
        self, ticket_id: str, value: dict[str, object]
    ) -> bool: ...

    def reconciliation_publish_rewrite(
        self, branch: str, rewritten_head: str, expected_remote: str
    ) -> None: ...

    def reconciliation_pin_rewrite_evidence(
        self, reference: str, rewritten_head: str
    ) -> None: ...

    def reconciliation_lock(self) -> Any: ...


def admit_update_base(
    owner: UpdateBaseCapabilities, ticket_id: str, onto: str
) -> UpdateBaseAdmission:
    """Capture and prove the immutable inputs for one update-base operation."""
    reconciliation = owner.reconciliation_pending()
    if reconciliation.get("ticket_id") != ticket_id:
        raise ReconciliationTransactionError(
            "requested ticket does not match pending reconciliation"
        )
    target = owner.reconciliation_target(onto)
    original_base = reconciliation.get("original_base")
    control_head = reconciliation.get("control_head")
    checkpoint = reconciliation.get("worker_checkpoint")
    evidence_ref = reconciliation.get("evidence_ref")
    branch = reconciliation.get("execution_branch")
    path = reconciliation.get("execution_path")
    if not all(
        isinstance(value, str) and value
        for value in (
            original_base,
            control_head,
            checkpoint,
            evidence_ref,
            branch,
            path,
        )
    ):
        raise ReconciliationTransactionError("reconciliation evidence is incomplete")
    product = owner.reconciliation_product(original_base)
    if product.get("branch") is not None and product.get("dirty"):
        raise ReconciliationTransactionError("product checkout is dirty")
    if product.get("local_head") != target:
        raise ReconciliationTransactionError(
            "requested reconciliation target is not the current product HEAD"
        )
    if product.get("remote_head") != target:
        raise ReconciliationTransactionError(
            "requested reconciliation target is not the fresh product remote HEAD"
        )
    if not owner.reconciliation_target_is_descendant(original_base, target):
        if not owner.reconciliation_same_parent_replacement(original_base, target):
            raise ReconciliationTransactionError(
                "requested reconciliation target is not a descendant of the original "
                "base or a proven same-parent replacement"
            )
    if not owner.reconciliation_evidence_matches(evidence_ref, checkpoint):
        raise ReconciliationTransactionError(
            "preserved worker checkpoint evidence changed"
        )
    if not owner.reconciliation_workspace_binding(ticket_id, reconciliation):
        raise ReconciliationTransactionError(
            "reconciliation workspace binding is invalid"
        )
    remote = reconciliation.get("execution_remote_head")
    if remote is not None and not isinstance(remote, str):
        raise ReconciliationTransactionError(
            "execution predecessor identity is invalid"
        )
    return UpdateBaseAdmission(
        ticket_id=ticket_id,
        target=target,
        original_base=original_base,
        control_head=control_head,
        checkpoint=checkpoint,
        execution_branch=branch,
        execution_path=path,
        evidence_ref=evidence_ref,
        execution_remote_head=remote,
    )


def publish_rewritten_lineage(
    owner: UpdateBaseCapabilities,
    *,
    branch: str,
    rewritten_head: str,
    expected_remote: str | None,
    evidence_ref: str,
) -> None:
    """Publish one proven rewrite and retain its immutable evidence.

    The owner performs the actual Git operations.  The rewritten tip is
    pinned before remote publication, so a crash after the lease-guarded push
    leaves enough evidence for exact replay.
    """
    owner.reconciliation_pin_rewrite_evidence(evidence_ref, rewritten_head)
    if expected_remote is not None:
        owner.reconciliation_publish_rewrite(branch, rewritten_head, expected_remote)


def prove_workspace_lineage(
    owner: UpdateBaseCapabilities, workspace: Any, root: str, tip: str
) -> tuple[str, ...]:
    """Prove that ``tip`` is a non-empty, single-parent chain from ``root``.

    This is deliberately an observation-only transaction step.  It is shared by
    initial reconciliation admission and replay after a crash, so neither path
    can accidentally weaken the merge/side-branch checks.
    """
    try:
        commits = commit_range(
            workspace.path, f"{root}..{tip}", reverse=True, runner=_git
        )
    except GitObservationError as error:
        raise ReconciliationTransactionError(
            f"cannot prove workspace lineage: {error}"
        ) from error
    parents: dict[str, tuple[str, ...]] = {}
    for commit in commits:
        try:
            observed = commit_parents(
                workspace.path, commit, absent_is_none=False, runner=_git
            )
        except GitObservationError as error:
            raise ReconciliationTransactionError(
                f"cannot prove workspace lineage: {error}"
            ) from error
        if observed is None:
            raise ReconciliationTransactionError(
                "cannot prove workspace lineage: commit parent is unavailable"
            )
        parents[commit] = observed
    proof = prove_linear_lineage(root, commits, parents)
    if not proof.valid or (commits and commits[-1] != tip):
        reason = (
            proof.reason
            if not proof.valid
            else "lineage does not reach the expected tip"
        )
        raise ReconciliationTransactionError(
            reason
        )
    return proof.commits


def authorize_workspace_rewrite(
    owner: UpdateBaseCapabilities,
    workspace: Any,
    root: str,
    tip: str,
    published_head: str | None,
    rewrite_published: bool,
) -> tuple[str, ...]:
    """Prove the rewritten chain and require explicit publication authorization."""
    commits = prove_workspace_lineage(owner, workspace, root, tip)
    proof = LineageProof(root, commits, True, "linear lineage proven")
    if not rewrite_is_authorized(
        published_head=published_head,
        lineage=proof,
        rewrite_published=rewrite_published,
    ):
        raise ReconciliationTransactionError(
            "published execution ref requires explicit rewrite authorization"
        )
    return commits


def run_update_base(
    owner: UpdateBaseCapabilities,
    ticket_id: str,
    onto: str,
    *,
    rewrite_published: bool = False,
    take_lock: bool = False,
) -> int:
    """Dispatch one serialized transaction from its durable WAL stage.

    A pending rewrite is recovery, never a new rewrite.  The owner retains
    mutation authority, while this module makes the durable-stage decision
    before any fresh admission or side effect is attempted.
    """
    authority = owner.reconciliation_lock() if take_lock else _NoopContext()
    with authority:
        state = owner.reconciliation_pending()
        stage = state.get("rewrite_stage")
        if stage not in {None, "local-pending", "remote-pending"}:
            raise ReconciliationTransactionError("unknown rewrite stage")
        admission = admit_update_base(owner, ticket_id, onto)
        if stage == "local-pending":
            return recover_local_pending(
                owner, ticket_id, admission, rewrite_published
            )
        if stage == "remote-pending":
            return recover_remote_pending(
                owner, ticket_id, admission, rewrite_published
            )
        return start_fresh_rewrite(owner, ticket_id, admission, rewrite_published)


def _git(cwd: Any, *args: str, check: bool = False) -> Any:
    """Keep reconciliation's historical non-raising Git result semantics."""
    return run_git(cwd, *args, check=check)


def _clear_rewrite(
    reconciliation: dict[str, object], fields: set[str]
) -> dict[str, object]:
    return {
        key: value
        for key, value in reconciliation.items()
        if key not in fields
    }


def _resolve(owner: Any, ticket_id: str, reconciliation: dict[str, object],
              target: str, checkpoint: str, remote: str | None, evidence: str) -> int:
    resolved = {
        **_clear_rewrite(
            reconciliation,
            {
                "rewrite_stage",
                "rewrite_checkpoint",
                "rewrite_expected_remote",
                "rewrite_target",
                "rewrite_count",
            },
        ),
        "status": "resolved",
        "resolution": "update-base",
        "resolution_class": (
            "update-base with published-lineage rewrite" if remote is not None
            else "update-base"
        ),
        "effective_base": target,
        "worker_checkpoint": checkpoint,
        "execution_remote_head": remote,
        "evidence_ref": evidence,
    }
    resolved.pop("execution_report", None)
    todo_fingerprint = owner.reconciliation_todo_fingerprint()
    owner._save_state(
        "idle",
        execution_base_head=target,
        execution_start_head=target,
        execution_remote_head=remote,
        handled_remote_head=target,
        handled_control_head=str(reconciliation["control_head"]),
        handled_todo_fingerprint=todo_fingerprint,
        reconciliation=resolved,
        resume_required={"ticket_id": ticket_id, "status": "required"},
    )
    return 0


def recover_remote_pending(owner: Any, ticket_id: str, admission: UpdateBaseAdmission,
                           rewrite_published: bool) -> int:
    reconciliation = owner._state["reconciliation"]
    manager = owner.reconciliation_workspace_manager(ticket_id)
    workspace = owner._execution_workspace_for_rewrite_recovery(manager)
    checkpoint = str(reconciliation["worker_checkpoint"])
    rewrite_checkpoint = reconciliation.get("rewrite_checkpoint")
    if (
        not isinstance(rewrite_checkpoint, str)
        or reconciliation.get("rewrite_target") != admission.target
    ):
        raise ReconciliationTransactionError(
            "interrupted execution rewrite evidence is invalid"
        )
    if workspace.dirty:
        raise ReconciliationTransactionError(
            "interrupted execution rewrite workspace is dirty"
        )
    observed = owner._execution_remote_head(manager.branch)
    expected = reconciliation.get("rewrite_expected_remote")
    if observed == expected:
        restored = reset_hard(workspace.path, checkpoint, runner=_git)
        if restored.returncode:
            raise ReconciliationTransactionError(
                "interrupted execution rewrite could not be safely aborted"
            )
        owner._save_state(
            "idle",
            execution_base_head=admission.original_base,
            execution_start_head=admission.original_base,
            reconciliation=_clear_rewrite(
                reconciliation,
                {
                    "rewrite_stage",
                    "rewrite_checkpoint",
                    "rewrite_expected_remote",
                    "rewrite_target",
                },
            ),
        )
        return 0
    if observed != rewrite_checkpoint or workspace.head != rewrite_checkpoint:
        raise ReconciliationTransactionError(
            "execution branch remote changed during interrupted rewrite"
        )
    displaced_ref = reconciliation.get("displaced_evidence_ref")
    rewritten_ref = reconciliation.get("rewritten_evidence_ref")
    if not isinstance(displaced_ref, str) or not isinstance(rewritten_ref, str):
        raise ReconciliationTransactionError(
            "interrupted execution rewrite evidence is invalid"
        )
    try:
        displaced = resolve_ref_commit(owner.repo, displaced_ref, runner=_git)
        rewritten = resolve_ref_commit(owner.repo, rewritten_ref, runner=_git)
    except GitObservationError as error:
        raise ReconciliationTransactionError(
            f"cannot inspect interrupted execution rewrite evidence: {error}"
        ) from error
    if displaced != checkpoint or rewritten != rewrite_checkpoint:
        raise ReconciliationTransactionError(
            "interrupted execution rewrite evidence is invalid"
        )
    try:
        commits = commit_range(
            workspace.path,
            f"{admission.target}..{rewrite_checkpoint}",
            reverse=True,
            runner=_git,
        )
    except GitObservationError as error:
        raise ReconciliationTransactionError(
            f"cannot inspect interrupted execution rewrite: {error}"
        ) from error
    if not commits:
        raise ReconciliationTransactionError(
            "interrupted execution rewrite result is invalid"
        )
    previous = admission.target
    for commit in commits:
        try:
            parents = commit_parents(
                workspace.path, commit, absent_is_none=False, runner=_git
            )
        except GitObservationError as error:
            raise ReconciliationTransactionError(
                f"cannot inspect interrupted execution rewrite: {error}"
            ) from error
        if parents is None or len(parents) != 1 or parents[0] != previous:
            raise ReconciliationTransactionError(
                "interrupted execution rewrite is not linear"
            )
        previous = commit
    if previous != rewrite_checkpoint:
        raise ReconciliationTransactionError(
            "interrupted execution rewrite does not reach HEAD"
        )
    return _resolve(
        owner,
        ticket_id,
        reconciliation,
        admission.target,
        rewrite_checkpoint,
        rewrite_checkpoint,
        str(reconciliation.get("rewritten_evidence_ref")),
    )


def recover_local_pending(owner: Any, ticket_id: str, admission: UpdateBaseAdmission,
                          rewrite_published: bool) -> int:
    reconciliation = owner._state["reconciliation"]
    manager = owner.reconciliation_workspace_manager(ticket_id)
    workspace = owner._execution_workspace_for_rewrite_recovery(manager)
    checkpoint = str(reconciliation["worker_checkpoint"])
    if reconciliation.get("rewrite_target") != admission.target:
        raise ReconciliationTransactionError(
            "interrupted execution rewrite target does not match the requested target"
        )
    if workspace.dirty:
        aborted = _git(workspace.path, "rebase", "--abort")
        if aborted.returncode:
            raise ReconciliationTransactionError(
                "interrupted execution rewrite workspace is dirty"
            )
        workspace = owner._execution_workspace_for_rewrite_recovery(manager)
    if workspace.head != checkpoint:
        try:
            commits = commit_range(
                workspace.path,
                f"{admission.target}..{workspace.head}",
                reverse=True,
                runner=_git,
            )
        except GitObservationError as error:
            raise ReconciliationTransactionError(
                f"cannot inspect interrupted execution rewrite: {error}"
            ) from error
        if (len(commits) != reconciliation.get("rewrite_count") or
                not _linear(owner, workspace.path, admission.target, commits)):
            raise ReconciliationTransactionError(
                "interrupted execution rewrite cannot be safely aborted"
            )
        restored = reset_hard(workspace.path, checkpoint, runner=_git)
        if restored.returncode:
            raise ReconciliationTransactionError(
                "interrupted execution rewrite could not be safely aborted"
            )
    owner._save_state(
        "idle",
        execution_base_head=admission.original_base,
        execution_start_head=admission.original_base,
        reconciliation=_clear_rewrite(
            reconciliation, {"rewrite_stage", "rewrite_target", "rewrite_count"}
        ),
    )
    return start_fresh_rewrite(owner, ticket_id, admission, rewrite_published)


def _linear(owner: Any, workspace: Any, root: str, commits: Sequence[str]) -> bool:
    previous = root
    for commit in commits:
        try:
            parents = commit_parents(
                workspace, commit, absent_is_none=False, runner=_git
            )
        except GitObservationError:
            return False
        if parents is None or len(parents) != 1 or parents[0] != previous:
            return False
        previous = commit
    return True


def start_fresh_rewrite(owner: Any, ticket_id: str, admission: UpdateBaseAdmission,
                        rewrite_published: bool) -> int:
    reconciliation = owner._state["reconciliation"]
    manager = owner.reconciliation_workspace_manager(ticket_id)
    workspace = owner._execution_workspace_for_recovery()
    checkpoint = admission.checkpoint
    if workspace.head != checkpoint or workspace.dirty:
        raise ReconciliationTransactionError(
            "execution workspace changed before reconciliation"
        )
    try:
        lineage = list(
            prove_workspace_lineage(
                owner, workspace, admission.original_base, checkpoint
            )
        )
    except ReconciliationTransactionError as error:
        reason = str(error).replace("a merge", "merge")
        if reason == "lineage contains merge, side branch, or gap":
            reason = (
                "execution lineage contains merge, side branch, or unrelated commit"
            )
        raise ReconciliationTransactionError(reason) from error
    execution_remote = owner._execution_remote_head(manager.branch)
    expected_remote = admission.execution_remote_head
    if execution_remote != expected_remote:
        raise ReconciliationTransactionError(
            "execution branch remote changed since reconciliation observation"
        )
    if execution_remote is not None:
        if not rewrite_published:
            raise ReconciliationTransactionError(
                f"published execution ref refs/heads/{manager.branch} requires "
                "--rewrite-published authorization"
            )
        if execution_remote not in lineage:
            raise ReconciliationTransactionError(
                "published execution ref is outside the proven execution lineage"
            )
    previous_execution_base = owner._state["execution_base_head"]
    try:
        admission = admit_update_base(owner, ticket_id, admission.target)
    except ReconciliationTransactionError:
        raise
    operation = {
        **reconciliation,
        "rewrite_authorized": bool(rewrite_published),
        "rewrite_expected_remote": execution_remote,
        "displaced_evidence_ref": admission.evidence_ref,
        "rewritten_evidence_ref": rewritten_evidence_ref(
            ticket_id, str(reconciliation["execution_id"])
        ),
        "rewrite_target": admission.target,
        "rewrite_count": len(lineage),
        "rewrite_stage": "local-pending",
    }
    owner._save_state("idle", reconciliation=operation)
    owner._state["execution_base_head"] = admission.target
    try:
        rebased = _git(workspace.path, "rebase", "--onto", admission.target,
                       admission.original_base)
        if rebased.returncode:
            _git(workspace.path, "rebase", "--abort")
            owner._state["execution_base_head"] = previous_execution_base
            try:
                restored = resolve_commit(workspace.path, "HEAD", runner=_git)
                clean = not working_tree_status(workspace.path, runner=_git)
            except GitObservationError as error:
                raise ReconciliationTransactionError(
                    f"reconciliation conflict could not be safely observed: {error}"
                ) from error
            if restored != admission.checkpoint or not clean:
                raise ReconciliationTransactionError(
                    "reconciliation conflict could not be safely aborted"
                )
            raise ReconciliationTransactionError(
                "reconciliation conflict; manual/advanced reconciliation is required"
            )
    except OSError as error:
        owner._state["execution_base_head"] = previous_execution_base
        raise ReconciliationTransactionError(
            f"reconciliation failed: {error}"
        ) from error
    updated = owner._execution_workspace_for_recovery()
    try:
        rewritten = list(authorize_workspace_rewrite(owner, updated, admission.target,
                                                       updated.head, execution_remote,
                                                       rewrite_published))
        rewrite_error = ""
    except ReconciliationTransactionError as error:
        rewritten = []
        rewrite_error = str(error)
    if (updated.dirty or rewrite_error or len(rewritten) != len(lineage) or
            updated.head == admission.target):
        restored = reset_hard(workspace.path, admission.checkpoint, runner=_git)
        owner._state["execution_base_head"] = previous_execution_base
        if restored.returncode:
            raise ReconciliationTransactionError(
                "reconciled execution lineage is invalid"
            )
        owner._save_state("idle", execution_base_head=admission.original_base,
                          execution_start_head=admission.original_base,
                          reconciliation=reconciliation)
        raise ReconciliationTransactionError(
            rewrite_error or "reconciled execution lineage is invalid"
        )
    if not _linear(owner, updated.path, admission.target, rewritten):
        raise ReconciliationTransactionError(
            "reconciled execution lineage is not ordered and linear"
        )
    if execution_remote is not None:
        operation = {**operation, "rewrite_stage": "remote-pending",
                     "rewrite_checkpoint": updated.head}
        owner._save_state("idle", reconciliation=operation)
    rewritten_evidence = str(operation["rewritten_evidence_ref"])
    publish_rewritten_lineage(owner, branch=manager.branch, rewritten_head=updated.head,
                              expected_remote=execution_remote,
                              evidence_ref=rewritten_evidence)
    return _resolve(owner, ticket_id, operation, admission.target, updated.head,
                    updated.head if execution_remote is not None else None,
                    rewritten_evidence)


class _NoopContext:
    def __enter__(self) -> _NoopContext:
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        return None
