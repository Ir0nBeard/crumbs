#!/usr/bin/env python3
"""Crumbs MCP server — minimal stdio implementation (MCP JSON-RPC 2.0).

Tools (thin wrappers over the Crumbs ledger API, docs/ATTRIBUTION_PROTOCOL.md):
  request_journey     consent-gated receipt issuance (POST /v1/journeys)
  verify_receipt      receipt status check (POST /v1/verify — bearer never in URL)
  declare_conversion  idempotent conversion stamping (POST /v1/conversions)

Pure stdlib (urllib) — no framework dependency, so it runs in any Python
3.10+ interpreter. Not yet published to any registry (v0.1).

Config (env) — required:
  CRUMBS_MCP_API_URL      base URL of the Crumbs ledger instance you run
                          (no default endpoint; tools error until set)
  CRUMBS_MCP_MERCHANT_ID  merchant id (m_...) used when a tool call omits one
                          (optional — pass merchant_id per call instead)
Optional:
  CRUMBS_MCP_API_KEY      X-Crumbs-Key for conversion posts
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = os.environ.get("CRUMBS_MCP_API_URL", "").rstrip("/")
MERCHANT_ID = os.environ.get("CRUMBS_MCP_MERCHANT_ID", "")
API_KEY = os.environ.get("CRUMBS_MCP_API_KEY", "")

TOOLS = [
    {
        "name": "request_journey",
        "description": "Request a signed-attribution receipt (journey start). "
                       "Consent-gated: pass consent.basis (gpp|tcf|explicit|88b).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "merchant_id": {"type": "string", "description": "Merchant id (m_...)"},
                "surface": {"type": "string", "enum": ["browser", "api", "chat"],
                            "default": "api"},
                "consent": {
                    "type": "object",
                    "properties": {
                        "basis": {"type": "string", "enum": ["gpp", "tcf", "explicit", "88b"]},
                        "ref": {"type": "string"},
                    },
                    "required": ["basis"],
                },
            },
            "required": ["consent"],
        },
    },
    {
        "name": "verify_receipt",
        "description": "Verify a receipt's current status (signature, expiry, "
                       "revocation, nonce, journey budget state).",
        "inputSchema": {
            "type": "object",
            "properties": {"receipt": {"type": "string"}},
            "required": ["receipt"],
        },
    },
    {
        "name": "declare_conversion",
        "description": "Stamp an attributed conversion at checkout (idempotent "
                       "on receipt rid + order id).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "receipt": {"type": "string"},
                "order_id": {"type": "string"},
                "cart_value_minor_units": {"type": "integer"},
                "currency": {"type": "string", "default": "USD"},
            },
            "required": ["receipt", "order_id", "cart_value_minor_units", "currency"],
        },
    },
]


def _http(method: str, path: str, payload: dict | None = None) -> dict:
    if not API_URL:
        raise MCPToolError(
            "ledger not configured — set CRUMBS_MCP_API_URL to the ledger base "
            "URL (there is no default endpoint)"
        )
    url = API_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    if API_KEY:
        req.add_header("X-Crumbs-Key", API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            detail = {"error": exc.reason}
        raise MCPToolError(f"ledger returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise MCPToolError(f"cannot reach ledger at {API_URL}: {exc.reason}") from exc


class MCPToolError(Exception):
    pass


def call_tool(name: str, args: dict) -> dict:
    """Dispatch a tool call; returns MCP content blocks (text)."""
    try:
        if name == "request_journey":
            mid = args.get("merchant_id") or MERCHANT_ID
            if not mid:
                raise MCPToolError(
                    "merchant not configured — pass merchant_id or set "
                    "CRUMBS_MCP_MERCHANT_ID"
                )
            result = _http("POST", "/v1/journeys", {
                "merchant_id": mid,
                "surface": args.get("surface", "api"),
                "consent": args.get("consent"),
            })
            # The full receipt IS echoed intentionally: it is the carrier value
            # the calling agent needs to stamp the conversion later (the ledger
            # issues it, the agent carries it). Only ids are echoed for journeys
            # issued without an explicit request for the wire form.
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "rid": result.get("rid"),
                        "journey_id": result.get("journey_id"),
                        "agent_id": result.get("agent_id"),
                        "exp": result.get("exp"),
                        "receipt": result.get("receipt"),  # carrier value for stamping
                    }, sort_keys=True),
                }]
            }
        if name == "verify_receipt":
            # POST /v1/verify — the signed bearer receipt travels in the BODY,
            # never in a query string (docs/ATTRIBUTION_PROTOCOL.md §5).
            result = _http("POST", "/v1/verify", {"receipt": args["receipt"]})
            return {"content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}]}
        if name == "declare_conversion":
            mid = args.get("merchant_id") or MERCHANT_ID
            if not mid:
                raise MCPToolError(
                    "merchant not configured — pass merchant_id or set "
                    "CRUMBS_MCP_MERCHANT_ID"
                )
            result = _http("POST", "/v1/conversions", {
                "receipt": args["receipt"],
                "merchant_id": mid,
                "order_id": args["order_id"],
                "cart_value_minor_units": args["cart_value_minor_units"],
                "currency": args.get("currency", "USD"),
            })
            return {"content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}]}
        raise MCPToolError(f"unknown tool: {name}")
    except MCPToolError as exc:
        return {
            "isError": True,
            "content": [{"type": "text", "text": "crumbs: " + str(exc)}],
        }


# ---------------------------------------------------------------------------
# MCP JSON-RPC loop (stdio)
# ---------------------------------------------------------------------------

def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send({"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"},
                   "id": None})
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "crumbs-journey", "version": "0.1.0"},
                },
            })
        elif method == "notifications/initialized":
            pass  # no reply expected
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params", {})
            result = call_tool(params.get("name", ""), params.get("arguments", {}))
            _send({"jsonrpc": "2.0", "id": rid, "result": result})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            _send({"jsonrpc": "2.0", "id": rid,
                   "error": {"code": -32601, "message": f"method not found: {method}"}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
