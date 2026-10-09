#!/usr/bin/env python3
"""Small deterministic Debian binary-package format implementation."""

from __future__ import annotations

import ctypes
import ctypes.util
import gzip
import io
import tarfile
from pathlib import Path, PurePosixPath

TOOL_IDENTITY = {
    "name": "devlegate-deb-format",
    "version": "1",
    "platform": "python-stdlib",
    "source": "tools/deb_format.py",
    "digest": "owned-source-v1",
}


class DebFormatError(ValueError):
    """The Debian container is malformed or unsafe."""


def _tar_bytes(root: Path, *, timestamp: int, include_control: bool = False) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.GNU_FORMAT) as archive:
        paths = sorted(
            root.rglob("*"), key=lambda path: path.relative_to(root).as_posix()
        )
        for path in paths:
            relative = path.relative_to(root)
            if not include_control and relative.parts and relative.parts[0] == "DEBIAN":
                continue
            info = archive.gettarinfo(str(path), arcname=relative.as_posix())
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = timestamp
            if info.isreg():
                with path.open("rb") as source:
                    archive.addfile(info, source)
            else:
                archive.addfile(info)
    return gzip.compress(output.getvalue(), compresslevel=9, mtime=timestamp)


def _ar_member(name: str, payload: bytes, timestamp: int) -> bytes:
    encoded = name.encode("ascii")
    if len(encoded) > 16:
        raise DebFormatError(f"Debian member name is too long: {name}")
    header = (
        encoded.ljust(16, b" ")
        + str(timestamp).encode("ascii").ljust(12, b" ")
        + b"0".ljust(6, b" ")
        + b"0".ljust(6, b" ")
        + b"100644".ljust(8, b" ")
        + str(len(payload)).encode("ascii").ljust(10, b" ")
        + b"`\n"
    )
    return header + payload + (b"\n" if len(payload) % 2 else b"")


def build(root: Path, output: Path, *, timestamp: int) -> None:
    if not (root / "DEBIAN/control").is_file():
        raise DebFormatError("Debian package lacks DEBIAN/control")
    control_root = root / "DEBIAN"
    control = _tar_bytes(control_root, timestamp=timestamp, include_control=False)
    data = _tar_bytes(root, timestamp=timestamp)
    output.write_bytes(
        b"!<arch>\n"
        + _ar_member("debian-binary", b"2.0\n", timestamp)
        + _ar_member("control.tar.gz", control, timestamp)
        + _ar_member("data.tar.gz", data, timestamp)
    )


def members(package: Path) -> dict[str, bytes]:
    data = package.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise DebFormatError("not a Debian ar archive")
    position = 8
    result: dict[str, bytes] = {}
    while position < len(data):
        if position + 60 > len(data) or data[position + 58 : position + 60] != b"`\n":
            raise DebFormatError("invalid Debian ar member header")
        header = data[position : position + 60]
        name = header[:16].decode("ascii").strip().rstrip("/")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError as error:
            raise DebFormatError("invalid Debian ar member size") from error
        start = position + 60
        end = start + size
        if end > len(data):
            raise DebFormatError("truncated Debian ar member")
        result[name] = data[start:end]
        position = end + (size % 2)
    if "debian-binary" not in result:
        raise DebFormatError("Debian package has unexpected ar members")
    if result["debian-binary"] != b"2.0\n":
        raise DebFormatError("unsupported Debian binary format")
    return result


def _decompress(payload: bytes, name: str) -> bytes:
    if name.endswith(".gz"):
        return gzip.decompress(payload)
    if not name.endswith(".zst"):
        raise DebFormatError(f"unsupported Debian compression: {name}")
    library_name = ctypes.util.find_library("zstd")
    if library_name is None:
        raise DebFormatError("zstd support is unavailable")
    library = ctypes.CDLL(library_name)
    library.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    library.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
    library.ZSTD_decompress.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    library.ZSTD_decompress.restype = ctypes.c_size_t
    source = ctypes.create_string_buffer(payload)
    size = library.ZSTD_getFrameContentSize(source, len(payload))
    if size >= (1 << 64) - 2:
        raise DebFormatError("unsupported streaming zstd Debian member")
    target = ctypes.create_string_buffer(size)
    result = library.ZSTD_decompress(target, size, source, len(payload))
    library.ZSTD_isError.argtypes = [ctypes.c_size_t]
    library.ZSTD_isError.restype = ctypes.c_uint
    if library.ZSTD_isError(result):
        raise DebFormatError("invalid zstd Debian member")
    return target.raw[:result]


def _member_payload(archive: dict[str, bytes], prefix: str) -> tuple[str, bytes]:
    matches = [
        (name, value) for name, value in archive.items() if name.startswith(prefix)
    ]
    if len(matches) != 1:
        raise DebFormatError(f"Debian package lacks unique {prefix} member")
    return matches[0]


def control(package: Path) -> dict[str, str]:
    container = members(package)
    name, payload = _member_payload(container, "control.tar.")
    archive = tarfile.open(fileobj=io.BytesIO(_decompress(payload, name)))
    try:
        try:
            control_member = next(
                member
                for member in archive.getmembers()
                if Path(member.name).name == "control"
            )
            content = archive.extractfile(control_member).read().decode("utf-8")
        except (KeyError, AttributeError, UnicodeError) as error:
            raise DebFormatError("Debian package lacks control metadata") from error
    finally:
        archive.close()
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key] = value.strip()
    return values


def extract(package: Path, destination: Path, *, control_only: bool = False) -> None:
    payload = members(package)
    name, compressed = _member_payload(
        payload, "control.tar." if control_only else "data.tar."
    )
    with tarfile.open(fileobj=io.BytesIO(_decompress(compressed, name))) as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise DebFormatError("Debian archive contains an unsafe path")
            archive.extract(member, destination)
