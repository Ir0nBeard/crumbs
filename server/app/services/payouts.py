"""Payout scheduling — records ONLY, no float.

The commission split is computed and payout rows are SCHEDULED here; actual
settlement (x402/USDC via a CDP facilitator, or Stripe Connect) is a STUB —
env-gated and NOT implemented in v0.1. Crumbs never self-holds funds; payout
records are an accounting ledger that licensed rails execute against.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.ulid import make_ulid
from ..db.models import Agent, Conversion, Payout, Split
from ..db.session import audit


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
                       "note": "settlement execution is a STUB (x402/CDP facilitator)"})
        scheduled += 1

    db.commit()
    return {"scheduled": scheduled, "rail": "x402", "settled": 0,
            "note": "scheduling records only — actual settlement is a STUB (env-gated)"}
