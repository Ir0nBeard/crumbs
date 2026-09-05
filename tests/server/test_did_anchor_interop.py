"""did:pkh agent anchoring + x402 referral-settlement interop.

Proves the seller/worker interop loop end-to-end against the real API:

1. A journey is issued for an agent anchored to a did:pkh (CAIP-10 wallet
   identity) — the anchor lands in agents.registry_ref and is echoed in the
   issuance + verify responses, so receipts are provably tied to one
   on-chain identity.
2. Repeated journeys for the SAME did resolve to the SAME agent id
   (cross-merchant stitching).
3. A Python-style seller flow: conversion -> finalized -> payout scheduled ->
   settlement recorded with the x402 referral ref (journey id echoed from a
   PAYMENT-RESPONSE) + ERC-8021 bc_crumbs calldata -> on-chain proof envelope.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from sqlalchemy import select

from app.core.buildercode import encode_builder_code_suffix
from app.seed import DEFAULT_WEBHOOK_SECRET

ADMIN = {"X-Crumbs-Admin-Token": "test-admin-token"}

DID_A = "did:pkh:eip155:8453:0xAbCdef0123456789abcdef0123456789abcdef01"
DID_B = "did:pkh:eip155:8453:0x1111111111111111111111111111111111111111"
DID_SOL = "did:pkh:solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" \
          ":7a8T3xTqLnF6Kc9xYQhPqYyXkZxVvNnMmJjHhGgFfDdSs"


def _issue_with_did(client, merchant_id: str, agent_did: str, surface: str = "api"):
    return client.post(
        "/v1/journeys",
        json={
            "merchant_id": merchant_id,
            "surface": surface,
            "consent": {"basis": "explicit", "ref": "interop-test"},
            "agent_did": agent_did,
        },
    )


def webhook_sig(body: bytes, secret: str = DEFAULT_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_webhook(client, payload: dict, secret: str = DEFAULT_WEBHOOK_SECRET):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/v1/webhooks/orders",
        content=body,
        headers={"X-Crumbs-Signature": webhook_sig(body, secret)},
    )


def stamp(client, receipt, oid="ord_interop", value=100_00, currency="USD"):
    from app.core.receipt import parse_receipt

    payload = parse_receipt(receipt)
    body = {
        "receipt": receipt,
        "merchant_id": payload["mid"],
        "order_id": oid,
        "cart_value_minor_units": value,
        "currency": currency,
    }
    headers = {
        "X-Crumbs-Key": "test-merchant-key",
        "Idempotency-Key": f"{payload['rid']}:{oid}",
    }
    return client.post("/v1/conversions", json=body, headers=headers)


# --- issuance + anchoring ---------------------------------------------------


def test_journey_issued_for_did_anchored_agent(client, seeded_merchant):
    resp = _issue_with_did(client, seeded_merchant.mid, DID_A)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["agent_id"].startswith("ag_")
    assert data["agent_did"] == DID_A

    # Verify echoes the anchor — the receipt is provably tied to the identity.
    v = client.post("/v1/verify", json={"receipt": data["receipt"]})
    assert v.status_code == 200
    body = v.json()
    assert body["valid"] is True
    assert body["agent_did"] == DID_A


def test_same_did_resolves_to_same_agent(client, seeded_merchant):
    """Repeated journeys for one did must stitch to the same agent."""
    r1 = _issue_with_did(client, seeded_merchant.mid, DID_B).json()
    r2 = _issue_with_did(client, seeded_merchant.mid, DID_B).json()
    assert r1["agent_id"] == r2["agent_id"]
    assert r1["agent_did"] == DID_B == r2["agent_did"]
    assert r1["journey_id"] != r2["journey_id"]  # distinct journeys, one agent


def test_did_anchor_is_shared_across_merchants(client, db_session, seeded_merchant):
    from app.seed import seed_merchant

    m2 = seed_merchant(db_session)
    r1 = _issue_with_did(client, seeded_merchant.mid, DID_SOL).json()
    r2 = _issue_with_did(client, m2.mid, DID_SOL).json()
    assert r1["agent_id"] == r2["agent_id"]
    assert r1["agent_did"] == DID_SOL == r2["agent_did"]


def test_rejects_malformed_did(client, seeded_merchant):
    for bad in ("did:web:example.com", "did:pkh:eip155:1:0xzz", "not-a-did",
                "did:pkh:eip155:8453:0x123"):
        resp = _issue_with_did(client, seeded_merchant.mid, bad)
        assert resp.status_code == 422, (bad, resp.text)
        assert resp.json()["detail"]["code"] == "INVALID_AGENT_DID"


def test_anchor_conflict_rejected(client, seeded_merchant):
    """An existing agent id cannot be rebound to a different did."""
    # Register agent under DID_A, then try the same agent row via agent_id with
    # a conflicting did -> 409.
    first = _issue_with_did(client, seeded_merchant.mid, DID_A).json()
    aid = first["agent_id"]
    resp = client.post(
        "/v1/journeys",
        json={
            "merchant_id": seeded_merchant.mid,
            "surface": "api",
            "consent": {"basis": "explicit", "ref": "conflict-test"},
            "agent_id": aid,
            "agent_did": DID_B,
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "AGENT_DID_CONFLICT"


def test_anchor_backfills_unset_agent(client, seeded_merchant, db_session):
    """Journeys issued before anchoring (plain agent_id) get anchored later."""
    from app.db.models import Agent

    # Issue an UNANCHORED journey via agent_id.
    r0 = client.post(
        "/v1/journeys",
        json={
            "merchant_id": seeded_merchant.mid,
            "surface": "api",
            "consent": {"basis": "explicit", "ref": "backfill"},
            "agent_id": "ag_backfillagent12345678",
        },
    ).json()
    assert r0["agent_did"] is None

    # Now anchor the same agent row.
    r1 = client.post(
        "/v1/journeys",
        json={
            "merchant_id": seeded_merchant.mid,
            "surface": "api",
            "consent": {"basis": "explicit", "ref": "backfill2"},
            "agent_id": "ag_backfillagent12345678",
            "agent_did": DID_B,
        },
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["agent_did"] == DID_B

    # Verify the anchor is now visible on old receipts too.
    v = client.post("/v1/verify", json={"receipt": r0["receipt"]})
    assert v.json()["agent_did"] == DID_B


# --- referral-settlement proof loop -----------------------------------------


def test_referral_settlement_end_to_end(client, seeded_merchant):
    """Full loop: anchored journey -> conversion -> payout -> bc_crumbs
    settlement with the x402 referral ref echoed -> on-chain proof envelope."""
    from app.db.session import get_session_factory
    from app.db.models import Payout

    # 1. Journey for the did-anchored agent (the paying worker identity).
    jr = _issue_with_did(client, seeded_merchant.mid, DID_A).json()
    assert jr["agent_did"] == DID_A
    receipt = jr["receipt"]

    # 2. The x402 PAYMENT-RESPONSE referral field carries the JOURNEY id
    #    (cross-merchant stitching key) — the value a facilitator echoes back.
    from app.core.receipt import parse_receipt

    parsed = parse_receipt(receipt)
    referral_ref = parsed["jid"]

    # 3. Conversion stamped + merchant confirmation -> finalized.
    conv = stamp(client, receipt, oid="ord_interop_1", value=50_00)
    assert conv.status_code == 201, conv.text
    cid = conv.json()["conversion_id"]
    wh = post_webhook(client, {
        "conversion_id": cid,
        "order_status": "finalized",
        "final_cart_value_minor_units": 50_00,
        "t": int(time.time()),
    })
    assert wh.status_code == 200, wh.text

    # 4. Payout batch schedules the finalized conversion.
    batch = client.post("/v1/payouts/batch", json={"limit": 100}, headers=ADMIN)
    assert batch.status_code == 200, batch.text
    assert batch.json()["scheduled"] == 1

    db = get_session_factory()()
    try:
        pid = db.execute(select(Payout)).scalars().all()[0].pid
    finally:
        db.close()

    # 5. Settlement proof with ERC-8021 bc_crumbs calldata + referral ref.
    tx = "0x" + "cd" * 32
    calldata = "0xdeadbeef" + encode_builder_code_suffix(
        {"a": "bc_some_app", "s": ["bc_crumbs"]})[2:]
    settle = client.post(f"/v1/payouts/{pid}/settlement", headers=ADMIN, json={
        "tx_hash": tx,
        "calldata": calldata,
        "builder_code": "bc_crumbs",
        "referral_ref": referral_ref,
        "rail_ref": "fac-12345",
        "asset": "USDC",
        "network": "eip155:8453",
    })
    assert settle.status_code == 200, settle.text
    body = settle.json()
    assert body["status"] == "settled"
    assert body["proof_mode"] == "onchain"
    assert body["referral_ref"] == referral_ref
    assert body["builder_code"] == "bc_crumbs"

    # 6. Proof envelope is readable and carries the anchor's identity lineage.
    detail = client.get(f"/v1/payouts/{pid}", headers=ADMIN)
    assert detail.status_code == 200
    d = detail.json()
    assert d["status"] == "settled"
    assert d["proof_mode"] == "onchain"
    assert d["referral_ref"] == referral_ref
