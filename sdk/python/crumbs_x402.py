"""Crumbs Python x402 interop kit — zero-dependency helpers for x402 sellers.

The JS SDK (``sdk/src/crumbs-core.cjs``) emits the x402 ``PAYMENT-RESPONSE``
referral field and the ERC-8021 builder code. This module is the Python
equivalent for server-side x402 sellers and workers (pure stdlib — import
it anywhere, no pip install):

    from crumbs_x402 import x402_referral_field, builder_code

    # on the seller's 402 challenge / paid response, when the seller holds a
    # Crumbs journey receipt for the paying agent:
    ref = x402_referral_field(receipt_wire)          # {"referral": {...}}
    code = builder_code()                            # "bc_crumbs"

Helpers mirror the JS SDK semantics exactly (see sdk tests:
``sdk/test/sdk.test.mjs`` "carriers" case):

  * ``x402_referral_field`` returns None when no receipt is held (or the
    wire string is unparseable) — never a half-built object.
  * the journey id (``jrn_…``) is the default referral ref (the
    cross-merchant stitching key); pass ``refer="rid"`` to use the
    single-receipt id.
  * ``builder_code()`` returns the registered ERC-8021 service code
    ``bc_crumbs`` (``/^[a-z0-9_]{1,32}$/``).

Also included: did:pkh helpers (agent anchoring) and a tiny ledger client
for journey issuance/verification, so a Python seller can request a journey
for the paying agent (consent-gated), carry the receipt, and echo it back.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

__version__ = "0.1.0"

# --- receipt wire parsing ---------------------------------------------------
# The signed receipt is a JCS-canonical JSON object (docs/ATTRIBUTION_PROTOCOL.md).
# Parsing here is deliberately lenient (field access only) — the LEDGER is the
# signature authority; this client never needs to validate the HMAC itself.

_REQUIRED_ID_FIELDS = ("rid", "jid", "aid", "mid")


def parse_receipt_wire(receipt_wire: str | None) -> dict | None:
    """Parse a receipt wire string into a dict, or None when unparseable."""
    if not receipt_wire:
        return None
    try:
        obj = json.loads(receipt_wire)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    # The id fields must be present non-empty strings; value fields (v, cv,
    # iat, exp, kid, crb, ntb) are ints on the wire. The LEDGER is the
    # signature authority — this client only needs the ids it echoes.
    if not all(isinstance(obj.get(f), str) and obj.get(f)
               for f in _REQUIRED_ID_FIELDS):
        return None
    return obj


# --- x402 PAYMENT-RESPONSE referral field (mirrors JS SDK getX402ReferralField)
def x402_referral_field(receipt_wire: str | None, *, refer: str = "jid") -> dict | None:
    """Emit the x402 referral field for a held Crumbs receipt.

    Shape (cross-vendor x402 referral attribution, docs/ATTRIBUTION_PROTOCOL.md):
        {"referral": {"ref": "<jid|rid>", "provider": "crumbs"}}

    ``refer`` selects the echoed id: "jid" (default — journey id, the
    cross-merchant stitching key) or "rid" (single receipt id).
    Returns None when no receipt is held or the wire is unparseable.
    """
    parsed = parse_receipt_wire(receipt_wire)
    if parsed is None:
        return None
    ref = parsed.get("rid") if refer == "rid" else parsed.get("jid")
    if not ref:
        return None
    return {"referral": {"ref": ref, "provider": "crumbs"}}


# --- ERC-8021 builder code ---------------------------------------------------
_BUILDER_CODE = "bc_crumbs"
_BUILDER_CODE_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def builder_code() -> str:
    """Registered ERC-8021 Schema 2 service code (appended to settlement
    calldata by the facilitator; verified by the ledger)."""
    return _BUILDER_CODE


def is_valid_builder_code(code: str) -> bool:
    return bool(_BUILDER_CODE_RE.match(code))


# --- did:pkh helpers (agent anchoring; mirrors server/app/core/did.py) ------
DID_PREFIX = "did:pkh:"
_DID_PKH_RE = re.compile(r"^did:pkh:[a-z0-9]+:[a-zA-Z0-9]+:[a-zA-Z0-9]+$")
_EIP155_ACCOUNT_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
MAX_REGISTRY_REF = 255


def is_did_pkh(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if len(value) > MAX_REGISTRY_REF or not value.startswith(DID_PREFIX):
        return False
    parts = value.split(":")
    if len(parts) != 5:
        return False
    if not _DID_PKH_RE.match(value):
        return False
    if parts[2] == "eip155" and not _EIP155_ACCOUNT_RE.match(parts[4]):
        return False
    return True


def build_did_pkh(namespace: str, reference: str, account: str) -> str:
    did = f"did:pkh:{namespace}:{reference}:{account}"
    if not is_did_pkh(did):
        raise ValueError("cannot build did:pkh from those parts")
    return did


# --- minimal ledger client ---------------------------------------------------
# Mirrors the MCP server's HTTP pattern (mcp/crumbs_mcp.py) — urllib only,
# no framework. Journey issuance is CONSENT-GATED server-side; the client
# simply forwards the consent basis the agent/user recorded.

DEFAULT_TIMEOUT = 15


class CrumbsLedgerError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(f"ledger returned {status}: {detail}")
        self.status = status
        self.detail = detail


class CrumbsLedgerClient:
    """Thin client over the Crumbs ledger API (journeys + verify).

    Configure with the base URL of the ledger instance you run — there is
    deliberately NO default endpoint (mirrors the SDK's no-default rule).
    """

    def __init__(self, api_url: str, *, timeout: float = DEFAULT_TIMEOUT):
        if not api_url:
            raise ValueError("crumbs: api_url is required — configure the ledger base URL")
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.api_url + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode())
            except Exception:  # noqa: BLE001
                detail = {"error": exc.reason}
            raise CrumbsLedgerError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise CrumbsLedgerError(0, {"error": f"cannot reach ledger: {exc.reason}"}) from exc

    def request_journey(self, merchant_id: str, *, surface: str = "api",
                        consent: dict | None = None,
                        agent_id: str | None = None) -> dict:
        """Consent-gated receipt issuance (POST /v1/journeys)."""
        body = {
            "merchant_id": merchant_id,
            "surface": surface,
            "consent": consent or {"basis": "explicit", "ref": "crumbs-python-sdk"},
            "agent_id": agent_id,
        }
        if agent_id is None:
            del body["agent_id"]
        return self._request("POST", "/v1/journeys", body)

    def verify_receipt(self, receipt_wire: str) -> dict:
        """Receipt status check (POST /v1/verify — body, never a URL)."""
        return self._request("POST", "/v1/verify", {"receipt": receipt_wire})
