#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Build validated Devlegate distributions from one source-tree graph."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from email.message import Message
from email.parser import Parser
from pathlib import Path

from devlegate.cli_common import ConciseArgumentParser

TARGETS = ("wheel", "sdist", "python", "standalone", "deb", "full-source", "all")
GRAPH = {
    "wheel": (),
    "sdist": (),
    "standalone": ("wheel",),
    "deb": ("standalone",),
    "full-source": (),
}
ALL_TARGETS = ("wheel", "sdist", "standalone", "deb", "full-source")


class DistributionError(RuntimeError):
    """The selected distribution graph cannot be completed safely."""


@dataclass(frozen=True)
class Source:
    repo: Path
    commit: str
    version: str
    python: str


@dataclass(frozen=True)
class Artifact:
    path: Path
    kind: str


def run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = (
            "\n".join(
                part for part in (result.stdout.strip(), result.stderr.strip()) if part
            )
            or "no output"
        )
        raise DistributionError(f"command failed ({' '.join(command)}):\n{detail}")
    return result.stdout


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def project_version(repo: Path) -> str:
    data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def source_identity(repo: Path, python: str) -> Source:
    status = run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"]
    )
    if status:
        raise DistributionError("packaging requires a clean Git worktree")
    return Source(
        repo=repo.resolve(),
        commit=run(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip(),
        version=project_version(repo),
        python=python,
    )


def require_tools(names: tuple[str, ...]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise DistributionError(f"required host tool(s) missing: {', '.join(missing)}")


def artifact_in(directory: Path, pattern: str, kind: str) -> Artifact:
    candidates = sorted(directory.glob(pattern))
    if len(candidates) != 1:
        raise DistributionError(
            f"expected one {kind} artifact matching {pattern}, found {candidates}"
        )
    return Artifact(candidates[0], kind)


def metadata_from_zip(archive: zipfile.ZipFile) -> tuple[str, Message]:
    names = set(archive.namelist())
    metadata_names = sorted(
        name for name in names if name.endswith(".dist-info/METADATA")
    )
    if len(metadata_names) != 1:
        raise DistributionError("wheel must contain exactly one METADATA file")
    return metadata_names[0], Parser().parsestr(
        archive.read(metadata_names[0]).decode()
    )


def safe_member(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise DistributionError(f"unsafe archive path: {name}")


def validate_wheel(artifact: Artifact, source: Source) -> None:
    required = {
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "LICENSES/CC0-1.0.txt",
        "src/devlegate/_vendor/nanoyaml/LICENSE",
    }
    with zipfile.ZipFile(artifact.path) as archive:
        for name in archive.namelist():
            safe_member(name)
        metadata_name, metadata = metadata_from_zip(archive)
        names = set(archive.namelist())
        dist_info = metadata_name.removesuffix("/METADATA")
        if metadata.get("Name") != "devlegate":
            raise DistributionError("wheel metadata has the wrong project name")
        if metadata.get("Version") != source.version:
            raise DistributionError("wheel version does not match pyproject.toml")
        if metadata.get("Requires-Python") != ">=3.12":
            raise DistributionError("wheel Requires-Python is not >=3.12")
        if any(
            line.startswith("Requires-Dist:") and "extra ==" not in line
            for line in metadata.as_string().splitlines()
        ):
            raise DistributionError("wheel acquired unexpected runtime dependencies")
        if not any(name.startswith("devlegate/") for name in names):
            raise DistributionError("wheel has no Devlegate package payload")
        if not any(name.startswith("devlegate/default_templates/") for name in names):
            raise DistributionError("wheel has no packaged templates")
        missing = [
            path
            for path in required
            if path not in names and f"{dist_info}/licenses/{path}" not in names
        ]
        if missing:
            raise DistributionError(f"wheel is missing license payload: {missing}")
        if any(name.startswith("dev/") for name in names):
            raise DistributionError("wheel contains source-tree developer tooling")


def validate_sdist(artifact: Artifact, source: Source) -> None:
    expected_root = f"devlegate-{source.version}"
    with tarfile.open(artifact.path, "r:gz") as archive:
        members = archive.getmembers()
        if not members or members[0].name != expected_root:
            raise DistributionError("sdist has an unexpected top-level directory")
        for member in members:
            safe_member(member.name)
            if not (
                member.name == expected_root
                or member.name.startswith(expected_root + "/")
            ):
                raise DistributionError("sdist contains a path outside its root")
            if not (member.isdir() or member.isreg()):
                raise DistributionError(
                    f"sdist contains unsupported member: {member.name}"
                )
        names = {member.name.removeprefix(expected_root + "/") for member in members}
        required = {"pyproject.toml", "README.md", "LICENSE", "NOTICE", "LICENSING.md"}
        if not required <= names or not any(
            name.startswith("src/devlegate/") for name in names
        ):
            raise DistributionError("sdist source or license content is incomplete")
        if any("/tmp/" in name or name.startswith("build/") for name in names):
            raise DistributionError("sdist contains transient build paths")


def prove_python_install(artifact: Artifact, source: Source, work: Path) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PEX_PATH"}
    }
    venv = work / f"proof-{artifact.kind}"
    run([source.python, "-m", "venv", str(venv)])
    proof_python = venv / "bin" / "python"
    run(
        [
            str(proof_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            str(artifact.path),
        ]
    )
    result = subprocess.run(
        [str(venv / "bin" / "devlegate"), "version"],
        cwd=work,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise DistributionError(
            f"{artifact.kind} isolated install proof failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def build_python_artifact(source: Source, work: Path, kind: str) -> Artifact:
    output = work / kind
    output.mkdir()
    flag = "--wheel" if kind == "wheel" else "--sdist"
    run(
        [
            source.python,
            "-m",
            "build",
            "--no-isolation",
            flag,
            "--outdir",
            str(output),
            str(source.repo),
        ]
    )
    pattern = "devlegate-*.whl" if kind == "wheel" else "devlegate-*.tar.gz"
    return artifact_in(output, pattern, kind)


def build_standalone(
    source: Source, wheel: Artifact, work: Path
) -> dict[str, Artifact]:
    require_tools(("file", "git"))
    output = work / "standalone-build"
    output.mkdir()
    run(
        [
            source.python,
            str(source.repo / "tools/build_standalone.py"),
            "build",
            "--repo",
            str(source.repo),
            "--output",
            str(output),
            "--python",
            source.python,
            "--wheel",
            str(wheel.path),
        ]
    )
    executable = artifact_in(
        output, "devlegate-*-linux-x86_64", "standalone executable"
    )
    report = output / "standalone-build.json"
    if not report.is_file():
        raise DistributionError("standalone builder did not produce its build report")
    package_output = work / "standalone-package"
    package_output.mkdir()
    run(
        [
            source.python,
            str(source.repo / "tools/package_standalone.py"),
            "package",
            "--repo",
            str(source.repo),
            "--artifact",
            str(executable.path),
            "--build-report",
            str(report),
            "--output-dir",
            str(package_output),
        ]
    )
    archive = artifact_in(
        package_output, "devlegate-*-linux-x86_64.tar.gz", "standalone archive"
    )
    sidecar = Artifact(
        archive.path.with_name(f"{archive.path.name}.sha256"), "standalone checksum"
    )
    validate_dir = work / "standalone-validated"
    validated_root = Path(
        run(
            [
                source.python,
                str(source.repo / "tools/validate_standalone_package.py"),
                "--archive",
                str(archive.path),
                "--sidecar",
                str(sidecar.path),
                "--repo",
                str(source.repo),
                "--build-report",
                str(report),
                "--extract-dir",
                str(validate_dir),
            ]
        )
        .strip()
        .splitlines()[-1]
    )
    smoke_environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(work / "smoke-home"),
        "XDG_CONFIG_HOME": str(work / "smoke-config"),
        "XDG_STATE_HOME": str(work / "smoke-state"),
        "XDG_CACHE_HOME": str(work / "smoke-cache"),
        "PEX_ROOT": str(work / "smoke-pex-root"),
    }
    result = subprocess.run(
        [str(validated_root / "devlegate"), "version"],
        cwd=work,
        env=smoke_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise DistributionError(
            f"standalone isolated execution proof failed:\n{result.stderr}"
        )
    return {
        "archive": archive,
        "sidecar": sidecar,
        "executable": executable,
        "report": Artifact(report, "standalone report"),
    }


def build_deb(source: Source, standalone: dict[str, Artifact], work: Path) -> Artifact:
    require_tools(("dpkg-deb",))
    output = work / "deb"
    output.mkdir()
    run(
        [
            source.python,
            str(source.repo / "tools/package_deb.py"),
            "--repo",
            str(source.repo),
            "--archive",
            str(standalone["archive"].path),
            "--sidecar",
            str(standalone["sidecar"].path),
            "--build-report",
            str(standalone["report"].path),
            "--output-dir",
            str(output),
        ]
    )
    package = artifact_in(output, "devlegate_*.deb", "Debian package")
    extracted = work / "deb-validated"
    binary_command = run(
        [
            source.python,
            str(source.repo / "tools/validate_deb.py"),
            "--package",
            str(package.path),
            "--build-report",
            str(standalone["report"].path),
            "--extract-dir",
            str(extracted),
        ]
    ).strip()
    binary = Path(binary_command.splitlines()[-1])
    result = subprocess.run(
        [str(binary), "version"], cwd=work, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise DistributionError(
            f"Debian extracted payload proof failed:\n{result.stderr}"
        )
    sidecar = package.path.with_name(f"{package.path.name}.sha256")
    sidecar.write_text(
        f"{digest(package.path)}  {package.path.name}\n", encoding="ascii"
    )
    return package


def build_full_source(source: Source, work: Path) -> dict[str, Artifact]:
    output = work / "full-source"
    output.mkdir()
    release_ref = f"v{source.version}"
    run(
        [
            source.python,
            str(source.repo / "tools/build_full_source.py"),
            "--repo",
            str(source.repo),
            "--ref",
            source.commit,
            "--version",
            release_ref,
            "--output-dir",
            str(output),
        ]
    )
    archive = artifact_in(
        output, "devlegate-*-full-source.tar.gz", "full-source archive"
    )
    sidecar = Artifact(
        archive.path.with_name(f"{archive.path.name}.sha256"), "full-source checksum"
    )
    run(
        [
            source.python,
            str(source.repo / "tools/validate_full_source.py"),
            "--archive",
            str(archive.path),
            "--sidecar",
            str(sidecar.path),
            "--repo",
            str(source.repo),
            "--ref",
            source.commit,
            "--version",
            release_ref,
            "--extract-dir",
            str(work / "full-source-validated"),
        ]
    )
    return {"archive": archive, "sidecar": sidecar}


def expand_targets(target: str) -> tuple[str, ...]:
    if target == "python":
        return ("wheel", "sdist")
    if target == "all":
        return ALL_TARGETS
    return (target,)


def dependency_order(targets: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []

    def visit(node: str, trail: tuple[str, ...] = ()) -> None:
        if node in trail:
            raise DistributionError(
                f"package graph cycle: {' -> '.join((*trail, node))}"
            )
        for dependency in GRAPH.get(node, ()):
            visit(dependency, (*trail, node))
        if node not in result:
            result.append(node)

    for target in targets:
        visit(target)
    return tuple(result)


def publish(artifacts: list[Path], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    published = []
    for artifact in artifacts:
        temporary = output_dir / f".{artifact.name}.pending-{os.getpid()}"
        shutil.copy2(artifact, temporary)
        os.replace(temporary, output_dir / artifact.name)
        published.append(output_dir / artifact.name)
    return published


def output_directory(repo: Path, output_dir: Path | None) -> Path:
    return output_dir.resolve() if output_dir is not None else repo.resolve() / "dist"


def selected_final_files(target: str, values: dict[str, object]) -> list[Path]:
    files: list[Path] = []
    if target in {"wheel", "python", "all"}:
        files.append(values["wheel"].path)
    if target in {"sdist", "python", "all"}:
        files.append(values["sdist"].path)
    if target in {"standalone", "all"}:
        standalone = values["standalone"]
        files.extend((standalone["archive"].path, standalone["sidecar"].path))
    if target in {"deb", "all"}:
        package = values["deb"]
        files.append(package.path)
        files.append(package.path.with_name(f"{package.path.name}.sha256"))
    if target in {"full-source", "all"}:
        full_source = values["full-source"]
        files.extend((full_source["archive"].path, full_source["sidecar"].path))
    return files


def package(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    source = source_identity(repo, str(Path(args.python)))
    require_tools(("git",))
    if args.target in {"standalone", "deb", "all"}:
        require_tools(("file",))
    if args.target in {"deb", "all"}:
        require_tools(("dpkg-deb",))
    selected = expand_targets(args.target)
    order = dependency_order(selected)
    workspace_path = Path(tempfile.mkdtemp(prefix="devlegate-distribution-"))
    print(f"Source: {source.commit}")
    print(f"Version: {source.version}")
    values: dict[str, object] = {}
    try:
        for index, node in enumerate(order, 1):
            print(f"[{index}/{len(order)}] Building {node}")
            if node == "wheel":
                artifact = build_python_artifact(source, workspace_path, "wheel")
                validate_wheel(artifact, source)
                values[node] = artifact
            elif node == "sdist":
                artifact = build_python_artifact(source, workspace_path, "sdist")
                validate_sdist(artifact, source)
                values[node] = artifact
            elif node == "standalone":
                values[node] = build_standalone(source, values["wheel"], workspace_path)
            elif node == "deb":
                values[node] = build_deb(source, values["standalone"], workspace_path)
            elif node == "full-source":
                values[node] = build_full_source(source, workspace_path)
        if args.target in {"wheel", "python", "all"}:
            print(f"[{len(order) + 1}/{len(order) + 1}] Proving Python installations")
            prove_python_install(values["wheel"], source, workspace_path)
        if args.target in {"sdist", "python", "all"}:
            if args.target == "sdist":
                print(
                    f"[{len(order) + 1}/{len(order) + 1}] Proving Python installation"
                )
            prove_python_install(values["sdist"], source, workspace_path)
        final_files = publish(
            selected_final_files(args.target, values),
            output_directory(repo, args.output_dir),
        )
        print("\nDevlegate distributions ready")
        for path in final_files:
            print(f"{path}: {path.stat().st_size} bytes {digest(path)}")
        return 0
    finally:
        if args.keep_work:
            print(f"Temporary workspace kept: {workspace_path}")
        else:
            shutil.rmtree(workspace_path, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    repo = Path(__file__).parents[1].resolve()

    def add_common_options(target, *, defaults: bool) -> None:
        target.add_argument(
            "--output-dir",
            type=Path,
            metavar="PATH",
            default=None if defaults else argparse.SUPPRESS,
            help="write final validated artifacts to PATH instead of <repo>/dist",
        )
        target.add_argument(
            "--repo",
            type=Path,
            default=repo if defaults else argparse.SUPPRESS,
            metavar="PATH",
            help=(
                "package this Devlegate source repository instead of the "
                "default checkout"
            ),
        )
        target.add_argument(
            "--keep-work",
            action="store_true",
            default=False if defaults else argparse.SUPPRESS,
            help="keep temporary build files and print their location",
        )
        target.add_argument(
            "--python",
            default=sys.executable if defaults else argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )

    common = argparse.ArgumentParser(add_help=False)
    add_common_options(common, defaults=False)
    command = ConciseArgumentParser(
        prog="./dev package",
        description="Build and validate Devlegate distribution artifacts.",
        usage="%(prog)s COMMAND [options]",
        error_prefix="dev: package",
        quote_unknown_commands=False,
        help_program="./dev package",
    )
    command._optionals.title = "Options"
    add_common_options(command, defaults=True)
    commands = command.add_subparsers(
        dest="target", required=True, parser_class=ConciseArgumentParser
    )
    descriptions = {
        "wheel": "Build and validate the Python wheel.",
        "sdist": "Build and validate the Python source distribution.",
        "python": "Build wheel and sdist and prove isolated pip installation.",
        "standalone": (
            "Build and validate the self-contained Linux distribution. "
            "The wheel is built automatically."
        ),
        "deb": (
            "Build and validate the Debian package. Wheel and standalone "
            "prerequisites are built automatically."
        ),
        "full-source": "Build and validate the materialized full-source archive.",
        "all": "Build and validate all supported distribution formats.",
    }
    for target in TARGETS:
        child = commands.add_parser(
            target,
            prog=f"./dev package {target}",
            parents=[common],
            help=descriptions[target].split(".", 1)[0],
            description=descriptions[target],
            error_prefix=f"dev: package {target}",
            quote_unknown_commands=False,
            help_program=f"./dev package {target}",
        )
        child._optionals.title = "Options"
        child.set_defaults(target=target)
    return command


def main() -> int:
    args = parser().parse_args()
    try:
        return package(args)
    except (
        DistributionError,
        OSError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as error:
        print(f"dev: package: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
