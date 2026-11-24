# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Private, immutable ticket notes under the project-instance state root."""

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

ENTRY_BYTES = 16 * 1024
TICKET_BYTES = 128 * 1024
ENTRY_COUNT = 32
PAGE_BYTES = 8 * 1024
_RECORD_BYTES = 6 * ENTRY_BYTES + 4096  # Worst-case JSON escapes plus metadata.
_ID = re.compile(r"[0-9a-f]{32}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FORMATS = {"text", "json", "diagnostic"}


class BlackboardError(RuntimeError):
    """A bounded public status, never an OS error or file path."""


def serialize(value: object) -> bytes:
    # Escape control characters and Unicode formatting controls in tool output.
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "".join(
        json.dumps(char, ensure_ascii=True)[1:-1]
        if ord(char) >= 127 and unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"}
        else char for char in text
    ).encode("utf-8")


def _validate(title: str, content: str, format: str) -> bytes:
    if (not isinstance(title, str) or not title.strip()
            or any(unicodedata.category(c)[0] in "CZ" and c != " " for c in title)
            or not isinstance(content, str) or not isinstance(format, str)
            or format not in FORMATS):
        raise BlackboardError("INVALID_REQUEST")
    try:
        raw = content.encode("utf-8")
        title_bytes = title.encode("utf-8")
    except UnicodeError as error:
        raise BlackboardError("INVALID_REQUEST") from error
    if len(raw) > ENTRY_BYTES or len(title_bytes) > 160:
        raise BlackboardError("LIMIT_EXCEEDED")
    return raw


@dataclass(frozen=True)
class _Note:
    id: str
    title: str
    format: str
    content: str
    byte_length: int
    sha256: str
    creation_order: int
    creator_execution: str
    cursor_key: str

    def metadata(self) -> dict:
        return {key: getattr(self, key) for key in (
            "id", "title", "format", "byte_length", "sha256", "creation_order",
        )}

    def cursor(self, query: str, position: str = "") -> str:
        signature = hmac.digest(
            bytes.fromhex(self.cursor_key), f"{self.id}:{query}:{position}".encode(),
            "sha256",
        ).hex()
        return position + signature


def _scope_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _check_private(fd: int, *, directory: bool) -> None:
    current = os.fstat(fd)
    if (current.st_uid != os.geteuid() or current.st_mode & 0o077
            or (not directory and (not stat.S_ISREG(current.st_mode)
                                   or current.st_nlink != 1))):
        raise BlackboardError("INTEGRITY_ERROR")


def _private_child(parent: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    fd = os.open(name, _DIRECTORY, dir_fd=parent)
    try:
        _check_private(fd, directory=True)
        # Also confirms a directory created before a previous interrupted fsync.
        os.fsync(parent)
    except BaseException:
        os.close(fd)
        raise
    return fd


class BlackboardStore:
    """Owner-only board bound once to a canonical instance and ticket."""

    def __init__(self, state_dir: Path, instance: str, ticket: str) -> None:
        self._root = state_dir.absolute()
        self._instance = _scope_key(instance)
        self._ticket = _scope_key(ticket)

    @contextmanager
    def _directory(self, child: str) -> Iterator[int]:
        # Walk with stable directory descriptors; never follow symlinks, even
        # on ancestors accessed after admission. The state root already exists.
        fd = os.open(self._root.anchor, _DIRECTORY)
        try:
            for part in self._root.parts[1:]:
                if part in {"..", "."}:
                    raise BlackboardError("INTEGRITY_ERROR")
                next_fd = os.open(part, _DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            root = os.fstat(fd)
            if root.st_uid != os.geteuid() or root.st_mode & 0o022:
                raise BlackboardError("INTEGRITY_ERROR")
            for part in ("blackboard", self._instance, child):
                next_fd = _private_child(fd, part)
                os.close(fd)
                fd = next_fd
            # The service already holds instance ownership. This extra local
            # lock ties scanning and publication to the same directory inode.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield fd
        except OSError as error:
            raise BlackboardError("UNAVAILABLE") from error
        finally:
            os.close(fd)

    def reserve_execution_budget(self, execution: str) -> bool:
        """Reserve one lifetime budget; a restarted owner never refills it.

        A marker contains no session data. Once created, even an interrupted
        reservation is spent. Only a fresh execution can receive a new budget.
        """
        with self._directory("budgets") as directory:
            try:
                fd = os.open(
                    _scope_key(execution), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600, dir_fd=directory,
                )
            except FileExistsError:
                return False
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(directory)
        return True

    @staticmethod
    def _load(directory: int, name: str) -> _Note:
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
        except OSError as error:
            raise BlackboardError("INTEGRITY_ERROR") from error
        with os.fdopen(fd, "rb") as source:
            _check_private(source.fileno(), directory=False)
            data = source.read(_RECORD_BYTES + 1)
        try:
            if len(data) > _RECORD_BYTES:
                raise ValueError
            note = _Note(**json.loads(data.decode("utf-8")))
            raw = _validate(note.title, note.content, note.format)
            if (note.id != name[:-5] or not _ID.fullmatch(note.cursor_key)
                    or type(note.byte_length) is not int or note.byte_length != len(raw)
                    or type(note.creation_order) is not int
                    or not 1 <= note.creation_order <= ENTRY_COUNT
                    or not isinstance(note.creator_execution, str)
                    or not 1 <= len(note.creator_execution) <= 160
                    or note.sha256 != hashlib.sha256(raw).hexdigest()):
                raise ValueError
        except (ValueError, TypeError, RecursionError, BlackboardError) as error:
            raise BlackboardError("INTEGRITY_ERROR") from error
        return note

    def _notes(self, directory: int) -> list[_Note]:
        # Confirm the directory before exposing a complete record that may have
        # been renamed just before a previous owner stopped without acknowledging it.
        os.fsync(directory)
        notes: list[_Note] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name == ".pending":
                    continue  # An interrupted, unpublished save.
                if (len(notes) >= ENTRY_COUNT or not entry.name.endswith(".json")
                        or not _ID.fullmatch(entry.name[:-5])):
                    raise BlackboardError("INTEGRITY_ERROR")
                notes.append(self._load(directory, entry.name))
        notes.sort(key=lambda note: note.creation_order)
        if ([note.creation_order for note in notes] != list(range(1, len(notes) + 1))
                or sum(note.byte_length for note in notes) > TICKET_BYTES):
            raise BlackboardError("INTEGRITY_ERROR")
        return notes

    def save(self, title: str, content: str, format: str, *, execution: str) -> dict:
        raw = _validate(title, content, format)
        with self._directory(self._ticket) as directory:
            notes = self._notes(directory)
            for note in notes:
                if (note.title, note.format, note.content) == (title, format, content):
                    return {"status": "OK", "id": note.id, "duplicate": True}
            used = sum(note.byte_length for note in notes)
            if len(notes) >= ENTRY_COUNT or used + len(raw) > TICKET_BYTES:
                raise BlackboardError("LIMIT_EXCEEDED")
            note = _Note(secrets.token_hex(16), title, format, content, len(raw),
                         hashlib.sha256(raw).hexdigest(), len(notes) + 1, execution,
                         secrets.token_hex(16))
            self._publish(directory, note)
            return {"status": "OK", "id": note.id, "duplicate": False}

    @staticmethod
    def _publish(directory: int, note: _Note) -> None:
        pending = ".pending"
        name = f"{note.id}.json"
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BlackboardError("INTEGRITY_ERROR")
        try:
            os.unlink(pending, dir_fd=directory)  # Remove interrupted unpublished data.
        except FileNotFoundError:
            pass
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        published = False
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(serialize(asdict(note)))
                out.flush()
                os.fsync(out.fileno())
            # Under exclusive board ownership, the checked destination is absent.
            os.rename(pending, name, src_dir_fd=directory, dst_dir_fd=directory)
            published = True
            os.fsync(directory)
        except OSError:
            os.unlink(name if published else pending, dir_fd=directory)
            if published:
                os.fsync(directory)
            raise

    @staticmethod
    def _page(cursor: str | None, **fields: object) -> dict:
        return {"status": "OK", "untrusted": True, "complete": cursor is None,
                "has_more": cursor is not None, "next_cursor": cursor, **fields}

    def list(self, cursor: str | None) -> dict:
        with self._directory(self._ticket) as directory:
            notes = self._notes(directory)[::-1]
        if cursor is not None:
            for index, note in enumerate(notes):
                if hmac.compare_digest(cursor, note.cursor("list")):
                    notes = notes[index + 1:]
                    break
            else:
                raise BlackboardError("INVALID_CURSOR")
        page = self._page(None, entries=[])
        entries: list[dict] = []
        for index, note in enumerate(notes):
            after = note.cursor("list") if index + 1 < len(notes) else None
            candidate = self._page(after, entries=[*entries, note.metadata()])
            if len(serialize(candidate)) > PAGE_BYTES:
                break
            page = candidate
            entries.append(note.metadata())
        return page

    def read(self, note_id: str, cursor: str | None) -> dict:
        if not _ID.fullmatch(note_id):
            raise BlackboardError("INVALID_REQUEST")
        with self._directory(self._ticket) as directory:
            notes = self._notes(directory)
        note = next((note for note in notes if note.id == note_id), None)
        if note is None:
            raise BlackboardError("NOT_FOUND")
        start = 0
        if cursor is not None:
            if not re.fullmatch(r"[0-9a-f]{68}", cursor):
                raise BlackboardError("INVALID_CURSOR")
            start = int(cursor[:4], 16)
            expected = note.cursor("read", cursor[:4])
            if (not 0 < start < len(note.content)
                    or not hmac.compare_digest(cursor, expected)):
                raise BlackboardError("INVALID_CURSOR")

        def at(end: int) -> dict:
            after = None
            if end < len(note.content):
                after = note.cursor("read", f"{end:04x}")
            return self._page(
                after, id=note.id, format=note.format, content=note.content[start:end],
                byte_length=note.byte_length, full_sha256=note.sha256,
            )

        complete = at(len(note.content))
        if len(serialize(complete)) <= PAGE_BYTES:
            return complete
        low, high = start, len(note.content) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if len(serialize(at(mid))) <= PAGE_BYTES:
                low = mid
            else:
                high = mid - 1
        return at(low)
