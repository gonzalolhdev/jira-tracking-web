#!/bin/bash
# Hot/cold launcher. Settings live in scripts/launcher.conf.
#   launch.sh hot   (default) open the app; resume the existing container if it is stopped.
#                   Never rebuilds, so it does not pick up code or env changes.
#                   Falls back to a cold launch when no container exists yet.
#   launch.sh cold  rebuild the image and recreate the container (re-reads env files).
#   launch.sh stop  stop the container without deleting it (hot can resume it).
set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=launcher.conf
source "$ROOT/scripts/launcher.conf"
URL="http://localhost:$PORT"
LOG="${TMPDIR:-/tmp}/$APP_SLUG-launcher.log"
MODE="${1:-hot}"

notify() { osascript -e "display notification \"$1\" with title \"$APP_NAME\"" >/dev/null 2>&1 || true; }
fail() { notify "$1 (see $LOG)"; echo "ERROR: $1" >&2; exit 1; }
healthy() { curl -fs "$URL$HEALTH_PATH" >/dev/null 2>&1; }

cd "$ROOT"

if [ "$MODE" = "stop" ]; then
  docker compose stop
  notify "Stopped"
  exit 0
fi

# If the app window is already open in Chrome, bring it to the front
# (with "reload", also reload it so it reflects a fresh cold launch).
focus_existing() {
  local reload="${1:-}"
  pgrep -x "Google Chrome" >/dev/null || return 1
  [ "$(osascript <<OSA 2>/dev/null
tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t starts with "$URL" then
        if "$reload" is "reload" then reload t
        set index of w to 1
        activate
        return "found"
      end if
    end repeat
  end repeat
  return "none"
end tell
OSA
)" = "found" ]
}

ensure_docker() {
  docker info >/dev/null 2>&1 && return 0
  notify "Starting Docker..."
  # Headless engine start (no dashboard window); fall back to a hidden app launch.
  docker desktop start >>"$LOG" 2>&1 || open -g -j -a Docker
  for _ in $(seq 1 90); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
  docker info >/dev/null 2>&1 || fail "Docker did not start"
}

cold_start() {
  ensure_docker
  notify "Rebuilding..."
  docker compose up -d --build --force-recreate >>"$LOG" 2>&1 || fail "docker compose failed"
}

wait_healthy() {
  for _ in $(seq 1 60); do
    healthy && return 0
    sleep 1
  done
  fail "App did not become healthy"
}

reload=""
if [ "$MODE" = "cold" ]; then
  cold_start
  reload="reload"
elif ! healthy; then
  ensure_docker
  if [ -z "$(docker compose ps -aq)" ]; then
    cold_start   # first run or after "down": nothing to resume
    reload="reload"
  else
    docker compose start >>"$LOG" 2>&1 || fail "Could not start the container"
  fi
fi
wait_healthy
notify "Ready"

focus_existing "$reload" || open -na "Google Chrome" --args --app="$URL"
