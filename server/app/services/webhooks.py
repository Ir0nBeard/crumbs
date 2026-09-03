"""Merchant order webhook — conversion lock-in / voiding (spec A.6.8 + D.1).

The merchant re-confirms final order state with a signed webhook before payout
scheduling; cart value is cross-checked against the confirmed order
(conversion-padding control).

Hardening (P3 C-M5 / D-L3):
  * `conversion_id` is REQUIRED — no ambiguous oid-only resolution
  * `t` timestamp (unix seconds) in the body, tolerance window (300s) against
    replay of captured signed bodies
  * monotonic status transitions: pending -> {finalized,cancelled,refunded},
    finalized -> {refunded}, cancelled/refunded are terminal
  * idempotent re-delivery of the same (cid, order_status) returns current state
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Conversion, Merchant
from ..db.session import audit

ORDER_STATUSES = ("finalized", "cancelled", "refunded")
# Legal transitions: current -> {allowed next}
TRANSITIONS = {
    "pending": {"finalized", "cancelled", "refunded"},
    "finalized": {"refunded"},
    "cancelled": set(),
    "refunded": set(),
}

# Reject this known dev constant outside local SQLite dev DBs (P3 C-M3)
DEV_WEBHOOK_SECRET = "dev-webhook-secret-do-not-use-in-prod"


class WebhookError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _hmac_hex(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_webhook_signature(merchant: Merchant, body: bytes, signature: str) -> bool:
    """HMAC-SHA256 over the RAW request body, hex-encoded, constant-time compare.

    STUB NOTE: in production the merchant's webhook secret lives in a secret
    manager (KMS/encrypted), NOT in the DB; v0.1 keeps it on the merchants row
    and guards the dev default (see process_order_webhook).
    """
    if not merchant.webhook_secret:
        return False
    expected = _hmac_hex(merchant.webhook_secret.encode("utf-8"), body)
    return hmac.compare_digest(expected, signature or "")


def process_order_webhook(
    db: Session,
    *,
    mid: str,
    body: bytes,
    signature: str,
    settings=None,
) -> dict:
    """Handle POST /v1/webhooks/orders. Raises WebhookError on failure."""
    settings = settings or get_settings()
    merchant = db.get(Merchant, mid)
    if merchant is None:
        raise WebhookError("UNKNOWN_MERCHANT", "unknown merchant", 404)

    # Dev-default guard: the known constant must never authenticate outside a
    # local SQLite dev database (P3 C-M3).
    if merchant.webhook_secret == DEV_WEBHOOK_SECRET and not settings.database_url.startswith(
        "sqlite"
    ):
        raise WebhookError("DEV_SECRET_GUARD",
                           "merchant webhook secret is the dev default — refusing", 500)

    if not verify_webhook_signature(merchant, body, signature):
        raise WebhookError("BAD_SIGNATURE", "webhook signature invalid", 401)

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WebhookError("MALFORMED", "invalid JSON body") from exc

    cid = data.get("conversion_id") or data.get("cid")
    order_status = data.get("order_status")
    confirmed_value = data.get("final_cart_value_minor_units")

    if order_status not in ORDER_STATUSES:
        raise WebhookError("BAD_REQUEST", f"order_status must be one of {ORDER_STATUSES}")

    # Replay window: `t` (unix seconds) must be within tolerance (P3 D-L3)
    t = data.get("t")
    try:
        t_int = int(t) if t is not None else None
    except (TypeError, ValueError):
        raise WebhookError("BAD_REQUEST", "t must be a unix timestamp (seconds)")
    if t_int is None:
        raise WebhookError("BAD_REQUEST", "body must include t (unix seconds)")
    if abs(int(time.time()) - t_int) > settings.webhook_tolerance_seconds:
        raise WebhookError("STALE_WEBHOOK", "webhook timestamp outside tolerance window")

    # Unambiguous resolution: conversion_id is required (P3 C-M5 / D-L5)
    if not cid:
        raise WebhookError("BAD_REQUEST",
                           "conversion_id is required (order_id-only resolution is ambiguous)")
    conversion = db.get(Conversion, cid)
    if conversion is None:
        raise WebhookError("NOT_FOUND", "conversion not found", 404)

    # Idempotent re-delivery: same (cid, order_status) -> return current state
    if conversion.order_status == order_status:
        return {"conversion_id": conversion.cid, "order_status": conversion.order_status,
                "idempotent": True}

    # Monotonic state machine (P3 D-L3)
    if order_status not in TRANSITIONS.get(conversion.order_status, set()):
        raise WebhookError(
            "INVALID_TRANSITION",
            f"cannot transition {conversion.order_status} -> {order_status}",
            409,
        )

    if order_status == "finalized":
        conversion.order_status = "finalized"
        conversion.verified_at = datetime.now(timezone.utc)
        # Conversion-padding cross-check (spec A.6.8): |reported - confirmed| tolerance
        if confirmed_value is not None:
            diff = abs(conversion.cart_value_minor_units - int(confirmed_value))
            tolerance = max(
                1,
                conversion.cart_value_minor_units
                * settings.conversion_padding_tolerance_bps
                // 10000,
            )
            if diff > tolerance:
                conversion.cart_mismatch = True
        audit(db, "conversion_finalized", "conversion", conversion.cid,
              actor="merchant:" + mid,
              payload={"order_status": "finalized", "cart_mismatch": conversion.cart_mismatch})
    else:  # cancelled | refunded
        conversion.order_status = order_status
        audit(db, "conversion_voided", "conversion", conversion.cid, actor="merchant:" + mid,
              payload={"order_status": order_status})

    db.commit()
    return {
        "conversion_id": conversion.cid,
        "order_status": conversion.order_status,
        "cart_mismatch": conversion.cart_mismatch,
        "idempotent": False,
    }
