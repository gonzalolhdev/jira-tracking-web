#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Error: project virtualenv not found at .venv/bin/python" >&2
  echo "Create it with: python3.13 -m venv .venv && .venv/bin/python -m pip install -e .[dev]" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m pytest "$@"
