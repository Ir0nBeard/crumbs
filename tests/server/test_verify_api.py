"""GET /v1/verify — receipt status check across states."""
from __future__ import annotations

import json

from app.core.receipt import parse_receipt


def _verify(client, receipt):
    return client.get("/v1/verify", params={"receipt": receipt})


def test_verify_valid(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    resp = _verify(client, journey["receipt"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["rid"] == journey["rid"]
    assert body["journey"]["max_conversions"] == 5
    assert body["journey"]["conversions_used"] == 0


def test_verify_reflects_budget_after_conversion(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    client.post(
        "/v1/conversions",
        json={
            "receipt": journey["receipt"],
            "merchant_id": seeded_merchant.mid,
            "order_id": "ord_v1",
            "cart_value_minor_units": 2500,
            "currency": "USD",
        },
        headers={
            "X-Crumbs-Key": "test-merchant-key",
            "Idempotency-Key": f"{journey['rid']}:ord_v1",
        },
    )
    body = _verify(client, journey["receipt"]).json()
    # The receipt was redeemed (nonce spent) so it is no longer redeemable —
    # but journey budget counters remain visible for diagnostics.
    assert body["valid"] is False
    assert body["reason"] == "REPLAYED_NONCE"
    assert body["journey"]["conversions_used"] == 1
    assert body["journey"]["cart_value_used_usd"] == 2500


def test_verify_replayed_after_conversion(client, seeded_merchant, issue_receipt):
    """Once the nonce is spent, verify reports REPLAYED (used nonce)."""
    journey = issue_receipt()
    client.post(
        "/v1/conversions",
        json={
            "receipt": journey["receipt"],
            "merchant_id": seeded_merchant.mid,
            "order_id": "ord_v2",
            "cart_value_minor_units": 100,
            "currency": "USD",
        },
        headers={"X-Crumbs-Key": "test-merchant-key"},
    )
    body = _verify(client, journey["receipt"]).json()
    assert body["valid"] is False
    assert body["reason"] == "REPLAYED_NONCE"


def test_verify_revoked(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    client.post(
        "/v1/admin/revoke",
        json={"kind": "receipt", "id": journey["rid"], "reason": "test"},
        headers={"X-Crumbs-Admin-Token": "test-admin-token"},
    )
    body = _verify(client, journey["receipt"]).json()
    assert body["valid"] is False
    assert body["reason"] == "REVOKED_RECEIPT"


def test_verify_tampered(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    tampered = json.loads(journey["receipt"])
    tampered["aid"] = "ag_evil00000000000000"
    wire = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    body = _verify(client, wire).json()
    assert body["valid"] is False
    assert body["reason"] == "BAD_SIGNATURE"


def test_verify_post_body(client, seeded_merchant, issue_receipt):
    """POST /v1/verify — the canonical verify call (P3 D-M6)."""
    journey = issue_receipt()
    resp = client.post("/v1/verify", json={"receipt": journey["receipt"]})
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_verify_malformed(client):
    body = _verify(client, "not-json").json()
    assert body["valid"] is False
    assert body["reason"] == "MALFORMED_RECEIPT"
