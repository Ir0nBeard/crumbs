"""Seed / fixture builders — used by tests and local dev demos.

Creates the merchant + program + (optionally) agent/owner rows a journey or
conversion needs. NOT an admin API — a dev utility (and the seed data model for
the test suite).
"""
from __future__ import annotations

from .core.receipt import new_agent_id, new_merchant_id
from .core.ulid import make_ulid
from .db.models import Agent, AgentOwner, Merchant, MerchantProgram
DEFAULT_WEBHOOK_SECRET = "dev-webhook-secret-do-not-use-in-prod"


def seed_merchant(
    db,
    *,
    mid: str | None = None,
    name: str = "Test Merchant",
    owner_id: str | None = None,
    commission_rate_bps: int = 1200,
    network_take_bps: int = 1500,
    self_referral_policy: str = "forbid",
    webhook_secret: str = DEFAULT_WEBHOOK_SECRET,
    program_name: str = "default",
) -> Merchant:
    """Insert a merchant + one active program. Returns the Merchant row."""
    mid = mid or new_merchant_id()
    merchant = db.get(Merchant, mid)
    if merchant is None:
        merchant = Merchant(mid=mid, name=name, owner_id=owner_id,
                            webhook_secret=webhook_secret)
        db.add(merchant)
        db.flush()
    program = MerchantProgram(
        program_id="prg_" + make_ulid(),
        mid=mid,
        name=program_name,
        commission_rate_bps=commission_rate_bps,
        network_take_bps=network_take_bps,
        self_referral_policy=self_referral_policy,
        status="active",
    )
    db.add(program)
    db.commit()
    return merchant


def seed_agent(db, *, aid: str | None = None, owner_id: str | None = None) -> Agent:
    aid = aid or new_agent_id()
    agent = db.get(Agent, aid)
    if agent is None:
        agent = Agent(aid=aid, owner_id=owner_id, status="active")
        db.add(agent)
        db.commit()
    return agent


def seed_agent_owner(db, *, owner_id: str | None = None) -> AgentOwner:
    owner_id = owner_id or "own_" + make_ulid()
    owner = db.get(AgentOwner, owner_id)
    if owner is None:
        owner = AgentOwner(owner_id=owner_id, display_name="Test Owner", status="active")
        db.add(owner)
        db.commit()
    return owner
