#!/usr/bin/env bash
# Run the full Crumbs test suite (server + MCP + SDK + browser-extension headless).
# Works from a fresh clone: creates/uses .venv if present, otherwise the
# active Python 3 interpreter (deps must be installed — see QUICKSTART.md).
# Node >= 18 is required for the SDK and extension suites; if node is not on
# your PATH (e.g. apt nodejs is too old), add a modern one first, e.g.
#   export PATH="$HOME/node/bin:$PATH"
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
echo "== extension (node --test, headless) =="
node --test tests/ext/*.test.mjs

echo
echo "ALL GREEN"
