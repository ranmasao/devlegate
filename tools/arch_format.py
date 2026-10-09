#!/usr/bin/env python3
"""Small deterministic Arch package container implementation.

The Arch package format is a tar stream compressed with zstd.  The encoder uses
valid zstd raw blocks, so package production has no dependency on an Arch host,
pacman, tar, or a zstd executable.
"""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path, PurePosixPath


class ArchFormatError(ValueError):
    """The Arch container is malformed or unsafe."""


def _source_digest() -> str:
    source = Path(__file__).read_bytes()
    source = re.sub(
        rb'("digest":\s*")[0-9a-f]{64}("),?',
        rb'\1<source-sha256>\2',
        source,
        count=1,
    )
    import hashlib

    return hashlib.sha256(source).hexdigest()


TOOL_IDENTITY = {
    "name": "devlegate-arch-format",
    "version": "1",
    "platform": "python-stdlib-zstd-raw-blocks",
    "source": "tools/arch_format.py",
    "digest": _source_digest(),
}


def _zstd_encode(payload: bytes) -> bytes:
    # Zstandard frame: single-segment, known content size, no checksum.
    if len(payload) >= 1 << 32:
        raise ArchFormatError("Arch tar stream is too large")
    descriptor = 0xA0
    header = b"\x28\xb5\x2f\xfd" + bytes([descriptor])
    header += len(payload).to_bytes(4, "little")
    chunks = []
    for offset in range(0, len(payload), 128 * 1024):
        chunk = payload[offset : offset + 128 * 1024]
        last = offset + len(chunk) >= len(payload)
        block_header = (len(chunk) << 3) | (1 if last else 0)
        chunks.append(block_header.to_bytes(3, "little") + chunk)
    if not chunks:
        chunks.append((1).to_bytes(3, "little"))
    return header + b"".join(chunks)


def _zstd_decode(payload: bytes) -> bytes:
    if len(payload) < 6 or payload[:4] != b"\x28\xb5\x2f\xfd":
        raise ArchFormatError("not a zstd frame")
    descriptor = payload[4]
    single_segment = bool(descriptor & 0x20)
    if not single_segment or descriptor & 0x08:
        raise ArchFormatError("unsupported zstd frame")
    size_bytes = 4 if ((descriptor >> 6) & 3) == 2 else 8
    position = 5 + size_bytes
    if position > len(payload):
        raise ArchFormatError("truncated zstd frame")
    output = bytearray()
    last = False
    while not last:
        if position + 3 > len(payload):
            raise ArchFormatError("truncated zstd block")
        block = int.from_bytes(payload[position : position + 3], "little")
        position += 3
        last = bool(block & 1)
        block_type = (block >> 1) & 3
        size = block >> 3
        if block_type != 0 or position + size > len(payload):
            raise ArchFormatError("unsupported zstd block")
        output.extend(payload[position : position + size])
        position += size
    return bytes(output)


def _tar_bytes(root: Path, timestamp: int) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for path in sorted(
            root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
        ):
            relative = path.relative_to(root)
            info = archive.gettarinfo(str(path), arcname=relative.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = timestamp
            if info.isreg():
                with path.open("rb") as source:
                    archive.addfile(info, source)
            else:
                archive.addfile(info)
    return output.getvalue()


def build(root: Path, output: Path, *, timestamp: int) -> None:
    if not (root / ".PKGINFO").is_file():
        raise ArchFormatError("Arch package lacks .PKGINFO")
    output.write_bytes(_zstd_encode(_tar_bytes(root, timestamp)))


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if not member.name or path.is_absolute() or ".." in path.parts:
            raise ArchFormatError("Arch archive contains an unsafe path")
        if member.name in names:
            raise ArchFormatError("Arch archive contains duplicate members")
        names.add(member.name)
        if not (member.isdir() or member.isreg()):
            raise ArchFormatError("Arch archive contains unsupported member type")
    return members


def members(package: Path) -> dict[str, bytes]:
    with tarfile.open(
        fileobj=io.BytesIO(_zstd_decode(package.read_bytes()))
    ) as archive:
        result = {}
        for member in _safe_members(archive):
            if member.isreg():
                handle = archive.extractfile(member)
                assert handle is not None
                result[member.name] = handle.read()
        return result


def extract(package: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(
        fileobj=io.BytesIO(_zstd_decode(package.read_bytes()))
    ) as archive:
        for member in _safe_members(archive):
            target = destination / PurePosixPath(member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(member.mode & 0o7777)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ArchFormatError("Arch archive member has no payload")
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o7777)
