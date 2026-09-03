"""Local dev demo — seed a merchant, issue a journey, stamp + finalize a
conversion, schedule a payout. Run from server/:

    CRUMBS_SIGNING_KEYS=1:<64 hex> python -m app.dev_demo

Uses a throwaway SQLite file (crumbs_demo.db) — safe to delete.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import time

from app.config import Settings
from app.db.session import create_all, get_session_factory, reset_engine_for_tests
from app.seed import DEFAULT_WEBHOOK_SECRET, seed_merchant
from app.services.ledger import issue_journey, record_conversion
from app.services.webhooks import process_order_webhook
from app.services.payouts import schedule_payouts
from app.signing import SigningService
from app.stores import MemoryNonceStore, MemoryRateLimiter

KEY = bytes.fromhex("ab" * 32)


def main() -> None:
    reset_engine_for_tests("sqlite:///./crumbs_demo.db")
    create_all()
    settings = Settings(
        signing_keys="1:" + "ab" * 32,
        database_url="sqlite:///./crumbs_demo.db",
        admin_token="demo-admin-token",
        payouts_enabled=True,  # explicit flip (fail-closed default is False)
    )
    db = get_session_factory()()
    merchant = seed_merchant(db, commission_rate_bps=1200, network_take_bps=1500)
    signing = SigningService(keys={1: KEY}, default_kid=1)
    nonces, limiter = MemoryNonceStore(), MemoryRateLimiter()

    print("== Crumbs ledger demo ==")
    print(f"merchant: {merchant.mid} (commission 12%, network take 15%)")

    journey = issue_journey(
        db, mid=merchant.mid, surface="browser",
        consent={"basis": "explicit", "ref": "demo"},
        client_ip="198.51.100.7", user_agent="demo-agent",
        signing=signing, nonce_store=nonces, rate_limiter=limiter, settings=settings,
    )
    print(f"journey: {journey['journey_id']}  rid: {journey['rid']}")
    print(f"receipt size: {len(journey['receipt'])} bytes (design rule < 1 KB)")

    conv = record_conversion(
        db, receipt_str=journey["receipt"], oid="demo-order-1",
        cart_value_minor_units=10000, currency="USD", surface="browser",
        signing=signing, nonce_store=nonces, rate_limiter=limiter, settings=settings,
    )
    print(f"conversion: {conv['conversion_id']} status={conv['status']}")

    body = json.dumps(
        {"conversion_id": conv["conversion_id"], "order_status": "finalized",
         "final_cart_value_minor_units": 10000, "t": int(time.time())},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(DEFAULT_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    wh = process_order_webhook(db, mid=merchant.mid, body=body, signature=sig, settings=settings)
    print(f"webhook: {wh['order_status']} cart_mismatch={wh['cart_mismatch']}")

    payout = schedule_payouts(db, settings=settings)
    print(f"payout batch: {payout['scheduled']} scheduled (rail=x402, records only)")

    from app.db.models import Split
    from sqlalchemy import select

    splits = db.execute(select(Split)).scalars().all()
    for s in splits:
        print(f"  split {s.party}: {s.amount_minor_units} {s.currency} ({s.pct_bps} bps)")

    db.close()
    print("\nDone. Tables live in server/crumbs_demo.db (delete freely).")


if __name__ == "__main__":
    main()
