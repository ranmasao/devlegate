This document is plain-language guidance about the licensing layout of this
repository. It does not modify the legal license terms. If this guide conflicts
with an applicable license, the actual license text controls.

# Licensing Guide

## 30-second overview

| Scenario | Result |
| --- | --- |
| Use Devlegate internally | Yes |
| Use Devlegate commercially | Yes |
| Use it to develop proprietary software | Yes |
| Build an independent proprietary client, plugin, adapter, or integration | Intended to be yes |
| Run Devlegate as part of a commercial service | Yes |
| Modify Devlegate itself | Yes |
| Keep covered Devlegate modifications closed when EUPL reciprocity applies | No |
| Copy generated/default prompts or templates into a proprietary project | Yes, CC0 |
| Re-license copies of CC0 template material | Yes |
| Obtain trademark rights to the Devlegate name or logo from the software license | No |

These are practical project interpretations, not unsupported legal guarantees.
The actual facts, applicable law, and license texts control.

## License map

| Scope | License | Legal text |
| --- | --- | --- |
| Devlegate implementation, tests, and tooling | EUPL-1.2 | [LICENSE](LICENSE) |
| `src/devlegate/default_templates/**` | CC0-1.0 | [LICENSES/CC0-1.0.txt](LICENSES/CC0-1.0.txt) |
| `src/devlegate/_vendor/nanoyaml/**` | MIT | [`src/devlegate/_vendor/nanoyaml/LICENSE`](src/devlegate/_vendor/nanoyaml/LICENSE) |

## Devlegate core: EUPL-1.2

### You can

* use Devlegate privately;
* use Devlegate commercially;
* modify Devlegate;
* redistribute Devlegate;
* operate Devlegate as a service;
* interoperate with independent software.

### You must, when applicable

* preserve required notices;
* provide applicable license and source information;
* comply with EUPL reciprocity for covered modifications;
* comply with EUPL network-communication provisions where they apply.

### The license does not automatically do

* license unrelated proprietary software merely because it interoperates with Devlegate;
* grant trademark rights;
* decide every derivative-work question.

Whether a particular plugin, integration, or other use is a derivative work
depends on the facts and applicable law.

## Intended interoperability boundary

The Devlegate project intends software that interacts with Devlegate solely
through documented external boundaries to remain independently licensable. These
boundaries include the CLI, IPC protocol, control-plane Git refs and data, and
the worker subprocess/process protocol. Such software is not intended to be
part of the Devlegate implementation merely because it interacts through one of
those boundaries.

This is a project interpretation for making the architecture and intended
licensing boundary reviewable by users. It is not a universal legal
determination. Actual derivative-work status depends on applicable law and the
particular facts. This interpretation is not a broad linking exception or a
custom license clause.

Future independently licensed worker backends should, where practical,
communicate through an external process and data contract rather than requiring
subclassing or importing Devlegate runtime internals.

## Devlegate output

Ordinary factual or operational project and runtime output does not acquire an
EUPL obligation merely because Devlegate produced it. Examples include
ExecutionReports, control-plane commits, checkpoint or integration commit
messages, scheduler or runtime observations, and generated factual metadata.

The user's project and its content remain under licenses chosen by the user or
the project. This does not claim that copyright can never exist in generated
output. It states only that Devlegate does not impose EUPL on ordinary output
merely by producing it.

Output that literally incorporates separately licensed source material retains
the relevant status. Copyable Devlegate defaults are CC0, while user-provided
material retains its own status.

## Default templates, prompts, and skills: CC0-1.0

The files under `src/devlegate/default_templates/**` are user-copyable default
prompts, skills, configuration templates, generated-file markers, project
context starters, and other starter material. They are dedicated to the public
domain under CC0-1.0 to the extent legally possible, with the CC0 license
fallback applying where needed.

Users may copy, modify, redistribute, use commercially, incorporate these
materials into proprietary projects, and relicense their copies. CC0 does not
require attribution. Generated user files are not polluted with visible EUPL
boilerplate merely because their source material came from this directory.

## NanoYAML

NanoYAML is a separate project included as a pinned dependency or submodule at
`src/devlegate/_vendor/nanoyaml`. It retains its own MIT License; Devlegate does
not relicense it. Its license is distributed at
`src/devlegate/_vendor/nanoyaml/LICENSE` alongside the materialized source.
The `_vendor` namespace is private implementation detail and is not a public
dependency API.

## Legal texts

The complete legal terms are in [LICENSE](LICENSE),
[LICENSES/CC0-1.0.txt](LICENSES/CC0-1.0.txt), and the NanoYAML MIT license
identified above. This guide is not a substitute for any of those legal texts.
