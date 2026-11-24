# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Execution-scoped, owner-polled Change Explorer channel."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import struct
from collections.abc import Callable
from pathlib import Path

from devlegate.abandonment_evidence import EvidenceError
from devlegate.change_explorer import ImmutableExplorer, ObservationError, serialize
from devlegate.change_revisions import RevisionNamespace
from devlegate.evidence_channel import EvidenceSocket
from devlegate.ipc_protocol import encode_frame


class RevisionChannel(EvidenceSocket):
    def __init__(self, path: Path, namespace: RevisionNamespace | None,
                 authorized: Callable[[int], bool], repo: Path | None = None, *,
                 git_dir: Path | None = None, common_dir: Path | None = None) -> None:
        self.token = secrets.token_hex(32)
        self.namespace = namespace
        self.authorized = authorized
        self.explorer = (ImmutableExplorer(
            repo, namespace, secrets.token_bytes(32),
            git_dir=git_dir, common_dir=common_dir,
        ) if repo is not None and namespace is not None else None)
        self._disabled = False
        self._budget_notice = encode_frame(serialize(self._status("BUDGET_EXHAUSTED")))
        super().__init__(None, path, requests=4096, response_bytes=65536,
                         session_seconds=float("inf"),
                          reply=self._revision_reply)

    def poll(self) -> None:
        if self._disabled:
            return
        try:
            super().poll()
        except (EvidenceError, OSError):
            # An exhausted or broken optional read channel must not terminate
            # the worker or change execution authorization.
            self.close()

    def close(self) -> None:
        if not self._disabled:
            self._disabled = True
            super().close()

    @staticmethod
    def _status(status: str) -> dict:
        return {"status": status, "complete": False, "has_more": False,
                "next_cursor": None}

    def _authorized_peer(self) -> bool:
        if self._client is None:
            return False
        size = struct.calcsize("3i")
        pid, uid, _gid = struct.unpack("3i", self._client.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, size,
        ))
        return uid == os.getuid() and pid > 0 and self.authorized(pid)

    def _revision_reply(self, raw: bytes) -> bytes:
        if self._remaining < len(self._budget_notice):
            raise EvidenceError("Change Explorer budget exhausted")
        try:
            request = json.loads(raw)
            valid = (isinstance(request, dict)
                     and isinstance(request["token"], str)
                     and isinstance(request.get("operation"), str)
                     and hmac.compare_digest(request["token"], self.token)
                     and self._authorized_peer())
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            valid = False
        if not valid:
            result = self._status("UNAVAILABLE")
        elif request.get("operation") == "revisions" and set(request) == {
            "token", "operation",
        } and self.namespace is not None:
            result = {**self._status("OK"), "complete": True,
                      **self.namespace.inventory()}
        elif request.get("operation") in {"revisions", "changes", "diff", "check"} and (
            self.namespace is None
        ):
            result = self._status("HISTORY_UNAVAILABLE")
        else:
            operation = request.get("operation")
            required = {"token", "operation", "from", "to"}
            if operation == "diff":
                required.add("path")
            allowed = required | {"cursor"}
            if (operation not in {"changes", "diff", "check"} or self.explorer is None
                    or not required <= set(request) <= allowed
                    or not all(isinstance(request[k], str) for k in required)
                    or ("cursor" in request
                        and not isinstance(request["cursor"], str))):
                result = self._status("UNAVAILABLE")
            else:
                try:
                    args = [request["from"], request["to"]]
                    if operation == "diff":
                        args.append(request["path"])
                    result = getattr(self.explorer, operation)(
                        *args, cursor=request.get("cursor")
                    )
                except (ObservationError, OSError, ValueError) as error:
                    status = (str(error) if isinstance(error, ObservationError)
                              else "UNAVAILABLE")
                    result = self._status(status)
        payload = serialize(result)
        if len(payload) > 8192:
            payload = serialize(self._status("TOO_LARGE"))
        frame = encode_frame(payload)
        if len(frame) + len(self._budget_notice) > self._remaining:
            self._remaining = len(self._budget_notice)
            return self._budget_notice
        return frame


def revisions_tool(path: Path, token: str) -> str:
    """Materialize a client with no worker-supplied authority or Git inputs."""
    return '''import { tool } from "@opencode-ai/plugin"
import { createConnection } from "node:net"
export default tool({
  description: "List available virtual task checkpoints and endpoints. " +
    "These are task states, not raw Git commits; use the inventory to " +
    "select only useful comparisons.",
  args: {},
  async execute() {
    const payload = Buffer.from(JSON.stringify({operation: "revisions", token: TOKEN}))
    const header = Buffer.alloc(4); header.writeUInt32BE(payload.length)
    return await new Promise((resolve, reject) => {
      const socket = createConnection(SOCKET)
      let received = Buffer.alloc(0)
      socket.setTimeout(30000, () => socket.destroy(new Error("request timed out")))
      socket.on("connect", () => socket.write(Buffer.concat([header, payload])))
      socket.on("data", chunk => {
        received = Buffer.concat([received, chunk])
        if (received.length > 8196) {
          socket.destroy(new Error("response too large")); return
        }
        if (received.length >= 4 && received.length === received.readUInt32BE(0) + 4) {
          socket.end(); resolve(received.subarray(4).toString("utf8"))
        }
      })
      socket.on("error", () => reject(new Error("Change Explorer unavailable")))
      socket.on("end", () => {
        if (received.length < 4 || received.length !== received.readUInt32BE(0) + 4)
          reject(new Error("incomplete response"))
      })
    })
  },
})
'''.replace("TOKEN", json.dumps(token)).replace("SOCKET", json.dumps(str(path)))


def comparison_tool(path: Path, token: str, operation: str) -> str:
    """Build one narrowly scoped client; authority remains in the owner."""
    if operation not in {"changes", "diff", "check"}:
        raise ValueError("unknown operation")
    extra = ""
    values = ""
    if operation == "changes":
        description = ("List only paths and metadata of changed files; never code or "
                       "patch text. Prefer this before individual diffs and page only "
                       "as far as needed. Submodule status is structural; .gitmodules "
                       "is separate and parent checkpoints do not save nested edits.")
    elif operation == "diff":
        description = ("Return a bounded text diff of one explicit file identified "
                       "with changes first. Request more pages only as needed; "
                       "has_more means the diff is incomplete. Binary, oversized or "
                       "expensive diffs can be unavailable, not unchanged. Submodule "
                       "nested diffs are unavailable; .gitmodules is separate and "
                       "parent checkpoints do not save nested edits.")
        extra = "path: tool.schema.string(),"
        values = ", path: args.path"
    else:
        description = (
            "On demand, use the equivalent of git diff --check for introduced "
            "whitespace errors and conflict markers. Use HEAD to WORKTREE for "
            "this attempt or BASE to WORKTREE for cumulative changes. Findings "
            "are advice, not a failure or an automatic fix; partial or "
            "unavailable results are not clean."
        )
    description += " WORKTREE pages are live observations; re-query after edits."
    client = revisions_tool(path, token)
    start = client.index('  description:')
    end = client.index('    const header', start)
    client = (client[:start] + f'''  description: {json.dumps(description)},
  args: {{from: tool.schema.string(), to: tool.schema.string(), {extra}
    cursor: tool.schema.string().optional()}},
  async execute(args) {{
    const payload = Buffer.from(JSON.stringify({{operation: {json.dumps(operation)},
      token: {json.dumps(token)}, from: args.from, to: args.to{values},
      ...(args.cursor === undefined ? {{}} : {{cursor: args.cursor}})}}))
''' + client[end:])
    return client
