"""Budget enforcement + self-referral velocity — service level, with
explicit Settings so caps are deterministic."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.core.receipt import parse_receipt
from app.db.session import get_session_factory
from app.seed import seed_agent, seed_agent_owner, seed_merchant
from app.services.ledger import (
    E_BUDGET_EXCEEDED,
    E_SELF_REFERRAL,
    E_VELOCITY_EXCEEDED,
    LedgerError,
    issue_journey,
    issue_receipt_for_journey,
    record_conversion,
)
from app.signing import SigningService
from app.stores import MemoryNonceStore, MemoryRateLimiter

KEY = bytes.fromhex("ab" * 32)

# Default tight caps: 10 conversions (headroom), 3 merchants, $50.00 cart (5000 minor)
TIGHT = Settings(
    budget_max_conversions=10,
    budget_max_merchants=3,
    budget_max_cart_value_usd=5000,
    self_referral_max_conversions=3,
    self_referral_window_seconds=3600,
    signing_keys="1:" + "ab" * 32,
    database_url="sqlite://",
)

# Conversion-cap settings: 2 conversions, ample merchants/cart
CONV_CAP = Settings(
    budget_max_conversions=2,
    budget_max_merchants=10,
    budget_max_cart_value_usd=500000,
    self_referral_max_conversions=3,
    self_referral_window_seconds=3600,
    signing_keys="1:" + "ab" * 32,
    database_url="sqlite://",
)


@pytest.fixture()
def db():
    from app.db.session import create_all, reset_engine_for_tests

    reset_engine_for_tests("sqlite://")
    create_all()
    session = get_session_factory()()
    yield session
    session.close()


@pytest.fixture()
def svc():
    return SigningService(keys={1: KEY}, default_kid=1)


@pytest.fixture()
def stores():
    return MemoryNonceStore(), MemoryRateLimiter()


def _journey(db, svc, stores, mid, surface="browser", agent_id=None, settings=TIGHT):
    result = issue_journey(
        db,
        mid=mid,
        surface=surface,
        consent={"basis": "explicit", "ref": "t1"},
        client_ip="203.0.113.9",
        user_agent="test",
        agent_id=agent_id,
        signing=svc,
        nonce_store=stores[0],
        rate_limiter=stores[1],
        settings=settings,
    )
    return result


def _receipt_on(db, svc, stores, journey, mid, settings=TIGHT):
    """A fresh receipt on the SAME journey (one receipt = one conversion)."""
    return issue_receipt_for_journey(db, jid=journey["journey_id"], mid=mid,
                                     signing=svc, settings=settings)["receipt"]


def _convert(db, svc, stores, receipt_str, oid, value, currency="USD", mid=None,
             settings=TIGHT):
    return record_conversion(
        db,
        receipt_str=receipt_str,
        oid=oid,
        cart_value_minor_units=value,
        currency=currency,
        surface="browser",
        signing=svc,
        nonce_store=stores[0],
        rate_limiter=stores[1],
        settings=settings,
    )


def test_conversion_budget_cap(db, svc, stores):
    m1 = seed_merchant(db, commission_rate_bps=1200, network_take_bps=1500)
    j = _journey(db, svc, stores, m1.mid, settings=CONV_CAP)

    assert _convert(db, svc, stores, j["receipt"], "o1", 1000, settings=CONV_CAP)["status"] == "pending"
    r2 = _receipt_on(db, svc, stores, j, m1.mid, settings=CONV_CAP)
    assert _convert(db, svc, stores, r2, "o2", 1000, settings=CONV_CAP)["status"] == "pending"
    # Third conversion for same journey exceeds max_conversions=2
    r3 = _receipt_on(db, svc, stores, j, m1.mid, settings=CONV_CAP)
    with pytest.raises(LedgerError) as exc:
        _convert(db, svc, stores, r3, "o3", 1000, settings=CONV_CAP)
    assert exc.value.code == E_BUDGET_EXCEEDED


def test_merchant_budget_cap(db, svc, stores):
    """One journey may convert at max_merchants distinct merchants."""
    merchants = [
        seed_merchant(db, commission_rate_bps=1000, network_take_bps=1500) for _ in range(4)
    ]
    j = _journey(db, svc, stores, merchants[0].mid)

    for i in range(3):
        r = _receipt_on(db, svc, stores, j, merchants[i].mid)
        assert _convert(db, svc, stores, r, f"m{i}", 100)["status"] == "pending"
    # 4th distinct merchant exceeds max_merchants=3
    r4 = _receipt_on(db, svc, stores, j, merchants[3].mid)
    with pytest.raises(LedgerError) as exc:
        _convert(db, svc, stores, r4, "m3", 100)
    assert exc.value.code == E_BUDGET_EXCEEDED


def test_cart_value_budget_cap(db, svc, stores):
    m1 = seed_merchant(db, commission_rate_bps=1200, network_take_bps=1500)
    j = _journey(db, svc, stores, m1.mid)

    assert _convert(db, svc, stores, j["receipt"], "c1", 3000)["status"] == "pending"
    # 3000 + 3000 > 5000 cap -> rejected
    r2 = _receipt_on(db, svc, stores, j, m1.mid)
    with pytest.raises(LedgerError) as exc:
        _convert(db, svc, stores, r2, "c2", 3000)
    assert exc.value.code == E_BUDGET_EXCEEDED


def test_cart_value_budget_cross_currency(db, svc, stores):
    """Cross-currency normalization via the FX table (EUR 1.09 -> caps in USD)."""
    m1 = seed_merchant(db, commission_rate_bps=1200, network_take_bps=1500)
    j = _journey(db, svc, stores, m1.mid)
    assert _convert(db, svc, stores, j["receipt"], "x1", 4000, currency="EUR")["status"] == "pending"
    # EUR 4000 * 1.09 = USD 4360; +1000 USD = 5360 > 5000 -> reject
    r2 = _receipt_on(db, svc, stores, j, m1.mid)
    with pytest.raises(LedgerError) as exc:
        _convert(db, svc, stores, r2, "x2", 1000, currency="USD")
    assert exc.value.code == E_BUDGET_EXCEEDED


def test_self_referral_rejected(db, svc, stores):
    """Merchant-owned agent cannot refer its own merchant (policy: forbid)."""
    owner = seed_agent_owner(db)
    agent = seed_agent(db, owner_id=owner.owner_id)
    merchant = seed_merchant(db, owner_id=owner.owner_id, self_referral_policy="forbid")

    j = _journey(db, svc, stores, merchant.mid, agent_id=agent.aid)
    with pytest.raises(LedgerError) as exc:
        _convert(db, svc, stores, j["receipt"], "sr1", 1000)
    assert exc.value.code == E_SELF_REFERRAL


def test_self_referral_velocity(db, svc, stores):
    """Same (agent, merchant) conversions in a window: velocity cap (limit 3)."""
    owner = seed_agent_owner(db)
    agent = seed_agent(db, owner_id=owner.owner_id)
    # Different owner -> not blocked by ownership, only by velocity
    merchant = seed_merchant(db, owner_id="own_other_owner", self_referral_policy="forbid")

    j = _journey(db, svc, stores, merchant.mid, agent_id=agent.aid)
    for i in range(3):
        r = _receipt_on(db, svc, stores, j, merchant.mid)
        assert _convert(db, svc, stores, r, f"v{i}", 100)["status"] == "pending"
    r4 = _receipt_on(db, svc, stores, j, merchant.mid)
    with pytest.raises(LedgerError) as exc:
        _convert(db, svc, stores, r4, "v3", 100)
    assert exc.value.code == E_VELOCITY_EXCEEDED


def test_failed_budget_attempt_does_not_burn_receipt(db, svc, stores):
    """nonce is consumed only after every reject-check — a failed budget
    attempt leaves the receipt reusable with corrected values."""
    m1 = seed_merchant(db, commission_rate_bps=1200, network_take_bps=1500)
    j = _journey(db, svc, stores, m1.mid)
    receipt = j["receipt"]

    with pytest.raises(LedgerError) as exc:
        _convert(db, svc, stores, receipt, "big", 6000)  # exceeds $50 cart cap
    assert exc.value.code == E_BUDGET_EXCEEDED

    # Same receipt, corrected (small) value — must still convert (nonce intact)
    result = _convert(db, svc, stores, receipt, "small", 1000)
    assert result["status"] == "pending"


def test_self_referral_allowed_by_policy(db, svc, stores):
    """Program with self_referral_policy=allow skips the ownership check."""
    owner = seed_agent_owner(db)
    agent = seed_agent(db, owner_id=owner.owner_id)
    merchant = seed_merchant(db, owner_id=owner.owner_id, self_referral_policy="allow")
    j = _journey(db, svc, stores, merchant.mid, agent_id=agent.aid)
    assert _convert(db, svc, stores, j["receipt"], "ok1", 100)["status"] == "pending"


def test_receipt_roundtrip_parse_and_budget_state(db, svc, stores):
    m1 = seed_merchant(db, commission_rate_bps=1200, network_take_bps=1500)
    j = _journey(db, svc, stores, m1.mid)
    parsed = parse_receipt(j["receipt"])
    assert parsed["rid"] == j["rid"]
    assert parsed["jid"] == j["journey_id"]
    # Wire size stays under the 1 KB design rule
    assert len(j["receipt"]) < 700
