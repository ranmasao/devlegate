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
import zipfile
from pathlib import Path

PEX_VERSION = "2.103.2"
BOOTSTRAP_VERSIONS = ("pip==23.2", "setuptools==68.0.0", "wheel==0.40.0")
TARGET = "linux-x86_64"


class BuildError(RuntimeError):
    """The standalone proof cannot be completed safely."""


def run(command: list[str], *, env=None, cwd=None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = "\n".join(
            part for part in (result.stdout.strip(), result.stderr.strip()) if part
        ) or "no output"
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


def validate_builder_python(python: str) -> None:
    result = run(
        [
            python,
            "-c",
            (
                "import platform, sys; "
                "print(platform.python_implementation(), sys.version_info[:2])"
            ),
        ]
    ).stdout.strip()
    if result != "CPython (3, 12)":
        raise BuildError(f"standalone build requires CPython 3.12, found {result}")


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


def download_packaging_tools(python: str, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
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
            f"pex=={PEX_VERSION}",
            *BOOTSTRAP_VERSIONS,
        ]
    )
    pex_wheel = next(directory.glob(f"pex-{PEX_VERSION}-*.whl"))
    runtime = directory / "pex-runtime"
    runtime.mkdir()
    with zipfile.ZipFile(pex_wheel) as archive:
        archive.extractall(runtime)
    return pex_wheel, runtime


def build_scie(
    wheel: Path,
    wheel_dir: Path,
    bootstrap_dir: Path,
    pex_runtime: Path,
    python: str,
    artifact: Path,
) -> Path:
    environment = {
        **os.environ,
        "PYTHONPATH": str(pex_runtime),
    }
    run(
        [
            python,
            "-m",
            "pex",
            "--no-pypi",
            "--find-links",
            str(wheel_dir),
            "--find-links",
            str(bootstrap_dir),
            f"devlegate=={project_version_from_wheel(wheel)}",
            "--entry-point",
            "devlegate",
            "--include-tools",
            "--scie",
            "eager",
            "--scie-only",
            "--scie-platform",
            TARGET,
            "--scie-python-version",
            "3.12",
            "--output-file",
            str(artifact),
        ],
        env=environment,
    )
    if not artifact.is_file() or not artifact.stat().st_mode & stat.S_IXUSR:
        raise BuildError(f"scie artifact is not executable: {artifact}")
    return artifact


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


def inspect_scie(artifact: Path) -> dict[str, object]:
    file_result = subprocess.run(
        ["file", str(artifact)], text=True, capture_output=True, check=False
    )
    if file_result.returncode or "ELF" not in file_result.stdout:
        raise BuildError(
            f"artifact is not a native ELF executable: {file_result.stdout}"
        )
    return {
        "filename": artifact.name,
        "size": artifact.stat().st_size,
        "sha256": sha256(artifact),
        "file": file_result.stdout.strip(),
        "pex_version": PEX_VERSION,
        "target": TARGET,
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
    validate_builder_python(args.python)
    version = project_version(repo)
    commit = source_commit(repo)
    epoch = source_epoch(repo)
    with tempfile.TemporaryDirectory(prefix="devlegate-standalone-") as temporary:
        root = Path(temporary)
        tools = root / "tools"
        _pex_wheel, pex_runtime = download_packaging_tools(args.python, tools)
        wheel_a = build_wheel(repo, root / "wheel-a", args.python, epoch)
        wheel_b = build_wheel(repo, root / "wheel-b", args.python, epoch)
        wheel_a_info = inspect_wheel(wheel_a)
        wheel_b_info = inspect_wheel(wheel_b)
        if wheel_a_info["sha256"] != wheel_b_info["sha256"]:
            raise BuildError("repeated wheel builds are not byte-identical")
        artifact_a_dir = root / "scie-a"
        artifact_b_dir = root / "scie-b"
        artifact_a_dir.mkdir()
        artifact_b_dir.mkdir()
        artifact_a = build_scie(
            wheel_a,
            wheel_a.parent,
            tools,
            pex_runtime,
            args.python,
            artifact_a_dir / f"devlegate-{version}-linux-x86_64",
        )
        artifact_b = build_scie(
            wheel_a,
            wheel_a.parent,
            tools,
            pex_runtime,
            args.python,
            artifact_b_dir / f"devlegate-{version}-linux-x86_64",
        )
        scie_a = inspect_scie(artifact_a)
        scie_b = inspect_scie(artifact_b)
        output.mkdir(parents=True, exist_ok=True)
        final_artifact = output / f"devlegate-{version}-linux-x86_64"
        shutil.copy2(artifact_a, final_artifact)
        final_artifact.chmod(final_artifact.stat().st_mode | stat.S_IXUSR)
        report = {
            "source_commit": commit,
            "source_epoch": epoch,
            "wheel": wheel_a_info,
            "wheel_reproducible": wheel_a_info["sha256"] == wheel_b_info["sha256"],
            "scie": scie_a,
            "scie_reproducible": scie_a["sha256"] == scie_b["sha256"],
            "scie_second_sha256": scie_b["sha256"],
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
