"""Conversion API: happy path, idempotency, replay, revocation, expiry,
bad signature, merchant webhook lock-in, payout scheduling."""
from __future__ import annotations

import hmac
import hashlib
import json
import time

from app.seed import DEFAULT_WEBHOOK_SECRET

ADMIN = {"X-Crumbs-Admin-Token": "test-admin-token"}


def webhook_sig(body: bytes, secret: str = DEFAULT_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def webhook_body(conversion_id=None, order_status="finalized", confirmed_value=None,
                 order_id=None, t=None):
    """Build a signed-ready webhook body dict (t defaults to now)."""
    payload = {
        "order_status": order_status,
        "t": int(t if t is not None else time.time()),
    }
    if conversion_id:
        payload["conversion_id"] = conversion_id
    if order_id:
        payload["order_id"] = order_id
    if confirmed_value is not None:
        payload["final_cart_value_minor_units"] = confirmed_value
    return payload


def post_webhook(client, payload: dict, secret: str = DEFAULT_WEBHOOK_SECRET):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/v1/webhooks/orders",
        content=body,
        headers={"X-Crumbs-Signature": webhook_sig(body, secret)},
    )


def stamp(client, receipt, oid="ord_1001", value=5000, currency="USD",
          surface=None, merchant_key="test-merchant-key", idem=True,
          merchant_id=None):
    from app.core.receipt import parse_receipt

    payload = parse_receipt(receipt)
    body = {
        "receipt": receipt,
        "merchant_id": merchant_id or payload["mid"],
        "order_id": oid,
        "cart_value_minor_units": value,
        "currency": currency,
    }
    if surface:
        body["surface"] = surface
    headers = {"X-Crumbs-Key": merchant_key}
    if idem:
        rid = payload["rid"]
        headers["Idempotency-Key"] = f"{rid}:{oid}"
    return client.post("/v1/conversions", json=body, headers=headers)


def webhook_sig(body: bytes, secret: str = DEFAULT_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_conversion_happy_path(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    resp = stamp(client, journey["receipt"], oid="ord_1", value=8000, currency="USD")
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["conversion_id"].startswith("c_")
    assert data["status"] == "pending"
    assert data["awaiting"] == "merchant order webhook (finalized|cancelled|refunded)"


def test_conversion_requires_merchant_key_when_configured(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    resp = client.post(
        "/v1/conversions",
        json={
            "receipt": journey["receipt"],
            "merchant_id": seeded_merchant.mid,
            "order_id": "ord_x",
            "cart_value_minor_units": 100,
            "currency": "USD",
        },
    )
    assert resp.status_code == 401


def test_conversion_idempotent_retry(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    first = stamp(client, journey["receipt"], oid="ord_retry", value=2500)
    assert first.status_code == 201
    second = stamp(client, journey["receipt"], oid="ord_retry", value=2500)
    assert second.status_code == 200
    assert second.json()["conversion_id"] == first.json()["conversion_id"]
    assert second.json()["idempotent"] is True


def test_conversion_rejects_reused_nonce_different_order(client, seeded_merchant, issue_receipt):
    """Same receipt, different oid -> nonce replay rejection."""
    journey = issue_receipt()
    assert stamp(client, journey["receipt"], oid="ord_a", value=100).status_code == 201
    resp = stamp(client, journey["receipt"], oid="ord_b", value=100)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "REPLAYED_NONCE"


def test_conversion_rejects_tampered_receipt(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    payload = json.loads(journey["receipt"])
    payload["ntb"] = 500  # attacker lowers network take
    import json as _json

    tampered = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
    resp = stamp(client, tampered, oid="ord_t", value=100)
    assert resp.status_code in (400, 422)
    code = resp.json()["detail"]["code"]
    assert code in ("BAD_SIGNATURE", "MALFORMED_RECEIPT")


def test_conversion_rejects_expired_receipt(client, seeded_merchant):
    import time

    from app.core.receipt import build_receipt_payload, sign_receipt
    from app.core.ulid import make_ulid
    from app.seed import seed_agent
    from app.db.session import get_session_factory

    db = get_session_factory()()
    try:
        seed_agent(db)
        payload = build_receipt_payload(
            jid="jrn_" + make_ulid(),
            aid="ag_expired_agent",
            mid=seeded_merchant.mid,
            cur="USD",
            crb=1200,
            ntb=1500,
            sf="browser",
            kid=1,
            iat=int(time.time()) - 4000,
            exp=int(time.time()) - 1000,  # already expired
        )
        wire = sign_receipt(payload, bytes.fromhex("ab" * 32))
        resp = stamp(client, wire, oid="ord_exp", value=100)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "EXPIRED"
    finally:
        db.close()


def test_conversion_revoked_receipt(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    rev = client.post(
        "/v1/admin/revoke",
        json={"kind": "receipt", "id": journey["rid"], "reason": "fraud suspicion"},
        headers={"X-Crumbs-Admin-Token": "test-admin-token"},
    )
    assert rev.status_code == 200
    resp = stamp(client, journey["receipt"], oid="ord_r", value=100)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "REVOKED_RECEIPT"


def test_conversion_revoked_journey(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    rev = client.post(
        "/v1/admin/revoke",
        json={"kind": "journey", "id": journey["journey_id"], "reason": "abuse"},
        headers={"X-Crumbs-Admin-Token": "test-admin-token"},
    )
    assert rev.status_code == 200
    resp = stamp(client, journey["receipt"], oid="ord_j", value=100)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "REVOKED_JOURNEY"


def test_conversion_revoked_agent(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    rev = client.post(
        "/v1/admin/revoke",
        json={"kind": "agent", "id": journey["agent_id"], "reason": "agent banned"},
        headers={"X-Crumbs-Admin-Token": "test-admin-token"},
    )
    assert rev.status_code == 200
    resp = stamp(client, journey["receipt"], oid="ord_a2", value=100)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "REVOKED_AGENT"


def test_admin_revoke_disabled_without_token(client, seeded_merchant, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "admin_token", "")
    resp = client.post(
        "/v1/admin/revoke",
        json={"kind": "receipt", "id": "rct_x", "reason": "test"},
    )
    assert resp.status_code == 501
    assert resp.json()["detail"]["code"] == "ADMIN_DISABLED"


def test_webhook_finalize_then_payout(client, seeded_merchant, issue_receipt, db_session):
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_pay", value=100_00)  # $100.00
    assert conv.status_code == 201
    cid = conv.json()["conversion_id"]

    resp = post_webhook(client, webhook_body(conversion_id=cid, confirmed_value=100_00))
    assert resp.status_code == 200, resp.text
    assert resp.json()["order_status"] == "finalized"
    assert resp.json()["cart_mismatch"] is False

    batch = client.post("/v1/payouts/batch", json={"limit": 100}, headers=ADMIN)
    assert batch.status_code == 200
    assert batch.json()["scheduled"] == 1

    from app.db.models import Payout, Split
    from sqlalchemy import select

    payouts = db_session.execute(select(Payout)).scalars().all()
    assert len(payouts) == 1
    assert payouts[0].status == "scheduled"
    splits = db_session.execute(select(Split)).scalars().all()
    parties = {s.party: s.amount_minor_units for s in splits}
    # $100 * 12% = $12.00 commission; 15% network take = $1.80; net $10.20
    # owner 20% = $2.04, agent 80% = $8.16
    assert parties["network"] == 180
    assert parties["owner"] == 204
    assert parties["agent"] == 816
    assert sum(parties.values()) == 1200  # full commission accounted


def test_webhook_rejects_bad_signature(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_sig", value=1000)
    cid = conv.json()["conversion_id"]
    body = json.dumps(webhook_body(conversion_id=cid)).encode()
    resp = client.post(
        "/v1/webhooks/orders",
        content=body,
        headers={"X-Crumbs-Signature": "deadbeef"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "BAD_SIGNATURE"


def test_webhook_requires_timestamp(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_not", value=1000)
    cid = conv.json()["conversion_id"]
    body = json.dumps({"conversion_id": cid, "order_status": "finalized"}).encode()
    resp = client.post(
        "/v1/webhooks/orders",
        content=body,
        headers={"X-Crumbs-Signature": webhook_sig(body)},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "BAD_REQUEST"


def test_webhook_rejects_stale_timestamp(client, seeded_merchant, issue_receipt):
    """Signed-body replay window: t beyond tolerance -> STALE_WEBHOOK."""
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_stale", value=1000)
    cid = conv.json()["conversion_id"]
    stale = webhook_body(conversion_id=cid, t=int(time.time()) - 4000)
    resp = post_webhook(client, stale)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "STALE_WEBHOOK"


def test_webhook_requires_conversion_id(client, seeded_merchant, issue_receipt):
    """oid-only resolution is ambiguous — conversion_id required."""
    journey = issue_receipt()
    stamp(client, journey["receipt"], oid="ord_amb", value=1000)
    resp = post_webhook(client, webhook_body(order_id="ord_amb"))
    assert resp.status_code == 400
    assert "conversion_id is required" in resp.json()["detail"]["message"]


def test_webhook_rejects_invalid_transition(client, seeded_merchant, issue_receipt):
    """Monotonic state machine: cancelled is terminal."""
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_mono", value=1000)
    cid = conv.json()["conversion_id"]
    assert post_webhook(client, webhook_body(conversion_id=cid, order_status="cancelled")).status_code == 200
    resp = post_webhook(client, webhook_body(conversion_id=cid, order_status="finalized"))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "INVALID_TRANSITION"


def test_webhook_allows_finalized_then_refunded(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_refund", value=1000)
    cid = conv.json()["conversion_id"]
    assert post_webhook(client, webhook_body(conversion_id=cid, order_status="finalized")).status_code == 200
    resp = post_webhook(client, webhook_body(conversion_id=cid, order_status="refunded"))
    assert resp.status_code == 200
    assert resp.json()["order_status"] == "refunded"


def test_webhook_cancelled_voids_conversion(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_void", value=1000)
    cid = conv.json()["conversion_id"]
    resp = post_webhook(client, webhook_body(conversion_id=cid, order_status="cancelled"))
    assert resp.status_code == 200
    assert resp.json()["order_status"] == "cancelled"
    # cancelled conversion is never paid
    batch = client.post("/v1/payouts/batch", json={"limit": 100}, headers=ADMIN)
    assert batch.json()["scheduled"] == 0


def test_webhook_cart_padding_flagged(client, seeded_merchant, issue_receipt):
    """Conversion-padding control: inflated cart vs confirmed order."""
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_pad", value=100_00)
    cid = conv.json()["conversion_id"]
    # confirmed order is $1 vs $100 claimed
    resp = post_webhook(client, webhook_body(conversion_id=cid, confirmed_value=1_00))
    assert resp.status_code == 200
    assert resp.json()["cart_mismatch"] is True
    # mismatch holds payout
    batch = client.post("/v1/payouts/batch", json={"limit": 100}, headers=ADMIN)
    assert batch.json()["scheduled"] == 0


def test_webhook_idempotent_redelivery(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_redel", value=1000)
    cid = conv.json()["conversion_id"]
    payload = webhook_body(conversion_id=cid)
    assert post_webhook(client, payload).status_code == 200
    second = post_webhook(client, payload)
    assert second.status_code == 200
    assert second.json()["idempotent"] is True


def test_payouts_batch_requires_admin_token(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    conv = stamp(client, journey["receipt"], oid="ord_payauth", value=1000)
    cid = conv.json()["conversion_id"]
    post_webhook(client, webhook_body(conversion_id=cid))
    resp = client.post("/v1/payouts/batch", json={"limit": 100})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


def test_conversion_surface_mismatch(client, seeded_merchant, issue_receipt):
    journey = issue_receipt(surface="browser")
    resp = stamp(client, journey["receipt"], oid="ord_sf", value=100, surface="api")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "SURFACE_MISMATCH"


def test_conversion_bad_idempotency_key(client, seeded_merchant, issue_receipt):
    journey = issue_receipt()
    resp = client.post(
        "/v1/conversions",
        json={
            "receipt": journey["receipt"],
            "merchant_id": seeded_merchant.mid,
            "order_id": "ord_ik",
            "cart_value_minor_units": 100,
            "currency": "USD",
        },
        headers={"X-Crumbs-Key": "test-merchant-key", "Idempotency-Key": "wrong:key"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "BAD_IDEMPOTENCY_KEY"


def test_conversion_unknown_receipt(client, seeded_merchant):
    """A well-formed, correctly-signed receipt for a merchant with no journey
    (receipt row absent) -> UNKNOWN_RECEIPT."""
    import time

    from app.core.receipt import build_receipt_payload, sign_receipt
    from app.core.ulid import make_ulid

    payload = build_receipt_payload(
        jid="jrn_" + make_ulid(),
        aid="ag_ghostagent",
        mid=seeded_merchant.mid,
        cur="USD",
        crb=1200,
        ntb=1500,
        sf="browser",
        kid=1,
    )
    wire = sign_receipt(payload, bytes.fromhex("ab" * 32))
    resp = stamp(client, wire, oid="ord_ghost", value=100)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "UNKNOWN_RECEIPT"
