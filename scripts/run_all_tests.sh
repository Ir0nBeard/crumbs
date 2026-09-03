#!/usr/bin/env bash
# Run the full Crumbs test suite (server + MCP + SDK).
# Works from a fresh clone: creates/uses .venv if present, otherwise the
# active Python 3 interpreter (deps must be installed — see QUICKSTART.md).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="${PYTHON:-python3}"
fi

echo "== server + mcp (pytest) =="
"$PY" -m pytest tests/ -q

echo
echo "== sdk (node --test) =="
(cd sdk && node --test test/sdk.test.mjs)

echo
echo "ALL GREEN"
