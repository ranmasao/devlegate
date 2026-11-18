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
from devlegate.change_revisions import RevisionNamespace
from devlegate.evidence_channel import EvidenceSocket
from devlegate.ipc_protocol import encode_frame


class RevisionChannel(EvidenceSocket):
    def __init__(self, path: Path, namespace: RevisionNamespace | None,
                 authorized: Callable[[], bool]) -> None:
        self.token = secrets.token_hex(32)
        self.namespace = namespace
        self.authorized = authorized
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
                     and set(request) == {"token", "operation"}
                     and isinstance(request["token"], str)
                     and hmac.compare_digest(request["token"], self.token)
                     and request["operation"] == "revisions" and self.authorized())
        except (ValueError, UnicodeError, OSError):
            valid = False
        if not valid:
            result = {"status": "UNAVAILABLE"}
        elif self.namespace is None:
            result = {"status": "HISTORY_UNAVAILABLE"}
        else:
            result = {"status": "OK", **self.namespace.inventory()}
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
