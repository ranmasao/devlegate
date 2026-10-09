# Distribution Proof

This document records the current distribution boundary and standalone release
package proof.

## Layering

The implemented path is:

```text
source -> wheel -> PEX eager scie standalone executable
```

The wheel is the main Python payload. The standalone build is for Linux
x86_64 with bundled CPython 3.12. No macOS, Windows, ARM64, or PyPI
distribution is implemented here.

Build and proof commands are explicit and separate:

```sh
python3 tools/build_standalone.py build --output /tmp/devlegate-standalone
python3 tools/build_standalone.py prove \
  --artifact /tmp/devlegate-standalone/devlegate-<version>-linux-x86_64
```

The build pins PEX `2.103.2`, Science `0.21.0`, Python Standalone Builds release
`20260901`, and bundled CPython `3.12.14`. The PBS archive target is
`x86_64-unknown-linux-gnu`, `install_only`. The wheel is built in an ephemeral
environment containing exact, hash-verified pip `24.3.1`, setuptools `77.0.3`,
and wheel `0.45.1`. The local wheel is resolved without PyPI for the Devlegate
payload, and the scie is assembled with eager mode. PEX 2.103.2 also requires
its own isolated bootstrap wheels, pip `24.1`, setuptools `70.1.0`, and wheel
`0.43.0`; these are build-tool inputs for PEX resolution, not the Devlegate
wheel build backend or runtime dependencies. It does not modify the normal
runtime dependency set or invoke package managers at runtime.

The standalone distribution unit is a deterministic
`devlegate-<version>-linux-x86_64.tar.gz` archive containing the executable,
Devlegate legal material, third-party notices and licenses, and a stable build
record. It is distinct from the full-source archive:

```text
devlegate-X.Y.Z-linux-x86_64.tar.gz
devlegate-X.Y.Z-linux-x86_64.tar.gz.sha256
```

The same validated archive can produce the portable Debian proof package:

```text
devlegate_<version>_amd64.deb
```

The package targets x86_64 / amd64 Debian-family Linux. It has no Python or
systemd package dependencies, no maintainer scripts, and no systemd unit.

The full-source pair remains separate:

```text
devlegate-X.Y.Z-full-source.tar.gz
devlegate-X.Y.Z-full-source.tar.gz.sha256
```

Devlegate-built distribution units are curated product boundaries. The full-source
archive removes `.env`, `.devlegate/**`, and project-generated integration targets
declared by `.devlegate/templates/artifacts.toml`; wheel, standalone, and Debian
units likewise contain product material and the packaged defaults under
`src/devlegate/default_templates/**`, not this repository's project configuration.
The release validators reject repository-integration paths rather than relying on
the current setuptools layout.

GitHub's automatic `Source code.zip` and `Source code.tar.gz` files are different:
they are literal snapshots of the tagged Git tree and may contain the repository's
self-hosting integration. They are GitHub-generated snapshots, outside this curated
Devlegate-built package guarantee.

The release workflow builds and validates these units in read-only jobs, then
passes only the exact validated pairs to a write-only upload job for the
existing release.

The reproducibility proof assembles wheel and scie A/B independently under
distinct temporary build roots, wheel-build environments, and PEX_ROOT caches.
They share only immutable, hash-verified downloaded inputs. PEX_ROOT is
build-time cache state only. The main scie assembly deliberately does not
override HOME or XDG cache variables, because doing so would turn a build cache
location into the generated runtime default. The generated executable does not
receive a build-machine runtime-pex-root or a Devlegate-owned runtime cache
policy. The proof scope is byte equality on the tested Linux x86_64 build
environment from these pinned inputs, not universal reproducibility across
arbitrary systems.

## Runtime Contract

Python-based distributions require compatible Python 3.12 or newer. Devlegate
does not require a virtual environment; an installer may use one as its own
isolation mechanism. `./dev` is a source-tree development interface only and
is not an installed-product runtime interface.

The eager scie contains its own CPython runtime. Its executable can therefore
run without host Python, a host virtual environment, pip, or network access at
runtime. Host integration remains separate from software distribution; ordinary
bare startup automatically chooses usable systemd supervision or direct
attachment, while explicit host-policy commands remain available when needed.

The host installation record does not identify a package manager or artifact.

Runtime identity uses only evidence owned by the relevant distribution boundary:

- Python package invocations read `devlegate` distribution metadata, including
  `INSTALLER` and optional PEP 610 `direct_url.json` data. Missing metadata is
  reported as `unknown`.
- Standalone invocations are proven only when the PEX and SCIE environment
  values identify the same executable ELF artifact. A standalone executable
  reports Debian installation only when the Debian package marker at
  `usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json` contains a SHA-256
  digest matching that exact executable; a missing, malformed, stale, or
  mismatching marker is `unknown`, not portable-path inference.
- Debian packaging writes and validates that payload-bound marker while copying
  the validated standalone payload. The marker is not part of the standalone
  archive.

`devlegate --version` is the concise `program version` probe. `devlegate version`
is the project-independent diagnostic banner and its JSON/YAML forms contain the
same runtime identity fields without project or service state.

## Relaunch Identity

The centralized `product_launcher()` normally returns the current Python
invocation:

```text
<sys.executable> -P -m devlegate
```

Inside the proven eager scie, PEX and SCIE runtime metadata both identify the
outer executable. The launcher selects that executable only when both values
agree and the path is an executable ELF file. It does not select the cached
bundled interpreter, `/proc/self/exe`, a virtual environment, or an arbitrary
environment value.

Internal detached hosting, daemon self-restart, and systemd unit generation all
consume this same product launch identity. Systemd serializes the final argv at
the unit boundary and escapes literal `%` and `$` as required by systemd.

## Build and Runtime Layers

The source compatibility floor is Python `>=3.12`. The standalone builder
acceptance rule is also Python `>=3.12`, but the currently validated build host
is CPython; acceptance by the version predicate is not a tested interpreter
support matrix. The builder is not coupled to the bundled runtime minor
version. The current bundled runtime is independently pinned to CPython
`3.12.14` from PBS release `20260901`.

The native standalone target is Linux x86_64 with glibc. Its eager scie uses
the official `scie-jump-gnu-linux-x86_64` asset from scie-jump `1.13.0`, not
the unqualified Linux asset. The PBS archive is the GNU Linux
`x86_64-unknown-linux-gnu` `install_only` flavor.

Science is build-time tooling and is not part of the eager runtime payload.
The eager runtime contains the Devlegate wheel, PEX bootstrap/runtime material,
scie-jump, and the PBS-produced CPython runtime and bundled libraries. ptex is
not included or declared in the eager artifact; it is relevant only to lazy
scie flows.

## Legal Inventory

The wheel carries Devlegate's EUPL material, CC0 template material, and
NanoYAML MIT material through its PEP 639 metadata and license files. The eager
scie additionally contains PEX bootstrap code, scie-jump, and a Python
Standalone Builds CPython distribution. Science is used to build the artifact,
not redistributed by the eager runtime. PBS build machinery and its MPL-2.0
project license is a build fact; it is not by itself a conclusion
that the entire standalone executable is MPL-2.0. Final standalone publication
still requires a separate review and packaging of the PEX, scie-jump, CPython,
and bundled-library notice/license material.

The standalone archive, rather than the naked executable, is the release
publication unit. The selected tag's own standalone tooling and compliance
manifest define the standalone boundary; full-source packaging continues to
use trusted current release tooling against the selected tag.
