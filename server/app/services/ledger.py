"""Ledger service — journey issuance, conversion recording, verification,
revocation, and self-referral velocity controls.

Every mutation appends an audit_events row (append-only).
"""
from __future__ import annotations

import json
import logging
import time

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.receipt import (
    P_JOURNEY,
    P_MERCHANT,
    P_RECEIPT,
    build_receipt_payload,
    new_agent_id,
    new_merchant_id,
    parse_receipt,
    receipt_expired,
)
from ..core.ulid import make_ulid
from ..db.models import (
    Agent,
    AgentOwner,
    AuditEvent,
    Conversion,
    Journey,
    Merchant,
    MerchantProgram,
    Receipt,
    RevokedAgent,
    RevokedJourney,
    RevokedReceipt,
    UsedNonce,
)
from ..db.session import audit
from ..signing import SigningService
from ..stores import fingerprint_ip, fingerprint_ua
from .consent import ConsentError, verify_consent_signal

log = logging.getLogger("crumbs.ledger")

# Error codes surfaced by the API (stable strings, not HTTP-status-dependent)
E_CONSENT_REQUIRED = "CONSENT_REQUIRED"
E_BAD_SIGNATURE = "BAD_SIGNATURE"
E_UNKNOWN_KID = "UNKNOWN_KID"
E_EXPIRED = "EXPIRED"
E_REPLAYED = "REPLAYED_NONCE"
E_REVOKED_RECEIPT = "REVOKED_RECEIPT"
E_REVOKED_JOURNEY = "REVOKED_JOURNEY"
E_REVOKED_AGENT = "REVOKED_AGENT"
E_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
E_SELF_REFERRAL = "SELF_REFERRAL"
E_VELOCITY_EXCEEDED = "VELOCITY_EXCEEDED"
E_UNKNOWN_MERCHANT = "UNKNOWN_MERCHANT"
E_UNKNOWN_RECEIPT = "UNKNOWN_RECEIPT"
E_IDEMPOTENT = "IDEMPOTENT_REPLAY"  # internal marker (API returns 200 + existing)
E_MALFORMED = "MALFORMED_RECEIPT"
E_SURFACE_MISMATCH = "SURFACE_MISMATCH"
E_UNVERIFIED_CART = "CART_VALUE_MISMATCH"


class LedgerError(Exception):
    """Raised with a stable error code; API layer maps to responses."""

    def __init__(self, code: str, message: str, status_code: int = 400, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.extra = extra or {}


def _to_usd_minor(minor_units: int, currency: str, settings) -> int:
    rate = settings.parsed_fx.get(currency.upper(), 1.0)
    return int(round(minor_units * rate))


def _lookup_program(db: Session, mid: str) -> MerchantProgram | None:
    return db.execute(
        select(MerchantProgram).where(
            MerchantProgram.mid == mid, MerchantProgram.status == "active"
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Journeys
# ---------------------------------------------------------------------------

def issue_journey(
    db: Session,
    *,
    mid: str,
    surface: str,
    consent: dict,
    client_ip: str | None = None,
    user_agent: str | None = None,
    agent_id: str | None = None,
    signing: SigningService,
    nonce_store,
    rate_limiter,
    settings=None,
) -> dict:
    """Consent-gated receipt issuance (docs/ATTRIBUTION_PROTOCOL.md §6).

    No receipt is issued without a recorded lawful basis. Returns:
      {"receipt": "<canonical receipt string>", "journey_id": ..., "consent": {...}}
    Raises LedgerError(E_CONSENT_REQUIRED) when consent is missing.
    """
    settings = settings or get_settings()
    basis = (consent or {}).get("basis")
    if not basis or basis not in {"gpp", "tcf", "explicit", "88b"}:
        raise LedgerError(E_CONSENT_REQUIRED, "receipt issuance requires a recorded consent basis",
                          403, {"basis": basis})

    # Server-side consent-signal verification (services/consent.py) — real
    # verifier replacing the v0.1 501 STUB. Local structural checks
    # always run; when CRUMBS_CMP_VERIFY_URL is set, the CMP's verdict is
    # authoritative (fail-closed on any non-"valid": true outcome).
    try:
        consent_verdict = verify_consent_signal(consent, settings, surface=surface)
    except ConsentError as exc:
        raise LedgerError(exc.code, exc.message, exc.status_code, exc.extra) from exc

    # Rate limit issuance per IP (fake-journey control)
    if client_ip:
        allowed, _count = rate_limiter.hit(
            "journeys", client_ip, settings.issuance_rate_limit, 3600
        )
        if not allowed:
            raise LedgerError("RATE_LIMITED", "too many journey starts", 429)

    program = _lookup_program(db, mid)
    if program is None:
        raise LedgerError(E_UNKNOWN_MERCHANT, f"no active program for merchant {mid}", 404)

    if agent_id is None:
        agent_id = new_agent_id()
    agent = db.get(Agent, agent_id)
    if agent is None:
        agent = Agent(aid=agent_id, status="active")
        db.add(agent)
        audit(db, "agent_registered", "agent", agent_id, actor="system",
              payload={"surface": surface})

    if agent.revoked or db.get(RevokedAgent, agent_id) is not None:
        raise LedgerError(E_REVOKED_AGENT, "agent is revoked", 403)

    jid = P_JOURNEY + make_ulid()
    # IP/UA stored as HASHES only (raw IPs/UAs are PII). Truncated to 64 chars
    # for storage.
    ip_hash = None
    ua_hash = None
    if client_ip:
        ip_hash = fingerprint_ip(client_ip)
    if user_agent:
        ua_hash = fingerprint_ua(user_agent)[:64]
    journey = Journey(
        jid=jid,
        aid=agent_id,
        surface=surface,
        consent_basis=basis,
        consent_ref=(consent or {}).get("ref"),
        ip_hash=ip_hash,
        ua_hash=ua_hash,
        max_conversions=settings.budget_max_conversions,
        max_merchants=settings.budget_max_merchants,
        max_cart_value_usd=settings.budget_max_cart_value_usd,
        status="active",
    )
    db.add(journey)

    payload = build_receipt_payload(
        jid=jid,
        aid=agent_id,
        mid=mid,
        cur="USD",  # issuance carries no value; currency is stamped at conversion
        crb=program.commission_rate_bps,
        ntb=program.network_take_bps,
        sf=surface,
        kid=signing.current_kid(),
        ttl=settings.receipt_ttl_seconds,
    )
    signed = signing.sign(payload)
    receipt_str = _canonical_with_sig(signed)

    receipt = Receipt(
        rid=signed["rid"],
        jid=jid,
        aid=agent_id,
        mid=mid,
        v=signed["v"],
        sf=signed["sf"],
        nc=signed["nc"],
        iat=signed["iat"],
        exp=signed["exp"],
        kid=signed["kid"],
        sig=signed["sig"],
        status="issued",
    )
    db.add(receipt)
    audit(db, "journey_issued", "journey", jid, actor="agent:" + agent_id,
          payload={"rid": signed["rid"], "mid": mid, "surface": surface,
                   "consent_basis": basis,
                   "consent_mode": consent_verdict["mode"],
                   "consent_checks": consent_verdict["checks"]})
    db.commit()

    return {
        "receipt": receipt_str,
        "rid": signed["rid"],
        "journey_id": jid,
        "agent_id": agent_id,
        "exp": signed["exp"],
        "consent": {"basis": basis, "recorded": True,
                    "verified": consent_verdict["mode"]},
    }


def _canonical_with_sig(payload: dict) -> str:
    from ..core.jcs import canonical_json

    return canonical_json(payload)


def issue_receipt_for_journey(
    db: Session,
    *,
    jid: str,
    mid: str,
    surface: str | None = None,
    signing: SigningService,
    settings=None,
) -> dict:
    """Issue an ADDITIONAL receipt on an EXISTING active journey (receipts are
    per-journey; one receipt = one conversion, so multi-merchant journeys
    request a new receipt per merchant visit). Rides the consent
    already recorded on the journey.
    """
    settings = settings or get_settings()
    journey = db.get(Journey, jid)
    if journey is None or journey.status != "active":
        raise LedgerError(E_REVOKED_JOURNEY, "journey not active")
    agent = db.get(Agent, journey.aid)
    if agent is None or agent.revoked or db.get(RevokedAgent, journey.aid) is not None:
        raise LedgerError(E_REVOKED_AGENT, "agent not active")
    program = _lookup_program(db, mid)
    if program is None:
        raise LedgerError(E_UNKNOWN_MERCHANT, f"no active program for merchant {mid}", 404)

    payload = build_receipt_payload(
        jid=jid,
        aid=journey.aid,
        mid=mid,
        cur="USD",
        crb=program.commission_rate_bps,
        ntb=program.network_take_bps,
        sf=surface or journey.surface,
        kid=signing.current_kid(),
        ttl=settings.receipt_ttl_seconds,
    )
    signed = signing.sign(payload)
    receipt = Receipt(
        rid=signed["rid"],
        jid=jid,
        aid=journey.aid,
        mid=mid,
        v=signed["v"],
        sf=signed["sf"],
        nc=signed["nc"],
        iat=signed["iat"],
        exp=signed["exp"],
        kid=signed["kid"],
        sig=signed["sig"],
        status="issued",
    )
    db.add(receipt)
    audit(db, "receipt_issued", "receipt", signed["rid"], actor="agent:" + journey.aid,
          payload={"jid": jid, "mid": mid})
    db.commit()
    return {
        "receipt": _canonical_with_sig(signed),
        "rid": signed["rid"],
        "journey_id": jid,
        "agent_id": journey.aid,
        "exp": signed["exp"],
    }


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------

def record_conversion(
    db: Session,
    *,
    receipt_str: str,
    oid: str,
    cart_value_minor_units: int,
    currency: str,
    surface: str | None = None,
    merchant_key: str | None = None,
    signing: SigningService,
    nonce_store,
    rate_limiter,
    settings=None,
) -> dict:
    """Record a stamped conversion (docs/ATTRIBUTION_PROTOCOL.md §4.3).

    Pipeline: parse -> signature -> kid -> expiry -> revocation -> nonce replay
    -> journey/agent checks -> budget -> self-referral/velocity -> idempotency
    -> record. Idempotent on (rid, oid): a repeat call returns the existing
    conversion with E_IDEMPOTENT marker (API maps to 200).

    A failed attempt rolls the transaction back before re-raising: the
    journey row lock and (agent, merchant) advisory lock taken along the way
    must not outlive the error, or a later statement on the same session
    would run inside the still-open, still-locked transaction and can
    deadlock concurrent conversions.
    """
    settings = settings or get_settings()
    try:
        return _record_conversion_impl(
            db,
            receipt_str=receipt_str,
            oid=oid,
            cart_value_minor_units=cart_value_minor_units,
            currency=currency,
            surface=surface,
            merchant_key=merchant_key,
            signing=signing,
            nonce_store=nonce_store,
            rate_limiter=rate_limiter,
            settings=settings,
        )
    except LedgerError:
        db.rollback()
        raise


def _record_conversion_impl(
    db: Session,
    *,
    receipt_str: str,
    oid: str,
    cart_value_minor_units: int,
    currency: str,
    surface: str | None = None,
    merchant_key: str | None = None,
    signing: SigningService,
    nonce_store,
    rate_limiter,
    settings,
) -> dict:
    try:
        payload = parse_receipt(receipt_str)
    except ValueError as exc:
        raise LedgerError(E_MALFORMED, str(exc)) from exc

    # 1. signature + kid (type confusion surfaces as MALFORMED, never a 500)
    ok, reason = signing.verify_detail(payload)
    if not ok:
        code = E_UNKNOWN_KID if reason == "unknown_kid" else E_BAD_SIGNATURE
        raise LedgerError(code, "receipt signature invalid",
                          extra={"verify": reason})
    if payload.get("oid") not in ("", None) and payload.get("oid") != oid:
        raise LedgerError(E_SURFACE_MISMATCH, "receipt was already stamped with another oid",
                          extra={"stamped_oid": payload.get("oid")})

    # 2. expiry
    if receipt_expired(payload):
        raise LedgerError(E_EXPIRED, "receipt expired")

    # 3. revocation (receipt / journey / agent)
    rid, jid, aid, mid = payload["rid"], payload["jid"], payload["aid"], payload["mid"]
    if db.get(RevokedReceipt, rid) is not None:
        raise LedgerError(E_REVOKED_RECEIPT, "receipt revoked")
    if db.get(RevokedJourney, jid) is not None:
        raise LedgerError(E_REVOKED_JOURNEY, "journey revoked")
    if db.get(RevokedAgent, aid) is not None:
        raise LedgerError(E_REVOKED_AGENT, "agent revoked")

    # 3.5 ledger presence — a correctly-signed receipt we never issued
    receipt_row = db.get(Receipt, rid)
    if receipt_row is None:
        raise LedgerError(E_UNKNOWN_RECEIPT, "receipt not found in ledger", 404)

    # 4. idempotency — unique (rid, oid); checked BEFORE nonce consumption so
    #    that safe retries of an already-recorded conversion succeed (idempotency)
    existing = db.execute(
        select(Conversion).where(Conversion.rid == rid, Conversion.oid == oid)
    ).scalar_one_or_none()
    if existing is not None:
        raise LedgerError(E_IDEMPOTENT, "conversion already recorded", 200,
                          {"conversion_id": existing.cid, "status": existing.order_status})

    # 5. journey + agent state
    journey = db.get(Journey, jid)
    if journey is None or journey.status != "active":
        raise LedgerError(E_REVOKED_JOURNEY, "journey not active")
    agent = db.get(Agent, aid)
    if agent is None or agent.revoked:
        raise LedgerError(E_REVOKED_AGENT, "agent not active")

    # 6. serialize this journey's recording. Conversion recording reads
    #    journey-scoped state — the distinct-merchant set, the budget
    #    counters, the velocity window — and then mutates the journey row.
    #    Without a lock, two concurrent conversions of a first-time merchant
    #    could both compute merchant_delta=1 and drift `merchants_used` above
    #    the true distinct count. SELECT ... FOR UPDATE (compiled away on
    #    SQLite, which is single-writer) makes the reads observe the latest
    #    committed state.
    journey = _lock_journey_for_update(db, jid)

    # 7. merchant program + surface + self-referral
    program = _lookup_program(db, mid)
    if program is None:
        raise LedgerError(E_UNKNOWN_MERCHANT, f"no active program for merchant {mid}", 404)
    if surface and surface != payload["sf"]:
        raise LedgerError(E_SURFACE_MISMATCH,
                          f"conversion surface {surface!r} != issuance surface {payload['sf']!r}")
    _check_self_referral(db, aid, mid, program, settings)

    # 8. budgets (docs/ATTRIBUTION_PROTOCOL.md §5) — conversions, distinct
    #    merchants, cart value. ATOMIC conditional UPDATE closes the
    #    check-then-increment TOCTOU: the row only advances when ALL counters
    #    have headroom.
    cart_usd = _to_usd_minor(cart_value_minor_units, currency, settings)
    merchant_delta = 0 if mid in _merchant_set(db, jid) else 1
    budget_result = db.execute(
        update(Journey)
        .where(
            Journey.jid == jid,
            Journey.conversions_used < Journey.max_conversions,
            Journey.merchants_used + merchant_delta <= Journey.max_merchants,
            Journey.cart_value_used_usd + cart_usd <= Journey.max_cart_value_usd,
        )
        .values(
            conversions_used=Journey.conversions_used + 1,
            merchants_used=Journey.merchants_used + merchant_delta,
            cart_value_used_usd=Journey.cart_value_used_usd + cart_usd,
        )
    )
    if budget_result.rowcount != 1:
        raise LedgerError(E_BUDGET_EXCEEDED, "journey budget exceeded",
                          extra={"limit": {
                              "conversions": journey.max_conversions,
                              "merchants": journey.max_merchants,
                              "cart_value_usd": journey.max_cart_value_usd,
                          }})

    # 9. nonce replay (docs/ATTRIBUTION_PROTOCOL.md §5) — consumed only AFTER
    #    every reject-check, so a failed attempt never burns the receipt.
    #    Grace extends past expiry, so a positive remaining window is
    #    guaranteed here.
    ttl_grace = int(payload["exp"]) + settings.nonce_grace_seconds - int(time.time())
    if ttl_grace <= 0:
        raise LedgerError(E_EXPIRED, "receipt expired")
    if not nonce_store.mark_used(rid, payload["nc"], ttl_grace):
        raise LedgerError(E_REPLAYED, "receipt nonce already used")

    # 10. record
    cid = "c_" + make_ulid()
    conversion = Conversion(
        cid=cid,
        rid=rid,
        jid=jid,
        mid=mid,
        oid=oid,
        cart_value_minor_units=cart_value_minor_units,
        currency=currency.upper(),
        crb=program.commission_rate_bps,
        ntb=program.network_take_bps,
        order_status="pending",
    )
    db.add(conversion)
    # Durable nonce fallback row (mirrors the Redis/memory store — guarantees
    # nonce dedup survives a single-process restart on SQLite)
    from datetime import datetime, timedelta, timezone

    db.add(UsedNonce(
        rid=rid,
        nc=payload["nc"],
        expires_at=datetime.fromtimestamp(
            int(payload["exp"]) + settings.nonce_grace_seconds, tz=timezone.utc
        ),
    ))

    audit(db, "conversion_recorded", "conversion", cid, actor="agent:" + aid,
          payload={"rid": rid, "oid": oid, "value_minor": cart_value_minor_units,
                   "currency": currency.upper(), "status": "pending"})
    db.commit()
    return {
        "conversion_id": cid,
        "rid": rid,
        "oid": oid,
        "status": "pending",
        "awaiting": "merchant order webhook (finalized|cancelled|refunded)",
    }


def _lock_journey_for_update(db: Session, jid: str) -> Journey:
    """Serialize conversion recording on one journey (Postgres row lock)."""
    journey = db.execute(
        select(Journey)
        .where(Journey.jid == jid, Journey.status == "active")
        .with_for_update()
    ).scalar_one_or_none()
    if journey is None:
        raise LedgerError(E_REVOKED_JOURNEY, "journey not active")
    return journey


def _lock_agent_merchant_window(db: Session, aid: str, mid: str) -> None:
    """Serialize the (agent, merchant) velocity window across journeys.

    The self-referral velocity count spans every journey of an agent, so a
    per-journey row lock does not cover two concurrent conversions of the
    same agent at the same merchant on DIFFERENT journeys. Postgres advisory
    transaction locks keyed on (aid, mid) close that window and release at
    commit. SQLite is single-writer — nothing to do.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    import zlib

    k1 = zlib.crc32(aid.encode("utf-8")) & 0x7FFFFFFF
    k2 = zlib.crc32(mid.encode("utf-8")) & 0x7FFFFFFF
    db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"), {"k1": k1, "k2": k2}
    )


def _check_self_referral(db: Session, aid: str, mid: str, program: MerchantProgram,
                         settings) -> None:
    """Spec A.6.7: merchant's own agents excluded; velocity against self-referral."""
    if program.self_referral_policy == "allow":
        return
    merchant = db.get(Merchant, mid)
    agent = db.get(Agent, aid)
    if merchant is None or agent is None:
        return
    # Relationship check: same owner id, or the agent is registered to the merchant
    if merchant.owner_id and agent.owner_id and merchant.owner_id == agent.owner_id:
        raise LedgerError(E_SELF_REFERRAL, "merchant-owned agent cannot self-refer")
    # Velocity: conversions for (agent, merchant) in the window. Serialize the
    # window across the agent's journeys so concurrent conversions cannot both
    # observe a count below the cap and overshoot it.
    _lock_agent_merchant_window(db, aid, mid)
    from datetime import datetime, timezone

    window_start_dt = datetime.fromtimestamp(
        int(time.time()) - settings.self_referral_window_seconds, tz=timezone.utc
    )
    count = db.execute(
        select(func.count(Conversion.cid)).where(
            Conversion.jid.in_(select(Journey.jid).where(Journey.aid == aid)),
            Conversion.mid == mid,
            Conversion.created_at >= window_start_dt,
        )
    ).scalar_one()
    if count >= settings.self_referral_max_conversions:
        raise LedgerError(E_VELOCITY_EXCEEDED,
                          "self-referral velocity exceeded",
                          extra={"limit": settings.self_referral_max_conversions})


def _merchant_set(db: Session, jid: str) -> set[str]:
    rows = db.execute(select(Conversion.mid).where(Conversion.jid == jid)).all()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_receipt(db: Session, receipt_str: str, signing: SigningService,
                   nonce_store, settings=None) -> dict:
    """GET /v1/verify — full status check (docs/ATTRIBUTION_PROTOCOL.md §5).

    `valid` = the receipt is redeemable RIGHT NOW (signature ok, not expired,
    not revoked, nonce unused). Journey budget state is attached whenever the
    journey row exists, so callers can inspect counters even for spent receipts.
    """
    settings = settings or get_settings()

    def _journey_state(jid: str) -> dict | None:
        journey = db.get(Journey, jid)
        if journey is None:
            return None
        return {
            "conversions_used": journey.conversions_used,
            "max_conversions": journey.max_conversions,
            "merchants_used": journey.merchants_used,
            "max_merchants": journey.max_merchants,
            "cart_value_used_usd": journey.cart_value_used_usd,
            "max_cart_value_usd": journey.max_cart_value_usd,
        }

    try:
        payload = parse_receipt(receipt_str)
    except ValueError as exc:
        return {"valid": False, "reason": E_MALFORMED, "detail": str(exc)}
    ok, reason = signing.verify_detail(payload)
    if not ok:
        code = E_UNKNOWN_KID if reason == "unknown_kid" else E_BAD_SIGNATURE
        return {"valid": False, "reason": code, "detail": "signature invalid"}
    rid, jid, aid = payload["rid"], payload["jid"], payload["aid"]
    base = {"rid": rid, "jid": jid, "aid": aid, "mid": payload["mid"],
            "exp": payload["exp"], "journey": _journey_state(jid)}
    if receipt_expired(payload):
        return {"valid": False, "reason": E_EXPIRED, **base}
    if db.get(RevokedReceipt, rid) is not None:
        return {"valid": False, "reason": E_REVOKED_RECEIPT, **base}
    if db.get(RevokedJourney, jid) is not None:
        return {"valid": False, "reason": E_REVOKED_JOURNEY, **base}
    if db.get(RevokedAgent, aid) is not None:
        return {"valid": False, "reason": E_REVOKED_AGENT, **base}
    if nonce_store.is_used(rid):
        return {"valid": False, "reason": E_REPLAYED, **base}
    journey = db.get(Journey, jid)
    if journey is None or journey.status != "active":
        return {"valid": False, "reason": E_REVOKED_JOURNEY, **base}
    return {"valid": True, **base}


# ---------------------------------------------------------------------------
# Revocation (admin)
# ---------------------------------------------------------------------------

def revoke(db: Session, kind: str, entity_id: str, reason: str, actor: str = "admin") -> None:
    """Revoke a receipt / journey / agent."""
    if kind == "receipt":
        db.merge(RevokedReceipt(rid=entity_id, reason=reason, by=actor))
        r = db.get(Receipt, entity_id)
        if r:
            r.status = "revoked"
        entity_type = "receipt"
    elif kind == "journey":
        db.merge(RevokedJourney(jid=entity_id, reason=reason, by=actor))
        j = db.get(Journey, entity_id)
        if j:
            j.status = "revoked"
        entity_type = "journey"
    elif kind == "agent":
        db.merge(RevokedAgent(aid=entity_id, reason=reason, by=actor))
        a = db.get(Agent, entity_id)
        if a:
            a.revoked = True
            a.status = "revoked"
        entity_type = "agent"
    else:
        raise LedgerError("BAD_REQUEST", f"unknown revocation kind {kind!r}")
    audit(db, f"revoked_{kind}", entity_type, entity_id, actor=actor,
          payload={"reason": reason})
    db.commit()
