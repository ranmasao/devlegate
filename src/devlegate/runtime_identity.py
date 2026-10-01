# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Project-independent identity facts for the Devlegate runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import devlegate
from devlegate import __version__


def _standalone_executable() -> Path | None:
    pex = os.environ.get("PEX")
    scie = os.environ.get("SCIE")
    if not pex or pex != scie:
        return None
    candidate = Path(pex)
    try:
        if not candidate.is_file() or candidate.stat().st_mode & 0o111 == 0:
            return None
        with candidate.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                return None
    except OSError:
        return None
    return candidate.resolve()


def _debian_marker(executable: Path) -> bool:
    marker = executable.parent.parent / "share" / "doc" / "devlegate" / (
        "INSTALLATION-PROVENANCE.json"
    )
    try:
        value = json.loads(marker.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    if value.get("distribution") != "debian" or value.get("package") != (
        "devlegate"
    ):
        return False
    digest = value.get("payload_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        return False
    if any(character not in "0123456789abcdef" for character in digest):
        return False
    try:
        observed = hashlib.sha256(executable.read_bytes()).hexdigest()
    except OSError:
        return False
    return observed == digest


def _metadata_matches_package(distribution: metadata.Distribution) -> bool:
    """Prove that distribution metadata describes the imported package."""
    package_file = Path(devlegate.__file__).resolve()
    files = distribution.files
    if files:
        package_entry = Path("devlegate") / "__init__.py"
        for entry in files:
            if Path(entry) == package_entry:
                expected = distribution.locate_file(entry)
                if expected.resolve() == package_file:
                    return True
                break

    direct_url = distribution.read_text("direct_url.json")
    if not direct_url:
        return False
    try:
        direct = json.loads(direct_url)
    except json.JSONDecodeError:
        return False
    info = direct.get("dir_info") if isinstance(direct, dict) else None
    if not isinstance(info, dict) or info.get("editable") is not True:
        return False
    url = direct.get("url") if isinstance(direct, dict) else None
    if not isinstance(url, str) or urlparse(url).scheme != "file":
        return False
    source_root = Path(url2pathname(unquote(urlparse(url).path))).resolve()
    try:
        package_file.relative_to(source_root)
    except ValueError:
        return False
    return package_file.is_file()


def _package_identity() -> dict[str, Any]:
    try:
        distribution = metadata.distribution("devlegate")
    except metadata.PackageNotFoundError:
        return {
            "form": "python-package",
            "installer": "unknown",
            "editable": "unknown",
            "direct_url": "unknown",
        }
    if not _metadata_matches_package(distribution):
        return {
            "form": "python-package",
            "installer": "unknown",
            "editable": "unknown",
            "direct_url": "unknown",
        }
    installer = distribution.read_text("INSTALLER")
    installer = installer.strip() if installer else "unknown"
    direct_url = distribution.read_text("direct_url.json")
    editable = "unknown"
    direct_url_state = "unknown"
    if direct_url:
        try:
            direct = json.loads(direct_url)
        except json.JSONDecodeError:
            direct = None
        if isinstance(direct, dict):
            direct_url_state = "present"
            info = direct.get("dir_info")
            if isinstance(info, dict) and isinstance(info.get("editable"), bool):
                editable = "yes" if info["editable"] else "no"
    return {
        "form": "python-package",
        "installer": installer,
        "editable": editable,
        "direct_url": direct_url_state,
    }


def _os_identity() -> dict[str, str]:
    try:
        release = platform.freedesktop_os_release()
    except (AttributeError, OSError):
        release = {}
    name = release.get("NAME") or platform.system() or "unknown"
    version = release.get("VERSION_ID") or release.get("VERSION")
    if not version:
        version = platform.release() or "unknown"
    return {"name": name, "version": version}


def identity() -> dict[str, Any]:
    """Return facts that do not require project or service state."""
    executable = _standalone_executable()
    if executable is not None:
        installation = "debian" if _debian_marker(executable) else "unknown"
        distribution = {
            "form": "standalone",
            "installer": installation,
            "editable": "no",
            "direct_url": "unknown",
        }
    else:
        distribution = _package_identity()
    return {
        "program": "devlegate",
        "version": __version__,
        "distribution": distribution,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "os": _os_identity(),
    }


def banner(value: dict[str, Any] | None = None) -> str:
    value = value or identity()
    distribution = value["distribution"]
    python = value["python"]
    operating_system = value["os"]
    return "\n".join(
        (
            f"Devlegate runtime {value['version']}",
            f"Distribution: {distribution['form']}",
            f"Installation: {distribution['installer']}",
            f"OS: {operating_system['name']} {operating_system['version']}",
            f"Python runtime: {python['implementation']} {python['version']}",
        )
    )
