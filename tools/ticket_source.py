# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Developer-only, read-only ticket resolution and private build capture.

This module deliberately does not construct the runtime engine or open SQLite.
Resource observations confer no owner, retry, rewrite or integration authority.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from devlegate.execution_provenance import lifecycle_commit_is_exact
from devlegate.execution_result import ExecutionReport
from devlegate.execution_workspace import ExecutionWorkspaceManager
from devlegate.resource_binding import ResourceBinding, _read, _safe_path
from devlegate.runtime_locator import RuntimeLocator, read_env
from devlegate.tickets import is_canonical_ticket_name

try:
    from source_preflight import _gitlinks, validate_source_submodules
except ModuleNotFoundError:
    from tools.source_preflight import _gitlinks, validate_source_submodules


class TicketSourceError(ValueError):
    """Independent local evidence cannot establish one safe build source."""


def git(
    repo: Path, *args: str, check: bool = True,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
         "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false",
         "-c", "core.untrackedCache=false", "-c", "core.filemode=true",
         "-C", str(repo), *args],
        capture_output=True, text=True, check=False,
        env={**{key: value for key, value in os.environ.items()
                if not key.startswith("GIT_")},
             "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0",
             "GIT_NO_LAZY_FETCH": "1", **(environment or {})},
    )
    if check and result.returncode:
        raise TicketSourceError(
            f"Git observation failed ({args[0]}): {result.stderr.strip()}"
        )
    return result


def text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def ancestor(repo: Path, before: str, after: str) -> bool:
    result = git(repo, "merge-base", "--is-ancestor", before, after, check=False)
    if result.returncode not in {0, 1}:
        raise TicketSourceError("source lineage objects are unavailable")
    return result.returncode == 0


@dataclass(frozen=True)
class Selection:
    repo: Path
    ticket: str
    execution: str
    generation: str
    head: str
    live: Path | None
    binding: ResourceBinding | None = None


def project(invoking: Path) -> tuple[Path, dict[str, str], RuntimeLocator]:
    """Use Git's registered primary checkout, never a similar repository name."""
    current_common = text(
        Path.cwd(), "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    common = text(invoking, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if current_common != common:
        raise TicketSourceError(
            "--ticket must be invoked within this Devlegate project"
        )
    records = git(invoking, "worktree", "list", "--porcelain").stdout.split("\n\n")
    first = records[0].splitlines()[0]
    if not first.startswith("worktree "):
        raise TicketSourceError("cannot identify the project's primary checkout")
    repo = Path(first.removeprefix("worktree "))
    _safe_path(repo)
    config = read_env(repo / ".env")
    for key in ("CONTROL_BRANCH", "BACKLOG_PATH", "TODO_PATH", "REVIEW_PATH",
                "ACCEPTED_PATH", "DONE_PATH", "ABANDONED_PATH"):
        if key not in config and key in os.environ:
            config[key] = os.environ[key]
    return repo, config, RuntimeLocator.from_config(repo / ".env", repo, config)


def resolve(
    repo: Path, config: dict[str, str], locator: RuntimeLocator, ticket: str
) -> Selection:
    if not is_canonical_ticket_name(f"{ticket}.md"):
        raise TicketSourceError("invalid ticket ID")
    branch = config.get("CONTROL_BRANCH", "devlegate/control")
    control = text(repo, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    paths = {
        state: config.get(f"{state.upper()}_PATH", f"kanban/{state}")
        for state in ("backlog", "todo", "review", "accepted", "done", "abandoned")
    }
    matches = [
        path for path in paths.values()
        if git(repo, "cat-file", "-e", f"{control}:{path}/{ticket}.md",
               check=False).returncode == 0
    ]
    if len(matches) != 1:
        raise TicketSourceError(
            "ticket is missing or ambiguous in committed project control evidence"
        )
    root = locator.state_dir / "worktrees" / locator.state_key
    manager = ExecutionWorkspaceManager(repo, root, ticket, runner=git)
    bindings = []
    _safe_path(root / "bindings")
    if (root / "bindings").exists():
        for directory in (root / "bindings").iterdir():
            _safe_path(directory)
            if not directory.is_dir() or not (directory / "intent.json").is_file():
                raise TicketSourceError("uncertain project generation evidence")
    for path in (root / "bindings").glob("*/intent.json"):
        intent = _read(path)
        if intent.get("ticket_id") != ticket:
            continue
        binding = ResourceBinding(manager, intent)
        if path.parent != binding.directory:
            raise TicketSourceError("generation intent has a foreign storage identity")
        binding._validate_intent()
        binding._require_settled()
        binding.require_worker_retirement()
        receipt = _read(binding.directory / "receipt.json")
        common = Path(str(intent["common"]))
        metadata = common.stat()
        if (receipt.get("generation") != intent["generation"]
                or receipt.get("common_identity")
                != [metadata.st_dev, metadata.st_ino]):
            raise TicketSourceError("foreign generation creation receipt")
        if not ancestor(repo, str(intent["control"]), control):
            raise TicketSourceError("generation is foreign to control history")
        blob = text(
            repo, "rev-parse", f"{intent['control']}:{paths['todo']}/{ticket}.md"
        )
        if blob != intent["ticket_blob"]:
            raise TicketSourceError("generation ticket specification identity differs")
        bindings.append(binding)

    # Order by committed publication history, not timestamps, status or opaque IDs.
    report_paths = git(
        repo, "ls-tree", "-r", "--name-only", control, f"executions/{ticket}/"
    ).stdout.splitlines()
    reports = {}
    for path in report_paths:
        if not path.endswith(".json"):
            continue
        report = ExecutionReport.from_dict(
            json.loads(git(repo, "show", f"{control}:{path}").stdout)
        )
        if (path != f"executions/{ticket}/{report.execution_id}.json"
                or report.ticket_id != ticket):
            raise TicketSourceError("foreign execution report identity")
        reports[path] = report
    latest_report = None
    publication = None
    history = git(
        repo, "log", "--format=%H", control, "--", f"executions/{ticket}/"
    ).stdout.splitlines()
    publications = []
    for commit in history:
        changed = set(git(
            repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit
        ).stdout.splitlines())
        changed = {path for path in changed
                   if path.startswith(f"executions/{ticket}/")
                   and path.endswith(".json")}
        if not changed:
            continue
        publications.append((commit, changed))
    # A merge can contain independently published executions. Git log's date
    # order does not establish which one superseded the other.
    newest_publications = [item for item in publications if not any(
        item[0] != other[0] and ancestor(repo, item[0], other[0])
        for other in publications
    )]
    if len(newest_publications) > 1:
        raise TicketSourceError("ambiguous latest execution publication history")
    if newest_publications:
        commit, changed = newest_publications[0]
        if len(changed) != 1:
            raise TicketSourceError("ambiguous latest execution publication")
        path = next(iter(changed))
        if path not in reports:
            raise TicketSourceError("latest execution evidence was removed")
        latest_report = reports[path]
        publication = commit
        if (latest_report.execution_branch != manager.branch
                or latest_report.execution_path != str(manager.path)):
            raise TicketSourceError("latest execution has foreign resource ownership")
        if not lifecycle_commit_is_exact(
            repo, commit, latest_report, todo_path=paths["todo"],
            review_path=paths["review"], runner=git,
        ):
            raise TicketSourceError("latest execution lacks exact admission evidence")

    # A later generation must descend from the publication or explicitly name
    # its predecessor. Equal control heads alone never order two generations.
    def later(left: ResourceBinding, right: ResourceBinding) -> bool:
        if left.intent.get("predecessor_generation") == right.intent["generation"]:
            return True
        for name in ("handoff-intent.json", "rework-started.json"):
            path = left.directory / name
            if (path.exists()
                    and _read(path).get("predecessor") == right.intent["generation"]):
                return True
        a, b = str(right.intent["control"]), str(left.intent["control"])
        return a != b and ancestor(repo, a, b)

    newest = [item for item in bindings if not any(
        later(other, item) for other in bindings if other is not item
    )]
    if len(newest) > 1 or (bindings and not newest):
        raise TicketSourceError("ambiguous ticket generations")
    binding = newest[0] if newest else None
    if (latest_report and binding
            and latest_report.execution_id != binding.intent["execution_id"]):
        if publication is None or not ancestor(
            repo, publication, str(binding.intent["control"])
        ):
            raise TicketSourceError("latest execution and resource generation disagree")
    if binding:
        intent = binding.intent
        head = binding._expected_head()
        if not isinstance(head, str):
            raise TicketSourceError("latest ticket checkpoint is unproven")
        if latest_report and latest_report.execution_id == intent["execution_id"]:
            if (latest_report.resource_generation != intent["generation"]
                    or latest_report.workspace_head != head
                    or latest_report.code_base_head != intent["base"]
                    or latest_report.control_head != intent["control"]):
                raise TicketSourceError("report and generation checkpoint disagree")
        registration = manager._registrations()
        live = None
        if manager.path.exists() or manager.path.resolve() in registration:
            live = binding.require_local(clean=False, read_only=True).path
        else:
            if any(item.get("branch") == manager.branch
                   for item in registration.values()):
                raise TicketSourceError("execution ref has a foreign worktree")
            binding.require_ref(head)
        selection = Selection(
            repo, ticket, str(intent["execution_id"]), str(intent["generation"]),
            head, live, binding,
        )
        base = str(intent["base"])
    elif latest_report:
        if not latest_report.resource_generation or not latest_report.workspace_head:
            raise TicketSourceError("latest execution lacks checkpoint identity")
        if manager.path.exists() or any(item.get("branch") == manager.branch
                for item in manager._registrations().values()):
            raise TicketSourceError("live resources lack ownership evidence")
        if git(repo, "show-ref", "--verify", f"refs/heads/{manager.branch}",
               check=False).returncode == 0:
            raise TicketSourceError("ticket ref lacks independent ownership evidence")
        selection = Selection(
            repo, ticket, latest_report.execution_id, latest_report.resource_generation,
            latest_report.workspace_head, None,
        )
        base = latest_report.code_base_head
    else:
        raise TicketSourceError("no independently attributable ticket execution source")
    if not re.fullmatch(r"[0-9a-f]{40}", selection.head):
        raise TicketSourceError("invalid ticket checkpoint object identity")
    if (text(repo, "rev-parse", "--verify", f"{selection.head}^{{commit}}")
            != selection.head or not ancestor(repo, base, selection.head)):
        raise TicketSourceError("ticket checkpoint lineage is unproven")
    # Traverse the entire selected tree now; absent newer objects are never
    # grounds to try a previous checkpoint or product HEAD.
    objects = git(repo, "rev-list", "--objects", "--missing=print", selection.head)
    if any(line.startswith("?") for line in objects.stdout.splitlines()):
        raise TicketSourceError("latest checkpoint objects are unavailable locally")
    return selection


def file_bytes(root: Path, relative: str) -> tuple[bytes, int, tuple[int, ...]]:
    path = root / relative
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise TicketSourceError(f"not a regular source file: {relative}")
        content = stream.read()
        after = os.fstat(stream.fileno())
    def signature(value):
        return (value.st_dev, value.st_ino, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns, value.st_mode)
    if (signature(before) != signature(after)
            or signature(after) != signature(path.stat())):
        raise TicketSourceError(f"source changed during capture: {relative}")
    return content, 0o755 if before.st_mode & 0o111 else 0o644, signature(after)


def inventory(repo: Path) -> list[str]:
    values = git(repo, "ls-files", "-z", "--cached", "--others",
                 "--exclude-standard").stdout.split("\0")
    links = {str(path) for path, _head in _gitlinks(
        repo, text(repo, "rev-parse", "HEAD")
    )}
    result = []
    tracked_files = set(git(repo, "ls-files", "-z", "--cached").stdout.split("\0"))
    for value in sorted(set(values) - {""} - links):
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise TicketSourceError(f"unsafe source path: {value!r}")
        # Untracked operational state and environment secrets are never source.
        tracked = value in tracked_files
        private_parts = {".devlegate", ".venv", "__pycache__", ".pytest_cache",
                         ".ruff_cache", "build", "dist"}
        if not tracked and (
            private_parts.intersection(path.parts)
            or path.parts[0].startswith(".env")
            or path.suffix in {".sqlite", ".sqlite3", ".db", ".log", ".pyc"}
            or path.name.endswith(("-wal", "-shm", "-journal"))
        ):
            raise TicketSourceError(f"untracked private/runtime source path: {value}")
        if (repo / path).exists() or (repo / path).is_symlink():
            result.append(value)
    return result


def clone_at(origin: Path, target: Path, head: str) -> None:
    entries = git(origin, "ls-tree", "-r", "-z", head).stdout.split("\0")
    if any(entry.startswith("120000 ") for entry in entries):
        raise TicketSourceError("symlinks are unsupported in private package sources")
    git(origin, "clone", "--no-hardlinks", "--no-checkout", "--",
        str(origin), str(target))
    git(target, "-c", "core.hooksPath=/dev/null", "checkout", "--detach", head)


def prepare_submodules(origin: Path, target: Path, head: str, *, live: bool) -> None:
    for relative, pinned in _gitlinks(target, head):
        path = str(relative)
        _safe_path(target / path)
        entries = git(target, "config", "--file", str(target / ".gitmodules"),
                      "--get-regexp", r"^submodule\..*\.path$").stdout.splitlines()
        names = [line.split(" ", 1)[0][10:-5] for line in entries
                 if line.split(" ", 1)[1] == path]
        if (len(names) != 1 or Path(names[0]).is_absolute()
                or ".." in Path(names[0]).parts or ".git" in Path(names[0]).parts):
            raise TicketSourceError(f"ambiguous pinned submodule identity: {path}")
        if live:
            source = origin / path
            if git(source, "status", "--porcelain", "--untracked-files=all").stdout:
                raise TicketSourceError(f"dirty submodule cannot be captured: {path}")
        else:
            # Submodule names need not equal their paths. Consult selected bytes.
            source = Path(text(origin, "rev-parse", "--path-format=absolute",
                               "--git-path", f"modules/{names[0]}"))
        _safe_path(source)
        if not source.exists():
            raise TicketSourceError(f"pinned submodule objects unavailable: {path}")
        destination = target / path
        destination.rmdir()
        clone_at(source, destination, pinned)
        git(target, "config", f"submodule.{names[0]}.url", str(destination))
        git(target, "config", f"submodule.{names[0]}.active", "true")
        prepare_submodules(source, destination, pinned, live=live)


def validate_index_metadata(live: Path, head: str) -> None:
    """Refuse staged metadata that a filesystem capture would silently omit."""
    committed = {}
    for entry in git(live, "ls-tree", "-r", "-z", head).stdout.split("\0"):
        if entry:
            metadata, path = entry.split("\t", 1)
            mode, _kind, object_id = metadata.split()
            committed[path] = (mode, object_id)
    staged = {}
    for entry in git(live, "ls-files", "--stage", "-z").stdout.split("\0"):
        if entry:
            metadata, path = entry.split("\t", 1)
            mode, object_id, _stage = metadata.split()
            staged[path] = (mode, object_id)
    committed_links = {path: value for path, value in committed.items()
                       if value[0] == "160000"}
    staged_links = {path: value for path, value in staged.items()
                    if value[0] == "160000"}
    if staged_links != committed_links:
        raise TicketSourceError(
            "staged submodule gitlink changes cannot be captured; "
            "restore the index to the verified HEAD pins before packaging"
        )
    for path, (mode, _object_id) in staged.items():
        if (mode not in {"100644", "100755"}
                or committed.get(path, (None, None))[0] == mode):
            continue
        # Working bytes take precedence over staged bytes, but an index-only
        # executable-bit change must not disappear into a committed artifact.
        working = live / path
        if (not working.exists()
                or file_bytes(live, path)[1] != (int(mode, 8) & 0o777)):
            raise TicketSourceError(
                f"staged executable mode differs from working file: {path}; "
                "align the index and working-file executable mode before packaging"
            )


def capture(selection: Selection, target: Path) -> dict[str, object]:
    live = selection.live
    index_path = None
    index_before = None
    staged_changes = False
    if live:
        validate_source_submodules(live, selection.head)
        if git(live, "ls-files", "--unmerged").stdout:
            raise TicketSourceError("source index contains unresolved conflicts")
        index_path = Path(text(live, "rev-parse", "--path-format=absolute",
                               "--git-path", "index"))
        index_before = file_bytes(index_path.parent, index_path.name)
        validate_index_metadata(live, selection.head)
        staged_changes = bool(git(
            live, "diff", "--cached", "--raw", "--no-ext-diff",
            "--ignore-submodules=none", selection.head, "--",
        ).stdout)
    clone_at(selection.repo, target, selection.head)
    prepare_submodules(
        live or selection.repo, target, selection.head, live=live is not None
    )
    provenance: dict[str, object] = {
        "purpose": "development-only; not release or product integration evidence",
        "ticket_id": selection.ticket, "execution_id": selection.execution,
        "generation": selection.generation, "source_head": selection.head,
        "source_kind": "committed", "wip_digest": None,
        "origin_repository": str(selection.repo),
        "selection": "live-worktree" if live else "checkpoint-history",
    }
    if live:
        assert index_path is not None
        before_status = git(
            live, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        files = inventory(live)
        observed = {name: file_bytes(live, name) for name in files}
        # Remove only private checkout files, never the original tree/index.
        for name in inventory(target):
            if name not in observed:
                (target / name).unlink()
        digest = hashlib.sha256()
        for name, (content, mode, _signature) in observed.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            destination.chmod(mode)
            digest.update(json.dumps([name, mode, len(content)],
                                     ensure_ascii=True).encode() + b"\0" + content)
        if files != inventory(live) or any(file_bytes(live, name) != value
                                          for name, value in observed.items()):
            raise TicketSourceError("source changed during capture")
        after_status = git(
            live, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        if (before_status != after_status
                or file_bytes(index_path.parent, index_path.name) != index_before
                or text(live, "rev-parse", "HEAD") != selection.head):
            raise TicketSourceError("worktree/index changed during capture")
        if selection.binding:
            selection.binding.require_local(clean=False, read_only=True)
            selection.binding.require_worker_retirement()
        if git(target, "status", "--porcelain", "--untracked-files=all").stdout:
            provenance.update(source_kind="wip-snapshot", wip_digest=digest.hexdigest())
            # Only inventoried source bytes exist here. The caller's local
            # exclude settings need not match this new repository's settings.
            git(target, "add", "--force", "--all", "--", ".")
            for name, (content, _mode, _signature) in observed.items():
                # A checkout's attributes or host filters must not rewrite WIP.
                object_id = text(target, "rev-parse", f":{name}")
                actual = subprocess.run(
                    ["git", "-C", str(target), "cat-file", "blob", object_id],
                    capture_output=True, check=True,
                ).stdout
                if actual != content:
                    raise TicketSourceError(
                        f"Git attributes/filters changed captured source bytes: {name}"
                    )
            tree = text(target, "write-tree")
            message = "Synthetic build-only snapshot\n\n" + json.dumps(
                provenance, sort_keys=True
            )
            commit = git(
                target, "-c", "user.name=Devlegate build-only", "-c",
                "user.email=build-only@invalid", "commit-tree", tree,
                "-p", selection.head, "-m", message,
                environment={
                    name: text(target, "show", "-s", "--format=%ct", selection.head)
                    + " +0000"
                    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
                },
            ).stdout.strip()
            git(target, "update-ref", "HEAD", commit)
        elif staged_changes:
            # A clean filesystem capture does not prove a clean live index:
            # staged bytes (or a deletion) may have been undone only on disk.
            # Never label that omitted staged state as a committed source.
            raise TicketSourceError(
                "staged changes differ from HEAD but captured working files "
                "match HEAD; "
                "align the index and working files before packaging"
            )
    validate_source_submodules(target, text(target, "rev-parse", "HEAD"))
    if git(target, "status", "--porcelain", "--untracked-files=all").stdout:
        raise TicketSourceError("private snapshot failed clean-source preflight")
    provenance["build_head"] = text(target, "rev-parse", "HEAD")
    provenance["version"] = tomllib.loads(
        (target / "pyproject.toml").read_text()
    )["project"]["version"]
    provenance["synthetic_build_only"] = provenance["source_kind"] == "wip-snapshot"
    provenance["toolchain_identity"] = hashlib.sha256(b"".join(
        name.encode() + b"\0" + (target / name).read_bytes()
        for name in inventory(target)
        if name.startswith(("tools/", "packaging/")) or name == "pyproject.toml"
    )).hexdigest()
    return provenance


def compatible_graph(source: Path, target: str) -> None:
    """Recognize the existing small graph, without importing caller components."""
    diagnostic = (
        "selected source lacks a compatible package graph; "
        "update the ticket source's development packaging tools"
    )
    try:
        module = ast.parse((source / "tools/build_distribution.py").read_text())
        graphs = [ast.literal_eval(node.value) for node in module.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(name, ast.Name) and name.id == "GRAPH"
                          for name in node.targets)]
        if len(graphs) != 1:
            raise ValueError("missing graph")
        graph = graphs[0]
        if (not isinstance(graph, dict) or graph.get("wheel") != ()
                or graph.get("standalone") != ("wheel",)
                or graph.get("arch") != ("standalone",)
                or graph.get("deb") != ("standalone",)):
            raise ValueError("unsupported graph")
        if target in {"sdist", "python", "all"} and graph.get("sdist") != ():
            raise ValueError("missing source distribution graph")
        if target in {"full-source", "all"} and graph.get("full-source") != ():
            raise ValueError("missing full source graph")
        required = ["build_distribution.py", "source_preflight.py"]
        if target in {"standalone", "arch", "deb", "all"}:
            required += ["build_standalone.py", "package_standalone.py",
                         "validate_standalone_package.py"]
        for native in ("arch", "deb"):
            if target in {native, "all"}:
                required += [f"package_{native}.py", f"validate_{native}.py"]
        if target in {"full-source", "all"}:
            required += ["build_full_source.py", "validate_full_source.py"]
        if any(not (source / "tools" / name).is_file() for name in required):
            raise ValueError("missing component")
    except (OSError, SyntaxError, ValueError, TypeError) as error:
        raise TicketSourceError(diagnostic) from error


def local_submodule_config(source: Path) -> dict[str, str]:
    """Keep the existing graph's recursive source clones entirely local.

    Environment-level module URLs survive the selected full-source builder's
    fresh clone and submodule sync, without editing any selected source bytes.
    """
    configured: dict[str, str] = {}

    def visit(repo: Path) -> None:
        links = _gitlinks(repo, text(repo, "rev-parse", "HEAD"))
        if not links:
            return
        entries = git(repo, "config", "--file", str(repo / ".gitmodules"),
                      "--get-regexp", r"^submodule\..*\.path$").stdout.splitlines()
        for relative, pinned in links:
            names = [line.split(" ", 1)[0][10:-5] for line in entries
                     if line.split(" ", 1)[1] == str(relative)]
            if len(names) != 1 or Path(names[0]).is_absolute():
                raise TicketSourceError("selected submodule mapping is inconsistent")
            child = repo / str(relative)
            key = f"submodule.{names[0]}.url"
            value = str(child)
            if key in configured and configured[key] != value:
                raise TicketSourceError("recursive submodule names are ambiguous")
            if text(child, "rev-parse", "HEAD") != pinned:
                raise TicketSourceError("captured submodule pin changed")
            configured[key] = value
            git(repo, "config", key, value)
            git(repo, "config", f"submodule.{names[0]}.active", "true")
            visit(child)

    visit(source)
    environment = {"GIT_CONFIG_COUNT": str(len(configured)),
                   "GIT_ALLOW_PROTOCOL": "file", "GIT_NO_REPLACE_OBJECTS": "1",
                   "GIT_NO_LAZY_FETCH": "1"}
    for index, (key, value) in enumerate(configured.items()):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    return environment


def package_ticket(args: argparse.Namespace, invoking: Path) -> int:
    repo, config, locator = project(invoking)
    workspace = Path(tempfile.mkdtemp(prefix="devlegate-ticket-build-"))
    try:
        protected = [repo, locator.state_dir,
                     Path(text(repo, "rev-parse", "--path-format=absolute",
                               "--git-common-dir"))]
        if any(workspace.resolve().is_relative_to(path) for path in protected) or (
            git(workspace, "rev-parse", "--show-toplevel", check=False).returncode == 0
        ):
            raise TicketSourceError("temporary source must be outside worktrees")
        # Retain the shared absence guard through capture, preventing an owner
        # from admitting a writer between retirement proof and the last read.
        _safe_path(locator.lock_path)
        with locator.absence_guard():
            selection = resolve(repo, config, locator, args.ticket)
            source = workspace / "source"
            provenance = capture(selection, source)
        compatible_graph(source, args.target)
        output = args.output_dir
        if output is None:
            identity = str(provenance["wip_digest"] or selection.head)
            output = repo / "dist" / "tickets" / selection.ticket / identity
        output = Path(output).resolve()
        if output.is_relative_to(workspace) or (
            selection.live and output.is_relative_to(selection.live)
        ) or output.is_relative_to(locator.state_dir):
            raise TicketSourceError("output must be outside execution resources")
        output.mkdir(parents=True, exist_ok=True)
        artifact_output = workspace / "artifacts"
        artifact_output.mkdir()
        command = [str(Path(args.python).absolute()), "-c",
                   "import runpy,sys,types; from pathlib import Path; "
                   "r=Path(sys.argv.pop(1)); "
                   "sys.path[:0]=[str(r),str(r/'src'),str(r/'tools')]; "
                   "t=types.ModuleType('tools'); t.__path__=[str(r/'tools')]; "
                   "sys.modules['tools']=t; "
                   "sys.argv[0]=str(r/'tools/build_distribution.py'); "
                   "runpy.run_path(sys.argv[0],run_name='__main__')",
                   str(source), args.target, "--repo", str(source),
                   "--output-dir", str(artifact_output)]
        if args.keep_work:
            command.append("--keep-work")
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"PYTHONPATH", "PYTHONHOME"}
                       and not key.startswith("GIT_")}
        environment["PYTHONPATH"] = os.pathsep.join((str(source), str(source / "src")))
        environment.update(local_submodule_config(source))
        result = subprocess.run(command, cwd=source, env=environment, check=False)
        provenance["returncode"] = result.returncode
        artifacts = sorted(artifact_output.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in artifacts):
            raise TicketSourceError("selected builder emitted an unsafe artifact")
        provenance["artifacts"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts
            if path.is_file() and path.suffix not in {".json", ".log"}
        }
        report = artifact_output / f"devlegate-{args.target}-ticket-source.json"
        report.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n",
                          encoding="ascii")
        log_path = artifact_output / f"devlegate-{args.target}-build.log"
        with log_path.open("a", encoding="ascii") as log:
            log.write("\nDevelopment ticket source (not release evidence):\n"
                      + json.dumps(provenance, indent=2, sort_keys=True) + "\n")
        for path in artifact_output.iterdir():
            if path.is_symlink() or not path.is_file():
                raise TicketSourceError("selected builder emitted an unsafe artifact")
            temporary = output / f".{path.name}.pending-{os.getpid()}"
            with temporary.open("xb") as destination, path.open("rb") as origin:
                shutil.copyfileobj(origin, destination)
            os.replace(temporary, output / path.name)
        print(f"Development packages and provenance: {output}")
        return result.returncode
    finally:
        if args.keep_work:
            print(f"Private build-only source kept: {workspace}")
        else:
            shutil.rmtree(workspace)
