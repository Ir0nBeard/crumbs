"""Receipt core: JCS canonicalization, sign/verify, tamper, kid rotation, size.

Pure unit tests — no DB/API involved.
"""
from __future__ import annotations

import json

import pytest

from app.core.jcs import canonical_json
from app.core.receipt import (
    DEFAULT_TTL_SECONDS,
    build_receipt_payload,
    new_agent_id,
    new_merchant_id,
    new_nonce,
    parse_receipt,
    receipt_expired,
    sign_payload,
    sign_receipt,
    verify_signature,
)
from app.core.ulid import make_ulid
from app.signing import SigningService

KEY1 = bytes.fromhex("11" * 32)
KEY2 = bytes.fromhex("22" * 32)


def make_payload(**overrides):
    base = dict(
        jid="jrn_" + make_ulid(),
        aid=new_agent_id(),
        mid=new_merchant_id(),
        cur="USD",
        crb=1200,
        ntb=1500,
        sf="browser",
        kid=1,
    )
    base.update(overrides)
    return build_receipt_payload(**base)


# ---------------------------------------------------------------------------


def test_jcs_canonicalizes_key_order():
    a = {"b": 1, "a": "x", "c": 3}
    b = {"c": 3, "a": "x", "b": 1}
    assert canonical_json(a) == canonical_json(b) == '{"a":"x","b":1,"c":3}'


def test_jcs_escapes_only_required_chars():
    # RFC 8785 s3.2.2.2.2.1: escape " \ and control chars only
    assert canonical_json({"k": 'a"b\\c\nd'}) == r'{"k":"a\"b\\c\nd"}'
    # control char -> lowercase \uXXXX
    assert canonical_json({"k": "\x01"}) == r'{"k":"\u0001"}'


def test_sign_verify_roundtrip():
    payload = make_payload()
    wire = sign_receipt(payload, KEY1)
    assert verify_signature(parse_receipt(wire), KEY1)


def test_verification_constant_time_and_wrong_key():
    payload = make_payload()
    signed = parse_receipt(sign_receipt(payload, KEY1))
    assert not verify_signature(signed, KEY2)


def test_tamper_any_field_breaks_signature():
    payload = make_payload()
    wire = json.loads(sign_receipt(payload, KEY1))
    for field in ("rid", "jid", "aid", "mid", "cv", "exp", "sf", "nc", "ntb", "crb"):
        mutated = dict(wire)
        if isinstance(wire[field], int):
            mutated[field] = wire[field] + 1
        else:
            mutated[field] = wire[field] + "x"
        assert not verify_signature(mutated, KEY1), f"tamper not caught: {field}"


def test_parse_receipt_rejects_non_canonical_wire_form():
    payload = make_payload()
    wire = json.loads(sign_receipt(payload, KEY1))
    # Re-serialize with a different key order -> not canonical -> must fail parse
    non_canonical = json.dumps({k: wire[k] for k in sorted(wire.keys(), reverse=True)})
    with pytest.raises(ValueError, match="canonical"):
        parse_receipt(non_canonical)


def test_parse_receipt_rejects_type_confusion():
    """P3 C-M1: float/bool/string in int fields -> ValueError (never TypeError/500)."""
    payload = make_payload()
    wire = json.loads(sign_receipt(payload, KEY1))
    for field, bad_value in (("exp", 123.0), ("kid", "abc"), ("crb", True), ("cv", "10")):
        mutated = dict(wire)
        mutated[field] = bad_value
        with pytest.raises(ValueError):
            parse_receipt(json.dumps(mutated, sort_keys=True, separators=(",", ":")))


def test_unknown_kid_verify_detail():
    from app.signing import SigningService

    svc = SigningService(keys={1: KEY1}, default_kid=1)
    payload = make_payload(kid=99)
    payload["sig"] = sign_payload(payload, KEY1)
    ok, reason = svc.verify_detail(payload)
    assert ok is False
    assert reason == "unknown_kid"
    # malformed kid (string) -> "malformed", never raises
    payload2 = make_payload()
    payload2["kid"] = "abc"
    payload2["sig"] = sign_payload(payload2, KEY1)
    assert svc.verify_detail(payload2) == (False, "malformed")


def test_receipt_size_under_1kb_design_rule():
    payload = make_payload()
    wire = sign_receipt(payload, KEY1)
    assert len(wire) < 700, f"receipt is {len(wire)} bytes — over the 1 KB design rule"
    assert len(wire) > 250  # sanity: still carries the full field set


def test_nonce_is_16_bytes_base64url_unpadded():
    nc = new_nonce()
    assert len(nc) == 22
    assert "=" not in nc
    import base64

    raw = base64.urlsafe_b64decode(nc + "==")
    assert len(raw) == 16


def test_ids_use_spec_prefixes_and_lengths():
    assert new_agent_id().startswith("ag_")
    assert new_merchant_id().startswith("m_")
    assert len(make_ulid()) == 26


def test_expiry_and_ttl_default():
    import time

    payload = make_payload(iat=1_000_000_000)
    assert payload["exp"] == 1_000_000_000 + DEFAULT_TTL_SECONDS
    assert receipt_expired(payload, now=payload["exp"])
    assert not receipt_expired(payload, now=payload["exp"] - 1)


def test_signing_service_kid_rotation():
    svc = SigningService(keys={1: KEY1, 2: KEY2}, default_kid=1)
    payload = make_payload()
    signed = svc.sign(payload)
    assert signed["kid"] == 1
    assert svc.verify(signed)

    new_kid = svc.rotate()
    assert new_kid == 3
    assert svc.current_kid() == 3
    # Old receipts still verify after rotation
    assert svc.verify(signed)
    # New issuance uses kid 3
    signed2 = svc.sign(make_payload())
    assert signed2["kid"] == 3
    assert svc.verify(signed2)


def test_unknown_kid_fails_verification():
    svc = SigningService(keys={1: KEY1}, default_kid=1)
    payload = make_payload(kid=99)
    payload["sig"] = sign_payload(payload, KEY1)
    assert not svc.verify(payload)


def test_build_payload_validates_surface_and_currency():
    with pytest.raises(ValueError, match="surface"):
        make_payload(sf="carrier_pigeon")
    with pytest.raises(ValueError, match="cur"):
        make_payload(cur="USDollar")


def test_oid_and_cv_are_stamped_at_conversion_not_issuance():
    payload = make_payload()
    assert payload["oid"] == ""
    assert payload["cv"] == 0
