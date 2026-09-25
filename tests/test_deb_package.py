import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def load_tool(name):
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_tool("validate_deb")


def make_package(
    tmp_path: Path,
    *,
    depends: str | None = None,
    maintainer_script: bool = False,
) -> tuple[Path, Path]:
    root = tmp_path / "root"
    control = root / "DEBIAN"
    binary = root / "usr/lib/devlegate/devlegate"
    doc = root / "usr/share/doc/devlegate"
    control.mkdir(parents=True)
    binary.parent.mkdir(parents=True)
    doc.mkdir(parents=True)
    binary.write_bytes(b"standalone")
    binary.chmod(0o755)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/devlegate").symlink_to("../lib/devlegate/devlegate")
    archive = doc / "devlegate-0.5.4.dev0-linux-x86_64.tar.gz"
    archive.write_bytes(b"archive")
    import hashlib

    (doc / f"{archive.name}.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="ascii",
    )
    dependency = f"Depends: {depends}\n" if depends else ""
    (control / "control").write_text(
        "Package: devlegate\nVersion: 0.5.4.dev0\nArchitecture: amd64\n"
        f"{dependency}Description: test\n test\n",
        encoding="ascii",
    )
    if maintainer_script:
        script = control / "postinst"
        script.write_text("#!/bin/sh\n", encoding="ascii")
        script.chmod(0o755)
    package = tmp_path / "devlegate.deb"
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(root), str(package)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"wheel": {"version": "0.5.4.dev0"}}))
    return package, report


def test_deb_validator_accepts_dependency_free_payload(tmp_path):
    package, report = make_package(tmp_path)
    binary = VALIDATOR.validate(package, report, tmp_path / "extract")
    assert binary.is_file()


def test_deb_validator_rejects_runtime_dependencies(tmp_path):
    package, report = make_package(tmp_path, depends="python3")
    with pytest.raises(VALIDATOR.PackageError, match="runtime dependencies"):
        VALIDATOR.validate(package, report, tmp_path / "extract")


def test_deb_validator_rejects_maintainer_scripts(tmp_path):
    package, report = make_package(tmp_path, maintainer_script=True)
    with pytest.raises(VALIDATOR.PackageError, match="maintainer scripts"):
        VALIDATOR.validate(package, report, tmp_path / "extract")
