# Devlegate

Devlegate synchronizes a local checkout with its configured remote branch, then checks whether `WATCH_PATH` contains any regular files. If it does, Devlegate starts a fresh `opencode run` process with a prompt file. Successful agent changes are committed and pushed by Devlegate.

## Setup

1. Copy `.env.example` to `.env` and set `REPO_URL`, `WATCH_PATH`, and `AGENT_PROMPT_FILE`.
2. Choose authentication with `AUTH_MODE=auto`, `gh`, `token`, `url`, or `none`. `auto` precedence is authenticated URL, `GITHUB_TOKEN`, then `gh auth token`.
3. Ensure the checkout is clean and the configured branch is available.
4. Change into the local checkout and run `/path/to/devlegate/devlegate.sh` once, or add `--watch` for polling.

Agent sessions are intentionally ephemeral. A kernel-managed `flock` prevents concurrent runs for the same checkout; the lock is released automatically after crashes, `SIGKILL`, or power loss. Files may be created locally, left uncommitted, or arrive from the remote through fast-forward synchronization.

## Notes

When the working tree is clean, Devlegate fast-forwards it to the configured remote branch. When local changes exist, it leaves them untouched and still checks the watched directory. It allows the agent to modify files outside `WATCH_PATH`, stages all resulting changes, and pushes them to `PUSH_BRANCH`.
