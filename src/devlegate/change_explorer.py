# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Bounded observations of explicitly authorized task revisions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from devlegate.change_revisions import RevisionNamespace, RevisionUnavailable

_OBJECT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_TREE_LIMIT = 2 * 1024 * 1024
_FILE_LIMIT = 4 * 1024 * 1024
_LINE_LIMIT = 4096
_PAGE_LIMIT = 8 * 1024
_OBSERVATION_SECONDS = 5
_LIVE = "Live, best-effort observation; re-query if files change between pages."
_SUBMODULE = "A superproject checkpoint does not save uncommitted submodule edits."


def serialize(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


class ObservationError(Exception):
    """A bounded observation cannot be completed safely."""


def _git(repo: Path, *args: str, limit: int, deadline: float | None = None,
         accepted: tuple[int, ...] = (0,), git_dir: Path | None = None,
         common_dir: Path | None = None, isolated: bool = False,
         require_output: tuple[int, ...] = (), policy_config: bool = False) -> bytes:
    ceiling = time.monotonic() + _OBSERVATION_SECONDS
    deadline = min(deadline, ceiling) if deadline is not None else ceiling
    if time.monotonic() >= deadline:
        raise ObservationError("TOO_EXPENSIVE")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="",
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    if not policy_config:
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    if git_dir is not None and common_dir is not None:
        env.update(GIT_DIR=str(git_dir), GIT_COMMON_DIR=str(common_dir),
                   GIT_WORK_TREE=str(repo))
    if isolated:
        env["GIT_CEILING_DIRECTORIES"] = str(repo.parent)
    process = subprocess.Popen(
        ["git", "--literal-pathspecs", "-c", "core.fsmonitor=false", *args],
        cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
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
                    process.stdout.fileno(), min(65536, limit + 1 - len(output)),
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ObservationError("TOO_EXPENSIVE")
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if code not in accepted or (code in require_output and not output):
            raise ObservationError("UNAVAILABLE")
        return bytes(output)
    except subprocess.TimeoutExpired as error:
        raise ObservationError("TOO_EXPENSIVE") from error
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def _entry_path(path: str) -> str:
    if (not path or len(path.encode()) > 4096 or "\0" in path
            or any(part in {"", ".", "..", ".git"} for part in path.split("/"))):
        raise ObservationError("INVALID_PATH")
    return path


def _path(path: object) -> str:
    if (not isinstance(path, str) or path.startswith(":")
            or any(c in path for c in "\r\n\t*?[]\\")):
        raise ObservationError("INVALID_PATH")
    return _entry_path(path)


def _display_path(prefix: str, path: str) -> str:
    raw = (prefix + path).encode()
    if all(32 <= byte < 127 and byte not in (34, 92) for byte in raw):
        return raw.decode()
    return '"' + ''.join(chr(b) if 32 <= b < 127 and b not in (34, 92)
                         else f"\\{b:03o}" for b in raw) + '"'


def _response(status: str, live: bool, *, complete: bool = False,
              cursor: str | None = None, **fields: object) -> dict:
    result = {"status": status, "complete": complete, "has_more": cursor is not None,
              "next_cursor": cursor, **fields}
    if live:
        result.update(live=True, observation=_LIVE)
    return result


class ImmutableExplorer:
    """Compare owner-supplied task revisions and live editable paths."""

    def __init__(self, repo: Path, revisions: RevisionNamespace, secret: bytes, *,
                 git_dir: Path | None = None, common_dir: Path | None = None):
        if len(secret) < 32:
            raise ValueError("cursor secret must contain at least 32 bytes")
        self._repo, self._revisions, self._secret = repo, revisions, secret
        self._git_dir, self._common_dir = git_dir, common_dir
        self._deadline = 0.0

    def revisions(self) -> dict[str, object]:
        return self._revisions.inventory()

    def _run(self, *args: str, limit: int = _TREE_LIMIT,
             accepted: tuple[int, ...] = (0,), policy_config: bool = False) -> bytes:
        return _git(
            self._repo, *args, limit=limit, deadline=self._deadline, accepted=accepted,
            git_dir=self._git_dir, common_dir=self._common_dir,
            policy_config=policy_config,
        )

    def _tree(self, identity: str, path: str | None) -> dict[str, tuple[str, str]]:
        if not _OBJECT.fullmatch(identity):
            raise ObservationError("REVISION_UNAVAILABLE")
        args = ("--", path) if path is not None else ()
        data = self._run("ls-tree", "-r", "-z", "--full-tree", identity, *args)
        result = {}
        for record in data.split(b"\0"):
            if record:
                meta, raw_path = record.split(b"\t", 1)
                mode, _kind, obj = meta.decode("ascii").split()
                result[_entry_path(raw_path.decode())] = (mode, obj)
        if path and "/" in path:
            # `ls-tree -r -- child` does not emit the enclosing gitlink.
            parent = path.rsplit("/", 1)[0]
            while parent:
                entry = self._run("ls-tree", "-z", identity, "--", parent)
                if entry.startswith(b"160000 "):
                    raise ObservationError("INVALID_PATH")
                parent = parent.rpartition("/")[0]
        return result

    @contextmanager
    def _parent(self, path: str):
        if time.monotonic() >= self._deadline:
            raise ObservationError("TOO_EXPENSIVE")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(self._repo, flags)
        try:
            parts = path.split("/")
            for part in parts[:-1]:
                child = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = child
                try:
                    os.stat(".git", dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ObservationError("INVALID_PATH")
            yield directory, parts[-1]
        finally:
            os.close(directory)

    def _submodule_status(self, path: str) -> str:
        try:
            with self._parent(path) as (directory, name):
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                submodule = os.open(name, flags, dir_fd=directory)
                try:
                    os.stat(".git", dir_fd=submodule, follow_symlinks=False)
                finally:
                    os.close(submodule)
        except FileNotFoundError:
            return "uninitialized_or_absent"
        except (OSError, ObservationError):
            pass
        return "unknown"

    def _conversion_overrides(self) -> list[str]:
        # diff-index may verify a stat-clean entry by running Git's conversion
        # machinery. --no-ext-diff and --no-textconv don't protect that path.
        # Read names (including owner config includes) in the same Git context
        # as diff-index, and disable drivers before consulting live attributes.
        raw = self._run(
            "config", "--includes", "--null", "--name-only", "--get-regexp",
            r"^filter\..*\.(clean|process|required)$", limit=16 * 1024, accepted=(0, 1),
        )
        overrides = []
        for key in sorted(set(raw.split(b"\0")) - {b""}):
            if b"=" in key:  # Cannot represent this key in -c key=value form.
                raise ObservationError("WORKTREE_UNAVAILABLE")
            value = "false" if key.endswith(b".required") else ""
            overrides.extend(("-c", os.fsdecode(key) + "=" + value))
        return overrides

    def _live_file(self, path: str, read: int = 0) -> tuple[str, int, bytes] | None:
        """Open only a selected path, without following symlinks or special files."""
        try:
            with self._parent(path) as (directory, name):
                return self._live_entry(directory, name, read)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ObservationError("WORKTREE_UNAVAILABLE") from error

    @staticmethod
    def _live_entry(directory: int, name: str,
                    read: int) -> tuple[str, int, bytes] | None:
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                data = os.fsencode(os.readlink(name, dir_fd=directory)) if read else b""
                return "120000", info.st_size, data
            if stat.S_ISDIR(info.st_mode):
                return None
            if not stat.S_ISREG(info.st_mode):
                raise ObservationError("WORKTREE_UNAVAILABLE")
            mode = "100755" if info.st_mode & stat.S_IXUSR else "100644"
            if not read:
                return mode, info.st_size, b""
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
            with os.fdopen(fd, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    raise ObservationError("WORKTREE_UNAVAILABLE")
                mode = "100755" if opened.st_mode & stat.S_IXUSR else "100644"
                content = stream.read(min(read, opened.st_size + 1))
                if len(content) != min(read, opened.st_size):
                    raise ObservationError("WORKTREE_UNAVAILABLE")
                return mode, opened.st_size, content
        except FileNotFoundError:
            return None

    def _worktree(self, identity: str, historical: dict, path: str | None) -> dict:
        """Observe changed candidates once; gitlinks never require nested Git."""
        scope = ("--", path) if path else ("--",)
        # Stage records supply parent gitlink pointers and distinguish path
        # conflicts. File content is observed from the live side, not the index.
        index = self._run("ls-files", "--stage", "-z", *scope)
        links = {}
        for record in index.split(b"\0"):
            if record:
                meta, raw_path = record.split(b"\t", 1)
                mode, obj, stage = meta.decode("ascii").split()
                if stage != "0":
                    raise ObservationError("WORKTREE_UNAVAILABLE")
                if mode == "160000" and _OBJECT.fullmatch(obj):
                    links[_entry_path(raw_path.decode())] = (mode, obj)
        if path and "/" in path:
            parent = path.split("/")[:-1]
            prefixes = ["/".join(parent[:i + 1]) for i in range(len(parent))]
            raw = self._run("ls-files", "--stage", "-z", "--", *prefixes)
            if any(record.startswith(b"160000 ") and
                   record.partition(b"\t")[2].decode() in prefixes
                   for record in raw.split(b"\0")):
                raise ObservationError("INVALID_PATH")
        old_links = {p for p, entry in historical.items() if entry[0] == "160000"}
        raw = self._run(
            *self._conversion_overrides(), "diff-index", "--raw", "-z", "--no-renames",
            "--no-ext-diff", "--no-textconv", "--ignore-submodules=all",
            identity, *scope,
        )
        paths = {_entry_path(p.decode()) for p in raw.split(b"\0")[1:-1:2]}
        # After a staged gitlink removal, the former checkout is not an
        # ordinary untracked directory. Never enumerate its contents.
        excluded = ["--exclude=/" + re.sub(r"([\\*?\[\] ])", r"\\\1", p) + "/"
                    for p in sorted(old_links - links.keys())]
        if sum(len(item.encode()) for item in excluded) > 32768:
            raise ObservationError("TOO_EXPENSIVE")
        others = self._run("ls-files", "--others", "--exclude-standard", "-z",
                           *excluded, *scope, policy_config=True)
        if any(p.endswith(b"/") for p in others.split(b"\0")):
            raise ObservationError("WORKTREE_UNAVAILABLE")
        paths.update(_entry_path(p.decode()) for p in others.split(b"\0") if p)
        result = historical.copy()
        candidates = paths | old_links | links.keys() | ({path} if path else set())
        for file in sorted(candidates):
            if path is not None and path != file:
                continue
            if file in links:
                result[file] = links[file]
            elif file in paths or file in old_links:
                result.pop(file, None)
                info = self._live_file(file)
                if info is not None:
                    result[file] = (info[0], "")
            elif file in historical:
                self._live_file(file)  # Reject an unsafe explicitly named parent.
        return result

    def _query(self, op: str, before: str, after: str, path: str,
               cursor: str | None) -> tuple[dict, dict, int, bytes, bool]:
        try:
            a, b = self._revisions.comparison(before, after)
        except (RevisionUnavailable, TypeError) as error:
            raise ObservationError("REVISION_UNAVAILABLE") from error
        self._deadline = time.monotonic() + _OBSERVATION_SECONDS
        binding = serialize([op, before, after, a.identity, b.identity, path])
        offset = 0
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 80:
                    raise ValueError
                number, _ = cursor.split(".")
                offset = int(number)
                expected = self._cursor(binding, offset)
                if offset <= 0 or not hmac.compare_digest(cursor, expected):
                    raise ValueError
            except (ValueError, TypeError) as error:
                raise ObservationError("INVALID_CURSOR") from error
        if a.identity is None and b.identity is None:
            raise ObservationError("REVISION_UNAVAILABLE")
        if a.identity is not None and b.identity is not None:
            left = self._tree(a.identity, path or None)
            right = left if a.identity == b.identity else self._tree(
                b.identity, path or None,
            )
            return left, right, offset, binding, False
        identity = a.identity or b.identity
        historical = self._tree(identity, path or None)
        live = self._worktree(identity, historical, path or None)
        left, right = (historical, live) if a.identity else (live, historical)
        return left, right, offset, binding, True

    def _cursor(self, binding: bytes, offset: int) -> str:
        number = str(offset)
        tag = hmac.new(self._secret, binding + b"\0" + number.encode(), hashlib.sha256)
        return number + "." + tag.hexdigest()

    def _page(self, values: list, key: str, offset: int, binding: bytes,
              live: bool, max_items: int, *, status: str = "OK", **fields: object,
              ) -> dict:
        if offset > len(values):
            raise ObservationError("STALE_CURSOR" if live else "INVALID_CURSOR")
        end = min(len(values), offset + max_items)
        while end >= offset:
            cursor = self._cursor(binding, end) if end < len(values) else None
            page_status = "partial" if cursor and key == "diagnostics" else status
            complete = not cursor and status not in {"partial", "unavailable"}
            page = _response(page_status, live, complete=complete, cursor=cursor,
                             **{key: values[offset:end]}, **fields)
            if len(serialize(page)) <= _PAGE_LIMIT and (end > offset or not cursor):
                return page
            end -= 1
        raise ObservationError("LINE_TOO_LONG" if key == "lines" else "TOO_LARGE")

    def _blob_size(self, obj: str) -> int:
        if not _OBJECT.fullmatch(obj):
            raise ObservationError("UNAVAILABLE")
        return int(self._run("cat-file", "-s", obj, limit=128))

    def _content(self, path: str, entry: tuple[str, str] | None) -> bytes:
        if entry is None:
            return b""
        mode, obj = entry
        if mode == "160000":
            raise ObservationError("SUBMODULE_CONTENT_UNAVAILABLE")
        if not obj:
            info = self._live_file(path, _FILE_LIMIT + 1)
            if info is None or info[0] != mode:
                raise ObservationError("WORKTREE_UNAVAILABLE")
            if info[1] > _FILE_LIMIT or len(info[2]) > _FILE_LIMIT:
                raise ObservationError("TOO_LARGE")
            return info[2]
        if self._blob_size(obj) > _FILE_LIMIT:
            raise ObservationError("TOO_LARGE")
        return self._run("cat-file", "blob", obj, limit=_FILE_LIMIT)

    def _metadata(self, path: str, entry: tuple[str, str] | None
                  ) -> tuple[int | None, bytes | None]:
        if entry is None or entry[0] == "160000":
            return None, None
        if not entry[1]:
            info = self._live_file(path, 8000)
            if info is None:
                return None, None
            return info[1], info[2]
        size = self._blob_size(entry[1])
        if size <= 8000:
            return size, self._run("cat-file", "blob", entry[1], limit=8000)
        return size, None

    def changes(self, before: str, after: str, cursor: str | None = None) -> dict:
        a, b, offset, binding, live = self._query("changes", before, after, "", cursor)
        paths = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
        removed, renamed = {}, {}
        for p in paths:
            if p not in b:
                removed.setdefault(a[p], []).append(p)
        for p in paths:
            if p not in a and b[p][1] and removed.get(b[p]):
                renamed[p] = removed[b[p]].pop(0)
        paths = sorted(set(paths) - set(renamed.values()))
        if offset > len(paths):
            raise ObservationError("STALE_CURSOR" if live else "INVALID_CURSOR")
        records = []
        end = offset
        for path in paths[offset:]:
            old_path = renamed.get(path, path)
            old, new = a.get(old_path), b.get(path)
            kinds = {"120000": "symlink", "160000": "submodule"}
            kind = kinds.get((new or old)[0], "file")
            before_size, old_data = self._metadata(old_path, old)
            after_size, new_data = self._metadata(path, new)
            if (live and old and new and old[0] == new[0]
                    and before_size == after_size and old_data is not None
                    and old_data == new_data):
                # Raw Git can conservatively report a stat-dirty path or a
                # staged change that was reverted in the editable file.
                end += 1
                continue
            old_binary = b"\0" in old_data if old_data is not None else None
            new_binary = b"\0" in new_data if new_data is not None else None
            binary = (True if True in (old_binary, new_binary) else
                      False if (not old or old_binary is False) and
                      (not new or new_binary is False) else None)
            if kind == "file" and self._attributes(path)[b"diff"] == b"unset":
                binary = True
            change = (
                "added" if old is None else "deleted" if new is None else "modified"
            )
            record = {"path": path, "change": "renamed" if path in renamed else change,
                      "kind": kind, "before_mode": old[0] if old else None,
                      "after_mode": new[0] if new else None, "before_size": before_size,
                      "after_size": after_size, "binary": binary,
                      "content_unavailable": (kind == "submodule" or binary is True
                                              or max(before_size or 0, after_size or 0)
                                              > _FILE_LIMIT)}
            if path in renamed:
                record["before_path"] = old_path
            if any(e and e[0] == "160000" for e in (old, new)):
                record.update(pointer_changed=old != new, guidance=_SUBMODULE)
                if live:
                    record["checkout_status"] = self._submodule_status(path)
            continuation = (self._cursor(binding, end + 1)
                            if end + 1 < len(paths) else None)
            candidate = _response("OK", live, complete=not continuation,
                                  cursor=continuation, records=[*records, record])
            if len(serialize(candidate)) > _PAGE_LIMIT:
                if not records:
                    raise ObservationError("TOO_LARGE")
                break
            records.append(record)
            end += 1
            if len(records) == 30:
                break
        next_cursor = self._cursor(binding, end) if end < len(paths) else None
        return _response("OK", live, complete=next_cursor is None,
                         cursor=next_cursor, records=records)

    def _attributes(self, path: str) -> dict[bytes, bytes]:
        raw = self._run(
            "check-attr", "-z", "diff", "whitespace", "conflict-marker-size",
            "--", path, limit=32 * 1024, policy_config=True,
        ).split(b"\0")
        if len(raw) != 10 or raw[-1] or any(p != path.encode() for p in raw[::3][:-1]):
            raise ObservationError("UNAVAILABLE")
        return dict(zip(raw[1::3], raw[2::3], strict=True))

    def _file_diff(self, path: str, old: tuple | None, new: tuple | None,
                   *, check: bool = False, whitespace: str | None = None) -> bytes:
        a, b = self._content(path, old), self._content(path, new)
        attributes = self._attributes(path)
        if attributes[b"diff"] == b"unset" or any(b"\0" in data for data in (a, b)):
            raise ObservationError("BINARY_DIFF_UNAVAILABLE")
        if not check:
            for data in (a, b):
                try:
                    data.decode("utf-8")
                except UnicodeError as error:
                    raise ObservationError("BINARY_DIFF_UNAVAILABLE") from error
                if any(len(line) > _LINE_LIMIT for line in data.split(b"\n")):
                    raise ObservationError("LINE_TOO_LONG")
        # Diff only bounded copies with fixed filenames. No repo driver, nested
        # Git, index, worktree conversion, textconv, or external diff is invoked.
        with tempfile.TemporaryDirectory(prefix="devlegate-diff-") as temp:
            root = Path(temp)
            config = ["-c", "core.whitespace=" + whitespace] if whitespace else []
            if check:
                # Reapply selected diagnostic attributes to the fixed scratch
                # name, never copying repository attributes or config as-is.
                specs = []
                for key in (b"whitespace", b"conflict-marker-size"):
                    value = attributes[key]
                    if value == b"unspecified":
                        continue
                    if value == b"unset":
                        specs.append(b"-" + key)
                    elif value == b"set":
                        specs.append(key)
                    elif re.fullmatch(rb"[a-zA-Z0-9,=-]{1,256}", value):
                        specs.append(key + b"=" + value)
                    else:
                        raise ObservationError("UNAVAILABLE")
                if specs:
                    _git(root, "init", "-q", "--template=", limit=1024,
                         deadline=self._deadline, isolated=True)
                    rules = b"new " + b" ".join(specs) + b"\n"
                    (root / ".gitattributes").write_bytes(rules)
            for entry, name, data in ((old, "old", a), (new, "new", b)):
                if entry is not None:
                    (root / name).write_bytes(data)
                    if entry[0] == "100755":
                        (root / name).chmod(0o700)
            args = ["--check"] if check else ["--unified=3"]
            return _git(
                root, *config, "diff", "--no-index", "--no-ext-diff", "--no-textconv",
                "--no-color", *args, "--", "old" if old else os.devnull,
                "new" if new else os.devnull, limit=256 * 1024,
                deadline=self._deadline, accepted=(0, 1, 2, 3) if check else (0, 1),
                isolated=True, require_output=(2, 3),
            )

    def diff(self, before: str, after: str, path: str,
             cursor: str | None = None) -> dict:
        path = _path(path)
        a, b, offset, binding, live = self._query("diff", before, after, path, cursor)
        old, new = a.get(path), b.get(path)
        if old is None and new is None:
            raise ObservationError("INVALID_PATH")
        if any(entry and entry[0] == "160000" for entry in (old, new)):
            return _response("SUBMODULE_CONTENT_UNAVAILABLE", live,
                             pointer_changed=old != new, guidance=_SUBMODULE,
                             before_mode=old[0] if old else None,
                             after_mode=new[0] if new else None,
                             **({"checkout_status": self._submodule_status(path)}
                                if live else {}))
        if old and new and old[0] != new[0] and "120000" in (old[0], new[0]):
            raise ObservationError("TYPE_CHANGE_UNAVAILABLE")
        lines = []
        if old != new:
            raw = self._file_diff(path, old, new)
            text = raw.decode()
            # Git's no-index headers contain temporary names and blob IDs. Only
            # hunk lines and virtual repository-relative headers leave the owner.
            header = True
            for part in text.rstrip("\n").split("\n"):
                line = part + "\n"
                if line.startswith("@@ "):
                    header = False
                if not header:
                    lines.append(line)
                elif line.startswith("--- "):
                    name = _display_path("a/", path) if old else "/dev/null"
                    lines.append(f"--- {name}\n")
                elif line.startswith("+++ "):
                    name = _display_path("b/", path) if new else "/dev/null"
                    lines.append(f"+++ {name}\n")
        return self._page(lines, "lines", offset, binding, live, 120,
                          before_mode=old[0] if old else None,
                          after_mode=new[0] if new else None)

    def check(self, before: str, after: str, cursor: str | None = None) -> dict:
        live = "WORKTREE" in (before, after)
        try:
            return self._check(before, after, cursor)
        except (ObservationError, OSError, ValueError) as error:
            reason = str(error) if isinstance(error, ObservationError) else (
                "UNAVAILABLE"
            )
            return _response("unavailable", live, reason=reason, diagnostics=[])

    def _check(self, before: str, after: str, cursor: str | None) -> dict:
        a, b, offset, binding, live = self._query("check", before, after, "", cursor)
        raw = self._run(
            "config", "--null", "--get", "core.whitespace", limit=4096, accepted=(0, 1),
            policy_config=True,
        )
        whitespace = raw.removesuffix(b"\0").decode() if raw else None
        diagnostics = []
        unavailable = None
        # Recomputing a bounded result keeps immutable continuation deterministic.
        for path in sorted(a.keys() | b.keys()):
            old, new = a.get(path), b.get(path)
            if old == new:
                continue
            try:
                if any(e and e[0] == "160000" for e in (old, new)):
                    raise ObservationError("SUBMODULE_CONTENT_UNAVAILABLE")
                if new is None or new[0] == "120000":
                    # Deletions and introduced link text add no checked lines.
                    continue
                if old and old[0] == "120000":
                    old = None  # Git checks regular text as an addition on type change.
                raw = self._file_diff(path, old, new, check=True, whitespace=whitespace)
                last = None
                for line in raw.split(b"\n"):
                    if not line:
                        continue
                    match = re.fullmatch(rb"(?:[ab]/)?new:([0-9]+): (.+)", line)
                    if match:
                        message = match[2].decode("ascii")
                        kinds = message.rstrip(".").split(", ")
                        known = {
                            "trailing whitespace", "space before tab in indent",
                            "new blank line at EOF", "leftover conflict marker",
                            "indent with spaces", "tab in indent",
                        }
                        if not set(kinds) <= known:
                            raise ObservationError("UNAVAILABLE")
                        last = {"path": path, "line": int(match[1]), "kinds": kinds}
                        diagnostics.append(last)
                        if len(diagnostics) > 1024:
                            raise ObservationError("TOO_EXPENSIVE")
                    elif line.startswith(b"+") and last is not None:
                        if len(line) > _LINE_LIMIT:
                            last["excerpt_status"] = "LINE_TOO_LONG"
                        else:
                            last["excerpt"] = line[1:161].decode(errors="replace")
                        last = None
                    else:
                        raise ObservationError("UNAVAILABLE")
            except ObservationError as error:
                unavailable = unavailable or str(error)
                if str(error) == "TOO_EXPENSIVE":
                    break
        status = ("partial" if diagnostics else "unavailable") if unavailable else (
            "findings" if diagnostics else "clean"
        )
        return self._page(
            diagnostics, "diagnostics", offset, binding, live, 30, status=status,
            **({"reason": unavailable} if unavailable else {}),
        )
