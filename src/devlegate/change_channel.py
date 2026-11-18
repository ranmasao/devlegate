# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Execution-scoped, owner-polled revision inventory channel.

This initial channel deliberately does not infer checkpoint history from Git
parentage. A missing owner-proven namespace is reported as unavailable.
"""

from __future__ import annotations

import hmac
import json
import secrets
from collections.abc import Callable
from pathlib import Path

from devlegate.abandonment_evidence import EvidenceError
from devlegate.change_explorer import ImmutableExplorer, ObservationError
from devlegate.change_revisions import RevisionNamespace
from devlegate.evidence_channel import EvidenceSocket
from devlegate.ipc_protocol import encode_frame


class RevisionChannel(EvidenceSocket):
    def __init__(self, path: Path, namespace: RevisionNamespace | None,
                 authorized: Callable[[], bool], repo: Path | None = None) -> None:
        self.token = secrets.token_hex(32)
        self.namespace = namespace
        self.authorized = authorized
        self.explorer = (ImmutableExplorer(repo, namespace, secrets.token_bytes(32))
                         if repo is not None and namespace is not None else None)
        self._disabled = False
        super().__init__(None, path, requests=256, response_bytes=65536,
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

    def _revision_reply(self, raw: bytes) -> bytes:
        try:
            request = json.loads(raw)
            valid = (isinstance(request, dict)
                     and isinstance(request["token"], str)
                     and isinstance(request.get("operation"), str)
                     and hmac.compare_digest(request["token"], self.token)
                     and self.authorized())
        except (ValueError, UnicodeError, OSError, KeyError, TypeError):
            valid = False
        if not valid:
            result = {"status": "UNAVAILABLE"}
        elif request.get("operation") == "revisions" and set(request) == {
            "token", "operation",
        } and self.namespace is not None:
            result = {"status": "OK", **self.namespace.inventory()}
        elif request.get("operation") in {"revisions", "changes", "diff"} and (
            self.namespace is None
        ):
            result = {"status": "HISTORY_UNAVAILABLE"}
        else:
            operation = request.get("operation")
            required = {"token", "operation", "from", "to"}
            if operation == "diff":
                required.add("path")
            allowed = required | {"cursor"}
            if (operation not in {"changes", "diff"} or self.explorer is None
                    or not required <= set(request) <= allowed
                    or not all(isinstance(request[k], str) for k in required)
                    or ("cursor" in request
                        and not isinstance(request["cursor"], str))):
                result = {"status": "UNAVAILABLE"}
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
                    result = {"status": status, "complete": False, "has_more": False}
        return encode_frame(json.dumps(result, separators=(",", ":")).encode())


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
      socket.on("error", reject)
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
    if operation not in {"changes", "diff"}:
        raise ValueError("unknown operation")
    if operation == "changes":
        description = ("List only paths and metadata of changed files; never code or "
                       "patch text. Prefer this before individual diffs and page only "
                       "as far as needed. Submodule status is structural; .gitmodules "
                       "is separate and parent checkpoints do not save nested edits.")
        extra = ""
        values = ""
    else:
        description = ("Return a bounded text diff of one explicit file identified "
                       "with changes first. Request more pages only as needed; "
                       "has_more means the diff is incomplete. Binary, oversized or "
                       "expensive diffs can be unavailable, not unchanged. Submodule "
                       "nested diffs are unavailable; .gitmodules is separate and "
                       "parent checkpoints do not save nested edits.")
        extra = "path: tool.schema.string(),"
        values = ", path: args.path"
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
