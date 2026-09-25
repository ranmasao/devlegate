#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Build and prove the Linux x86_64 eager-scie Devlegate artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

PEX_VERSION = "2.103.2"
TARGET = "linux-x86_64"
PBS_PROVIDER = "PythonBuildStandalone"
PBS_RELEASE = "20260901"
PBS_PYTHON_VERSION = "3.12.14"
PBS_ARCHIVE = "cpython-3.12.14+20260901-x86_64-unknown-linux-gnu-install_only.tar.gz"
PBS_SHA256 = "936c246dfdbbfa7cb22dd01814a21f582a892689fae96b06071a5e433baffa22"
SCIENCE_VERSION = "0.21.0"
SCIENCE_ASSET = "science-fat-linux-x86_64"
SCIENCE_SHA256 = "2070de7f823033a3b0e8a2ceb56e9fdfa8375fda1762a0d71ba11cd36d5c730f"
SCIE_JUMP_VERSION = "1.13.0"
SCIE_JUMP_ASSET = "scie-jump-gnu-linux-x86_64"
SCIE_JUMP_SPLIT_NAME = "scie-jump"
SCIE_JUMP_SHA256 = "a5afd5cd99ac201865d329980e9856521d394e2780441c4abc2f73add5b7e2d0"
LIBC = "glibc"
PEX_WHEEL = "pex-2.103.2-py3.py312-none-any.whl"
PEX_SHA256 = "f1316f1f6f0e125c44c8d6f49cd6ebc4b294b7582a385b3999e814824e607ec7"
WHEEL_BUILD_TOOLS = {
    "pip": (
        "pip-24.3.1-py3-none-any.whl",
        "3790624780082365f47549d032f3770eeb2b1e8bd1f7b2e02dace1afa361b4ed",
    ),
    "setuptools": (
        "setuptools-77.0.3-py3-none-any.whl",
        "67122e78221da5cf550ddd04cf8742c8fe12094483749a792d56cd669d6cf58c",
    ),
    "wheel": (
        "wheel-0.45.1-py3-none-any.whl",
        "708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248",
    ),
}
PEX_BOOTSTRAP_TOOLS = {
    "pip": (
        "pip-23.2-py3-none-any.whl",
        "78e5353a9dda374b462f2054f83a7b63f3f065c98236a68361845c1b0ee7e35f",
    ),
    "setuptools": (
        "setuptools-68.0.0-py3-none-any.whl",
        "11e52c67415a381d10d6b462ced9cfb97066179f0e871399e006c4ab101fc85f",
    ),
    "wheel": (
        "wheel-0.40.0-py3-none-any.whl",
        "d236b20e7cb522daf2390fa84c55eea81c5c30190f90f29ae2ca1ad8355bf247",
    ),
}


@dataclass(frozen=True)
class StandaloneInputs:
    target: str = TARGET
    pex_version: str = PEX_VERSION
    pex_wheel: str = PEX_WHEEL
    pex_sha256: str = PEX_SHA256
    science_version: str = SCIENCE_VERSION
    science_asset: str = SCIENCE_ASSET
    science_sha256: str = SCIENCE_SHA256
    pbs_provider: str = PBS_PROVIDER
    pbs_release: str = PBS_RELEASE
    pbs_python_version: str = PBS_PYTHON_VERSION
    pbs_archive: str = PBS_ARCHIVE
    pbs_sha256: str = PBS_SHA256
    scie_jump_version: str = SCIE_JUMP_VERSION


INPUTS = StandaloneInputs()


class BuildError(RuntimeError):
    """The standalone proof cannot be completed safely."""


def run(command: list[str], *, env=None, cwd=None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = (
            "\n".join(
                part for part in (result.stdout.strip(), result.stderr.strip()) if part
            )
            or "no output"
        )
        raise BuildError(f"command failed ({' '.join(command)}): {detail}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_version(repo: Path) -> str:
    import tomllib

    value = tomllib.loads((repo / "pyproject.toml").read_text())
    return value["project"]["version"]


def source_epoch(repo: Path) -> str:
    return run(
        ["git", "-C", str(repo), "show", "-s", "--format=%ct", "HEAD"]
    ).stdout.strip()


def source_commit(repo: Path) -> str:
    status = run(["git", "-C", str(repo), "status", "--porcelain"]).stdout
    if status:
        raise BuildError("standalone build requires a clean Git worktree")
    return run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip()


def builder_python_info(python: str) -> dict[str, object]:
    result = run(
        [
            python,
            "-c",
            (
                "import json, platform, sys; "
                "print(json.dumps({'implementation': platform.python_implementation(), "
                "'version': '.'.join(map(str, sys.version_info[:3]))}))"
            ),
        ]
    )
    return json.loads(result.stdout)


def validate_builder_version(implementation: str, major: int, minor: int) -> None:
    if (major, minor) < (3, 12):
        raise BuildError(
            f"standalone build requires Python >= 3.12, found "
            f"{implementation} {major}.{minor}"
        )


def validate_builder_python(python: str) -> None:
    info = builder_python_info(python)
    major, minor = map(int, info["version"].split(".")[:2])
    validate_builder_version(info["implementation"], major, minor)


def verify_file(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise BuildError(
            f"sha256 mismatch for {path.name}: expected {expected}, found {actual}"
        )


def download_verified(url: str, path: Path, expected: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, path.open("wb") as destination:
        shutil.copyfileobj(response, destination)
    verify_file(path, expected)
    return path


def build_wheel(repo: Path, output: Path, python: str, epoch: str) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
    run(
        [
            python,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(output),
            str(repo),
        ],
        env=environment,
    )
    wheels = sorted(output.glob("devlegate-*.whl"))
    if len(wheels) != 1:
        raise BuildError(f"expected one Devlegate wheel in {output}, found {wheels}")
    return wheels[0]


def create_wheel_build_environment(
    python: str, directory: Path, tool_wheels: list[Path]
) -> tuple[Path, dict[str, str]]:
    environment = directory / "wheel-build-env"
    run([python, "-m", "venv", str(environment)])
    wheel_python = environment / "bin" / "python"
    run(
        [
            str(wheel_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            *(str(path) for path in tool_wheels),
        ]
    )
    versions = {
        name: run([str(wheel_python), "-c", code]).stdout.strip()
        for name, code in {
            "pip": "import pip; print(pip.__version__)",
            "setuptools": "import setuptools; print(setuptools.__version__)",
            "wheel": "import wheel; print(wheel.__version__)",
        }.items()
    }
    expected = {
        name: filename.split("-")[1]
        for name, (filename, _) in WHEEL_BUILD_TOOLS.items()
    }
    if versions != expected:
        raise BuildError(
            f"wheel build environment mismatch: expected {expected}, found {versions}"
        )
    return wheel_python, versions


def download_packaging_tools(
    python: str, directory: Path
) -> tuple[Path, Path, list[Path]]:
    directory.mkdir(parents=True, exist_ok=True)

    def download(requirements: list[str]) -> None:
        run(
            [
                python,
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-deps",
                "--dest",
                str(directory),
                *requirements,
            ]
        )

    download(
        [
            f"pex=={PEX_VERSION}",
            *(
                f"{name}=={filename.split('-')[1]}"
                for name, (filename, _) in WHEEL_BUILD_TOOLS.items()
            ),
        ]
    )
    download(
        [
            f"{name}=={filename.split('-')[1]}"
            for name, (filename, _) in PEX_BOOTSTRAP_TOOLS.items()
        ]
    )
    pex_wheel = directory / PEX_WHEEL
    if not pex_wheel.is_file():
        raise BuildError(f"expected PEX wheel was not downloaded: {PEX_WHEEL}")
    verify_file(pex_wheel, PEX_SHA256)
    tool_wheels = []
    for name, (filename, expected_hash) in WHEEL_BUILD_TOOLS.items():
        path = directory / filename
        if not path.is_file():
            raise BuildError(
                f"expected wheel-build tool was not downloaded: {filename}"
            )
        verify_file(path, expected_hash)
        tool_wheels.append(path)
    for name, (filename, expected_hash) in PEX_BOOTSTRAP_TOOLS.items():
        path = directory / filename
        if not path.is_file():
            raise BuildError(
                f"expected PEX bootstrap tool was not downloaded: {filename}"
            )
        verify_file(path, expected_hash)
    runtime = directory / "pex-runtime"
    runtime.mkdir()
    with zipfile.ZipFile(pex_wheel) as archive:
        archive.extractall(runtime)
    science = directory / SCIENCE_ASSET
    download_verified(
        f"https://github.com/a-scie/lift/releases/download/v{SCIENCE_VERSION}/{SCIENCE_ASSET}",
        science,
        SCIENCE_SHA256,
    )
    science.chmod(science.stat().st_mode | stat.S_IXUSR)
    observed_science_version = run([str(science), "--version"]).stdout.strip()
    if observed_science_version != SCIENCE_VERSION:
        raise BuildError(
            "Science version mismatch: expected "
            f"{SCIENCE_VERSION}, found {observed_science_version}"
        )
    return pex_wheel, runtime, tool_wheels


def build_environment(build_root: Path, epoch: str) -> dict[str, str]:
    return {
        **os.environ,
        "PEX_ROOT": str(build_root / "PEX_ROOT"),
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": epoch,
    }


def build_scie(
    wheel: Path,
    wheel_dir: Path,
    tools_dir: Path,
    pex_runtime: Path,
    python: str,
    artifact: Path,
    science: Path,
    build_root: Path,
    epoch: str,
) -> Path:
    environment = {
        **os.environ,
        "PYTHONPATH": str(pex_runtime),
        **build_environment(build_root, epoch),
    }
    run(
        scie_command(
            python,
            wheel,
            wheel_dir,
            tools_dir,
            artifact,
            science,
        ),
        env=environment,
    )
    if not artifact.is_file() or not artifact.stat().st_mode & stat.S_IXUSR:
        raise BuildError(f"scie artifact is not executable: {artifact}")
    return artifact


def scie_command(
    python: str,
    wheel: Path,
    wheel_dir: Path,
    tools_dir: Path,
    artifact: Path,
    science: Path,
) -> list[str]:
    return [
        python,
        "-m",
        "pex",
        "--no-pypi",
        "--find-links",
        str(wheel_dir),
        "--find-links",
        str(tools_dir),
        f"devlegate=={project_version_from_wheel(wheel)}",
        "--entry-point",
        "devlegate",
        "--include-tools",
        "--no-compile",
        "--no-use-system-time",
        "--scie",
        "eager",
        "--scie-only",
        "--scie-platform",
        TARGET,
        "--scie-pbs-release",
        PBS_RELEASE,
        "--scie-python-version",
        PBS_PYTHON_VERSION,
        "--scie-science-binary",
        str(science),
        "--output-file",
        str(artifact),
    ]


def project_version_from_wheel(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        for line in archive.read(metadata).decode().splitlines():
            if line.startswith("Version: "):
                return line.removeprefix("Version: ")
    raise BuildError(f"wheel has no version metadata: {wheel}")


def inspect_wheel(wheel: Path) -> dict[str, object]:
    required = {
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "LICENSES/CC0-1.0.txt",
        "src/devlegate/_vendor/nanoyaml/LICENSE",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        package = sorted(name for name in names if name.startswith("devlegate/"))
        templates = sorted(
            name for name in names if name.startswith("devlegate/default_templates/")
        )
        dist_info = next(name for name in names if name.endswith(".dist-info/METADATA"))
        dist_info_root = dist_info.removesuffix("/METADATA")
        missing = sorted(
            path
            for path in required
            if path not in names and f"{dist_info_root}/licenses/{path}" not in names
        )
        if not package or not templates or missing:
            raise BuildError(
                f"wheel payload incomplete: package={bool(package)} "
                f"templates={bool(templates)} missing={missing}"
            )
        if any(name.startswith("dev/") for name in names):
            raise BuildError("wheel unexpectedly contains repository dev tooling")
        metadata_name = dist_info
        metadata = archive.read(metadata_name).decode()
        runtime_requirements = [
            line
            for line in metadata.splitlines()
            if line.startswith("Requires-Dist:") and "extra ==" not in line
        ]
        if runtime_requirements:
            raise BuildError("Devlegate wheel acquired runtime dependencies")
        return {
            "filename": wheel.name,
            "size": wheel.stat().st_size,
            "sha256": sha256(wheel),
            "version": project_version_from_wheel(wheel),
            "package_files": len(package),
            "template_files": len(templates),
            "license_files": sorted(required),
        }


def inspect_scie(
    artifact: Path,
    inputs: StandaloneInputs = INPUTS,
    forbidden_build_root: Path | None = None,
) -> dict[str, object]:
    file_result = subprocess.run(
        ["file", "-b", str(artifact)], text=True, capture_output=True, check=False
    )
    if file_result.returncode or "ELF" not in file_result.stdout:
        raise BuildError(
            f"artifact is not a native ELF executable: {file_result.stdout}"
        )
    inspection = json.loads(
        run([str(artifact)], env={**os.environ, "SCIE": "inspect"}).stdout
    )
    observed = validate_scie_inspection(
        inspection, inputs, forbidden_build_root=forbidden_build_root
    )
    if (
        forbidden_build_root is not None
        and str(forbidden_build_root).encode() in artifact.read_bytes()
    ):
        raise BuildError(
            f"scie executable contains the temporary build root: {forbidden_build_root}"
        )
    return {
        "filename": artifact.name,
        "size": artifact.stat().st_size,
        "sha256": sha256(artifact),
        "file": file_result.stdout.strip(),
        "configured": {
            "provider": inputs.pbs_provider,
            "pbs_release": inputs.pbs_release,
            "python_version": inputs.pbs_python_version,
            "target": inputs.target,
            "libc": LIBC,
            "pbs_archive": inputs.pbs_archive,
            "pbs_sha256": inputs.pbs_sha256,
            "science_version": inputs.science_version,
            "scie_jump_version": inputs.scie_jump_version,
            "scie_jump_asset": SCIE_JUMP_ASSET,
            "scie_jump_sha256": SCIE_JUMP_SHA256,
        },
        "observed": observed,
    }


def split_scie(artifact: Path, destination: Path) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    run(
        [str(artifact), str(destination)],
        env={**os.environ, "SCIE": "split"},
    )
    files = {path.name: path for path in destination.iterdir() if path.is_file()}
    required = {
        SCIE_JUMP_SPLIT_NAME: "scie-jump",
        "pex": "pex",
        PBS_ARCHIVE: "pbs",
        "configure-binding.py": "configure-binding",
        "lift.json": "lift",
    }
    missing = [name for name in required if name not in files]
    if missing:
        raise BuildError(f"scie split is missing required files: {missing}")
    if any(name.startswith("ptex") for name in files):
        raise BuildError("eager scie split unexpectedly contains ptex")
    if sha256(files[SCIE_JUMP_SPLIT_NAME]) != SCIE_JUMP_SHA256:
        raise BuildError(
            "scie-jump hash mismatch: expected "
            f"{SCIE_JUMP_SHA256}, found {sha256(files[SCIE_JUMP_SPLIT_NAME])}"
        )
    if (
        run([str(files[SCIE_JUMP_SPLIT_NAME]), "--version"]).stdout.strip()
        != SCIE_JUMP_VERSION
    ):
        raise BuildError("scie-jump version mismatch in split payload")
    if sha256(files[PBS_ARCHIVE]) != PBS_SHA256:
        raise BuildError("split PBS archive hash mismatch")
    return files


def sanitize_inspection(value):
    if isinstance(value, dict):
        return {
            key: sanitize_inspection(item)
            for key, item in value.items()
            if key not in {"base", "pex_root"}
        }
    if isinstance(value, list):
        return [sanitize_inspection(item) for item in value]
    return value


def validate_scie_inspection(
    inspection: dict[str, object],
    inputs: StandaloneInputs = INPUTS,
    forbidden_build_root: Path | None = None,
) -> dict[str, object]:
    lift = inspection["scie"]["lift"]
    runtime_base = lift.get("base")
    if runtime_base:
        raise BuildError(
            f"scie contains an unintended custom runtime base: {runtime_base}"
        )
    if forbidden_build_root is not None:
        build_root_text = str(forbidden_build_root)
        if build_root_text in json.dumps(inspection):
            raise BuildError(
                f"scie inspection contains the temporary build root: {build_root_text}"
            )
    files = lift["files"]
    archive = next(
        (entry for entry in files if entry["name"] == inputs.pbs_archive), None
    )
    if archive is None:
        raise BuildError(
            f"scie does not contain configured PBS archive {inputs.pbs_archive}"
        )
    if archive["hash"] != inputs.pbs_sha256:
        raise BuildError(
            "PBS archive hash mismatch: expected "
            f"{inputs.pbs_sha256}, found {archive['hash']}"
        )
    jump = inspection["scie"].get("jump", {})
    if jump.get("version") != inputs.scie_jump_version:
        raise BuildError(
            f"scie-jump version mismatch: expected {inputs.scie_jump_version}, "
            f"found {jump.get('version')}"
        )
    ptex_present = "ptex" in json.dumps(inspection).lower()
    if ptex_present:
        raise BuildError("eager scie unexpectedly contains a ptex runtime payload")
    return {
        "provider": inputs.pbs_provider,
        "pbs_release": archive["name"].split("+", 1)[1].split("-", 1)[0],
        "pbs_archive": archive["name"],
        "pbs_sha256": archive["hash"],
        "python_version": inputs.pbs_python_version,
        "target": inputs.target,
        "scie_jump_version": jump.get("version"),
        "ptex_runtime_included": ptex_present,
        "custom_runtime_base": runtime_base,
        "scie_eager": archive["name"] in [entry["name"] for entry in files],
        "inspect": sanitize_inspection(inspection),
    }


def artifact_environment(root: Path, git: Path, true: Path) -> dict[str, str]:
    tool_bin = root / "bin"
    tool_bin.mkdir(parents=True, exist_ok=True)
    (tool_bin / "git").symlink_to(git)
    (tool_bin / "true").symlink_to(true)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "PEX_INTERPRETER",
            "PEX_BOOTSTRAP_URLS",
            "PEX_PATH",
            "PEX_INHERIT_PATH",
        }
    }
    environment.update(
        {
            "PATH": str(tool_bin),
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state-home"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "PEX_ROOT": str(root / "pex-root"),
        }
    )
    return environment


def prove(args: argparse.Namespace) -> int:
    artifact = args.artifact.resolve()
    if not artifact.is_file():
        raise BuildError(f"standalone artifact not found: {artifact}")
    inspect = inspect_scie(artifact)
    git = Path(shutil.which("git") or "")
    true = Path(shutil.which("true") or "")
    if not git.is_file() or not true.is_file():
        raise BuildError("git and true are required for the isolated lifecycle proof")
    with tempfile.TemporaryDirectory(prefix="devlegate-scie-proof-") as temporary:
        root = Path(temporary)
        environment = artifact_environment(root, git, true)
        executable = root / "Devlegate Product 100%" / "$build" / "devlegate"
        executable.parent.mkdir(parents=True)
        shutil.copy2(artifact, executable)
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        version = run([str(executable), "version"], env=environment).stdout.strip()
        identity = run(
            [
                str(executable),
                "-c",
                (
                    "import json, os, sys; "
                    "print(json.dumps({'sys_executable': sys.executable, "
                    "'argv0': sys.argv[0], 'PEX': os.environ.get('PEX'), "
                    "'SCIE': os.environ.get('SCIE'), "
                    "'SCIE_ARGV0': os.environ.get('SCIE_ARGV0'), "
                    "'proc_exe': os.readlink('/proc/self/exe')}))"
                ),
            ],
            env={**environment, "PEX_INTERPRETER": "1"},
        ).stdout.strip()
        pex_info = run(
            [str(executable), "info"],
            env={**environment, "PEX_TOOLS": "1"},
        ).stdout.strip()
        unit = run(
            [
                str(executable),
                "-c",
                (
                    "from pathlib import Path; "
                    "from devlegate.launcher import product_launcher; "
                    "from devlegate.runtime_locator import RuntimeLocator; "
                    "from devlegate.systemd_supervisor import render_unit; "
                    "print(render_unit(RuntimeLocator(Path('/repo/%i/$project'), "
                    "Path('/state'), 'a' * 64), "
                    "Path('/repo/%i/$project/.env'), "
                    "launcher=product_launcher()))"
                ),
            ],
            env={**environment, "PEX_INTERPRETER": "1"},
        ).stdout
        repo = root / "repo"
        remote = root / "remote.git"
        run([str(git), "init", "--bare", str(remote)])
        repo.mkdir()
        run([str(git), "init", "-b", "main"], cwd=repo)
        run([str(git), "config", "user.email", "proof@example.invalid"], cwd=repo)
        run([str(git), "config", "user.name", "Standalone Proof"], cwd=repo)
        (repo / "README.md").write_text("standalone proof\n")
        state = root / "state"
        env_file = repo / ".env"
        env_file.write_text(
            "REMOTE_BRANCH=main\n"
            "CONTROL_BRANCH=devlegate/control\n"
            "OPENCODE_BIN=true\n"
            "OPENCODE_MODEL=fake\n"
            "POLL_INTERVAL=1\n"
            f"STATE_DIR={state}\n"
        )
        run([str(git), "add", "README.md", ".env"], cwd=repo)
        run([str(git), "commit", "-m", "standalone-proof"], cwd=repo)
        run([str(git), "remote", "add", "origin", str(remote)], cwd=repo)
        run([str(git), "push", "-u", "origin", "main"], cwd=repo)
        run(
            [str(executable), "host", "install", "--supervisor", "internal"],
            env=environment,
        )
        run(
            [str(executable), "project", "alias", "proof", str(repo)],
            env=environment,
        )
        run(
            [str(executable), "--env", str(env_file), "control", "init"],
            env=environment,
            cwd=root,
        )
        start = run(
            [str(executable), "--env", str(env_file)], env=environment, cwd=root
        )
        status = run(
            [str(executable), "--env", str(env_file), "status", "--json"],
            env=environment,
            cwd=root,
        )
        restart = run(
            [str(executable), "--env", str(env_file), "restart"],
            env=environment,
            cwd=root,
        )
        restarted_status = run(
            [str(executable), "--env", str(env_file), "status", "--json"],
            env=environment,
            cwd=root,
        )
        stop = run(
            [str(executable), "--env", str(env_file), "stop"],
            env=environment,
            cwd=root,
        )
        report = {
            "artifact": inspect,
            "version": version,
            "identity": json.loads(identity),
            "pex_info": json.loads(pex_info),
            "path": str(artifact),
            "runtime_executable": str(executable),
            "path_has_spaces_percent_dollar": True,
            "systemd_exec_start": next(
                line for line in unit.splitlines() if line.startswith("ExecStart=")
            ),
            "systemd_working_directory": next(
                line
                for line in unit.splitlines()
                if line.startswith("WorkingDirectory=")
            ),
            "start_stdout": start.stdout.strip(),
            "status_stdout": status.stdout.strip(),
            "restart_stdout": restart.stdout.strip(),
            "restarted_status_stdout": restarted_status.stdout.strip(),
            "stop_stdout": stop.stdout.strip(),
            "host_python_path": environment["PATH"],
            "network_runtime_inputs": [],
        }
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def build(args: argparse.Namespace) -> int:
    repo = args.repo.resolve()
    output = args.output.resolve()
    builder = builder_python_info(args.python)
    major, minor = map(int, builder["version"].split(".")[:2])
    validate_builder_version(builder["implementation"], major, minor)
    version = project_version(repo)
    commit = source_commit(repo)
    epoch = source_epoch(repo)
    supplied_wheel = args.wheel.resolve() if args.wheel is not None else None
    if supplied_wheel is not None:
        if not supplied_wheel.is_file():
            raise BuildError(f"canonical wheel not found: {supplied_wheel}")
        supplied_info = inspect_wheel(supplied_wheel)
        if supplied_info["version"] != version:
            raise BuildError(
                "canonical wheel version does not match the source project: "
                f"{supplied_info['version']} != {version}"
            )
    with tempfile.TemporaryDirectory(prefix="devlegate-standalone-") as temporary:
        root = Path(temporary)
        tools = root / "tools"
        pex_wheel, pex_runtime, tool_wheels = download_packaging_tools(
            args.python, tools
        )
        build_a_root = root / "build-a"
        build_b_root = root / "build-b"
        if supplied_wheel is None:
            wheel_python_a, wheel_toolchain_a = create_wheel_build_environment(
                args.python, build_a_root, tool_wheels
            )
            wheel_python_b, wheel_toolchain_b = create_wheel_build_environment(
                args.python, build_b_root, tool_wheels
            )
            if wheel_toolchain_a != wheel_toolchain_b:
                raise BuildError("independent wheel build environments differ")
            wheel_a = build_wheel(
                repo, build_a_root / "wheel", str(wheel_python_a), epoch
            )
            wheel_b = build_wheel(
                repo, build_b_root / "wheel", str(wheel_python_b), epoch
            )
        else:
            wheel_toolchain_a = {
                name: filename.split("-")[1]
                for name, (filename, _digest) in WHEEL_BUILD_TOOLS.items()
            }
            wheel_toolchain_b = wheel_toolchain_a
            wheel_a = supplied_wheel
            wheel_b = supplied_wheel
        wheel_a_info = inspect_wheel(wheel_a)
        wheel_b_info = inspect_wheel(wheel_b)
        if wheel_a_info["sha256"] != wheel_b_info["sha256"]:
            raise BuildError("repeated wheel builds are not byte-identical")
        wheel_input_a = build_a_root / "wheel-input"
        wheel_input_b = build_b_root / "wheel-input"
        wheel_input_a.mkdir()
        wheel_input_b.mkdir()
        wheel_a_copy = wheel_input_a / wheel_a.name
        wheel_b_copy = wheel_input_b / wheel_b.name
        shutil.copy2(wheel_a, wheel_a_copy)
        shutil.copy2(wheel_b, wheel_b_copy)
        verify_file(wheel_a_copy, wheel_a_info["sha256"])
        verify_file(wheel_b_copy, wheel_b_info["sha256"])
        artifact_a_dir = build_a_root / "scie"
        artifact_b_dir = build_b_root / "scie"
        artifact_a_dir.mkdir()
        artifact_b_dir.mkdir()
        artifact_a = build_scie(
            wheel_a,
            wheel_input_a,
            tools,
            pex_runtime,
            args.python,
            artifact_a_dir / f"devlegate-{version}-linux-x86_64",
            tools / SCIENCE_ASSET,
            build_a_root,
            epoch,
        )
        artifact_b = build_scie(
            wheel_b,
            wheel_input_b,
            tools,
            pex_runtime,
            args.python,
            artifact_b_dir / f"devlegate-{version}-linux-x86_64",
            tools / SCIENCE_ASSET,
            build_b_root,
            epoch,
        )
        scie_a = inspect_scie(artifact_a, forbidden_build_root=root)
        scie_b = inspect_scie(artifact_b, forbidden_build_root=root)
        split_a = split_scie(artifact_a, build_a_root / "split")
        split_b = split_scie(artifact_b, build_b_root / "split")
        if {name: sha256(path) for name, path in split_a.items()} != {
            name: sha256(path) for name, path in split_b.items()
        }:
            raise BuildError("repeated scie split outputs are not identical")
        print("BUILD A")
        print(f"build_root: {build_a_root}")
        print(f"PEX_ROOT: {build_a_root / 'PEX_ROOT'}")
        print(f"wheel_sha256: {wheel_a_info['sha256']}")
        print(f"scie_sha256: {scie_a['sha256']}")
        print("BUILD B")
        print(f"build_root: {build_b_root}")
        print(f"PEX_ROOT: {build_b_root / 'PEX_ROOT'}")
        print(f"wheel_sha256: {wheel_b_info['sha256']}")
        print(f"scie_sha256: {scie_b['sha256']}")
        print("COMPARE")
        print(f"build_roots_differ: {build_a_root != build_b_root}")
        pex_roots_differ = (
            build_environment(build_a_root, epoch)["PEX_ROOT"]
            != (build_environment(build_b_root, epoch)["PEX_ROOT"])
        )
        print(f"PEX_ROOT_values_differ: {pex_roots_differ}")
        print(f"wheel_identical: {wheel_a_info['sha256'] == wheel_b_info['sha256']}")
        print(f"scie_identical: {scie_a['sha256'] == scie_b['sha256']}")
        print("custom_runtime_base_embedded: False")
        print("temporary_build_path_found_in_inspection: False")
        output.mkdir(parents=True, exist_ok=True)
        final_artifact = output / f"devlegate-{version}-linux-x86_64"
        shutil.copy2(artifact_a, final_artifact)
        final_artifact.chmod(final_artifact.stat().st_mode | stat.S_IXUSR)
        report = {
            "schema_version": 2,
            "source_commit": commit,
            "source_epoch": epoch,
            "builder": builder,
            "inputs": asdict(INPUTS),
            "pex": {
                "version": PEX_VERSION,
                "wheel_filename": pex_wheel.name,
                "wheel_sha256": sha256(pex_wheel),
                "bootstrap_tools": {
                    name: {"filename": filename, "sha256": expected_hash}
                    for name, (filename, expected_hash) in PEX_BOOTSTRAP_TOOLS.items()
                },
            },
            "science": {
                "version": SCIENCE_VERSION,
                "asset": SCIENCE_ASSET,
                "sha256": sha256(tools / SCIENCE_ASSET),
            },
            "wheel_build_toolchain": {
                "versions": wheel_toolchain_a,
                "artifacts": {
                    name: {"filename": filename, "sha256": expected_hash}
                    for name, (filename, expected_hash) in WHEEL_BUILD_TOOLS.items()
                },
            },
            "wheel": wheel_a_info,
            "wheel_second_sha256": wheel_b_info["sha256"],
            "wheel_reproducible": wheel_a_info["sha256"] == wheel_b_info["sha256"],
            "scie": scie_a,
            "scie_reproducible": scie_a["sha256"] == scie_b["sha256"],
            "scie_second_sha256": scie_b["sha256"],
            "target_libc": LIBC,
            "scie_jump": {
                "asset": SCIE_JUMP_ASSET,
                "version": SCIE_JUMP_VERSION,
                "sha256": SCIE_JUMP_SHA256,
            },
            "split_inventory": sorted(
                {
                    name: {
                        "size": split_a[name].stat().st_size,
                        "sha256": sha256(split_a[name]),
                    }
                    for name in split_a
                }.items()
            ),
            "reproducibility_scope": (
                "byte-reproducible on the tested Linux x86_64 build environment "
                "from the pinned immutable inputs"
            ),
            "independent_build_roots": build_a_root != build_b_root,
            "independent_pex_roots": build_environment(build_a_root, epoch)["PEX_ROOT"]
            != build_environment(build_b_root, epoch)["PEX_ROOT"],
            "temporary_build_path_embedded": False,
        }
    report_path = output / "standalone-build.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(final_artifact)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("command", choices=("build", "prove"))
    command.add_argument("--repo", type=Path, default=Path(__file__).parents[1])
    command.add_argument("--output", type=Path)
    command.add_argument("--artifact", type=Path)
    command.add_argument("--report", type=Path, default=Path("standalone-proof.json"))
    command.add_argument("--python", default=sys.executable)
    command.add_argument(
        "--wheel", type=Path, help="use an already validated canonical wheel"
    )
    return command


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "build":
            if args.output is None:
                parser().error("build requires --output")
            return build(args)
        if args.artifact is None:
            parser().error("prove requires --artifact")
        return prove(args)
    except (BuildError, OSError, subprocess.SubprocessError) as error:
        print(f"build-standalone: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
