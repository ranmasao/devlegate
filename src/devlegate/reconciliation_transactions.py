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
from typing import Any, Protocol

from devlegate.reconciliation import (
    LineageProof,
    prove_linear_lineage,
    rewrite_is_authorized,
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

    def reconciliation_git(
        self, cwd: Any, *args: str, check: bool = False
    ) -> Any: ...


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
    result = owner.reconciliation_git(
        workspace.path, "rev-list", "--reverse", f"{root}..{tip}", check=False
    )
    commits = result.stdout.splitlines() if result.returncode == 0 else []
    parents: dict[str, tuple[str, ...]] = {}
    for commit in commits:
        parent_result = owner.reconciliation_git(
            workspace.path,
            "rev-list",
            "--parents",
            "-n",
            "1",
            commit,
            check=False,
        )
        fields = parent_result.stdout.split()
        parents[commit] = tuple(fields[1:]) if parent_result.returncode == 0 else ()
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
