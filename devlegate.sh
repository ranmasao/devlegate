#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE=${ENV_FILE:-"$SCRIPT_DIR/.env"}

usage() {
  printf 'Usage: %s [--watch] [--once] [--env FILE]\n' "$(basename "$0")"
  printf '\nPoll a local repository for remote changes and run an ephemeral opencode agent.\n'
}

die() { printf 'devlegate: %s\n' "$*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

WATCH=false
while (($#)); do
  case "$1" in
    --watch) WATCH=true; shift ;;
    --once) WATCH=false; shift ;;
    --env) (($# >= 2)) || die '--env requires a file'; ENV_FILE=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -f "$ENV_FILE" ]] || die "configuration file not found: $ENV_FILE (copy .env.example to .env)"
# shellcheck disable=SC1090
source "$ENV_FILE"

REPO_URL=${REPO_URL:-}
REMOTE_NAME=${REMOTE_NAME:-origin}
REMOTE_BRANCH=${REMOTE_BRANCH:-main}
WATCH_PATH=${WATCH_PATH:-.}
POLL_INTERVAL=${POLL_INTERVAL:-300}
AGENT_PROMPT_FILE=${AGENT_PROMPT_FILE:-"$SCRIPT_DIR/agent-prompt.txt"}
OPENCODE_BIN=${OPENCODE_BIN:-opencode}
OPENCODE_MODEL=${OPENCODE_MODEL:-}
OPENCODE_AGENT=${OPENCODE_AGENT:-}
AUTH_MODE=${AUTH_MODE:-auto}
GITHUB_TOKEN=${GITHUB_TOKEN:-}
AUTHENTICATED_REPO_URL=${AUTHENTICATED_REPO_URL:-}
COMMIT_PREFIX=${COMMIT_PREFIX:-devlegate}
PUSH_BRANCH=${PUSH_BRANCH:-$REMOTE_BRANCH}

REPO_DIR=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) || {
  printf 'devlegate: current directory is not a git repository: %s\n' "$PWD" >&2
  exit 1
}
[[ -f "$AGENT_PROMPT_FILE" ]] || die "agent prompt file not found: $AGENT_PROMPT_FILE"
[[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]] || die 'POLL_INTERVAL must be an integer'
[[ -n "$REPO_URL" ]] || die 'REPO_URL is required'
command -v flock >/dev/null 2>&1 || die 'flock is required'

LOCK_ROOT=${STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/devlegate}
REPO_KEY=$(printf '%s' "$REPO_DIR" | sha256sum | cut -d' ' -f1)
LOCK_FILE="$LOCK_ROOT/$REPO_KEY.lock"
mkdir -p "$LOCK_ROOT"

exec {DEVLEGATE_LOCK_FD}>"$LOCK_FILE" || die "cannot open lock file: $LOCK_FILE"
if ! flock -n "$DEVLEGATE_LOCK_FD"; then
  die 'another devlegate instance is already running'
fi

git_auth_args=()
case "$AUTH_MODE" in
  auto)
    if [[ -n "$AUTHENTICATED_REPO_URL" ]]; then
      git_auth_args=(-c "remote.$REMOTE_NAME.url=$AUTHENTICATED_REPO_URL")
    elif [[ -n "$GITHUB_TOKEN" ]]; then
      git_auth_args=(-c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/")
    elif command -v gh >/dev/null 2>&1; then
      GITHUB_TOKEN=$(gh auth token 2>/dev/null || true)
      [[ -n "$GITHUB_TOKEN" ]] && git_auth_args=(-c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/")
    fi
    ;;
  gh)
    command -v gh >/dev/null 2>&1 || die 'AUTH_MODE=gh requires gh'
    GITHUB_TOKEN=$(gh auth token) || die 'gh is not authenticated'
    git_auth_args=(-c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/")
    ;;
  token)
    [[ -n "$GITHUB_TOKEN" ]] || die 'AUTH_MODE=token requires GITHUB_TOKEN'
    git_auth_args=(-c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/")
    ;;
  url)
    AUTHENTICATED_REPO_URL=${AUTHENTICATED_REPO_URL:-$REPO_URL}
    [[ "$AUTHENTICATED_REPO_URL" == *'@'* ]] || die 'AUTH_MODE=url requires AUTHENTICATED_REPO_URL or a credential-bearing REPO_URL'
    git_auth_args=(-c "remote.$REMOTE_NAME.url=$AUTHENTICATED_REPO_URL")
    ;;
  none) ;;
  *) die 'AUTH_MODE must be auto, gh, token, url, or none' ;;
esac

git_cmd() { git "${git_auth_args[@]}" -C "$REPO_DIR" "$@"; }

remote_ref="$REMOTE_NAME/$REMOTE_BRANCH"
if [[ "$(git_cmd remote get-url "$REMOTE_NAME" 2>/dev/null || true)" != "$REPO_URL" ]]; then
  log "setting $REMOTE_NAME URL to configured repository"
  git_cmd remote set-url "$REMOTE_NAME" "$REPO_URL"
fi

if [[ -z "$OPENCODE_MODEL" ]]; then
  opencode_model_args=()
else
  opencode_model_args=(--model "$OPENCODE_MODEL")
fi
if [[ -z "$OPENCODE_AGENT" ]]; then
  opencode_agent_args=()
else
  opencode_agent_args=(--agent "$OPENCODE_AGENT")
fi

has_watch_files() {
  local path
  local -a paths=()
  shopt -s nullglob dotglob globstar
  paths=("$REPO_DIR/$WATCH_PATH"/**)
  for path in "${paths[@]}"; do
    [[ -f "$path" ]] && return 0
  done
  return 1
}

run_once() {
  git_cmd fetch --prune "$REMOTE_NAME" "$REMOTE_BRANCH" || { log 'fetch failed'; return 1; }
  git_cmd rev-parse --verify "$remote_ref" >/dev/null || die "remote branch not found: $remote_ref"
  if [[ -z "$(git_cmd status --porcelain)" ]]; then
    git_cmd merge --ff-only "$remote_ref" >/dev/null || { log 'cannot fast-forward local checkout'; return 1; }
  else
    log 'working tree has local changes; leaving checkout untouched'
  fi
  if ! has_watch_files; then log "no files in $WATCH_PATH"; return 0; fi
  local prompt_file agent_status
  prompt_file=$(mktemp "${TMPDIR:-/tmp}/devlegate-prompt.XXXXXX")
  {
    cat "$AGENT_PROMPT_FILE"
    printf '\n\n--- Devlegate context ---\nRepository: %s\nWatched directory: %s\n\nInspect the watched directory first. Decide what work is needed, make necessary changes, and report positive or negative results clearly. Do not commit; Devlegate will commit and push your changes after you exit.\n' "$REPO_URL" "$WATCH_PATH"
  } > "$prompt_file"
  log "running ephemeral agent for files in $WATCH_PATH"
  set +e
  "$OPENCODE_BIN" run --auto "${opencode_model_args[@]}" "${opencode_agent_args[@]}" "$(<"$prompt_file")"
  agent_status=$?
  set -e
  rm -f "$prompt_file"
  if ((agent_status != 0)); then log "agent exited with status $agent_status"; return "$agent_status"; fi
  if [[ -n "$(git_cmd status --porcelain)" ]]; then
    git_cmd add -A
    git_cmd commit -m "$COMMIT_PREFIX: process watched files" || return 1
    git_cmd push "$REMOTE_NAME" "HEAD:$PUSH_BRANCH" || return 1
    log 'agent changes pushed'
  else
    log 'agent made no repository changes'
  fi
}

while :; do
  run_once || log 'run failed; continuing'
  $WATCH || break
  sleep "$POLL_INTERVAL"
done
