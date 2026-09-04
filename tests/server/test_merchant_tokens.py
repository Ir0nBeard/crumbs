"""Per-merchant keyed tokens + scoped CORS.

Covers: token issuance (plaintext shown once, hash at rest, audit), listing,
revocation, conversion scoping (token.mid == receipt.mid), origin allowlists
on tokens, the legacy shared-key fallback, the fail-closed
CRUMBS_REQUIRE_MERCHANT_TOKENS mode, the strict merchant_id/body contract,
and the CRUMBS_CORS_ORIGINS browser transport gate.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditEvent, MerchantToken
from app.seed import seed_merchant

ADMIN = {"X-Crumbs-Admin-Token": "test-admin-token"}


# --- local helpers (kept independent of test_conversions_api) -------------


def issue_receipt_str(client, mid: str) -> str:
    resp = client.post(
        "/v1/journeys",
        json={
            "merchant_id": mid,
            "surface": "browser",
            "consent": {"basis": "explicit", "ref": "tok-test-consent"},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["receipt"]


def stamp(client, receipt: str, *, key: str, mid: str, origin: str | None = None,
          oid: str = "ord_tok_1"):
    from app.core.receipt import parse_receipt

    payload = parse_receipt(receipt)
    headers = {"X-Crumbs-Key": key}
    if origin:
        headers["Origin"] = origin
    return client.post(
        "/v1/conversions",
        json={
            "receipt": receipt,
            "merchant_id": mid or payload["mid"],
            "order_id": oid,
            "cart_value_minor_units": 5000,
            "currency": "USD",
        },
        headers=headers,
    )


def issue_token(client, mid: str, *, label: str | None = None,
                origins: list[str] | None = None):
    body = {}
    if label:
        body["label"] = label
    if origins is not None:
        body["origins"] = origins
    return client.post(f"/v1/admin/merchants/{mid}/tokens", json=body, headers=ADMIN)


@pytest.fixture()
def second_merchant(db_session):
    return seed_merchant(db_session, name="Second Merchant")


# --- issuance / lifecycle ---------------------------------------------------


def test_issue_token_returns_plaintext_once_hash_at_rest(
    client, seeded_merchant, db_session
):
    resp = issue_token(client, seeded_merchant.mid, label="prod-site",
                       origins=["https://shop.example.com/"])
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["token"].startswith("cmk_")
    assert data["merchant_id"] == seeded_merchant.mid
    assert data["label"] == "prod-site"
    # trailing slash normalized away
    assert data["origins"] == ["https://shop.example.com"]
    assert "token_hash" not in data

    row = db_session.get(MerchantToken, data["token_id"])
    assert row is not None
    assert row.token_hash != data["token"]  # never the plaintext
    assert len(row.token_hash) == 64
    assert row.status == "active"

    # audit trail
    ev = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "merchant_token_issued")
    ).scalars().first()
    assert ev is not None
    assert ev.entity_id == data["token_id"]


def test_issue_token_rejects_bad_origin(client, seeded_merchant):
    resp = issue_token(client, seeded_merchant.mid,
                       origins=["https://shop.example.com/path"])
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "BAD_ORIGIN"


def test_list_tokens_metadata_only(client, seeded_merchant):
    issue_token(client, seeded_merchant.mid, label="a")
    resp = client.get(f"/v1/admin/merchants/{seeded_merchant.mid}/tokens",
                      headers=ADMIN)
    assert resp.status_code == 200
    tokens = resp.json()["tokens"]
    assert len(tokens) == 1
    assert tokens[0]["label"] == "a"
    assert "token" not in tokens[0] and "token_hash" not in tokens[0]


def test_admin_endpoints_require_admin_token(client, seeded_merchant):
    resp = client.post(f"/v1/admin/merchants/{seeded_merchant.mid}/tokens",
                       json={"label": "x"})
    assert resp.status_code == 401


def test_issue_token_unknown_merchant(client):
    resp = issue_token(client, "m_doesnotexist")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "MERCHANT_NOT_FOUND"


# --- conversion authorization -----------------------------------------------


def test_conversion_with_per_merchant_token(client, seeded_merchant, issue_receipt):
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    tok = issue_token(client, seeded_merchant.mid).json()["token"]
    resp = stamp(client, receipt, key=tok, mid=seeded_merchant.mid)
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending"


def test_conversion_token_merchant_scope_mismatch(
    client, seeded_merchant, second_merchant, issue_receipt
):
    # receipt belongs to merchant A; token belongs to merchant B -> 403
    # (body merchant_id is A — the receipt's merchant — so the strict
    # body/receipt contract passes and the TOKEN scope check must fire).
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    tok_b = issue_token(client, second_merchant.mid).json()["token"]
    resp = stamp(client, receipt, key=tok_b, mid=seeded_merchant.mid)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "TOKEN_MERCHANT_MISMATCH"


def test_conversion_bad_token(client, seeded_merchant, issue_receipt):
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    resp = stamp(client, receipt, key="cmk_not-a-real-token", mid=seeded_merchant.mid)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


def test_conversion_revoked_token(client, seeded_merchant, issue_receipt, db_session):
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    issued = issue_token(client, seeded_merchant.mid).json()
    rev = client.post(f"/v1/admin/tokens/{issued['token_id']}/revoke",
                      json={"reason": "rotating"}, headers=ADMIN)
    assert rev.status_code == 200
    assert rev.json()["status"] == "revoked"

    resp = stamp(client, receipt, key=issued["token"], mid=seeded_merchant.mid)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "TOKEN_REVOKED"

    row = db_session.get(MerchantToken, issued["token_id"])
    assert row.status == "revoked" and row.revoked_at is not None


def test_conversion_legacy_shared_key_still_accepted(
    client, seeded_merchant, issue_receipt
):
    # conftest sets CRUMBS_MERCHANT_API_KEY=test-merchant-key; require mode is
    # off by default, so the deprecated shared key keeps working.
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    resp = stamp(client, receipt, key="test-merchant-key", mid=seeded_merchant.mid)
    assert resp.status_code == 201, resp.text


def test_conversion_body_merchant_must_match_receipt(
    client, seeded_merchant, second_merchant, issue_receipt
):
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    resp = stamp(client, receipt, key="test-merchant-key", mid=second_merchant.mid)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "MERCHANT_MISMATCH"


# --- scoped CORS (per-token origin allowlist) -------------------------------


def test_conversion_token_origin_allowed(client, seeded_merchant, issue_receipt):
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    tok = issue_token(client, seeded_merchant.mid,
                      origins=["https://shop.example.com"]).json()["token"]
    resp = stamp(client, receipt, key=tok, mid=seeded_merchant.mid,
                 origin="https://shop.example.com")
    assert resp.status_code == 201, resp.text


def test_conversion_token_origin_denied(client, seeded_merchant, issue_receipt):
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    tok = issue_token(client, seeded_merchant.mid,
                      origins=["https://shop.example.com"]).json()["token"]
    resp = stamp(client, receipt, key=tok, mid=seeded_merchant.mid,
                 origin="https://evil.example")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "ORIGIN_NOT_ALLOWED"


def test_conversion_token_origin_absent_is_server_side(
    client, seeded_merchant, issue_receipt
):
    # No Origin header = server-to-server call; origin allowlist does not apply.
    receipt = issue_receipt_str(client, seeded_merchant.mid)
    tok = issue_token(client, seeded_merchant.mid,
                      origins=["https://shop.example.com"]).json()["token"]
    resp = stamp(client, receipt, key=tok, mid=seeded_merchant.mid)
    assert resp.status_code == 201, resp.text


# --- strict mode (CRUMBS_REQUIRE_MERCHANT_TOKENS) ---------------------------


@pytest.fixture()
def env_client(monkeypatch):
    """Factory: fresh app under explicit env (settings cache cleared on teardown)."""
    from app.config import get_settings
    from app.db.session import create_all, get_session_factory, reset_engine_for_tests

    made = []

    def _make(**env):
        reset_engine_for_tests("sqlite://")
        create_all()
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        from app.main import create_app

        client = TestClient(create_app())
        client.__enter__()
        made.append(client)
        return client, get_session_factory()()

    yield _make
    for c in made:
        c.__exit__(None, None, None)
    get_settings.cache_clear()  # drop patched-env settings before teardown undoes env


def test_strict_mode_rejects_legacy_key_and_missing_key(env_client):
    client, session = env_client(CRUMBS_REQUIRE_MERCHANT_TOKENS="true")
    merchant = seed_merchant(session, name="Strict Merchant")
    session.close()

    receipt = issue_receipt_str(client, merchant.mid)

    # legacy shared key -> rejected
    resp = stamp(client, receipt, key="test-merchant-key", mid=merchant.mid)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MERCHANT_TOKEN_REQUIRED"

    # no key -> rejected
    resp = stamp(client, receipt, key="", mid=merchant.mid)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MERCHANT_TOKEN_REQUIRED"

    # per-merchant token -> accepted
    tok = issue_token(client, merchant.mid).json()["token"]
    resp = stamp(client, receipt, key=tok, mid=merchant.mid, oid="ord_strict_2")
    assert resp.status_code == 201, resp.text


# --- app-level CORS gate ----------------------------------------------------


def test_cors_preflight_allowed_origin(env_client):
    client, session = env_client(CRUMBS_CORS_ORIGINS="https://shop.example.com")
    session.close()
    resp = client.options(
        "/v1/conversions",
        headers={
            "Origin": "https://shop.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-crumbs-key,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://shop.example.com"


def test_cors_preflight_denied_origin(env_client):
    client, session = env_client(CRUMBS_CORS_ORIGINS="https://shop.example.com")
    session.close()
    resp = client.options(
        "/v1/conversions",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-crumbs-key",
        },
    )
    # Starlette rejects the preflight (400) — either way the browser must not
    # see an allow header.
    assert "access-control-allow-origin" not in resp.headers


def test_cors_empty_env_is_fail_closed(env_client):
    # default: CRUMBS_CORS_ORIGINS empty -> no cross-origin browser access
    client, session = env_client()
    session.close()
    resp = client.options(
        "/v1/conversions",
        headers={
            "Origin": "https://shop.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in resp.headers


def test_origin_normalization_and_validation():
    from app.services import merchant_auth

    assert merchant_auth.validate_origin("https://shop.example.com") == (
        "https://shop.example.com")
    assert merchant_auth.validate_origin("https://shop.example.com/") == (
        "https://shop.example.com")
    assert merchant_auth.normalize_origins(
        ["https://b.example", "https://a.example", "https://b.example"]
    ) == ["https://a.example", "https://b.example"]
    with pytest.raises(ValueError):
        merchant_auth.validate_origin("https://shop.example.com/path")
    with pytest.raises(ValueError):
        merchant_auth.validate_origin("ftp://shop.example.com")
    with pytest.raises(ValueError):
        merchant_auth.validate_origin("not-a-url")
    # unparseable stored JSON fails closed to []
    assert merchant_auth._parse_origins("{not json") == []
