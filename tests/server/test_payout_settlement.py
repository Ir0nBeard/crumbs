"""Settlement-proof recording (x402 rail): full conversion flow through
payout scheduling, then admin recording of an executed rail settlement —
on-chain mode (ERC-8021 calldata proof) and attestation mode — plus every
rejection path. Money never moves in these tests (or anywhere in the
ledger); the endpoints record proofs of off-ledger rail settlements.
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

TX_HASH = "0x" + "ab" * 32


def webhook_sig(body: bytes, secret: str = DEFAULT_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_webhook(client, payload: dict, secret: str = DEFAULT_WEBHOOK_SECRET):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/v1/webhooks/orders",
        content=body,
        headers={"X-Crumbs-Signature": webhook_sig(body, secret)},
    )


def stamp(client, receipt, oid="ord_pay", value=100_00, currency="USD"):
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


def _finalized_payout(client, issue_receipt, oid="ord_pay", value=100_00):
    """Full path to a scheduled payout; returns (client, payout_pid)."""
    from app.db.session import get_session_factory

    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid=oid, value=value)
    assert conv.status_code == 201, conv.text
    cid = conv.json()["conversion_id"]

    webhook = post_webhook(client, {"conversion_id": cid, "order_status": "finalized",
                                    "final_cart_value_minor_units": value, "t": int(time.time())})
    assert webhook.status_code == 200, webhook.text

    batch = client.post("/v1/payouts/batch", json={"limit": 100}, headers=ADMIN)
    assert batch.status_code == 200, batch.text
    assert batch.json()["scheduled"] == 1

    db = get_session_factory()()
    try:
        from app.db.models import Payout

        return db.execute(select(Payout)).scalars().all()[0].pid
    finally:
        db.close()


def crumbs_calldata(extra_code=None):
    """Settlement calldata whose ERC-8021 suffix carries bc_crumbs."""
    attribution = {"a": "bc_some_app", "s": ["bc_crumbs"]}
    if extra_code:
        attribution["s"].append(extra_code)
    return "0xdeadbeef" + encode_builder_code_suffix(attribution)[2:]


def test_settle_onchain_proof_happy_path(client, seeded_merchant, issue_receipt, db_session):
    pid = _finalized_payout(client, issue_receipt)
    assert pid.startswith("p_")

    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata(),
              "referral_ref": "jrn_01M169J4VN8PHRJ78VS5ZD7TWQY",
              "rail_ref": "fct_987654"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "settled"
    assert data["proof_mode"] == "onchain"
    assert data["builder_code"] == "bc_crumbs"
    assert data["tx_hash"] == TX_HASH
    assert data["referral_ref"] == "jrn_01M169J4VN8PHRJ78VS5ZD7TWQY"
    assert data["rail_ref"] == "fct_987654"
    assert data["asset"] == "USDC"
    assert data["network"] == "eip155:8453"
    assert data["attribution"] == {"a": "bc_some_app", "s": ["bc_crumbs"]}
    assert "settled_at" in data

    # Proof envelope via GET
    detail = client.get(f"/v1/payouts/{pid}", headers=ADMIN)
    assert detail.status_code == 200
    env = detail.json()
    assert env["status"] == "settled"
    assert env["proof_mode"] == "onchain"
    assert env["tx_hash"] == TX_HASH
    parties = {s["party"]: s for s in env["splits"]}
    assert set(parties) == {"agent", "owner", "network"}
    assert all(s["status"] == "settled" for s in env["splits"])

    # Append-only audit trail
    from app.db.models import AuditEvent

    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.entity_id == pid)
    ).scalars().all()
    types = [e.event_type for e in events]
    assert "payout_scheduled" in types
    assert "payout_settled" in types
    settled = next(e for e in events if e.event_type == "payout_settled")
    payload = json.loads(settled.payload)
    assert payload["proof_mode"] == "onchain"
    assert payload["builder_code"] == "bc_crumbs"
    assert payload["tx_hash"] == TX_HASH


def test_settle_attestation_mode_without_calldata(client, seeded_merchant, issue_receipt):
    pid = _finalized_payout(client, issue_receipt, oid="ord_att")
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "rail_ref": "fct_attest_001"},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "settled"
    assert data["proof_mode"] == "attestation"  # labelled, never sold as on-chain
    assert data["attribution"] is None
    assert data["rail_ref"] == "fct_attest_001"


def test_settle_rejects_calldata_without_bc_crumbs(client, seeded_merchant, issue_receipt):
    pid = _finalized_payout(client, issue_receipt, oid="ord_nocrumb")
    # Suffix present but carries only an unrelated app code -> mismatch
    other = "0xdeadbeef" + encode_builder_code_suffix({"a": "bc_other_app"})[2:]
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": other},
        headers=ADMIN,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "ATTRIBUTION_MISMATCH"
    # No suffix at all -> not found
    resp2 = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": "0x" + "ef" * 32, "calldata": "0xdeadbeef"},
        headers=ADMIN,
    )
    assert resp2.status_code == 422
    assert resp2.json()["detail"]["code"] == "ATTRIBUTION_NOT_FOUND"


def test_settle_rejects_builder_code_not_proven_by_calldata(
    client, seeded_merchant, issue_receipt
):
    pid = _finalized_payout(client, issue_receipt, oid="ord_mismatch")
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata(),
              "builder_code": "bc_evil"},
        headers=ADMIN,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "ATTRIBUTION_MISMATCH"


def test_settle_rejects_malformed_calldata(client, seeded_merchant, issue_receipt):
    pid = _finalized_payout(client, issue_receipt, oid="ord_badcal")
    # length field 0xffff with no CBOR to back it
    bad = "0x" + "ffff" + "02" + "80218021802180218021802180218021"
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": bad},
        headers=ADMIN,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_CALLDATA"


def test_settle_rejects_bad_tx_hash(client, seeded_merchant, issue_receipt):
    pid = _finalized_payout(client, issue_receipt, oid="ord_badtx")
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": "0x1234"},  # not 64 hex
        headers=ADMIN,
    )
    assert resp.status_code == 422


def test_settle_rejects_unknown_payout(client, seeded_merchant):
    resp = client.post(
        "/v1/payouts/p_doesnotexist/settlement",
        json={"tx_hash": TX_HASH},
        headers=ADMIN,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "PAYOUT_NOT_FOUND"


def test_settle_only_once(client, seeded_merchant, issue_receipt):
    pid = _finalized_payout(client, issue_receipt, oid="ord_twice")
    first = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata()},
        headers=ADMIN,
    )
    assert first.status_code == 200
    second = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": "0x" + "cd" * 32, "calldata": crumbs_calldata()},
        headers=ADMIN,
    )
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "PAYOUT_NOT_SCHEDULED"


def test_get_unknown_payout_404(client, seeded_merchant):
    resp = client.get("/v1/payouts/p_doesnotexist", headers=ADMIN)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "PAYOUT_NOT_FOUND"


def test_settlement_requires_admin_token(client, seeded_merchant, issue_receipt):
    pid = _finalized_payout(client, issue_receipt, oid="ord_noauth")
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata()},
    )
    assert resp.status_code == 401
    bad = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata()},
        headers={"X-Crumbs-Admin-Token": "wrong"},
    )
    assert bad.status_code == 401
    detail = client.get(f"/v1/payouts/{pid}")
    assert detail.status_code == 401


def test_settlement_disabled_without_configured_token(client, seeded_merchant, issue_receipt, monkeypatch):
    from app.config import get_settings

    pid = _finalized_payout(client, issue_receipt, oid="ord_disabled")
    monkeypatch.setattr(get_settings(), "admin_token", "")
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata()},
        headers=ADMIN,
    )
    assert resp.status_code == 501
    assert resp.json()["detail"]["code"] == "ADMIN_DISABLED"


def test_settlement_rejects_bad_referral_ref(client, seeded_merchant, issue_receipt):
    pid = _finalized_payout(client, issue_receipt, oid="ord_badref")
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata(),
              "referral_ref": "evt_evil_referral"},
        headers=ADMIN,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_REFERRAL_REF"


def test_settlement_records_split_totals_match_schedule(
    client, seeded_merchant, issue_receipt, db_session
):
    pid = _finalized_payout(client, issue_receipt, oid="ord_split", value=100_00)
    resp = client.post(
        f"/v1/payouts/{pid}/settlement",
        json={"tx_hash": TX_HASH, "calldata": crumbs_calldata()},
        headers=ADMIN,
    )
    assert resp.status_code == 200
    detail = client.get(f"/v1/payouts/{pid}", headers=ADMIN).json()
    amounts = {s["party"]: s["amount_minor_units"] for s in detail["splits"]}
    # $100 * 12% = $12.00 commission; 15% network take = $1.80; net $10.20
    # owner 20% = $2.04, agent 80% = $8.16 (same math as test_conversions_api)
    assert amounts == {"network": 180, "owner": 204, "agent": 816}
