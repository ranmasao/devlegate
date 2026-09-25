# Project Setup

This guide explains how to attach Devlegate to an existing project. It is not
an installation guide for the Devlegate software; see the [README](../README.md)
for installation options.

## Requirements

Before attaching Devlegate, have:

- an existing Git repository with the intended product branch checked out;
- a configured remote and working Git authentication;
- enough project documentation for the Architect and Reviewer roles;
- a decision about which existing documents those roles should read.

Devlegate does not require a particular documentation layout. The project keeps
ownership of its README, agent instructions, documentation, and change process.

## Project Files

Devlegate adds a small project adapter and a separate workflow control history.
Project-owned files include:

- `.env`
- `.devlegate/project.md`
- `.devlegate/templates/`
- generated Architect and Reviewer files
- the project's existing documentation and agent instructions

`.devlegate/project.md` points Devlegate roles to existing project documents. It
does not replace those documents. Its frontmatter names files for `common`,
`architect`, and `reviewer`; Devlegate checks that the paths are safe and
readable but does not rewrite the referenced documents.

For example:

```text
---
"type": "devlegate.project"
"common":
  - "README.md"
"architect":
  - "docs/architecture.md"
"reviewer":
  - "docs/testing.md"
---
```

Architect receives `common` plus `architect`. Reviewer receives `common` plus
`reviewer`.

## Attach The Project

From the repository root, run the project bootstrap with a local alias:

```sh
devlegate init my-project
```

Inspect the generated `.env`, `.devlegate/project.md`, templates, and role files.
Complete the project context with the documents the roles should use. Review
and accept those project-side changes using the project's normal process.

Return to a clean, valid product checkout, then initialize the separate
Devlegate workflow history and validate the setup:

```sh
devlegate control init
devlegate check
```

`init` may create project files and leave the checkout dirty. That is expected
during setup. Execution starts only after the project accepts those changes and
the checkout passes `check`.

## Project Decisions

The project, not Devlegate, decides whether to:

- commit product changes;
- push product branches;
- create pull or merge requests;
- choose an integration policy;
- track or ignore `.env`;
- organize project documentation;
- replace existing agent instructions.

Devlegate prepares the adapter and validates the resulting setup. It does not
copy this guide into the project or maintain a second copy of project meaning.
