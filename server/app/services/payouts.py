"""Payout records — scheduling + settlement-proof recording, NO float.

The commission split is computed and payout rows are SCHEDULED here; actual
money movement runs on licensed rails (x402/USDC via a CDP facilitator, or
Stripe Connect) executed by an operator — the ledger never holds funds and
never moves them itself. What the ledger DOES record is the settlement
*proof*: `record_settlement()` transitions a scheduled payout to `settled`
with the rail's tx hash and, when settlement calldata is supplied, verifies
the ERC-8021 Schema 2 builder-code suffix actually carries `bc_crumbs`
(on-chain proof mode). Without calldata the record is a rail attestation
(`rail_ref` mode) and is labelled as such — a stored hash without verifiable
attribution is never presented as an on-chain proof.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.buildercode import (
    DEFAULT_BUILDER_CODE,
    BuilderCodeError,
    calldata_carries_code,
    parse_builder_code_suffix_from_calldata,
    valid_builder_code,
)
from ..core.ulid import make_ulid
from ..db.models import Agent, Conversion, Payout, Split, utcnow
from ..db.session import audit

# EVM transaction hash (Base mainnet x402 settlements).
TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
REFERRAL_REF_RE = re.compile(r"^(rct_|jrn_)[A-Za-z0-9_]{1,38}$")


class PayoutError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def schedule_payouts(db: Session, *, limit: int = 500, settings=None) -> dict:
    """Batch-schedule payouts for finalized, non-mismatch, unscheduled conversions.

    Split model:
      commission     = cart_value_minor_units * crb / 10000
      network_take   = commission * ntb / 10000            (10-20%)
      net_commission = commission - network_take
      agent_share    = net_commission * (10000 - owner_share_bps) / 10000
      owner_share    = net_commission * owner_share_bps / 10000
    All integer minor-unit arithmetic (no floats for money).
    """
    settings = settings or get_settings()
    if not settings.payouts_enabled:
        return {"scheduled": 0, "note": "payouts disabled via CRUMBS_PAYOUTS_ENABLED=false"}

    candidates = (
        db.execute(
            select(Conversion)
            .where(
                Conversion.order_status == "finalized",
                Conversion.cart_mismatch.is_(False),
                Conversion.payout_scheduled.is_(False),
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )

    scheduled = 0
    for conv in candidates:
        commission = conv.cart_value_minor_units * conv.crb // 10000
        network_take = commission * conv.ntb // 10000
        net = commission - network_take
        owner_share = net * settings.default_owner_share_bps // 10000
        agent_share = net - owner_share

        pid = "p_" + make_ulid()
        payout = Payout(pid=pid, rail="x402", status="scheduled")  # rail STUB
        db.add(payout)
        conv.payout_scheduled = True

        parties = [
            ("agent", agent_share),
            ("owner", owner_share),
            ("network", network_take),
        ]
        for party, amount in parties:
            pct = {
                "agent": (10000 - settings.default_owner_share_bps),
                "owner": settings.default_owner_share_bps,
                "network": conv.ntb,
            }[party]
            db.add(
                Split(
                    split_id="s_" + make_ulid(),
                    cid=conv.cid,
                    party=party,
                    amount_minor_units=amount,
                    currency=conv.currency,
                    pct_bps=pct,
                    status="scheduled",
                    payout_id=pid,
                )
            )
        audit(db, "payout_scheduled", "payout", pid, actor="system",
              payload={"cid": conv.cid, "rail": "x402",
                       "note": "settlement executes on a licensed rail; the ledger records the proof via record_settlement()"})
        scheduled += 1

    db.commit()
    return {"scheduled": scheduled, "rail": "x402", "settled": 0,
            "note": "scheduling records only — the ledger never holds or moves funds"}


# --- settlement-proof recording (x402 rail) --------------------------------


def record_settlement(
    db: Session,
    pid: str,
    *,
    tx_hash: str,
    calldata: str | None = None,
    builder_code: str = DEFAULT_BUILDER_CODE,
    referral_ref: str | None = None,
    rail_ref: str | None = None,
    asset: str = "USDC",
    network: str = "eip155:8453",
    executed_by: str = "rail",
    settings=None,
) -> dict:
    """Record an executed rail settlement against a scheduled payout.

    Money does not move here — a licensed rail (x402/CDP facilitator) or its
    operator executed the transfer off-ledger; this function records the
    proof. When *calldata* is supplied the ERC-8021 Schema 2 suffix is parsed
    and MUST carry *builder_code* (``bc_crumbs`` by default): an on-chain
    proof. Without calldata the record is a rail attestation (``rail_ref``
    mode) and the response says so.

    Rejects: unknown pid (404), non-scheduled payout (409), malformed
    tx_hash/builder_code/referral_ref (422), calldata that carries no valid
    suffix or a different builder code (422).
    """
    settings = settings or get_settings()
    if not settings.payouts_enabled:
        raise PayoutError("PAYOUTS_DISABLED",
                          "payouts disabled via CRUMBS_PAYOUTS_ENABLED=false", 403)

    payout = db.get(Payout, pid)
    if payout is None:
        raise PayoutError("PAYOUT_NOT_FOUND", f"no payout record {pid}", 404)
    if payout.status != "scheduled":
        raise PayoutError("PAYOUT_NOT_SCHEDULED",
                          f"payout {pid} is {payout.status!r}; only scheduled payouts settle", 409)
    if not TX_HASH_RE.match(tx_hash or ""):
        raise PayoutError("INVALID_TX_HASH",
                          "tx_hash must be a 0x-prefixed 64-hex EVM transaction hash", 422)
    if not builder_code or not valid_builder_code(builder_code):
        raise PayoutError("INVALID_BUILDER_CODE",
                          f"builder_code must match ^[a-z0-9_]{{1,32}}$ (got {builder_code!r})", 422)
    if referral_ref is not None and not REFERRAL_REF_RE.match(referral_ref):
        raise PayoutError("INVALID_REFERRAL_REF",
                          "referral_ref must be a crumbs receipt/journey id (rct_/jrn_ prefix)", 422)

    attribution = None
    proof_mode = "attestation"
    if calldata:
        try:
            attribution = parse_builder_code_suffix_from_calldata(calldata)
        except BuilderCodeError as exc:
            raise PayoutError("INVALID_CALLDATA", str(exc), 422) from exc
        if not attribution:
            raise PayoutError("ATTRIBUTION_NOT_FOUND",
                              "calldata carries no ERC-8021 Schema 2 builder-code suffix", 422)
        if not calldata_carries_code(calldata, builder_code):
            raise PayoutError("ATTRIBUTION_MISMATCH",
                              f"calldata attribution {attribution} does not carry {builder_code!r}", 422)
        proof_mode = "onchain"

    now = utcnow()
    payout.status = "settled"
    payout.settled_at = now
    payout.tx_hash = tx_hash.lower()
    payout.builder_code = builder_code
    payout.referral_ref = referral_ref
    payout.rail_ref = rail_ref
    payout.asset = asset
    payout.network = network
    payout.proof_mode = proof_mode

    splits = db.execute(select(Split).where(Split.payout_id == pid)).scalars().all()
    for split in splits:
        split.status = "settled"

    audit(
        db,
        "payout_settled",
        "payout",
        pid,
        actor=(executed_by or "rail")[:64],
        payload={
            "rail": payout.rail,
            "tx_hash": payout.tx_hash,
            "builder_code": builder_code,
            "referral_ref": referral_ref,
            "rail_ref": rail_ref,
            "asset": asset,
            "network": network,
            "proof_mode": proof_mode,
            "attribution": attribution,
            "split_count": len(splits),
            "note": "recorded proof of an off-ledger rail settlement; no funds held or moved here",
        },
    )
    db.commit()
    return {
        "pid": pid,
        "status": "settled",
        "settled_at": now.isoformat(),
        "rail": payout.rail,
        "tx_hash": payout.tx_hash,
        "builder_code": builder_code,
        "referral_ref": referral_ref,
        "rail_ref": rail_ref,
        "asset": asset,
        "network": network,
        "proof_mode": proof_mode,
        "attribution": attribution,
    }


def get_payout(db: Session, pid: str) -> dict | None:
    """Serialized payout record + splits (the proof envelope), or None."""
    payout = db.get(Payout, pid)
    if payout is None:
        return None
    splits = (
        db.execute(select(Split).where(Split.payout_id == pid).order_by(Split.party))
        .scalars()
        .all()
    )
    return {
        "pid": payout.pid,
        "rail": payout.rail,
        "status": payout.status,
        "scheduled_at": payout.scheduled_at.isoformat() if payout.scheduled_at else None,
        "settled_at": payout.settled_at.isoformat() if payout.settled_at else None,
        "tx_hash": payout.tx_hash,
        "builder_code": payout.builder_code,
        "referral_ref": payout.referral_ref,
        "rail_ref": payout.rail_ref,
        "asset": payout.asset,
        "network": payout.network,
        "proof_mode": payout.proof_mode,
        "splits": [
            {
                "split_id": s.split_id,
                "party": s.party,
                "amount_minor_units": s.amount_minor_units,
                "currency": s.currency,
                "pct_bps": s.pct_bps,
                "status": s.status,
            }
            for s in splits
        ],
    }
