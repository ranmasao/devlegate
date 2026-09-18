This document is a plain-language guide to the licensing layout of
this repository. It does not modify the legal license terms. If this
guide conflicts with an applicable license, the license text controls.

# Licensing Guide

## License map

| Scope | License | Legal text |
| --- | --- | --- |
| Devlegate implementation, tests, and tooling | EUPL-1.2 | [LICENSE](LICENSE) |
| `src/devlegate/default_templates/**` | CC0-1.0 | [LICENSES/CC0-1.0.txt](LICENSES/CC0-1.0.txt) |
| `src/devlegate/_vendor/nanoyaml/**` | MIT | [`src/devlegate/_vendor/nanoyaml/LICENSE`](src/devlegate/_vendor/nanoyaml/LICENSE) |

## Devlegate core: EUPL-1.2

The Devlegate implementation is licensed under the European Union Public
Licence v. 1.2. Subject to that license, users may use Devlegate privately or
commercially, modify it, redistribute it, and integrate it with independent
proprietary software. They may also create independent proprietary clients,
plugins, and adapters.

Covered modifications to Devlegate itself remain subject to the EUPL-1.2
reciprocity requirements. Those requirements include the EUPL rules that apply
when modified covered functionality is communicated as a network service.
Whether a particular plugin or integration is a derivative work depends on the
facts and applicable law; the project architecture is intended to support
interoperability without deciding that legal question.

## Default templates, prompts, and skills: CC0-1.0

The files under `src/devlegate/default_templates/**` are user-copyable default
prompts, skills, configuration templates, and starter material. They are
dedicated to the public domain under CC0-1.0 to the extent legally possible,
with the CC0 license fallback applying where needed.

Users may copy, modify, redistribute, use commercially, incorporate these
materials into proprietary projects, and relicense their copies. CC0 does not
require attribution. Running Devlegate bootstrap or generation does not cause a
user's project to become EUPL-licensed merely because it received Devlegate
default templates.

The repository-level declaration of this scope is intentional. Generated user
files are not polluted with visible license boilerplate merely because their
source template came from this directory.

## NanoYAML

NanoYAML is a separate project included as a pinned dependency/submodule at
`src/devlegate/_vendor/nanoyaml`. It retains its own MIT License; Devlegate does
not relicense it. The submodule's license is distributed at
`src/devlegate/_vendor/nanoyaml/LICENSE` alongside the materialized NanoYAML
source. The `_vendor` namespace is private implementation detail and is not a
public dependency API.

## Legal texts

The complete legal terms are in [LICENSE](LICENSE) and
[LICENSES/CC0-1.0.txt](LICENSES/CC0-1.0.txt). This guide is not a substitute
for either legal text.
