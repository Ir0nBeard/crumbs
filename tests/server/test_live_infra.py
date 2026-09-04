"""Live-infrastructure tests — Postgres + Redis.

These tests exercise the real production stack the SQLite suite cannot:

* the canonical migration SQL (``server/migrations/*.sql``) applied to a real
  Postgres server — clean apply, idempotent re-run, and table parity with the
  SQLAlchemy models;
* the service layer (journey -> receipt -> conversion -> verify) against
  Postgres, covering dialect-specific behaviour (timestamptz columns, row
  locks, advisory locks);
* Redis-backed nonce/rate-limit stores, including the fail-closed startup
  behaviour and fail-closed semantics when Redis is unreachable mid-flight;
* concurrent conversion recording on Postgres, asserting budget counters never
  overshoot their caps and the distinct-merchant counter never drifts above
  the true distinct count.

The tests are gated on purpose: set ``CRUMBS_LIVE_TEST_DB_URL`` (Postgres DSN)
and ``CRUMBS_LIVE_TEST_REDIS_URL`` (point it at a dedicated test Redis, e.g.
``redis://127.0.0.1:6379/15``) to enable them. The default CI run (SQLite
only) skips them; configure a Postgres service in CI if you want them there.

The fail-closed startup test needs no live services (it targets a closed
port) and runs everywhere.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

LIVE_DB_URL = os.environ.get("CRUMBS_LIVE_TEST_DB_URL", "").strip()
LIVE_REDIS_URL = os.environ.get("CRUMBS_LIVE_TEST_REDIS_URL", "").strip()

requires_pg = pytest.mark.skipif(
    not LIVE_DB_URL, reason="CRUMBS_LIVE_TEST_DB_URL not set (Postgres live tests off)"
)
requires_redis = pytest.mark.skipif(
    not LIVE_REDIS_URL,
    reason="CRUMBS_LIVE_TEST_REDIS_URL not set (Redis live tests off)",
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "server" / "migrations"

KEY = bytes.fromhex("ab" * 32)
SIGNING_ENV = "1:" + "ab" * 32

EXPECTED_TABLES = {
    "agent_owners", "agents", "merchants", "merchant_programs",
    "journeys", "receipts", "conversions", "splits", "payouts",
    "disputes", "revoked_receipts", "revoked_journeys", "revoked_agents",
    "used_nonces", "audit_events", "merchant_tokens",
}


# ---------------------------------------------------------------------------
# Postgres fixtures
# ---------------------------------------------------------------------------


def _connect():
    psycopg2 = pytest.importorskip("psycopg2")
    return psycopg2.connect(LIVE_DB_URL)


def _apply_migrations(conn) -> None:
    """Drop and recreate the public schema from the canonical migration SQL."""
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
    cur.execute("CREATE SCHEMA public")
    for sql_file in ("0001_init.sql", "0002_merchant_tokens.sql"):
        cur.execute((MIGRATIONS_DIR / sql_file).read_text())
    conn.close()


@pytest.fixture()
def pg_schema():
    """A freshly migrated Postgres schema for each test."""
    if not LIVE_DB_URL:
        pytest.skip("CRUMBS_LIVE_TEST_DB_URL not set")
    _apply_migrations(_connect())
    yield LIVE_DB_URL


def _pg_session(url: str):
    """A session bound to the given Postgres URL on the app engine."""
    from app.db.session import get_session_factory, reset_engine_for_tests

    reset_engine_for_tests(url)
    return get_session_factory()()


def _seed(session) -> tuple:
    """Owner + agent + merchant + program rows; returns (owner, agent, merchant)."""
    from app.seed import seed_agent, seed_agent_owner, seed_merchant

    owner = seed_agent_owner(session)
    agent = seed_agent(session, owner_id=owner.owner_id)
    merchant = seed_merchant(session, commission_rate_bps=1200,
                             network_take_bps=1500, owner_id="owner_other")
    session.commit()
    return owner, agent, merchant


def _live_settings(**overrides) -> "Settings":
    from app.config import Settings

    base = dict(
        signing_keys=SIGNING_ENV,
        database_url=LIVE_DB_URL,
        payouts_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def _issue_journey(session, svc, stores, settings, mid, agent_id):
    from app.services.ledger import issue_journey

    return issue_journey(
        session, mid=mid, surface="browser",
        consent={"basis": "explicit", "ref": "live-t1"},
        client_ip="203.0.113.9", user_agent="live-test",
        agent_id=agent_id, signing=svc, nonce_store=stores[0],
        rate_limiter=stores[1], settings=settings,
    )


def _receipt(session, svc, settings, journey, mid):
    from app.services.ledger import issue_receipt_for_journey

    return issue_receipt_for_journey(
        session, jid=journey["journey_id"], mid=mid,
        signing=svc, settings=settings,
    )["receipt"]


def _convert(session, svc, stores, settings, receipt_str, oid, value=100):
    from app.services.ledger import record_conversion

    return record_conversion(
        session, receipt_str=receipt_str, oid=oid,
        cart_value_minor_units=value, currency="USD", surface="browser",
        signing=svc, nonce_store=stores[0], rate_limiter=stores[1],
        settings=settings,
    )


# ---------------------------------------------------------------------------
# 1. Migrations on real Postgres
# ---------------------------------------------------------------------------


@requires_pg
def test_migrations_apply_and_rerun_idempotently(pg_schema):
    """0001 + 0002 apply cleanly to Postgres and re-run without error."""
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
    )
    tables = {r[0] for r in cur.fetchall()}
    assert tables == EXPECTED_TABLES, tables ^ EXPECTED_TABLES

    # Money columns are integer minor units, never floats.
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='conversions' AND column_name='cart_value_minor_units'"
    )
    assert cur.fetchone()[0] in ("bigint", "integer")
    cur.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='journeys' AND column_name='conversions_used'"
    )
    assert cur.fetchone()[0] in ("bigint", "integer")

    # Item-1 settlement-proof columns present on payouts.
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='payouts' AND column_name IN "
        "('builder_code','proof_mode','referral_ref','tx_hash')"
    )
    cols = {r[0] for r in cur.fetchall()}
    assert cols == {"builder_code", "proof_mode", "referral_ref", "tx_hash"}

    # Unique conversion idempotency constraint present.
    cur.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='conversions'::regclass AND contype='u'"
    )
    assert {r[0] for r in cur.fetchall()} == {"uq_conversions_rid_oid"}

    # Re-running both files is a no-op (IF NOT EXISTS everywhere).
    cur.execute((MIGRATIONS_DIR / "0001_init.sql").read_text())
    cur.execute((MIGRATIONS_DIR / "0002_merchant_tokens.sql").read_text())
    conn.close()


@requires_pg
def test_model_metadata_matches_migration_ddl(pg_schema):
    """Every ORM table exists in the migrated schema and vice versa."""
    from app.db.models import Base

    model_tables = {t.name for t in Base.metadata.sorted_tables}
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    db_tables = {r[0] for r in cur.fetchall()}
    conn.close()
    assert model_tables == EXPECTED_TABLES
    assert db_tables == EXPECTED_TABLES


# ---------------------------------------------------------------------------
# 2. Service layer on Postgres
# ---------------------------------------------------------------------------


@requires_pg
def test_service_roundtrip_on_postgres(pg_schema):
    """Journey -> receipt -> conversion -> verify against real Postgres."""
    from app.services.ledger import (
        E_BUDGET_EXCEEDED,
        E_REPLAYED,
        LedgerError,
        verify_receipt,
    )
    from app.signing import SigningService
    from app.stores import MemoryNonceStore, MemoryRateLimiter

    session = _pg_session(LIVE_DB_URL)
    svc = SigningService(keys={1: KEY}, default_kid=1)
    stores = MemoryNonceStore(), MemoryRateLimiter()
    settings = _live_settings(budget_max_conversions=5,
                              budget_max_merchants=5,
                              budget_max_cart_value_usd=5000,
                              self_referral_max_conversions=100)

    _, agent, merchant = _seed(session)
    journey = _issue_journey(session, svc, stores, settings,
                             merchant.mid, agent.aid)
    receipt = _receipt(session, svc, settings, journey, merchant.mid)

    result = _convert(session, svc, stores, settings, receipt, "o1", 1000)
    assert result["status"] == "pending"

    # Fresh session: counters advanced exactly once.
    fresh = _pg_session(LIVE_DB_URL)
    from app.db.models import Conversion, Journey

    j = fresh.get(Journey, journey["journey_id"])
    assert j.conversions_used == 1
    assert j.merchants_used == 1
    assert fresh.query(Conversion).filter_by(jid=journey["journey_id"]).count() == 1

    # Spent receipt now fails verification (nonce consumed).
    verdict = verify_receipt(fresh, receipt, svc, stores[0], settings)
    assert verdict["valid"] is False
    assert verdict["reason"] == E_REPLAYED

    # Budget rejects a conversion beyond the cart cap.
    big_receipt = _receipt(fresh, svc, settings, journey, merchant.mid)
    from app.services.ledger import record_conversion

    with pytest.raises(LedgerError) as exc:
        record_conversion(
            fresh, receipt_str=big_receipt, oid="o-big",
            cart_value_minor_units=999999, currency="USD", surface="browser",
            signing=svc, nonce_store=stores[0], rate_limiter=stores[1],
            settings=settings,
        )
    assert exc.value.code == E_BUDGET_EXCEEDED
    fresh.close()
    session.close()



# ---------------------------------------------------------------------------
# 3. Redis fail-closed + store semantics
# ---------------------------------------------------------------------------


def test_build_stores_fails_closed_when_redis_unreachable(monkeypatch):
    """CRUMBS_REDIS_URL configured but unreachable -> refuse to start."""
    from app.config import get_settings
    from app.stores import build_stores

    monkeypatch.setenv("CRUMBS_REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="refusing"):
            build_stores()
    finally:
        monkeypatch.delenv("CRUMBS_REDIS_URL", raising=False)
        get_settings.cache_clear()


@requires_redis
def test_redis_store_semantics():
    """Nonce dedup + rate-limit windows behave atomically on real Redis."""
    import redis as redis_lib

    from app.stores import RedisNonceStore, RedisRateLimiter

    client = redis_lib.Redis.from_url(LIVE_REDIS_URL, decode_responses=True)
    client.flushdb()

    nonce = RedisNonceStore(client)
    # First claim wins; replay is rejected; presence is visible.
    assert nonce.mark_used("r_live_1", "nc1", 300) is True
    assert nonce.mark_used("r_live_1", "nc1", 300) is False
    assert nonce.is_used("r_live_1") is True
    assert nonce.mark_used("r_live_2", "nc2", 300) is True

    limiter = RedisRateLimiter(client)
    allowed = [limiter.hit("scope", "k1", 3, 60) for _ in range(4)]
    assert [a for a, _ in allowed] == [True, True, True, False]
    assert allowed[-1][1] == 4  # counter keeps counting past the limit
    client.flushdb()
    client.close()


@requires_redis
def test_redis_store_fails_closed_mid_flight():
    """A dead Redis must RAISE on use — never silently accept a replay/rate hit."""
    import redis as redis_lib

    from app.stores import RedisNonceStore, RedisRateLimiter

    dead = redis_lib.Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=1
    )
    with pytest.raises(redis_lib.exceptions.ConnectionError):
        RedisNonceStore(dead).mark_used("r_x", "nc", 60)
    with pytest.raises(redis_lib.exceptions.ConnectionError):
        RedisRateLimiter(dead).hit("s", "k", 5, 60)
    dead.close()


# ---------------------------------------------------------------------------
# 4. Concurrent budget / velocity races on Postgres
# ---------------------------------------------------------------------------


def _run_concurrent(fn, n_threads):
    """Run fn(i) in n_threads; return per-thread results in a list."""
    barrier = threading.Barrier(n_threads)
    results: list = [None] * n_threads
    errors: list = [None] * n_threads

    def _worker(i: int):
        try:
            barrier.wait()
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001 — collected per thread
            errors[i] = exc

    threads = [threading.Thread(target=_worker, args=(i,))
               for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(errors), errors
    return results


@requires_pg
def test_concurrent_conversions_never_overshoot_budget(pg_schema):
    """Exactly max_conversions conversions survive concurrent recording."""
    from app.services.ledger import (
        E_BUDGET_EXCEEDED,
        LedgerError,
    )
    from app.signing import SigningService
    from app.stores import MemoryNonceStore, MemoryRateLimiter

    session = _pg_session(LIVE_DB_URL)
    svc = SigningService(keys={1: KEY}, default_kid=1)
    shared = MemoryNonceStore(), MemoryRateLimiter()
    settings = _live_settings(budget_max_conversions=3,
                              budget_max_merchants=5,
                              budget_max_cart_value_usd=500000,
                              self_referral_max_conversions=100)
    _, agent, merchant = _seed(session)
    journey = _issue_journey(session, svc, shared, settings,
                             merchant.mid, agent.aid)
    jid = journey["journey_id"]
    mid = merchant.mid
    session.close()

    def worker(i: int) -> str:
        from app.services.ledger import record_conversion

        outcomes = []
        s = _pg_session(LIVE_DB_URL)
        for k in range(6):
            receipt = _receipt(s, svc, settings,
                               {"journey_id": jid}, mid)
            try:
                record_conversion(
                    s, receipt_str=receipt, oid=f"o{i}-{k}",
                    cart_value_minor_units=100, currency="USD",
                    surface="browser", signing=svc, nonce_store=shared[0],
                    rate_limiter=shared[1], settings=settings,
                )
                outcomes.append("ok")
            except LedgerError as exc:
                outcomes.append(exc.code)
        s.close()
        return outcomes

    results = _run_concurrent(worker, 6)
    flat = [o for r in results for o in r]
    assert flat.count("ok") == 3, flat
    assert flat.count(E_BUDGET_EXCEEDED) == 36 - 3, flat

    fresh = _pg_session(LIVE_DB_URL)
    from app.db.models import Conversion, Journey

    j = fresh.get(Journey, jid)
    assert j.conversions_used == 3
    assert fresh.query(Conversion).filter_by(jid=jid).count() == 3
    fresh.close()


@requires_pg
def test_concurrent_first_merchant_does_not_drift_counter(pg_schema):
    """Concurrent conversions at one first-time merchant keep merchants_used
    at the true distinct count (1), not one increment per race winner."""
    from app.services.ledger import LedgerError
    from app.signing import SigningService
    from app.stores import MemoryNonceStore, MemoryRateLimiter

    session = _pg_session(LIVE_DB_URL)
    svc = SigningService(keys={1: KEY}, default_kid=1)
    shared = MemoryNonceStore(), MemoryRateLimiter()
    settings = _live_settings(budget_max_conversions=40,
                              budget_max_merchants=40,
                              budget_max_cart_value_usd=500000,
                              self_referral_max_conversions=1000)
    _, agent, merchant = _seed(session)
    journey = _issue_journey(session, svc, shared, settings,
                             merchant.mid, agent.aid)
    jid = journey["journey_id"]
    mid = merchant.mid
    session.close()

    def worker(i: int) -> int:
        from app.services.ledger import record_conversion

        ok = 0
        s = _pg_session(LIVE_DB_URL)
        for k in range(4):
            receipt = _receipt(s, svc, settings,
                               {"journey_id": jid}, mid)
            try:
                record_conversion(
                    s, receipt_str=receipt, oid=f"m{i}-{k}",
                    cart_value_minor_units=100, currency="USD",
                    surface="browser", signing=svc, nonce_store=shared[0],
                    rate_limiter=shared[1], settings=settings,
                )
                ok += 1
            except LedgerError:
                pass
        s.close()
        return ok

    results = _run_concurrent(worker, 8)
    assert sum(results) == 8 * 4  # generous caps: every attempt converts

    fresh = _pg_session(LIVE_DB_URL)
    from sqlalchemy import func, select

    from app.db.models import Conversion, Journey

    j = fresh.get(Journey, jid)
    distinct = fresh.execute(
        select(func.count(func.distinct(Conversion.mid)))
        .where(Conversion.jid == jid)
    ).scalar_one()
    assert j.conversions_used == 32
    assert j.merchants_used == distinct == 1, (
        "merchants_used drifted above the true distinct-merchant count"
    )
    fresh.close()


@requires_pg
def test_concurrent_velocity_window_does_not_overshoot(pg_schema):
    """Self-referral velocity caps (agent, merchant, window) hold under
    concurrency across DIFFERENT journeys of the same agent."""
    from app.services.ledger import (
        E_VELOCITY_EXCEEDED,
        LedgerError,
    )
    from app.signing import SigningService
    from app.stores import MemoryNonceStore, MemoryRateLimiter

    session = _pg_session(LIVE_DB_URL)
    svc = SigningService(keys={1: KEY}, default_kid=1)
    shared = MemoryNonceStore(), MemoryRateLimiter()
    settings = _live_settings(
        budget_max_conversions=50, budget_max_merchants=50,
        budget_max_cart_value_usd=500000,
        self_referral_max_conversions=3,
        self_referral_window_seconds=3600,
    )
    _, agent, merchant = _seed(session)
    agent_id = agent.aid
    j1 = _issue_journey(session, svc, shared, settings, merchant.mid, agent_id)
    j2 = _issue_journey(session, svc, shared, settings, merchant.mid, agent_id)
    jids = [j1["journey_id"], j2["journey_id"]]
    mid = merchant.mid
    session.close()

    def worker(i: int) -> list[str]:
        from app.services.ledger import record_conversion

        outcomes = []
        s = _pg_session(LIVE_DB_URL)
        for k in range(6):
            jid = jids[(i + k) % 2]  # alternate journeys of the same agent
            receipt = _receipt(s, svc, settings, {"journey_id": jid}, mid)
            try:
                record_conversion(
                    s, receipt_str=receipt, oid=f"v{i}-{k}",
                    cart_value_minor_units=100, currency="USD",
                    surface="browser", signing=svc, nonce_store=shared[0],
                    rate_limiter=shared[1], settings=settings,
                )
                outcomes.append("ok")
            except LedgerError as exc:
                outcomes.append(exc.code)
        s.close()
        return outcomes

    results = _run_concurrent(worker, 6)
    flat = [o for r in results for o in r]
    assert flat.count("ok") == 3, flat
    assert flat.count(E_VELOCITY_EXCEEDED) == 36 - 3, flat

    fresh = _pg_session(LIVE_DB_URL)
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.db.models import Conversion, Journey

    window_start = datetime.now(timezone.utc) - timedelta(hours=1)
    count = fresh.execute(
        select(func.count(Conversion.cid)).where(
            Conversion.jid.in_(
                select(Journey.jid).where(Journey.aid == agent_id)
            ),
            Conversion.mid == mid,
            Conversion.created_at >= window_start,
        )
    ).scalar_one()
    assert count == 3, count
    fresh.close()
