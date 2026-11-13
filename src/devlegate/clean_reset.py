# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Identity-bound operator authorization. No resource effects are implemented."""

import hashlib
import json
import re

from devlegate.clean_preview import SCHEMA, canonical_digest, inspect_clean

INTENT_SCHEMA = "devlegate.clean-reset-intent.v1"
REMOTE_POLICY = "exact-observation-only-no-continuity-claim"


def prepare_reset(owner, ticket_id, digest, generation):
    """Reobserve under the service owner; a supplied digest is never evidence."""
    if owner._state_write_uncertain:
        raise ValueError("runtime state write outcome uncertain; restart required")
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
               for v in (digest, generation)):
        raise ValueError("clean apply requires exact plan digest and generation")
    preview = inspect_clean(owner, ticket_id)
    if not preview["snapshot_complete"] or preview["digest"] != digest:
        raise ValueError("stale or incomplete clean preview; inspect again")
    identity = preview["identity"]
    if identity["ticket"]["state"] not in {"backlog", "done", "abandoned"}:
        raise ValueError("active ticket state cannot authorize reset")
    current = [e for e in identity["executions"]
               if e.get("proof", {}).get("local_state") == "proved"]
    if len(current) != 1 or current[0]["intent"]["generation"] != generation:
        raise ValueError("confirmed generation differs from proved resource")
    persisted, revision = owner._runtime_store.observe()
    # Preview excludes presentation fields; compare the full live state here
    # to avoid treating those exclusions as write authority.
    if persisted != owner._state:
        raise ValueError(
            "runtime authority differs from durable state; restart required"
        )
    return {
        "schema": INTENT_SCHEMA, "status": "authorized-pre-effect",
        "plan_digest": digest, "generation": generation,
        "identity": identity, "remote_policy": REMOTE_POLICY,
        "runtime_revision": revision,
    }


def validate_reset_intent(state):
    """Restore only a complete authorization/receipt pair, never execute it."""
    intent = state.get("clean_reset_intent")
    if intent is None:
        receipts = state.get("mutable_receipts", {})
        if isinstance(receipts, dict) and any(
            isinstance(r, dict) and r.get("method") == "clean-apply"
            for r in receipts.values()
        ):
            raise ValueError("clean reset receipt has no durable intent")
        return
    try:
        if not isinstance(intent, dict) or set(intent) != {
            "schema", "status", "plan_digest", "generation", "identity",
            "remote_policy", "request_id", "fingerprint",
            "runtime_revision",
        }:
            raise ValueError("invalid clean reset intent fields")
        identity = intent["identity"]
        ticket = identity["ticket"]
        if (intent["schema"] != INTENT_SCHEMA
                or intent["status"] != "authorized-pre-effect"
                or intent["remote_policy"] != REMOTE_POLICY
                or identity["schema"] != SCHEMA
                or canonical_digest(identity) != intent["plan_digest"]
                or ticket["state"] not in {"backlog", "done", "abandoned"}
                or state.get("phase") != "idle"
                or state.get("worker_identity") is not None):
            raise ValueError("invalid clean reset authorization")
        if not isinstance(intent["runtime_revision"], str) or not re.fullmatch(
            r"sqlite:[1-9][0-9]*", intent["runtime_revision"],
        ):
            raise ValueError("invalid clean reset runtime revision")
        current = [e for e in identity["executions"]
                   if e.get("proof", {}).get("local_state") == "proved"]
        if len(current) != 1 or (
            current[0]["intent"]["generation"] != intent["generation"]
        ):
            raise ValueError("invalid clean reset generation")
        receipt = state["mutable_receipts"][intent["request_id"]]
        fingerprint = hashlib.sha256(json.dumps({
            "method": "clean-apply", "payload": {
                "ticket_id": ticket["id"], "plan_digest": intent["plan_digest"],
                "generation": intent["generation"],
            },
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (receipt["method"] != "clean-apply" or receipt["accepted"] is not True
                or intent["fingerprint"] != fingerprint
                or receipt["fingerprint"] != intent["fingerprint"]
                or receipt["ticket_id"] != ticket["id"]
                or receipt["result"] != reset_ack(intent)):
            raise ValueError("clean reset receipt differs from authorization")
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"invalid persisted clean reset intent: {error}") from error


def reset_ack(intent):
    return {
        "accepted": True, "ticket_id": intent["identity"]["ticket"]["id"],
        "plan_digest": intent["plan_digest"], "generation": intent["generation"],
        "status": "authorized-pre-effect", "effects_performed": False,
    }
