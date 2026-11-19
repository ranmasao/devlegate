# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Bounded tree observations for the owner-side Change Explorer.

This component is not an authorization boundary or a worker tool. Its caller
must supply a proven namespace and an authorized repository.
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import selectors
import stat
import subprocess
import time
from pathlib import Path

from devlegate.change_revisions import RevisionNamespace, RevisionUnavailable

_OBJECT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_TREE_LIMIT = 2 * 1024 * 1024
_FILE_LIMIT = 32 * 1024
_DIFF_FILE_LIMIT = 4 * 1024 * 1024
_LINE_LIMIT = 4096
_PAGE_LIMIT = 8 * 1024
# Both verification passes share this budget. Index metadata is not trusted as
# evidence of unchanged contents (including for assume-unchanged entries).
_WORKTREE_LIMIT = 128 * 1024 * 1024
_WORKTREE_SECONDS = 3


def _serialized_size(value: object) -> int:
    """Count the complete JSON response, including escaped metadata."""
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


class ObservationError(Exception):
    """A bounded observation cannot be completed safely."""


def _git(repo: Path, *args: str, limit: int, deadline: float | None = None) -> bytes:
    deadline = min(deadline, time.monotonic() + 3) if deadline is not None else (
        time.monotonic() + 3
    )
    if time.monotonic() >= deadline:
        raise ObservationError("TOO_EXPENSIVE")
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
        self._worktree_blobs: dict[str, bytes] = {}
        self._large_worktree_blobs: set[str] = set()
        self._observation = ""
        self._worktree_sizes: dict[str, int] = {}

    def revisions(self) -> dict[str, object]:
        return self._revisions.inventory()

    def _trees(
        self, before: str, after: str, diff_path: str | None = None
    ) -> tuple[dict, dict]:
        try:
            a, b = self._revisions.comparison(before, after)
        except (RevisionUnavailable, TypeError) as error:
            raise ObservationError("REVISION_UNAVAILABLE") from error
        self._worktree_blobs = {}
        self._large_worktree_blobs = set()
        self._worktree_sizes = {}
        worktree = None
        if a.identity is None or b.identity is None:
            # There is no owner-controlled change tracker for this workspace.
            # Consequently completeness requires content-reading every eligible
            # entry, not trusting index/stat shortcuts. Two bounded passes detect
            # changes between observations; they do not make a mutable worktree
            # an atomic snapshot. Cursors re-observe content before every page.
            budget = [_WORKTREE_LIMIT, time.monotonic() + _WORKTREE_SECONDS]
            worktree = self._worktree(diff_path, budget)
            if worktree != self._worktree(diff_path, budget):
                raise ObservationError("WORKTREE_UNAVAILABLE")
        trees = (self._tree(a.identity) if a.identity else worktree,
                 self._tree(b.identity) if b.identity else worktree)
        self._observation = hmac.new(
            self._secret, repr(trees).encode(), hashlib.sha256
        ).hexdigest()
        return trees

    def _worktree(
        self, diff_path: str | None, budget: list
    ) -> dict[str, tuple[str, str]]:
        """Read bounded parent entries without traversing symlinks or gitlinks."""
        index = _git(self._repo, "ls-files", "--stage", "-z", limit=_TREE_LIMIT,
                     deadline=budget[1])
        others = _git(self._repo, "ls-files", "--others", "--exclude-standard",
                      "-z", limit=_TREE_LIMIT, deadline=budget[1])
        entries: dict[str, tuple[str, str] | None] = {}
        try:
            for record in index.split(b"\0"):
                if not record:
                    continue
                meta, raw_path = record.split(b"\t", 1)
                mode, obj, stage = meta.decode("ascii").split(" ")
                path = _path(raw_path.decode("utf-8"))
                if stage != "0" or mode not in {"100644", "100755", "120000", "160000"}:
                    raise ObservationError("WORKTREE_UNAVAILABLE")
                entries[path] = (mode, obj)
            for raw_path in others.split(b"\0"):
                if raw_path and not raw_path.endswith(b"/"):
                    entries.setdefault(_path(raw_path.decode("utf-8")), None)
        except (ValueError, UnicodeError) as error:
            raise ObservationError("WORKTREE_UNAVAILABLE") from error
        result = {}
        deadline = budget[1]
        root = os.open(self._repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for path, indexed in sorted(entries.items()):
                if time.monotonic() >= deadline:
                    raise ObservationError("TOO_EXPENSIVE")
                # A one-file diff needs only that entry and any enclosing
                # gitlink, not the content of unrelated worktree files.
                if diff_path is not None and path != diff_path and not (
                    indexed and indexed[0] == "160000"
                    and diff_path.startswith(path + "/")
                ):
                    continue
                if indexed and indexed[0] == "160000":
                    result[path] = indexed
                    continue
                directory = os.dup(root)
                try:
                    parts = path.split("/")
                    for part in parts[:-1]:
                        child = os.open(
                            part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory,
                        )
                        os.close(directory)
                        directory = child
                    try:
                        info = os.stat(parts[-1], dir_fd=directory,
                                       follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISLNK(info.st_mode):
                        data = os.fsencode(os.readlink(parts[-1], dir_fd=directory))
                        current = os.stat(parts[-1], dir_fd=directory,
                                          follow_symlinks=False)
                        if _file_identity(info) != _file_identity(current):
                            raise ObservationError("WORKTREE_UNAVAILABLE")
                        mode = "120000"
                    elif stat.S_ISREG(info.st_mode):
                        self._worktree_sizes[path] = info.st_size
                        if info.st_size > budget[0]:
                            raise ObservationError("TOO_EXPENSIVE")
                        fd = os.open(
                            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                            dir_fd=directory,
                        )
                        try:
                            cache_limit = (
                                _DIFF_FILE_LIMIT if path == diff_path else _FILE_LIMIT
                            )
                            opened = os.fstat(fd)
                            if (not stat.S_ISREG(opened.st_mode)
                                    or _file_identity(opened) != _file_identity(info)):
                                raise ObservationError("WORKTREE_UNAVAILABLE")
                            base = self._revisions.resolve("BASE").identity
                            algorithm = (
                                hashlib.sha256 if len(base) == 64 else hashlib.sha1
                            )
                            digest = algorithm(
                                b"blob " + str(info.st_size).encode() + b"\0"
                            )
                            chunks = []
                            read = 0
                            while True:
                                if time.monotonic() >= deadline:
                                    raise ObservationError("TOO_EXPENSIVE")
                                chunk = os.read(fd, min(65536, info.st_size - read + 1))
                                if not chunk:
                                    break
                                read += len(chunk)
                                budget[0] -= len(chunk)
                                if read > info.st_size:
                                    raise ObservationError("WORKTREE_UNAVAILABLE")
                                digest.update(chunk)
                                if info.st_size <= cache_limit:
                                    chunks.append(chunk)
                            if read != info.st_size:
                                raise ObservationError("WORKTREE_UNAVAILABLE")
                            if _file_identity(os.fstat(fd)) != _file_identity(opened):
                                raise ObservationError("WORKTREE_UNAVAILABLE")
                        finally:
                            os.close(fd)
                        mode = "100755" if info.st_mode & stat.S_IXUSR else "100644"
                        obj = digest.hexdigest()
                        result[path] = (mode, obj)
                        if info.st_size <= cache_limit:
                            self._worktree_blobs[obj] = b"".join(chunks)
                        else:
                            self._large_worktree_blobs.add(obj)
                        continue
                    else:
                        raise ObservationError("WORKTREE_UNAVAILABLE")
                    budget[0] -= len(data)
                    if budget[0] < 0:
                        raise ObservationError("TOO_EXPENSIVE")
                    base = self._revisions.resolve("BASE").identity
                    algorithm = hashlib.sha256 if len(base) == 64 else hashlib.sha1
                    obj = algorithm(
                        b"blob " + str(len(data)).encode() + b"\0" + data
                    ).hexdigest()
                    result[path] = (mode, obj)
                    self._worktree_blobs[obj] = data
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise ObservationError("WORKTREE_UNAVAILABLE") from error
                finally:
                    os.close(directory)
        finally:
            os.close(root)
        return result

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
        payload = json.dumps(
            [operation, before, after, path, offset, self._observation],
            separators=(",", ":"), ensure_ascii=True,
        ).encode()
        tag = self._cursor_tag(payload, before, after)
        return payload.hex() + "." + tag

    def _cursor_tag(self, payload: bytes, before: str, after: str) -> str:
        try:
            a, b = self._revisions.comparison(before, after)
        except (RevisionUnavailable, TypeError) as error:
            raise ObservationError("REVISION_UNAVAILABLE") from error
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
            op, a, b, p, offset, observation = json.loads(payload)
            if (op, a, b, p) != (operation, before, after, path):
                raise ValueError
            if type(offset) is not int or offset <= 0:
                raise ValueError
            if observation != self._observation:
                raise ObservationError("STALE_CURSOR")
            return offset
        except (ValueError, TypeError, UnicodeError) as error:
            raise ObservationError("INVALID_CURSOR") from error

    def changes(self, before: str, after: str, cursor: str | None = None) -> dict:
        a, b = self._trees(before, after)
        offset = self._offset(cursor, "changes", before, after, "")
        mutable = "WORKTREE" in (before, after)
        paths = sorted(p for p in a.keys() | b.keys()
                       if a.get(p) != b.get(p) or (mutable and any(
                           entry and entry[0] == "160000"
                           for entry in (a.get(p), b.get(p)))))
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
            if after == "WORKTREE" and new and new[0] != "160000":
                record["after_size"] = self._worktree_sizes.get(path)
            if before == "WORKTREE" and old and old[0] != "160000":
                record["before_size"] = self._worktree_sizes.get(path)
            if old and new and (old[0] == "160000" or new[0] == "160000"):
                record["pointer_changed"] = old[1] != new[1]
            if mutable and any(entry and entry[0] == "160000" for entry in (old, new)):
                record["checkout_status"] = "unknown"
                if old == new:
                    record["change"] = "unknown"
                record["guidance"] = (
                    "Uncommitted submodule edits are not preserved by a "
                    "superproject checkpoint."
                )
            candidate = records + [record]
            end = offset + len(candidate)
            more = end < len(paths)
            response = {"status": "OK", "records": candidate, "complete": not more,
                        "has_more": more, "next_cursor": self._cursor(
                            "changes", before, after, "", end) if more else None}
            if _serialized_size(response) > _PAGE_LIMIT:
                break
            records = candidate
        if not records and offset < len(paths):
            raise ObservationError("TOO_LARGE")
        end = offset + len(records)
        more = end < len(paths)
        return {"status": "OK", "records": records, "complete": not more,
                "has_more": more, "next_cursor": self._cursor(
                    "changes", before, after, "", end) if more else None}

    def diff(self, before: str, after: str, path: str,
             cursor: str | None = None) -> dict:
        path = _path(path)
        a, b = self._trees(before, after, path)
        offset = self._offset(cursor, "diff", before, after, path)
        if any(path.startswith(p + "/") for p, entry in (a | b).items()
               if entry[0] == "160000"):
            raise ObservationError("INVALID_PATH")
        old, new = a.get(path), b.get(path)
        if old is None and new is None:
            raise ObservationError("INVALID_PATH")
        if any(entry and entry[0] == "160000" for entry in (old, new)):
            result = {"status": "SUBMODULE_CONTENT_UNAVAILABLE", "complete": True,
                      "has_more": False, "pointer_changed": old != new,
                      "guidance": "Uncommitted submodule edits are not preserved "
                      "by a superproject checkpoint."}
            if "WORKTREE" in (before, after):
                result["checkout_status"] = "unknown"
            return result
        if old and new and old[0] != new[0] and "120000" in (old[0], new[0]):
            raise ObservationError("TYPE_CHANGE_UNAVAILABLE")
        contents = []
        for entry in (old, new):
            if entry is None:
                contents.append("")
                continue
            data = self._worktree_blobs.get(entry[1])
            if data is None:
                if entry[1] in self._large_worktree_blobs:
                    raise ObservationError("TOO_LARGE")
                size = _git(self._repo, "cat-file", "-s", entry[1], limit=128)
                if int(size) > _DIFF_FILE_LIMIT:
                    raise ObservationError("TOO_LARGE")
                data = _git(
                    self._repo, "cat-file", "blob", entry[1], limit=_DIFF_FILE_LIMIT
                )
            if len(data) > _DIFF_FILE_LIMIT:
                raise ObservationError("TOO_LARGE")
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
        # Strip identical outer regions before invoking the quadratic matcher.
        # Keep the normal three context lines and adjust hunk coordinates back
        # to the original files. This makes a localized edit in a large file
        # affordable without treating an output limit as a compute limit.
        prefix = 0
        while (prefix < min(len(lines_a), len(lines_b))
               and lines_a[prefix] == lines_b[prefix]):
            prefix += 1
        suffix = 0
        while (suffix < min(len(lines_a), len(lines_b)) - prefix
               and lines_a[-suffix - 1] == lines_b[-suffix - 1]):
            suffix += 1
        shift = max(0, prefix - 3)
        trim_end = max(0, suffix - 3)
        if shift or trim_end:
            lines_a = lines_a[shift:len(lines_a) - trim_end if trim_end else None]
            lines_b = lines_b[shift:len(lines_b) - trim_end if trim_end else None]
        if len(lines_a) + len(lines_b) > 1200:
            raise ObservationError("TOO_EXPENSIVE")
        lines = list(difflib.unified_diff(lines_a, lines_b, fromfile="a/" + path,
                                          tofile="b/" + path))
        if shift:
            def adjust_hunk(match: re.Match) -> str:
                return (f"@@ -{int(match[1]) + shift}{match[2] or ''} "
                        f"+{int(match[3]) + shift}{match[4] or ''} @@")

            lines = [re.sub(r"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@",
                            adjust_hunk, line) if line.startswith("@@ ") else line
                     for line in lines]
        lines = [part for line in lines for part in (
            [line] if line.endswith("\n") else
            [line + "\n", "\\ No newline at end of file\n"]
        )]
        if offset > len(lines):
            raise ObservationError("INVALID_CURSOR")
        page = []
        while offset + len(page) < len(lines) and len(page) < 120:
            candidate = page + [lines[offset + len(page)]]
            end = offset + len(candidate)
            more = end < len(lines)
            response = {"status": "OK", "lines": candidate, "complete": not more,
                        "has_more": more, "next_cursor": self._cursor(
                            "diff", before, after, path, end) if more else None}
            if _serialized_size(response) > _PAGE_LIMIT:
                break
            page = candidate
        if not page and offset < len(lines):
            raise ObservationError("LINE_TOO_LONG")
        end = offset + len(page)
        more = end < len(lines)
        return {"status": "OK", "lines": page, "complete": not more,
                "has_more": more, "next_cursor": self._cursor(
                    "diff", before, after, path, end) if more else None}
