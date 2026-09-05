"""Python x402 interop kit — pure-helper parity with the JS SDK.

Imports the standalone module from sdk/python/ (zero dependencies — no app
imports), so this suite also proves the kit is embeddable by any Python x402
seller without the ledger server installed.

JS parity reference: sdk/test/sdk.test.mjs "carriers: header value, x402
referral field, builder code".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "sdk" / "python"))

import crumbs_x402  # noqa: E402
from crumbs_x402 import (  # noqa: E402
    CrumbsLedgerClient,
    CrumbsLedgerError,
    builder_code,
    build_did_pkh,
    is_did_pkh,
    parse_receipt_wire,
    x402_referral_field,
)

# JS SDK fixture parity (sdk/test/sdk.test.mjs RECEIPT)
RECEIPT = {
    "v": 1,
    "rid": "rct_01M169J4VN8PHRJ78VS5ZD7TWQX",
    "jid": "jrn_01M169J4VN8PHRJ78VS5ZD7TWQY",
    "aid": "ag_testagent1234567890",
    "mid": "m_testmerchant1234567",
    "oid": "",
    "cv": 0,
    "cur": "USD",
    "crb": 1200,
    "ntb": 1500,
    "sf": "browser",
    "nc": "aGVsbG8td29ybGQtdGVzdA",
    "iat": 1787991495,
    "exp": 1790583495,
    "kid": 1,
    "sig": "test-signature-placeholder-not-validated-by-sdk",
}
RECEIPT_WIRE = __import__("json").dumps(RECEIPT, separators=(",", ":"))


def test_parse_receipt_wire_roundtrip():
    parsed = parse_receipt_wire(RECEIPT_WIRE)
    assert parsed == RECEIPT


def test_parse_receipt_wire_none_on_garbage():
    assert parse_receipt_wire(None) is None
    assert parse_receipt_wire("") is None
    assert parse_receipt_wire("not-json") is None
    assert parse_receipt_wire("[]") is None  # not a dict
    assert parse_receipt_wire('{"rid": 1}') is None  # missing string fields


def test_x402_referral_field_parity_with_js():
    # No receipt -> null, never a half-built object (JS parity)
    assert x402_referral_field(None) is None
    assert x402_referral_field("not-json") is None
    # jid is the default referral ref (cross-merchant stitching key)
    assert x402_referral_field(RECEIPT_WIRE) == {
        "referral": {"ref": RECEIPT["jid"], "provider": "crumbs"}}
    # rid is opt-in
    assert x402_referral_field(RECEIPT_WIRE, refer="rid") == {
        "referral": {"ref": RECEIPT["rid"], "provider": "crumbs"}}


def test_builder_code_format():
    code = builder_code()
    assert code == "bc_crumbs"
    assert crumbs_x402.is_valid_builder_code(code)
    assert crumbs_x402.is_valid_builder_code("bc_crumbs") is True
    assert crumbs_x402.is_valid_builder_code("Bad Code!") is False


def test_did_pkh_helpers():
    assert is_did_pkh("did:pkh:eip155:8453:0xAbCdef0123456789abcdef0123456789abcdef01")
    # eip155 accounts must be 0x + 40 hex
    assert not is_did_pkh("did:pkh:eip155:8453:nothex")
    assert not is_did_pkh("did:web:example.com")  # not pkh
    assert not is_did_pkh("did:pkh:eip155:1:0x123")  # short account
    assert not is_did_pkh(None)
    assert not is_did_pkh("")
    did = build_did_pkh("eip155", "8453", "0xAbCdef0123456789abcdef0123456789abcdef01")
    assert is_did_pkh(did)
    assert did == "did:pkh:eip155:8453:0xAbCdef0123456789abcdef0123456789abcdef01"


def test_ledger_client_requires_api_url():
    with pytest.raises(ValueError):
        CrumbsLedgerClient("")
    with pytest.raises(ValueError):
        CrumbsLedgerClient(None)  # type: ignore[arg-type]


def test_ledger_client_error_shape():
    """Unreachable ledger -> CrumbsLedgerError (fail loud, not silent)."""
    client = CrumbsLedgerClient("http://127.0.0.1:1", timeout=1)
    with pytest.raises(CrumbsLedgerError):
        client.request_journey("m_x")
    with pytest.raises(CrumbsLedgerError):
        client.verify_receipt(RECEIPT_WIRE)
