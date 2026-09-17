import hashlib
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "tools" / "build_full_source.py"


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir()
    git(path.parent, "init", "-b", "main", path)
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.com")


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def build(repo: Path, version: str, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "python3",
            str(BUILDER),
            "--repo",
            str(repo),
            "--ref",
            "HEAD",
            "--version",
            version,
            "--output-dir",
            str(output),
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def validate(
    archive: Path, repo: Path, version: str, extract_dir: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "python3",
            str(ROOT / "tools" / "validate_full_source.py"),
            "--archive",
            str(archive),
            "--sidecar",
            str(archive.with_name(f"{archive.name}.sha256")),
            "--repo",
            str(repo),
            "--ref",
            "HEAD",
            "--version",
            version,
            "--extract-dir",
            str(extract_dir),
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def archive_files(archive: Path) -> list[str]:
    with tarfile.open(archive, "r:gz") as tar:
        return tar.getnames()


def test_submodule_archive_is_reproducible_and_self_contained(tmp_path: Path) -> None:
    nano = tmp_path / "nano"
    init_repo(nano)
    (nano / "__init__.py").write_text("VALUE = 'pinned'\n")
    nano_commit = commit_all(nano, "nano")

    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("fixture\n")
    git(
        project,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(nano),
        "src/nanoyaml",
    )
    project_commit = commit_all(project, "project")

    first = tmp_path / "first"
    second = tmp_path / "second"
    assert build(project, "v1.2.3", first).returncode == 0
    assert build(project, "v1.2.3", second).returncode == 0
    archive = first / "devlegate-1.2.3-full-source.tar.gz"
    archive_again = second / archive.name
    assert hashlib.sha256(archive.read_bytes()).digest() == hashlib.sha256(
        archive_again.read_bytes()
    ).digest()
    assert archive_files(archive)[0] == "devlegate-1.2.3"
    names = archive_files(archive)
    assert "devlegate-1.2.3/src/nanoyaml/__init__.py" in names
    assert all(".git" not in PurePosixPath(name).parts for name in names)

    with tarfile.open(archive, "r:gz") as tar:
        manifest = tar.extractfile("devlegate-1.2.3/SOURCE-MANIFEST").read().decode()
    assert "release: v1.2.3" in manifest
    assert f"devlegate-commit: {project_commit}" in manifest
    assert f"    commit: {nano_commit}" in manifest
    assert validate(archive, project, "v1.2.3", tmp_path / "extracted").returncode == 0
    assert (first / "devlegate-1.2.3-full-source.tar.gz.sha256").is_file()
    assert (first / f"{archive.name}.sha256").read_text() == (
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )


def test_vendored_tree_is_packaged_without_submodule_manifest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "src/nanoyaml").mkdir(parents=True)
    (project / "src/nanoyaml/__init__.py").write_text("VALUE = 'vendored'\n")
    commit_all(project, "vendored")
    output = tmp_path / "output"
    assert build(project, "v2.0.0", output).returncode == 0
    archive = output / "devlegate-2.0.0-full-source.tar.gz"
    with tarfile.open(archive, "r:gz") as tar:
        manifest = tar.extractfile("devlegate-2.0.0/SOURCE-MANIFEST").read().decode()
        assert "  - none\n" in manifest
        assert tar.extractfile(
            "devlegate-2.0.0/src/nanoyaml/__init__.py"
        ).read() == b"VALUE = 'vendored'\n"


def test_arbitrary_submodule_is_validated_without_nanoyaml_assumption(
    tmp_path: Path,
) -> None:
    dependency = tmp_path / "dependency"
    init_repo(dependency)
    (dependency / "module.py").write_text("VALUE = 'dependency'\n")
    dependency_commit = commit_all(dependency, "dependency")

    project = tmp_path / "project"
    init_repo(project)
    git(
        project,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(dependency),
        "vendor/otherlib",
    )
    commit_all(project, "other submodule")
    output = tmp_path / "output"
    assert build(project, "v3.0.0", output).returncode == 0
    archive = output / "devlegate-3.0.0-full-source.tar.gz"
    result = validate(archive, project, "v3.0.0", tmp_path / "extracted")
    assert result.returncode == 0, result.stderr
    manifest = (tmp_path / "extracted/devlegate-3.0.0/SOURCE-MANIFEST").read_text()
    assert "path: vendor/otherlib" in manifest
    assert f"    commit: {dependency_commit}" in manifest


def test_unavailable_gitlink_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / ".gitmodules").write_text(
        '[submodule "nanoyaml"]\n\tpath = src/nanoyaml\n\turl = /does/not/exist\n'
    )
    git(project, "add", ".gitmodules")
    fake_sha = "1" * 40
    git(
        project,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{fake_sha},src/nanoyaml",
    )
    git(project, "commit", "-m", "broken gitlink")
    result = build(project, "v1.0.0", tmp_path / "output")
    assert result.returncode != 0
    assert "cannot initialize source submodules" in result.stderr


def test_invalid_version_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_repo(project)
    (project / "README.md").write_text("fixture\n")
    commit_all(project, "project")
    result = build(project, "release-1", tmp_path / "output")
    assert result.returncode != 0
    assert "version must match vX.Y.Z" in result.stderr
