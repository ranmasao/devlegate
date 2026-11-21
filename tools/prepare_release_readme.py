"""Set the README coverage endpoint while preparing a release branch."""

import re
import sys
from pathlib import Path


def prepare_readme(path: Path, version: str) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError(f"invalid release version: {version}")
    contents = path.read_text()
    updated, count = re.subn(
        r"(?<=/badges/)coverage(?:-[0-9]+\.[0-9]+\.[0-9]+)?\.json(?=\))",
        f"coverage-{version}.json",
        contents,
    )
    if count != 1:
        raise ValueError("README must contain exactly one coverage badge endpoint")
    path.write_text(updated)


if __name__ == "__main__":
    prepare_readme(Path("README.md"), sys.argv[1])
