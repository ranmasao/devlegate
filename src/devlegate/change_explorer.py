# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Bounded, immutable-tree observations for the owner-side Change Explorer.

This component is not an authorization boundary or a worker tool. Its caller
must supply a proven namespace and an authorized repository. WORKTREE requires
a separately verified snapshot and is deliberately unavailable here.
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import selectors
import subprocess
import time
from pathlib import Path

from devlegate.change_revisions import RevisionNamespace, RevisionUnavailable

_OBJECT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_TREE_LIMIT = 2 * 1024 * 1024
_FILE_LIMIT = 32 * 1024
_LINE_LIMIT = 4096


class ObservationError(Exception):
    """A bounded observation cannot be completed safely."""


def _git(repo: Path, *args: str, limit: int) -> bytes:
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_NO_REPLACE_OBJECTS="1")
    process = subprocess.Popen(
        ["git", "-c", "core.fsmonitor=false", *args], cwd=repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + 3
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                timeout = deadline - time.monotonic()
                if timeout <= 0 or not selector.select(timeout):
                    raise ObservationError("TOO_EXPENSIVE")
                chunk = os.read(
                    process.stdout.fileno(), min(65536, limit + 1 - len(output))
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ObservationError("TOO_EXPENSIVE")
        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
            raise ObservationError("UNAVAILABLE")
        return bytes(output)
    except subprocess.TimeoutExpired as error:
        raise ObservationError("TOO_EXPENSIVE") from error
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def _path(path: object) -> str:
    if not isinstance(path, str) or not path or len(path.encode("utf-8")) > 4096:
        raise ObservationError("INVALID_PATH")
    if (path.startswith("/") or any(c in path for c in "\x00*?[]\\")
            or any(part in {"", ".", "..", ".git"} for part in path.split("/"))):
        raise ObservationError("INVALID_PATH")
    return path


class ImmutableExplorer:
    """Compare owner-supplied immutable task trees, never arbitrary worker refs."""

    def __init__(self, repo: Path, revisions: RevisionNamespace, secret: bytes):
        if len(secret) < 32:
            raise ValueError("cursor secret must contain at least 32 bytes")
        self._repo, self._revisions, self._secret = repo, revisions, secret

    def revisions(self) -> dict[str, object]:
        return self._revisions.inventory()

    def _trees(self, before: str, after: str) -> tuple[dict, dict]:
        try:
            a, b = self._revisions.comparison(before, after)
        except (RevisionUnavailable, TypeError) as error:
            raise ObservationError("REVISION_UNAVAILABLE") from error
        if a.identity is None or b.identity is None:
            raise ObservationError("WORKTREE_UNAVAILABLE")
        return self._tree(a.identity), self._tree(b.identity)

    def _tree(self, identity: str) -> dict[str, tuple[str, str]]:
        if not _OBJECT.fullmatch(identity):
            raise ObservationError("REVISION_UNAVAILABLE")
        data = _git(self._repo, "ls-tree", "-r", "-z", "--full-tree", identity,
                    limit=_TREE_LIMIT)
        result = {}
        try:
            for record in data.split(b"\0"):
                if not record:
                    continue
                meta, raw_path = record.split(b"\t", 1)
                mode, _kind, obj = meta.decode("ascii").split(" ")
                path = raw_path.decode("utf-8")
                _path(path)
                if mode not in {"100644", "100755", "120000", "160000"}:
                    raise ValueError
                if not _OBJECT.fullmatch(obj):
                    raise ValueError
                result[path] = (mode, obj)
        except (ValueError, UnicodeError, ObservationError) as error:
            raise ObservationError("UNAVAILABLE") from error
        return result

    def _cursor(self, operation: str, before: str, after: str, path: str,
                offset: int) -> str:
        payload = json.dumps([operation, before, after, path, offset],
                             separators=(",", ":"), ensure_ascii=True).encode()
        tag = self._cursor_tag(payload, before, after)
        return payload.hex() + "." + tag

    def _cursor_tag(self, payload: bytes, before: str, after: str) -> str:
        try:
            a, b = self._revisions.comparison(before, after)
        except (RevisionUnavailable, TypeError) as error:
            raise ObservationError("REVISION_UNAVAILABLE") from error
        if a.identity is None or b.identity is None:
            raise ObservationError("WORKTREE_UNAVAILABLE")
        binding = json.dumps([a.identity, b.identity]).encode()
        return hmac.new(
            self._secret, binding + b"\0" + payload, hashlib.sha256
        ).hexdigest()

    def _offset(self, cursor: str | None, operation: str, before: str,
                after: str, path: str) -> int:
        if cursor is None:
            return 0
        try:
            if not isinstance(cursor, str) or len(cursor) > 20000:
                raise ValueError
            encoded, tag = cursor.split(".")
            payload = bytes.fromhex(encoded)
            expected = self._cursor_tag(payload, before, after)
            if not hmac.compare_digest(tag, expected):
                raise ValueError
            op, a, b, p, offset = json.loads(payload)
            if (op, a, b, p) != (operation, before, after, path):
                raise ValueError
            if type(offset) is not int or offset <= 0:
                raise ValueError
            return offset
        except (ValueError, TypeError, UnicodeError) as error:
            raise ObservationError("INVALID_CURSOR") from error

    def changes(self, before: str, after: str, cursor: str | None = None) -> dict:
        offset = self._offset(cursor, "changes", before, after, "")
        a, b = self._trees(before, after)
        paths = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
        if offset > len(paths):
            raise ObservationError("INVALID_CURSOR")
        records = []
        for path in paths[offset:offset + 30]:
            old, new = a.get(path), b.get(path)
            mode = (new or old)[0]
            kind = {"120000": "symlink", "160000": "submodule"}.get(mode, "file")
            record = {"path": path, "change": "added" if old is None else
                      "deleted" if new is None else "modified", "kind": kind,
                      "before_mode": old[0] if old else None,
                      "after_mode": new[0] if new else None,
                      "before_size": None, "after_size": None,
                      "added_lines": None, "deleted_lines": None}
            if old and new and (old[0] == "160000" or new[0] == "160000"):
                record["pointer_changed"] = old[1] != new[1]
            records.append(record)
        end = offset + len(records)
        more = end < len(paths)
        return {"status": "OK", "records": records, "complete": not more,
                "has_more": more, "next_cursor": self._cursor(
                    "changes", before, after, "", end) if more else None}

    def diff(self, before: str, after: str, path: str,
             cursor: str | None = None) -> dict:
        path = _path(path)
        offset = self._offset(cursor, "diff", before, after, path)
        a, b = self._trees(before, after)
        if any(path.startswith(p + "/") for p, entry in (a | b).items()
               if entry[0] == "160000"):
            raise ObservationError("INVALID_PATH")
        old, new = a.get(path), b.get(path)
        if old is None and new is None:
            raise ObservationError("INVALID_PATH")
        if any(entry and entry[0] == "160000" for entry in (old, new)):
            return {"status": "SUBMODULE_CONTENT_UNAVAILABLE", "complete": True,
                    "has_more": False, "pointer_changed": old != new,
                    "guidance": "Uncommitted submodule edits are not preserved "
                    "by a superproject checkpoint."}
        if old and new and old[0] != new[0] and "120000" in (old[0], new[0]):
            raise ObservationError("TYPE_CHANGE_UNAVAILABLE")
        contents = []
        for entry in (old, new):
            if entry is None:
                contents.append("")
                continue
            size = _git(self._repo, "cat-file", "-s", entry[1], limit=128)
            if int(size) > _FILE_LIMIT:
                raise ObservationError("TOO_LARGE")
            data = _git(self._repo, "cat-file", "blob", entry[1], limit=_FILE_LIMIT)
            if b"\x00" in data:
                raise ObservationError("BINARY_DIFF_UNAVAILABLE")
            try:
                text = data.decode("utf-8")
            except UnicodeError as error:
                raise ObservationError("BINARY_DIFF_UNAVAILABLE") from error
            if any(
                len(line.encode()) > _LINE_LIMIT
                for line in text.splitlines(keepends=True)
            ):
                raise ObservationError("LINE_TOO_LONG")
            contents.append(text)
        lines_a, lines_b = (text.splitlines(keepends=True) for text in contents)
        if len(lines_a) + len(lines_b) > 1200:
            raise ObservationError("TOO_EXPENSIVE")
        lines = list(difflib.unified_diff(lines_a, lines_b, fromfile="a/" + path,
                                          tofile="b/" + path))
        lines = [part for line in lines for part in (
            [line] if line.endswith("\n") else
            [line + "\n", "\\ No newline at end of file\n"]
        )]
        if offset > len(lines):
            raise ObservationError("INVALID_CURSOR")
        page = []
        while offset + len(page) < len(lines) and len(page) < 120:
            candidate = page + [lines[offset + len(page)]]
            if len(json.dumps(candidate, ensure_ascii=True).encode()) > 6000:
                break
            page = candidate
        if not page and offset < len(lines):
            raise ObservationError("LINE_TOO_LONG")
        end = offset + len(page)
        more = end < len(lines)
        return {"status": "OK", "lines": page, "complete": not more,
                "has_more": more, "next_cursor": self._cursor(
                    "diff", before, after, path, end) if more else None}
