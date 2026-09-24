# Distribution Proof

This document records the current distribution boundary. It is a proof of the
standalone build path, not a release or package publication specification.

## Layering

The implemented path is:

```text
source -> wheel -> PEX eager scie standalone executable
```

The wheel is the canonical Python payload. The standalone build is for Linux
x86_64 with bundled CPython 3.12. No macOS, Windows, ARM64, PyPI, distro
package, or release-asset workflow is implemented here.

Build and proof commands are explicit and separate:

```sh
python3 tools/build_standalone.py build --output /tmp/devlegate-standalone
python3 tools/build_standalone.py prove \
  --artifact /tmp/devlegate-standalone/devlegate-0.5.4.dev0-linux-x86_64
```

The build pins PEX `2.103.2`, builds the wheel first, resolves the local wheel
without PyPI for the Devlegate payload, selects eager scie, and targets
CPython 3.12 on Linux x86_64. It does not modify the normal runtime dependency
set or invoke package managers at runtime.

## Runtime Contract

Python-based distributions require compatible Python 3.12 or newer. Devlegate
does not require a virtual environment; an installer may use one as its own
isolation mechanism. `./dev` is a source-tree development interface only and
is not an installed-product runtime interface.

The eager scie contains its own CPython runtime. Its executable can therefore
run without host Python, a host virtual environment, pip, or network access at
runtime. Host integration remains a separate explicit operation:

```sh
devlegate host install --supervisor internal
```

The host installation record does not identify a package manager or artifact.

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

## Legal Inventory

The wheel carries Devlegate's EUPL material, CC0 template material, and
NanoYAML MIT material through its PEP 639 metadata and license files. The eager
scie additionally contains PEX bootstrap code, a Science/scie launcher, and a
Python Standalone Builds CPython distribution. The proof records PEX metadata
and the bundled CPython version; final standalone publication still requires a
separate review of the Science/scie and CPython notice/license material.

This proof does not claim that the single-file executable is publication-ready.
