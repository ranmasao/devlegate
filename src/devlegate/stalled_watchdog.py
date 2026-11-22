# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Serialized live-worker watchdog decision and write-ahead retirement."""

from devlegate.recovery_transactions import (
    persist_stalled_intent,
    retire_stalled_intent,
)
from devlegate.worker_supervisor import observe_worker_identity


def poll_stalled(owner, execution_id, activity, timeout, process, containment):
    """Return a supervisor decision; never signal on an uncommitted intent."""

    def persist_if_eligible(output_evidence):
        state = owner.recovery_state()
        identity = owner.recovery_worker_identity(
            state.get("worker_identity"), execution_id
        )
        if (
            state.get("execution_id") != execution_id
            or state.get("phase") != "agent_running"
            or state.get("execution_stage") != "worker-running"
            or state.get("stalled_recovery") is not None
            or (
                isinstance(state.get("ticket_reset"), dict)
                and state["ticket_reset"].get("status") not in {"queued", "started"}
            )
            or getattr(owner, "_state_write_uncertain", False)
            or getattr(getattr(owner, "_stop_event", None), "kind", None) is not None
            or identity is None
            or containment is None
            or identity.pid != process.pid
            or identity.containment_id != getattr(containment, "identity", None)
            or process.poll() is not None
            or observe_worker_identity(identity) != "matching-live"
        ):
            return False
        member_check = getattr(containment, "has_other_processes", None)
        if member_check is None or member_check(identity.pid) is not False:
            return False
        evidence = owner._workers.analysis_launch_evidence(execution_id)
        if (
            process.poll() is not None
            or observe_worker_identity(identity) != "matching-live"
            or member_check(identity.pid) is not False
        ):
            return False
        persist_stalled_intent(
            owner, launch_evidence=evidence, output_evidence=output_evidence
        )
        return True

    try:
        if not activity.if_idle_with_output(timeout, persist_if_eligible):
            return None
        retire_stalled_intent(owner)
        return "retired"
    except Exception:
        # A failed write may have committed. The supervisor must not interpret
        # this as permission for watchdog retirement or continuation.
        return "failed"
