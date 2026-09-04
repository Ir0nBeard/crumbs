"""Consent verifier (services/consent.py) — replaces the v0.1 501 stub.

Real-signal fixtures:
- make_tc_string() builds spec-shaped IAB Europe TCF EU v2 TC strings
  (base64, standard alphabet, the fixed 200-bit prefix + publisher country
  code + tail), so the API tests exercise the real decode path.
- make_gpp_string() builds spec-shaped GPP strings (websafe-base64 header
  with Type=3/Version=1 => leading chars "DB", "~"-joined sections). v1 GPP
  checks are shape-only by design — see the module docstring.
"""
from __future__ import annotations

import base64
import time

import httpx
import pytest

from app.services import consent as cmod
from app.services.consent import CONSENT_INVALID, ConsentError, parse_tc_string


# ---------------------------------------------------------------------------
# Signal builders (test fixtures)
# ---------------------------------------------------------------------------

def purposes(*ids: int) -> int:
    """Bitmask with TCF purpose N set (purpose N = bit (24 - N), MSB-first)."""
    return sum(1 << (24 - i) for i in ids)


def make_tc_string(*, created: int | None = None,
                   purpose_consents: int | None = None,
                   consent_language=(5, 14),  # "en" with a=1
                   tcf_policy_version: int = 3,
                   version: int = 2,
                   cmp_id: int = 27) -> str:
    """Build a structurally valid IAB TCF EU v2 TC string (default: purposes
    1+2 consented, created 1h ago -> passes every local check)."""
    created = int(time.time()) - 3600 if created is None else int(created)
    if purpose_consents is None:
        purpose_consents = purposes(1, 2)
    last_updated = created
    lang_bits = (consent_language[0] << 6) | consent_language[1]
    fields = [
        (version, 6),            # version
        (created, 36),           # created
        (last_updated, 36),      # lastUpdated
        (cmp_id, 12),            # cmpId
        (1, 12),                 # cmpVersion
        (0, 6),                  # consentScreen
        (lang_bits, 12),         # consentLanguage
        (3, 12),                 # vendorListVersion
        (tcf_policy_version, 6),  # tcfPolicyVersion
        (0, 1),                  # isServiceSpecific
        (0, 1),                  # useNonStandardStacks
        (0, 12),                 # specialFeatureOptins
        (purpose_consents, 24),  # purposeConsents
        (0, 24),                 # purposeLegitimateInterests
        ((7 << 6) | 2, 12),      # publisherCountryCode "gb"
        (0, 6),                  # numCustomPurposes (tail padding)
        (0, 1),                  # tail bits (not parsed by the verifier)
    ]
    buf, nbits = 0, 0
    for value, width in fields:
        buf = (buf << width) | (value & ((1 << width) - 1))
        nbits += width
    while nbits % 8:
        buf <<= 1
        nbits += 1
    return base64.b64encode(buf.to_bytes(nbits // 8, "big")).decode()


def make_gpp_string(*sections: str) -> str:
    """Build a GPP string: header (Type=3, Version=1 -> "DB") + sections."""
    return "DB" + ("~" + "~".join(sections) if sections else "")


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

def test_parse_tc_string_roundtrip():
    parsed = parse_tc_string(make_tc_string())
    assert parsed["version"] == 2
    assert parsed["consent_language"] == "en"
    assert parsed["purpose1"] is True
    assert parsed["purposes_consented"] == [1, 2]
    assert abs(parsed["created"] - (int(time.time()) - 3600)) <= 5


def test_parse_tc_string_purpose_bit_positions():
    parsed = parse_tc_string(make_tc_string(purpose_consents=purposes(3)))
    assert parsed["purpose1"] is False
    assert parsed["purposes_consented"] == [3]


def test_parse_tc_string_rejects_garbage():
    for bad in ("", "A", "###!", "tcstring-v2-abc", "not a tc string",
                make_tc_string(version=1)):
        with pytest.raises(ValueError):
            parse_tc_string(bad)


def test_consent_error_shape():
    err = ConsentError(CONSENT_INVALID, "boom", 422, {"k": 1})
    assert err.code == CONSENT_INVALID
    assert err.status_code == 422
    assert err.extra == {"k": 1}


# ---------------------------------------------------------------------------
# API-level: local structural verification (no CMP configured)
# ---------------------------------------------------------------------------

def test_journey_tcf_real_string_accepted(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf", "ref": make_tc_string()}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["consent"] == {
        "basis": "tcf", "recorded": True, "verified": "local"}


def test_journey_tcf_purpose1_missing_rejected(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf",
                          "ref": make_tc_string(purpose_consents=purposes(2, 3))}},
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "CONSENT_REFUSED"
    assert detail["required_purpose"] == 1


def test_journey_tcf_stale_rejected(client, seeded_merchant):
    stale = make_tc_string(created=int(time.time()) - 500 * 86400)
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf", "ref": stale}},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CONSENT_STALE"


def test_journey_tcf_garbage_rejected(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf", "ref": "tcstring-v2-abc"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == CONSENT_INVALID


def test_journey_tcf_missing_ref_rejected(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == CONSENT_INVALID


def test_journey_gpp_valid_accepted(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "gpp", "ref": make_gpp_string("AA", "AA")}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["consent"]["verified"] == "local"


def test_journey_gpp_bad_header_rejected(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "gpp", "ref": "XX~AA"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == CONSENT_INVALID


def test_journey_gpp_no_sections_rejected(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "gpp", "ref": "DB"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == CONSENT_INVALID


def test_journey_gpp_bad_charset_rejected(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "gpp", "ref": "DB~AA==~AA"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == CONSENT_INVALID


def test_journey_88b_requires_ref(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "88b"}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == CONSENT_INVALID


def test_journey_88b_with_ref_accepted(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "88b", "ref": "merchant-consent-2026-09-04"},
              "agent_id": "ag_referringagent123"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["consent"] == {
        "basis": "88b", "recorded": True, "verified": "local"}


def test_journey_explicit_record_mode(client, seeded_merchant):
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "chat",
              "consent": {"basis": "explicit"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["consent"] == {
        "basis": "explicit", "recorded": True, "verified": "record"}


# ---------------------------------------------------------------------------
# API-level: CMP re-validation path (CRUMBS_CMP_VERIFY_URL configured)
# ---------------------------------------------------------------------------

def _enable_cmp(monkeypatch, valid_resp=None, exc=None):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "cmp_verify_url", "https://cmp.example.test/verify")
    monkeypatch.setattr(settings, "cmp_verify_timeout_seconds", 1.0)
    captured = {"url": None, "payload": None, "timeout": None}

    class FakeResp:
        status_code = 200

        def json(self):
            return valid_resp

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        captured["timeout"] = timeout
        if exc is not None:
            raise exc
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    return captured


def test_cmp_accept_sets_cmp_mode(client, seeded_merchant, monkeypatch):
    captured = _enable_cmp(monkeypatch, valid_resp={"valid": True})
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf", "ref": make_tc_string()}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["consent"]["verified"] == "cmp"
    assert captured["payload"] == {"basis": "tcf", "surface": "browser",
                                   "ref": captured["payload"]["ref"]}
    assert captured["payload"]["ref"]  # the TC string was sent


def test_cmp_reject_fails_closed(client, seeded_merchant, monkeypatch):
    _enable_cmp(monkeypatch, valid_resp={"valid": False})
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf", "ref": make_tc_string()}},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CONSENT_REFUSED"


def test_cmp_unreachable_fails_closed(client, seeded_merchant, monkeypatch):
    _enable_cmp(monkeypatch, exc=httpx.ConnectError("cmp down"))
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "browser",
              "consent": {"basis": "tcf", "ref": make_tc_string()}},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "CMP_UNREACHABLE"


def test_cmp_not_called_for_explicit(client, seeded_merchant, monkeypatch):
    captured = _enable_cmp(monkeypatch, valid_resp={"valid": True})
    resp = client.post(
        "/v1/journeys",
        json={"merchant_id": seeded_merchant.mid, "surface": "chat",
              "consent": {"basis": "explicit"}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["consent"]["verified"] == "record"
    assert captured["payload"] is None  # httpx.post never invoked
