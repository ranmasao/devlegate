# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_distribution", Path(__file__).parents[1] / "tools/build_distribution.py"
)
GRAPH = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = GRAPH
SPEC.loader.exec_module(GRAPH)


def test_target_expansion_and_dependency_order():
    assert GRAPH.expand_targets("python") == ("wheel", "sdist")
    assert GRAPH.dependency_order(GRAPH.expand_targets("all")) == (
        "wheel",
        "sdist",
        "standalone",
        "deb",
        "full-source",
    )


def test_semantic_plan_is_frozen_and_in_dependency_order():
    plan = GRAPH.semantic_plan(("deb",))
    assert [step.target for step in plan] == ["wheel"] * 2 + ["standalone"] * 11 + [
        "deb"
    ] * 7
    assert plan[0].name == "build wheel"
    assert plan[-1].name == "prove Debian extracted binary"


def test_direct_wheel_includes_install_proof_but_dependency_wheel_does_not():
    direct = GRAPH.semantic_plan(("wheel",))
    dependency = GRAPH.semantic_plan(("deb",))
    assert direct[-1].name == "prove Python wheel installation"
    assert all(step.name != "prove Python wheel installation" for step in dependency)


def test_progress_reports_named_completion_skip_and_failure(capsys):
    first = GRAPH.SemanticStep("demo", "first")
    second = GRAPH.SemanticStep("demo", "second")
    reporter = GRAPH.ProgressReporter((first, second))
    reporter.emit(GRAPH.ProgressEvent("start", first))
    reporter.emit(GRAPH.ProgressEvent("complete", first))
    reporter.emit(GRAPH.ProgressEvent("skip", second))
    output = capsys.readouterr().out
    assert "DONE demo: first" in output
    assert "SKIP demo: second" in output
    assert reporter.completed == 2

    failing = GRAPH.ProgressReporter((first,))
    failing.emit(GRAPH.ProgressEvent("start", first))
    failing.emit(GRAPH.ProgressEvent("fail", first))
    assert failing.completed == 0
    assert "FAILED demo: first" in capsys.readouterr().out
    assert GRAPH.dependency_order(("deb", "standalone")) == (
        "wheel",
        "standalone",
        "deb",
    )


def test_component_tree_freezes_nested_ids_and_rejects_future_failure():
    first = GRAPH.ComponentStep("same label", key="first")
    second = GRAPH.ComponentStep("same label", key="second")
    tree = GRAPH.ComponentPlan(
        "root",
        (
            GRAPH.ComponentPlan("left", (GRAPH.ComponentPlan.leaf(first),), key="left"),
            GRAPH.ComponentPlan(
                "right", (GRAPH.ComponentPlan.leaf(second),), key="right"
            ),
        ),
    )
    frozen = GRAPH.freeze_plan(tree)
    assert [leaf_id for leaf_id, _step in frozen] == [
        "left/first",
        "right/second",
    ]

    steps = tuple(
        GRAPH.SemanticStep("root", step.name, leaf_id) for leaf_id, step in frozen
    )
    reporter = GRAPH.ProgressReporter(steps)
    reporter.emit(GRAPH.ProgressEvent("start", steps[0]))
    with pytest.raises(GRAPH.DistributionError, match="not started"):
        reporter.emit(GRAPH.ProgressEvent("fail", steps[1]))


def test_nested_component_execution_emits_frozen_hierarchical_ids():
    first = GRAPH.ComponentStep("same label", key="first")
    second = GRAPH.ComponentStep("same label", key="second")
    left = GRAPH.ComponentPlan("left", (GRAPH.ComponentPlan.leaf(first),), key="left")
    right = GRAPH.ComponentPlan(
        "right", (GRAPH.ComponentPlan.leaf(second),), key="right"
    )
    root = GRAPH.ComponentPlan("root", (left, right))

    def leaf(step):
        return GRAPH.FunctionComponent(
            GRAPH.ComponentPlan.leaf(step),
            lambda emit: (
                emit(GRAPH.ComponentEvent("start", step, step.identity)),
                emit(GRAPH.ComponentEvent("complete", step, step.identity)),
            ),
        )

    component = GRAPH.TreeComponent(
        root,
        (
            GRAPH.TreeComponent(left, (leaf(first),)),
            GRAPH.TreeComponent(right, (leaf(second),)),
        ),
    )
    frozen = GRAPH.freeze_plan(root)
    steps = tuple(
        GRAPH.SemanticStep("root", step.name, leaf_id)
        for leaf_id, step in frozen
    )
    reporter = GRAPH.ProgressReporter(steps)
    component.run(
        lambda event: reporter.emit(
            GRAPH.ProgressEvent(
                event.action,
                GRAPH.SemanticStep("root", event.step.name, event.leaf_id),
            )
        )
    )
    assert reporter.completed == len(steps)


def test_real_standalone_adapter_emits_frozen_internal_steps(monkeypatch, tmp_path):
    import tools.build_standalone as standalone
    import tools.package_standalone as package_standalone

    source = GRAPH.Source(tmp_path, "a" * 40, "1.2.3", sys.executable)
    wheel = tmp_path / "wheel.whl"
    wheel.write_bytes(b"wheel")
    values = {"wheel": GRAPH.Artifact(wheel, "wheel")}
    executable = tmp_path / "standalone-build" / "devlegate-1.2.3-linux-x86_64"
    report = executable.parent / "standalone-build.json"

    def fake_build(args, emit=None):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"executable")
        report.write_text(json.dumps({"source_commit": source.commit}))
        for step in standalone.semantic_plan():
            if step.identity in {"wheel-a", "wheel-b"}:
                emit(GRAPH.ComponentEvent("skip", step, step.identity))
            else:
                emit(GRAPH.ComponentEvent("start", step, step.identity))
                emit(GRAPH.ComponentEvent("complete", step, step.identity))

    archive = tmp_path / "standalone.tar.gz"
    sidecar = tmp_path / "standalone.sha256"

    def fake_package(*_args, **_kwargs):
        archive.write_bytes(b"archive")
        sidecar.write_text("checksum")
        return archive, sidecar

    monkeypatch.setattr(standalone, "build", fake_build)
    monkeypatch.setattr(package_standalone, "package", fake_package)
    monkeypatch.setattr(GRAPH, "validate_standalone_archive", lambda *_args: tmp_path)
    monkeypatch.setattr(GRAPH, "prove_standalone", lambda *_args: None)

    component = GRAPH._component_for_target(
        "standalone", source, tmp_path, values
    )
    frozen = GRAPH.freeze_plan(GRAPH._target_plan("standalone"))
    events = []
    component.run(events.append)

    assert [event.leaf_id for event in events if event.action == "complete"] == [
        leaf_id for leaf_id, _step in frozen if "wheel" not in leaf_id
    ]
    assert {event.leaf_id for event in events if event.action == "skip"} == {
        "build/wheel-a",
        "build/wheel-b",
    }


def test_real_debian_adapter_emits_package_and_post_build_steps(monkeypatch, tmp_path):
    import tools.package_deb as package_deb
    import tools.validate_deb as validate_deb

    source = GRAPH.Source(tmp_path, "a" * 40, "1.2.3", sys.executable)
    archive = tmp_path / "standalone.tar.gz"
    sidecar = tmp_path / "standalone.sha256"
    report = tmp_path / "standalone-build.json"
    archive.write_bytes(b"archive")
    sidecar.write_text("checksum")
    report.write_text(json.dumps({"wheel": {"version": "1.2.3"}}))
    deb = tmp_path / "devlegate_1.2.3_amd64.deb"
    values = {
        "standalone": {
            "archive": GRAPH.Artifact(archive, "archive"),
            "sidecar": GRAPH.Artifact(sidecar, "checksum"),
            "report": GRAPH.Artifact(report, "report"),
        }
    }

    def fake_package(*, emit=None, **_kwargs):
        deb.write_bytes(b"deb")
        for step in package_deb.semantic_plan():
            emit(GRAPH.ComponentEvent("start", step, step.identity))
            emit(GRAPH.ComponentEvent("complete", step, step.identity))
        return deb

    binary = tmp_path / "devlegate"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(package_deb, "package", fake_package)
    monkeypatch.setattr(validate_deb, "validate", lambda *_args: binary)
    monkeypatch.setattr(
        GRAPH.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )

    component = GRAPH._component_for_target("deb", source, tmp_path, values)
    frozen = GRAPH.freeze_plan(GRAPH._target_plan("deb"))
    events = []
    component.run(events.append)

    assert [event.leaf_id for event in events if event.action == "complete"] == [
        leaf_id for leaf_id, _step in frozen
    ]
    assert events[-1].leaf_id == "prove-deb"


def test_real_component_progress_output_is_deterministic_and_noninteractive(
    monkeypatch, tmp_path, capsys
):
    import tools.build_standalone as standalone
    import tools.package_standalone as package_standalone

    source = GRAPH.Source(tmp_path, "a" * 40, "1.2.3", sys.executable)
    wheel = tmp_path / "wheel.whl"
    wheel.write_bytes(b"wheel")
    values = {"wheel": GRAPH.Artifact(wheel, "wheel")}

    def fake_build(args, emit=None):
        output = args.output
        output.mkdir(parents=True, exist_ok=True)
        (output / "devlegate-1.2.3-linux-x86_64").write_bytes(b"x")
        (output / "standalone-build.json").write_text("{}")
        for step in standalone.semantic_plan():
            if step.identity in {"wheel-a", "wheel-b"}:
                emit(GRAPH.ComponentEvent("skip", step, step.identity))
            else:
                emit(GRAPH.ComponentEvent("start", step, step.identity))
                emit(GRAPH.ComponentEvent("complete", step, step.identity))

    archive = tmp_path / "archive"
    sidecar = tmp_path / "sidecar"
    monkeypatch.setattr(standalone, "build", fake_build)
    monkeypatch.setattr(
        package_standalone,
        "package",
        lambda *_args, **_kwargs: (archive, sidecar),
    )
    archive.write_bytes(b"archive")
    sidecar.write_text("sidecar")
    monkeypatch.setattr(GRAPH, "validate_standalone_archive", lambda *_args: tmp_path)
    monkeypatch.setattr(GRAPH, "prove_standalone", lambda *_args: None)

    component = GRAPH._component_for_target("standalone", source, tmp_path, values)
    frozen = GRAPH.freeze_plan(GRAPH._target_plan("standalone"))
    steps = tuple(
        GRAPH.SemanticStep("standalone", step.name, leaf_id)
        for leaf_id, step in frozen
    )
    reporter = GRAPH.ProgressReporter(steps)
    component.run(
        lambda event: reporter.emit(
            GRAPH.ProgressEvent(
                event.action,
                GRAPH.SemanticStep("standalone", event.step.name, event.leaf_id),
            )
        )
    )
    output = capsys.readouterr().out
    assert reporter.completed == len(steps)
    assert "Progress complete" not in output
    assert "[0/" in output and f"[{len(steps)}/{len(steps)}]" in output
    assert "\x1b[" not in output


def test_target_parser_rejects_pip():
    with pytest.raises(SystemExit):
        GRAPH.parser().parse_args(["pip"])


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "dev: package: a command is required"),
        (["banana"], "dev: package: unknown command banana"),
    ],
)
def test_package_parser_errors_are_concise(argv, message, capsys):
    with pytest.raises(SystemExit) as error:
        GRAPH.parser().parse_args(argv)
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert message in stderr
    assert "Try './dev package --help' for usage." in stderr
    assert "invalid choice" not in stderr


def test_package_help_is_subcommand_oriented():
    help_text = GRAPH.parser().format_help()
    for target in GRAPH.TARGETS:
        assert target in help_text
    assert "{wheel,sdist" not in help_text
    assert "--repo" in help_text
    assert "--output-dir" in help_text
    assert "--keep-work" in help_text
    assert "--python" not in help_text


def test_package_help_has_target_options(capsys):
    parser = GRAPH.parser()
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["deb", "--help"])
    assert error.value.code == 0
    assert "Build and validate the Debian package." in capsys.readouterr().out


def test_default_repo_and_output_dir_are_repository_relative(tmp_path):
    parser = GRAPH.parser()
    args = parser.parse_args(["wheel"])
    expected = Path(GRAPH.__file__).parents[1].resolve()
    assert args.repo == expected
    assert GRAPH.output_directory(args.repo, args.output_dir) == expected / "dist"

    other = tmp_path / "other"
    other.mkdir()
    args = parser.parse_args(["wheel", "--repo", str(other)])
    assert GRAPH.output_directory(args.repo, args.output_dir) == other / "dist"
    explicit = tmp_path / "artifacts"
    args = parser.parse_args(
        ["wheel", "--repo", str(other), "--output-dir", str(explicit)]
    )
    assert GRAPH.output_directory(args.repo, args.output_dir) == explicit.resolve()


def test_keep_work_preserves_failed_workspace(monkeypatch, capsys):
    source = GRAPH.Source(Path("/repo"), "a" * 40, "1.2.3", "python")
    monkeypatch.setattr(GRAPH, "source_identity", lambda *_args: source)
    monkeypatch.setattr(GRAPH, "require_tools", lambda _names: None)

    def fail(*_args):
        raise GRAPH.DistributionError("failed stage")

    monkeypatch.setattr(GRAPH, "build_python_artifact", fail)
    args = GRAPH.parser().parse_args(["wheel", "--keep-work"])
    with pytest.raises(GRAPH.DistributionError):
        GRAPH.package(args)

    output = capsys.readouterr().out
    kept = Path(output.split("Temporary workspace kept: ", 1)[1].strip())
    assert kept.is_dir()
    import shutil

    shutil.rmtree(kept)


def test_failed_workspace_is_removed_without_keep(monkeypatch, tmp_path):
    source = GRAPH.Source(Path("/repo"), "a" * 40, "1.2.3", "python")
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(GRAPH, "source_identity", lambda *_args: source)
    monkeypatch.setattr(GRAPH, "require_tools", lambda _names: None)
    monkeypatch.setattr(GRAPH.tempfile, "mkdtemp", lambda **_kwargs: str(workspace))
    monkeypatch.setattr(
        GRAPH,
        "build_python_artifact",
        lambda *_args: (_ for _ in ()).throw(GRAPH.DistributionError("failed stage")),
    )
    args = GRAPH.parser().parse_args(["wheel"])
    with pytest.raises(GRAPH.DistributionError):
        GRAPH.package(args)
    assert not workspace.exists()


def test_graph_cycle_detection(monkeypatch):
    monkeypatch.setitem(GRAPH.GRAPH, "wheel", ("standalone",))
    with pytest.raises(GRAPH.DistributionError, match="cycle"):
        GRAPH.dependency_order(("wheel",))


def test_missing_target_specific_tools(monkeypatch):
    monkeypatch.setattr(GRAPH.shutil, "which", lambda name: None)
    with pytest.raises(GRAPH.DistributionError, match="dpkg-deb"):
        GRAPH.require_tools(("dpkg-deb",))


def test_artifact_discovery_uses_actual_filename(tmp_path):
    (tmp_path / "devlegate-9.8.7-py3-none-any.whl").write_bytes(b"wheel")
    artifact = GRAPH.artifact_in(tmp_path, "devlegate-*.whl", "wheel")
    assert artifact.path.name == "devlegate-9.8.7-py3-none-any.whl"


def test_publish_replaces_only_after_candidate_is_ready(tmp_path):
    source = tmp_path / "candidate.whl"
    source.write_bytes(b"new")
    output = tmp_path / "dist"
    existing = output / source.name
    output.mkdir()
    existing.write_bytes(b"old")

    published = GRAPH.publish([source], output)

    assert published == [existing]
    assert existing.read_bytes() == b"new"
    assert not list(output.glob("*.pending-*"))


def test_run_reports_failed_stage(monkeypatch):
    def fail(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, stdout="stdout", stderr="stderr")

    monkeypatch.setattr(GRAPH.subprocess, "run", fail)
    with pytest.raises(GRAPH.DistributionError, match="stderr"):
        GRAPH.run(["failing-stage"])
