"""Journey issuance API: consent gate, issuance shape, rate limiting."""
from __future__ import annotations

from test_consent_verifier import make_tc_string


def test_journey_requires_consent(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CONSENT_REQUIRED"


def test_journey_rejects_unknown_consent_basis(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={
            "merchant_id": seeded_merchant.mid,
            "surface": "browser",
            "consent": {"basis": "maybe?"},
        },
    )
    assert resp.status_code == 422  # pydantic pattern gate


def test_journey_issuance_shape(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={
            "merchant_id": seeded_merchant.mid,
            "surface": "chat",
            "consent": {"basis": "tcf", "ref": make_tc_string()},
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["rid"].startswith("rct_")
    assert data["journey_id"].startswith("jrn_")
    assert data["agent_id"].startswith("ag_")
    assert data["consent"] == {"basis": "tcf", "recorded": True,
                               "verified": "local"}
    # The wire receipt parses and verifies against the ledger's key
    from app.core.receipt import parse_receipt

    payload = parse_receipt(data["receipt"])
    assert payload["rid"] == data["rid"]
    assert payload["jid"] == data["journey_id"]
    assert payload["aid"] == data["agent_id"]
    assert payload["mid"] == seeded_merchant.mid
    assert payload["sf"] == "chat"
    assert payload["oid"] == ""
    assert payload["cv"] == 0
    assert payload["crb"] == 1200
    assert payload["ntb"] == 1500


def test_journey_unknown_merchant(client):
    resp = client.post(
        "/v1/journeys",
        json={
            "merchant_id": "m_doesnotexist",
            "surface": "browser",
            "consent": {"basis": "explicit"},
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "UNKNOWN_MERCHANT"


def test_journey_accepts_explicit_agent_id(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={
            "merchant_id": seeded_merchant.mid,
            "surface": "api",
            "consent": {"basis": "88b", "ref": "gpc-signal"},
            "agent_id": "ag_referringagent123",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["agent_id"] == "ag_referringagent123"


def test_journey_rate_limit(client, seeded_merchant, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "issuance_rate_limit", 3)
    for _ in range(3):
        resp = client.post(
            "/v1/journeys",
            json={
                "merchant_id": seeded_merchant.mid,
                "surface": "browser",
                "consent": {"basis": "explicit"},
            },
        )
        assert resp.status_code == 201, resp.text
    resp = client.post(
        "/v1/journeys",
        json={
            "merchant_id": seeded_merchant.mid,
            "surface": "browser",
            "consent": {"basis": "explicit"},
        },
    )
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "RATE_LIMITED"
