"""MCP server smoke tests — drive the stdio JSON-RPC loop as a subprocess.

Network-touching tool calls (request_journey / declare_conversion) are covered
by the server API + SDK suites; here we verify the MCP protocol surface:
initialize, tools/list, tools/call dispatch (including the error path), ping.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP = REPO_ROOT / "mcp" / "crumbs_mcp.py"


def _session(messages: list[dict], env: dict | None = None) -> list[dict]:
    """Feed JSON-RPC messages to the MCP server over stdin; return responses."""
    payload = "".join(json.dumps(m) + "\n" for m in messages)
    proc_env = {"CRUMBS_MCP_API_URL": "http://127.0.0.1:9",  # unreachable — tool calls error fast
                "CRUMBS_MCP_MERCHANT_ID": "m_testmcp"}
    if env:
        proc_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(MCP)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        env=proc_env,
    )
    assert proc.returncode == 0, proc.stderr
    out = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return out


def test_initialize_and_tools_list():
    responses = _session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    assert responses[0]["result"]["serverInfo"]["name"] == "crumbs-journey"
    tools = {t["name"] for t in responses[1]["result"]["tools"]}
    assert tools == {"request_journey", "verify_receipt", "declare_conversion"}


def test_tools_call_unknown_tool_returns_error():
    responses = _session([
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
    ])
    assert responses[0]["result"]["isError"] is True
    assert "unknown tool" in responses[0]["result"]["content"][0]["text"]


def test_ping():
    responses = _session([{"jsonrpc": "2.0", "id": 4, "method": "ping"}])
    assert responses[0]["result"] == {}


def test_parse_error_returns_jsonrpc_error():
    proc = subprocess.run(
        [sys.executable, str(MCP)],
        input="not-json\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    msg = json.loads(proc.stdout.strip())
    assert msg["error"]["code"] == -32700


def test_tool_call_to_unreachable_ledger_reports_cleanly():
    """A network failure surfaces as isError content, not a crash."""
    responses = _session([
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "request_journey",
                    "arguments": {"merchant_id": "m_x", "surface": "api",
                                  "consent": {"basis": "explicit"}}}},
    ])
    assert responses[0]["result"]["isError"] is True
    assert "cannot reach ledger" in responses[0]["result"]["content"][0]["text"]


def test_verify_receipt_posts_to_v1_verify():
    """verify_receipt POSTs the bearer receipt in the BODY — never a GET query
    string (regression guard). The fake ledger asserts the exact request shape
    the MCP server must emit."""
    import http.server
    import threading

    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            seen["method"] = "POST"
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(length) or b"{}")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"valid": true}')

        def log_message(self, *args):  # keep test output clean
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        responses = _session([
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": "verify_receipt",
                        "arguments": {"receipt": "signed.wire.value"}}},
        ], env={"CRUMBS_MCP_API_URL": url})
        # Success shape: isError is only present (True) on failures.
        assert responses[0]["result"].get("isError") is not True
        assert seen["method"] == "POST"
        assert seen["path"] == "/v1/verify"
        assert seen["body"] == {"receipt": "signed.wire.value"}
    finally:
        srv.shutdown()
        srv.server_close()
