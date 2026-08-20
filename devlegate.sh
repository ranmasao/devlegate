#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=${ENV_FILE:-"$PWD/.env"}

usage() {
  printf 'Usage: %s [--watch] [--once] [--env FILE]\n' "$(basename "$0")"
  printf '\nPoll the repository in $PWD for remote changes and run an ephemeral OpenCode implementation agent.\n'
}

die() { printf 'devlegate: %s\n' "$*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

WATCH=true
while (($#)); do
  case "$1" in
    --watch) WATCH=true; shift ;;
    --once) WATCH=false; shift ;;
    --env) (($# >= 2)) || die '--env requires a file'; ENV_FILE=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -f "$ENV_FILE" ]] || die "configuration file not found: $ENV_FILE (copy devlegate's .env.example to $PWD/.env)"
# shellcheck disable=SC1090
source "$ENV_FILE"

REMOTE_NAME=${REMOTE_NAME:-origin}
REMOTE_BRANCH=${REMOTE_BRANCH:-}
TODO_PATH=${TODO_PATH:-kanban/todo}
REVIEW_PATH=${REVIEW_PATH:-kanban/review}
POLL_INTERVAL=${POLL_INTERVAL:-300}
AGENT_PROMPT_FILE=${AGENT_PROMPT_FILE:-"$SCRIPT_DIR/agent-prompt.txt"}
OPENCODE_BIN=${OPENCODE_BIN:-opencode}
OPENCODE_MODEL=${OPENCODE_MODEL:-}
OPENCODE_AGENT=${OPENCODE_AGENT:-}

REPO_DIR=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) || die "current directory is not a git repository: $PWD"
REPO_DIR=$(cd "$REPO_DIR" && pwd -P)
CURRENT_DIR=$(pwd -P)
[[ "$CURRENT_DIR" == "$REPO_DIR" ]] || die "run devlegate from repository root: $REPO_DIR"

[[ -f "$AGENT_PROMPT_FILE" ]] || die "agent prompt file not found: $AGENT_PROMPT_FILE"
[[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]] || die 'POLL_INTERVAL must be an integer'
[[ -n "$OPENCODE_MODEL" ]] || die 'OPENCODE_MODEL is required'
command -v flock >/dev/null 2>&1 || die 'flock is required'
command -v "$OPENCODE_BIN" >/dev/null 2>&1 || die "OpenCode executable not found: $OPENCODE_BIN"

git_cmd() { git -C "$REPO_DIR" "$@"; }

CURRENT_BRANCH=$(git_cmd symbolic-ref --quiet --short HEAD) || die 'detached HEAD is not supported'
REMOTE_BRANCH=${REMOTE_BRANCH:-$CURRENT_BRANCH}
[[ "$CURRENT_BRANCH" == "$REMOTE_BRANCH" ]] || die "current branch '$CURRENT_BRANCH' does not match REMOTE_BRANCH '$REMOTE_BRANCH'"
git_cmd remote get-url "$REMOTE_NAME" >/dev/null 2>&1 || die "git remote not found: $REMOTE_NAME"

LOCK_ROOT=${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/devlegate}
REPO_KEY=$(printf '%s' "$REPO_DIR" | sha256sum | cut -d' ' -f1)
LOCK_FILE="$LOCK_ROOT/$REPO_KEY.lock"
mkdir -p "$LOCK_ROOT"

exec {DEVLEGATE_LOCK_FD}>"$LOCK_FILE" || die "cannot open lock file: $LOCK_FILE"
if ! flock -n "$DEVLEGATE_LOCK_FD"; then
  die 'another devlegate instance is already running for this checkout'
fi

if [[ -z "$OPENCODE_AGENT" ]]; then
  opencode_agent_args=()
else
  opencode_agent_args=(--agent "$OPENCODE_AGENT")
fi

has_todo_files() {
  local todo_dir="$REPO_DIR/$TODO_PATH"
  [[ -d "$todo_dir" ]] || return 1
  [[ -n "$(find "$todo_dir" -type f ! -name '.gitkeep' -print -quit 2>/dev/null)" ]]
}

run_once() {
  local local_head remote_head remote_ref changed_paths prompt_file agent_status local_after remote_after

  if [[ -n "$(git_cmd status --porcelain)" ]]; then
    log 'working tree is dirty; refusing to pull or start the agent'
    return 1
  fi

  local_head=$(git_cmd rev-parse HEAD)
  remote_ref="$REMOTE_NAME/$REMOTE_BRANCH"

  git_cmd fetch --prune "$REMOTE_NAME" "$REMOTE_BRANCH" || { log 'fetch failed'; return 1; }
  git_cmd rev-parse --verify "$remote_ref" >/dev/null || { log "remote branch not found: $remote_ref"; return 1; }
  remote_head=$(git_cmd rev-parse "$remote_ref")

  if [[ "$local_head" == "$remote_head" ]]; then
    log 'no remote changes'
    return 0
  fi

  changed_paths=$(git_cmd diff --name-only "$local_head" "$remote_head" || true)
  git_cmd merge --ff-only "$remote_ref" >/dev/null || { log 'cannot fast-forward local checkout'; return 1; }
  log "updated $CURRENT_BRANCH from ${local_head:0:12} to ${remote_head:0:12}"

  if ! has_todo_files; then
    log "no actionable ticket files in $TODO_PATH"
    return 0
  fi

  prompt_file=$(mktemp "${TMPDIR:-/tmp}/devlegate-prompt.XXXXXX")
  {
    cat "$AGENT_PROMPT_FILE"
    printf '\n\n--- Devlegate context ---\n'
    printf 'Repository root: %s\n' "$REPO_DIR"
    printf 'Remote: %s\n' "$REMOTE_NAME"
    printf 'Branch: %s\n' "$REMOTE_BRANCH"
    printf 'Pulled revision: %s -> %s\n' "$local_head" "$remote_head"
    printf 'Todo directory: %s\n' "$TODO_PATH"
    printf 'Review directory: %s\n' "$REVIEW_PATH"
    printf '\nChanged paths from the pulled revision:\n%s\n' "${changed_paths:-<none>}"
  } > "$prompt_file"

  log "running OpenCode for tickets in $TODO_PATH"
  set +e
  "$OPENCODE_BIN" run --auto --model "$OPENCODE_MODEL" "${opencode_agent_args[@]}" "$(<"$prompt_file")"
  agent_status=$?
  set -e
  rm -f "$prompt_file"

  if ((agent_status != 0)); then
    log "agent exited with status $agent_status"
    return "$agent_status"
  fi

  if [[ -n "$(git_cmd status --porcelain)" ]]; then
    log 'agent exited successfully but left uncommitted repository changes'
    return 1
  fi

  local_after=$(git_cmd rev-parse HEAD)
  git_cmd fetch "$REMOTE_NAME" "$REMOTE_BRANCH" >/dev/null || { log 'post-agent fetch failed'; return 1; }
  remote_after=$(git_cmd rev-parse "$remote_ref")
  if [[ "$local_after" != "$remote_after" ]]; then
    log 'agent did not leave the local branch synchronized with the remote branch; expected it to commit and push'
    return 1
  fi

  log 'agent completed; local and remote branch heads are synchronized'
}

while :; do
  if run_once; then
    status=0
  else
    status=$?
    log "run failed with status $status"
  fi

  if ! $WATCH; then
    exit "$status"
  fi
  sleep "$POLL_INTERVAL"
done
