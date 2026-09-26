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
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from email.parser import Parser
from pathlib import Path

from devlegate.cli_common import ConciseArgumentParser

try:
    from build_progress import (
        Component,
        ComponentEvent,
        ComponentPlan,
        ComponentStep,
        freeze_plan,
    )
except ModuleNotFoundError:
    from tools.build_progress import (
        Component,
        ComponentEvent,
        ComponentPlan,
        ComponentStep,
        freeze_plan,
    )

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


@dataclass(frozen=True)
class SemanticStep:
    target: str
    name: str
    leaf_id: str | None = None

    @property
    def identity(self) -> str:
        return self.leaf_id or f"{self.target}:{self.name}"


@dataclass(frozen=True)
class ProgressEvent:
    action: str
    step: SemanticStep


class ProgressReporter:
    """Render one deterministic progress stream for the complete build plan."""

    def __init__(self, steps: tuple[SemanticStep, ...]) -> None:
        self.steps = steps
        self.completed = 0
        self.skipped: list[SemanticStep] = []
        self._started: SemanticStep | None = None

    def emit(self, event: ProgressEvent) -> None:
        matches = [step for step in self.steps if step.identity == event.step.identity]
        if len(matches) != 1:
            raise DistributionError(
                f"progress event is not in the frozen plan: {event.step}"
            )
        step = matches[0]
        position = self.steps.index(step) + 1
        if event.action == "start":
            if position != self.completed + 1 or self._started is not None:
                raise DistributionError(
                    f"progress step is out of order: {event.step.name}"
                )
            self._started = step
            print(
                f"[{self.completed}/{len(self.steps)}] START {step.target}: {step.name}"
            )
        elif event.action in {"complete", "skip"}:
            if event.action == "complete" and self._started != step:
                raise DistributionError(f"progress step was not started: {step.name}")
            if event.action == "skip" and self._started not in {None, step}:
                raise DistributionError(f"progress step was not started: {step.name}")
            if position != self.completed + 1:
                raise DistributionError(f"progress step is out of order: {step.name}")
            self.completed += 1
            status = "SKIP" if event.action == "skip" else "DONE"
            if event.action == "skip":
                self.skipped.append(step)
            print(
                f"[{self.completed}/{len(self.steps)}] {status} "
                f"{step.target}: {step.name}"
            )
            self._started = None
        elif event.action == "fail":
            if self._started != step:
                raise DistributionError(f"progress step was not started: {step.name}")
            print(
                f"[{self.completed}/{len(self.steps)}] FAILED "
                f"{step.target}: {step.name}"
            )
        else:
            raise DistributionError(f"unknown progress event: {event.action}")

    def step(self, semantic_step: SemanticStep, action: Callable[[], object]) -> object:
        self.emit(ProgressEvent("start", semantic_step))
        try:
            result = action()
        except Exception:
            self.emit(ProgressEvent("fail", semantic_step))
            raise
        self.emit(ProgressEvent("complete", semantic_step))
        return result


class FunctionComponent:
    """Component adapter for an existing operation or component builder."""

    def __init__(
        self,
        plan: ComponentPlan,
        action: Callable[[Callable[[ComponentEvent], None]], object],
    ) -> None:
        self._plan = plan
        self._action = action

    def plan(self) -> ComponentPlan:
        return self._plan

    def run(self, emit: Callable[[ComponentEvent], None]) -> object:
        return self._action(emit)


class TreeComponent:
    """Composite component that scopes child leaf IDs by its stable tree key."""

    def __init__(self, plan: ComponentPlan, children: tuple[Component, ...]) -> None:
        self._plan = plan
        self._children = children

    def plan(self) -> ComponentPlan:
        return self._plan

    def run(self, emit: Callable[[ComponentEvent], None]) -> tuple[object, ...]:
        results = []
        for child, child_plan in zip(self._children, self._plan.children, strict=True):
            child_leaves = freeze_plan(child_plan, child_plan.key or "")
            by_identity = {step.identity: leaf_id for leaf_id, step in child_leaves}

            def scoped(
                event: ComponentEvent,
                *,
                child_leaves=child_leaves,
                by_identity=by_identity,
            ) -> None:
                local_id = event.leaf_id or event.step.identity
                if local_id in {leaf_id for leaf_id, _step in child_leaves}:
                    leaf_id = local_id
                else:
                    try:
                        leaf_id = by_identity[local_id]
                    except KeyError as error:
                        raise DistributionError(
                            f"component emitted an unknown leaf: {local_id}"
                        ) from error
                emit(ComponentEvent(event.action, event.step, leaf_id))

            results.append(child.run(scoped))
        return tuple(results)


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


def build_python_artifact(
    source: Source, work: Path, kind: str, reporter: ProgressReporter | None = None
) -> Artifact:
    output = work / kind

    def build() -> Artifact:
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

    label = "build wheel" if kind == "wheel" else "build source distribution"
    return _step(reporter, kind, label, build)  # type: ignore[return-value]


def build_standalone(
    source: Source,
    wheel: Artifact,
    work: Path,
    reporter: ProgressReporter | None = None,
    emit: Callable[[ComponentEvent], None] | None = None,
) -> dict[str, Artifact]:
    require_tools(("file", "git"))
    output = work / "standalone-build"
    output.mkdir()
    try:
        from build_standalone import build as standalone_build
    except ModuleNotFoundError:
        from tools.build_standalone import build as standalone_build
    try:
        from build_standalone import semantic_plan as standalone_plan
    except ModuleNotFoundError:
        from tools.build_standalone import semantic_plan as standalone_plan
    import argparse

    component_steps = standalone_plan() + tuple(
        ComponentStep(name, key=key)
        for key, name in (
            ("package", "package standalone archive"),
            ("validate", "validate standalone archive"),
            ("prove", "prove standalone execution"),
        )
    )
    emit = emit or (
        component_emitter(reporter, "standalone", component_steps) if reporter else None
    )
    standalone_build(
        argparse.Namespace(
            repo=source.repo,
            output=output,
            python=source.python,
            wheel=wheel.path,
        ),
        emit=emit,
    )
    executable = artifact_in(
        output, "devlegate-*-linux-x86_64", "standalone executable"
    )
    report = output / "standalone-build.json"
    if not report.is_file():
        raise DistributionError("standalone builder did not produce its build report")
    package_output = work / "standalone-package"
    package_output.mkdir()
    component_step(
        emit,
        "package",
        "package standalone archive",
        lambda: run(
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
        ),
    )
    archive = artifact_in(
        package_output, "devlegate-*-linux-x86_64.tar.gz", "standalone archive"
    )
    sidecar = Artifact(
        archive.path.with_name(f"{archive.path.name}.sha256"), "standalone checksum"
    )
    validate_dir = work / "standalone-validated"
    validated_root = Path(
        component_step(
            emit,
            "validate",
            "validate standalone archive",
            lambda: run(
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
            ),
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

    def prove() -> None:
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

    component_step(emit, "prove", "prove standalone execution", prove)
    return {
        "archive": archive,
        "sidecar": sidecar,
        "executable": executable,
        "report": Artifact(report, "standalone report"),
    }


def validate_standalone_archive(
    source: Source, standalone: dict[str, Artifact], work: Path
) -> Path:
    try:
        from validate_standalone_package import validate
    except ModuleNotFoundError:
        from tools.validate_standalone_package import validate
    return validate(
        standalone["archive"].path,
        standalone["sidecar"].path,
        source.repo,
        standalone["report"].path,
        None,
        work / "standalone-validated",
    )


def prove_standalone(validated_root: Path, work: Path) -> None:
    validated = validated_root / "devlegate"
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(work / "smoke-home"),
        "XDG_CONFIG_HOME": str(work / "smoke-config"),
        "XDG_STATE_HOME": str(work / "smoke-state"),
        "XDG_CACHE_HOME": str(work / "smoke-cache"),
        "PEX_ROOT": str(work / "smoke-pex-root"),
    }
    result = subprocess.run(
        [str(validated), "version"],
        cwd=work,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise DistributionError(
            f"standalone isolated execution proof failed:\n{result.stderr}"
        )


def build_deb(
    source: Source,
    standalone: dict[str, Artifact],
    work: Path,
    reporter: ProgressReporter | None = None,
    emit: Callable[[ComponentEvent], None] | None = None,
) -> Artifact:
    require_tools(("dpkg-deb",))
    try:
        from package_deb import package as deb_package
        from validate_deb import validate as validate_deb
    except ModuleNotFoundError:
        from tools.package_deb import package as deb_package
        from tools.validate_deb import validate as validate_deb
    package = Artifact(
        deb_package(
            repo=source.repo,
            archive=standalone["archive"].path,
            sidecar=standalone["sidecar"].path,
            build_report=standalone["report"].path,
            output_dir=work / "deb",
            emit=emit,
        ),
        "Debian package",
    )
    validated = work / "deb-validated"
    binary = component_step(
        emit,
        "validate-deb",
        "validate Debian package",
        lambda: validate_deb(package.path, standalone["report"].path, validated),
    )

    def prove() -> None:
        result = subprocess.run(
            [str(binary), "version"],
            cwd=work,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise DistributionError(
                f"Debian extracted-binary proof failed:\n{result.stderr}"
            )

    component_step(emit, "prove-deb", "prove Debian extracted binary", prove)
    return package


def build_full_source(
    source: Source,
    work: Path,
    reporter: ProgressReporter | None = None,
    emit: Callable[[ComponentEvent], None] | None = None,
) -> dict[str, Artifact]:
    output = work / "full-source"
    output.mkdir()
    release_ref = f"v{source.version}"
    component_step(
        emit,
        "build",
        "build full-source archive",
        lambda: run(
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
        ),
    )
    archive = artifact_in(
        output, "devlegate-*-full-source.tar.gz", "full-source archive"
    )
    sidecar = Artifact(
        archive.path.with_name(f"{archive.path.name}.sha256"), "full-source checksum"
    )
    component_step(
        emit,
        "validate",
        "validate full-source archive",
        lambda: run(
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
        ),
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


def _owned_component_plan(target: str) -> ComponentPlan:
    """Load the plan from the module that owns the target's work."""
    if target == "standalone":
        try:
            from build_standalone import component_plan as plan
        except ModuleNotFoundError:
            from tools.build_standalone import component_plan as plan
    elif target == "deb":
        try:
            from package_deb import component_plan as plan
        except ModuleNotFoundError:
            from tools.package_deb import component_plan as plan
    else:
        plans = {
            "wheel": ("build wheel", "validate wheel"),
            "sdist": ("build source distribution", "validate source distribution"),
            "full-source": (
                "build full-source archive",
                "validate full-source archive",
            ),
        }
        return ComponentPlan(
            target,
            tuple(
                ComponentPlan.leaf(ComponentStep(name, key=key))
                for key, name in plans[target]
            ),
            key=target,
        )
    return ComponentPlan(plan().name, plan().children, key=target)


def _leaf_plan(key: str, name: str) -> ComponentPlan:
    return ComponentPlan.leaf(ComponentStep(name, key=key))


def _target_plan(target: str, *, include_proof: bool = False) -> ComponentPlan:
    """Compose the exact executable tree for one distribution target."""
    if target == "standalone":
        owned = _owned_component_plan(target)
        return ComponentPlan(
            target,
            (ComponentPlan(owned.name, owned.children, key="build"),
             _leaf_plan("package", "package standalone archive"))
            + (
                _leaf_plan("validate", "validate standalone archive"),
                _leaf_plan("prove", "prove standalone execution"),
            ),
            key=target,
        )
    if target == "deb":
        owned = _owned_component_plan(target)
        return ComponentPlan(
            target,
            (ComponentPlan(owned.name, owned.children, key="package"),
             _leaf_plan("validate-deb", "validate Debian package"),
             _leaf_plan("prove-deb", "prove Debian extracted binary")),
            key=target,
        )
    plans = {
        "wheel": (
            ("build", "build wheel"),
            ("validate", "validate wheel"),
        ),
        "sdist": (
            ("build", "build source distribution"),
            ("validate", "validate source distribution"),
        ),
        "full-source": (
            ("build", "build full-source archive"),
            ("validate", "validate full-source archive"),
        ),
    }
    children = tuple(_leaf_plan(key, name) for key, name in plans[target])
    if include_proof and target in {"wheel", "sdist"}:
        children += (_leaf_plan("prove", f"prove Python {target} installation"),)
    return ComponentPlan(
        target,
        children,
        key=target,
    )


def semantic_plan(targets: tuple[str, ...]) -> tuple[SemanticStep, ...]:
    """Return the complete, deterministic plan for the selected graph."""
    return tuple(
        SemanticStep(leaf_id.split("/", 1)[0], step.name, leaf_id)
        for leaf_id, step in freeze_plan(component_tree(targets))
    )


def component_tree(targets: tuple[str, ...]) -> ComponentPlan:
    """Return the selected graph as one deterministic component tree."""
    direct = set(targets)
    return ComponentPlan(
        "distribution",
        tuple(
            _target_plan(
                node,
                include_proof=node in direct or "all" in direct,
            )
            for node in dependency_order(targets)
        ),
    )


def _event_leaf(
    plan: ComponentPlan,
    emit: Callable[[ComponentEvent], None],
    action: Callable[[], object],
) -> object:
    step = plan.step
    assert step is not None
    emit(ComponentEvent("start", step, step.identity))
    try:
        result = action()
    except Exception:
        emit(ComponentEvent("fail", step, step.identity))
        raise
    emit(ComponentEvent("complete", step, step.identity))
    return result


def _component_for_target(
    target: str,
    source: Source,
    work: Path,
    values: dict[str, object],
    *,
    include_proof: bool = False,
) -> Component:
    """Build the executable component tree without repeating its stage list."""
    plan = _target_plan(target, include_proof=include_proof)

    def leaf(
        key: str, action: Callable[[], object], parent: ComponentPlan = plan
    ) -> FunctionComponent:
        child_plan = next(child for child in parent.children if child.key == key)
        return FunctionComponent(
            child_plan, lambda emit: _event_leaf(child_plan, emit, action)
        )

    if target == "wheel":

        def artifact() -> Artifact:
            return build_python_artifact(source, work, "wheel")

        values["wheel"] = None
        children = (
            leaf(
                "build",
                lambda: values.__setitem__("wheel", artifact()) or values["wheel"],
            ),
            leaf("validate", lambda: validate_wheel(values["wheel"], source)),
        )
        if include_proof:
            children += (
                leaf(
                    "prove",
                    lambda: prove_python_install(values["wheel"], source, work),
                ),
            )
    elif target == "sdist":

        def artifact() -> Artifact:
            return build_python_artifact(source, work, "sdist")

        values["sdist"] = None
        children = (
            leaf(
                "build",
                lambda: values.__setitem__("sdist", artifact()) or values["sdist"],
            ),
            leaf("validate", lambda: validate_sdist(values["sdist"], source)),
        )
        if include_proof:
            children += (
                leaf(
                    "prove",
                    lambda: prove_python_install(values["sdist"], source, work),
                ),
            )
    elif target == "full-source":
        return FunctionComponent(
            plan,
            lambda emit: values.__setitem__(
                "full-source", build_full_source(source, work, emit=emit)
            )
            or values["full-source"],
        )
    elif target == "standalone":
        try:
            from package_standalone import package as standalone_package
        except ModuleNotFoundError:
            from tools.package_standalone import package as standalone_package
        try:
            from build_standalone import build as standalone_build
        except ModuleNotFoundError:
            from tools.build_standalone import build as standalone_build
        build_plan = next(child for child in plan.children if child.key == "build")
        def build_action(emit: Callable[[ComponentEvent], None]) -> object:
            import argparse

            output = work / "standalone-build"
            output.mkdir()
            standalone_build(
                argparse.Namespace(
                    repo=source.repo,
                    output=output,
                    python=source.python,
                    wheel=values["wheel"].path,
                ),
                emit=emit,
            )
            executable = artifact_in(
                output, "devlegate-*-linux-x86_64", "standalone executable"
            )
            report = output / "standalone-build.json"
            values["standalone-executable"] = executable
            values["standalone-report"] = Artifact(report, "standalone report")
            return values["standalone-report"]

        def package_action() -> object:
            package_output = work / "standalone-package"
            archive, sidecar = standalone_package(
                source.repo,
                values["standalone-executable"].path,
                values["standalone-report"].path,
                package_output,
            )
            values["standalone"] = {
                "archive": Artifact(archive, "standalone archive"),
                "sidecar": Artifact(sidecar, "standalone checksum"),
                "executable": values["standalone-executable"],
                "report": values["standalone-report"],
            }
            return values["standalone"]

        children = (
            FunctionComponent(
                build_plan, build_action
            ),
            leaf("package", package_action),
            leaf(
                "validate",
                lambda: values.__setitem__(
                    "standalone-root",
                    validate_standalone_archive(source, values["standalone"], work),
                )
                or values["standalone-root"],
            ),
            leaf(
                "prove",
                lambda: prove_standalone(values["standalone-root"], work),
            ),
        )
    elif target == "deb":
        try:
            from package_deb import package as deb_package
            from validate_deb import validate as validate_deb
        except ModuleNotFoundError:
            from tools.package_deb import package as deb_package
            from tools.validate_deb import validate as validate_deb
        package_component_plan = next(
            child for child in plan.children if child.key == "package"
        )

        def package_action(emit: Callable[[ComponentEvent], None]) -> object:
            values["deb"] = Artifact(
                deb_package(
                    repo=source.repo,
                    archive=values["standalone"]["archive"].path,
                    sidecar=values["standalone"]["sidecar"].path,
                    build_report=values["standalone"]["report"].path,
                    output_dir=work / "deb",
                    emit=emit,
                ),
                "Debian package",
            )
            return values["deb"]

        def validate_action() -> object:
            values["deb-binary"] = validate_deb(
                values["deb"].path,
                values["standalone"]["report"].path,
                work / "deb-validated",
            )
            return values["deb-binary"]

        def prove_action() -> None:
            result = subprocess.run(
                [str(values["deb-binary"]), "version"],
                cwd=work,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise DistributionError(
                    f"Debian extracted-binary proof failed:\n{result.stderr}"
                )

        children = (
            FunctionComponent(package_component_plan, package_action),
            leaf("validate-deb", validate_action),
            leaf("prove-deb", prove_action),
        )
    else:
        raise DistributionError(f"unsupported component target: {target}")
    return TreeComponent(plan, tuple(children))


def _step(
    reporter: ProgressReporter | None,
    target: str,
    name: str,
    action: Callable[[], object],
) -> object:
    if reporter is None:
        return action()
    return reporter.step(SemanticStep(target, name), action)


def component_emitter(
    reporter: ProgressReporter, target: str, component_steps: tuple[ComponentStep, ...]
) -> Callable[[ComponentEvent], None]:
    """Bind component-owned events to the already frozen global leaf IDs."""
    bindings = {
        step.identity: SemanticStep(target, step.name, f"{target}/{step.identity}")
        for step in component_steps
    }

    def emit(event: ComponentEvent) -> None:
        try:
            semantic_step = bindings[event.leaf_id or event.step.identity]
        except KeyError as error:
            raise DistributionError(
                "component emitted an unknown leaf: "
                f"{event.leaf_id or event.step.identity}"
            ) from error
        reporter.emit(ProgressEvent(event.action, semantic_step))

    return emit


def component_step(
    emit: Callable[[ComponentEvent], None] | None,
    key: str,
    name: str,
    action: Callable[[], object],
) -> object:
    """Run one component-owned leaf and emit its exact local identity."""
    step = ComponentStep(name, key=key)
    if emit is None:
        return action()
    emit(ComponentEvent("start", step, step.identity))
    try:
        result = action()
    except Exception:
        emit(ComponentEvent("fail", step, step.identity))
        raise
    emit(ComponentEvent("complete", step, step.identity))
    return result


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
    frozen = freeze_plan(component_tree(selected))
    plan_steps = tuple(
        SemanticStep(leaf_id.split("/", 1)[0], step.name, leaf_id)
        for leaf_id, step in frozen
    )
    reporter = ProgressReporter(plan_steps)
    workspace_path = Path(tempfile.mkdtemp(prefix="devlegate-distribution-"))
    print(f"Source: {source.commit}")
    print(f"Version: {source.version}")
    values: dict[str, object] = {}
    try:
        direct_targets = set(selected)
        for node in order:
            _component_for_target(
                node,
                source,
                workspace_path,
                values,
                include_proof=node in direct_targets or "all" in direct_targets,
            ).run(
                lambda event, node=node: reporter.emit(
                    ProgressEvent(
                        event.action,
                        SemanticStep(
                            node,
                            event.step.name,
                            f"{node}/{event.leaf_id or event.step.identity}",
                        ),
                    )
                )
            )
        final_files = publish(
            selected_final_files(args.target, values),
            output_directory(repo, args.output_dir),
        )
        print("\nDevlegate distributions ready")
        for path in final_files:
            print(f"{path}: {path.stat().st_size} bytes {digest(path)}")
        print(
            f"Progress complete: {reporter.completed}/"
            f"{len(reporter.steps)} semantic steps"
        )
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
