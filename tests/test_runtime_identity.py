# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from devlegate import runtime_identity


def make_debian_layout(tmp_path: Path, marker: object) -> Path:
    executable = tmp_path / "usr/bin/devlegate"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"payload")
    executable.chmod(0o755)
    documentation = tmp_path / "usr/share/doc/devlegate"
    documentation.mkdir(parents=True)
    (documentation / "INSTALLATION-PROVENANCE.json").write_text(
        json.dumps(marker), encoding="ascii"
    )
    return executable


def test_debian_marker_requires_exact_payload_digest(tmp_path):
    executable = make_debian_layout(
        tmp_path,
        {
            "distribution": "debian",
            "package": "devlegate",
            "payload_sha256": hashlib.sha256(b"payload").hexdigest(),
        },
    )
    assert runtime_identity._debian_marker(executable)
    executable.write_bytes(b"portable")
    assert not runtime_identity._debian_marker(executable)


def test_debian_marker_missing_or_malformed_is_unknown(tmp_path):
    executable = make_debian_layout(tmp_path, {"distribution": "debian"})
    assert not runtime_identity._debian_marker(executable)


def test_metadata_must_resolve_to_imported_package(tmp_path, monkeypatch):
    current = tmp_path / "source/devlegate/__init__.py"
    current.parent.mkdir(parents=True)
    current.write_text("", encoding="ascii")
    monkeypatch.setattr(runtime_identity.devlegate, "__file__", str(current))
    unrelated = SimpleNamespace(
        files=[Path("devlegate/__init__.py")],
        locate_file=lambda _path: tmp_path / "other/devlegate/__init__.py",
        read_text=lambda _name: "pip",
    )
    assert not runtime_identity._metadata_matches_package(unrelated)
