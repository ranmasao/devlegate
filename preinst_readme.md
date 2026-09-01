# Devlegate Pre-Install Guide

Attach Devlegate to an existing Git checkout on its normal product branch, with a
configured remote, working authentication, and the project's existing documentation
and agent context ready for its own work.

Devlegate does not require `README.md`, `AGENTS.md`, `CLAUDE.md`, or a particular
`docs/` layout. Identify the documents that already provide project context. Do not
delete, normalize, replace, or reorganize existing agent files, skills, prompts, or
documentation.

Before bootstrap, prepare `.devlegate/project.md`. Its NanoYAML frontmatter explicitly
routes `common`, `architect`, and `reviewer` paths to existing project files. Devlegate
validates those paths but does not classify or interpret their contents.

The supported bootstrap sequence is:

```text
devlegate init
inspect and configure generated project-side artifacts
populate .devlegate/project.md
incorporate those changes using the project's normal workflow
return to the intended product checkout in a clean, valid state
devlegate control init
devlegate check
```

After `devlegate init`, review the generated or updated project-side artifacts and
populate `.devlegate/project.md`. Review and incorporate those changes using the
project's normal change-management workflow. Devlegate does not choose whether that
means a commit, branch, pull request, review system, or another project process.
Before running workflow execution, return to the intended product checkout in a
clean, valid state.

Prepare `.env` and select its runtime settings as needed. The project decides
whether `.env` is tracked, ignored, or managed another way; Devlegate does not edit
`.gitignore`. `init` may create an incomplete `.devlegate/project.md` skeleton, but
`check` will not pass until routing contains at least one existing readable file.

Devlegate distribution defaults and this guide are not project canon. The target owns
`.env`, `.devlegate/project.md`, `.devlegate/templates/`, `artifacts.toml`, generated
role artifacts, and its existing documentation. Upgrading Devlegate does not rewrite
project-owned role templates. An upgrade that predates project-context routing
requires an explicit project-owned template update followed by `devlegate render`.
Current project documents remain the project's current truth; Git history is
historical, and Devlegate does not maintain a semantic copy.
