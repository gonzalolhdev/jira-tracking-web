#!/bin/bash
# On-demand launcher: starts Docker + the app container if needed, waits for it
# to be healthy, then opens it in a standalone Chrome app window.
# Usage: scripts/launch.sh [start|stop]
set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL="http://localhost:8080"
LOG="${TMPDIR:-/tmp}/jira-track-launcher.log"

notify() { osascript -e "display notification \"$1\" with title \"Jira Track\"" >/dev/null 2>&1 || true; }
fail() { notify "$1 (see $LOG)"; echo "ERROR: $1" >&2; exit 1; }

cd "$ROOT"

if [ "${1:-start}" = "stop" ]; then
  docker compose down
  notify "Stopped"
  exit 0
fi

# If the app window is already open in Chrome, just bring it to the front.
focus_existing() {
  pgrep -x "Google Chrome" >/dev/null || return 1
  [ "$(osascript <<OSA 2>/dev/null
tell application "Google Chrome"
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t starts with "$URL" then
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
focus_existing && exit 0

if ! docker info >/dev/null 2>&1; then
  notify "Starting Docker..."
  # Headless engine start (no dashboard window); fall back to a hidden app launch.
  docker desktop start >>"$LOG" 2>&1 || open -g -j -a Docker
  for _ in $(seq 1 90); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
  docker info >/dev/null 2>&1 || fail "Docker did not start"
fi

notify "Starting app..."
docker compose up -d --build >>"$LOG" 2>&1 || fail "docker compose failed"

for _ in $(seq 1 60); do
  curl -fs "$URL/api/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fs "$URL/api/health" >/dev/null 2>&1 || fail "App did not become healthy"

open -na "Google Chrome" --args --app="$URL"
