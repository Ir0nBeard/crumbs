"""Shared pytest fixtures — Crumbs server test suite.

Env is pinned BEFORE any app import so get_settings() sees the test values.
Each test gets a fresh in-memory SQLite + fresh memory stores.
"""
from __future__ import annotations

import os

os.environ.setdefault("CRUMBS_DATABASE_URL", "sqlite://")
os.environ.setdefault(
    "CRUMBS_SIGNING_KEYS",
    "1:" + "ab" * 32,  # fixed test key (kid=1)
)
os.environ.setdefault("CRUMBS_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("CRUMBS_MERCHANT_API_KEY", "test-merchant-key")
os.environ.setdefault("CRUMBS_PAYOUTS_ENABLED", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import create_all, get_session_factory, reset_engine_for_tests  # noqa: E402


@pytest.fixture()
def db_session():
    """Fresh in-memory SQLite + session for each test."""
    reset_engine_for_tests("sqlite://")
    create_all()
    session = get_session_factory()()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    """ASGI test client with lifespan (fresh stores + signing per app instance)."""
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def seeded_merchant(db_session):
    """Merchant + program rows (kid=1 signing key; webhook secret known)."""
    from app.seed import seed_merchant

    return seed_merchant(db_session)


@pytest.fixture()
def issue_receipt(client, seeded_merchant):
    """Helper: POST /v1/journeys with consent -> parsed receipt dict + wire str."""

    def _issue(surface="browser", merchant_id=None, agent_id=None):
        mid = merchant_id or seeded_merchant.mid
        resp = client.post(
            "/v1/journeys",
            json={
                "merchant_id": mid,
                "surface": surface,
                "consent": {"basis": "explicit", "ref": "test-consent-1"},
                "agent_id": agent_id,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        return data  # {receipt, rid, journey_id, agent_id, exp, consent}

    return _issue
