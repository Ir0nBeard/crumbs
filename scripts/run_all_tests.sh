#!/usr/bin/env bash
# Run the full Crumbs MVP test suite (server + MCP + SDK).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== server + mcp (pytest) =="
.venv/bin/python -m pytest tests/ -q

echo
echo "== sdk (node --test) =="
(cd sdk && node --test test/sdk.test.mjs)

echo
echo "ALL GREEN"
