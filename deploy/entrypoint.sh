#!/usr/bin/env bash
set -euo pipefail

nginx -g 'daemon off;' &
nginx_pid=$!

jira-track-web &
backend_pid=$!

_term() {
  kill -TERM "$backend_pid" "$nginx_pid" 2>/dev/null || true
  wait "$backend_pid" "$nginx_pid" 2>/dev/null || true
}

trap _term TERM INT

wait -n "$backend_pid" "$nginx_pid"
exit_code=$?

kill -TERM "$backend_pid" "$nginx_pid" 2>/dev/null || true
wait "$backend_pid" "$nginx_pid" 2>/dev/null || true

exit "$exit_code"
