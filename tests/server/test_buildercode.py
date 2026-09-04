"""ERC-8021 Schema 2 builder-code codec — core level.

Covers the wire format from the x402 builder-code extension: suffix framing
(marker + schema id + big-endian CBOR length), code validation
(/^[a-z0-9_]{1,32}$/), parsing from synthetic settlement calldata, and the
malformed-suffix failures that must never decode silently.
"""
from __future__ import annotations

import pytest

from app.core.buildercode import (
    DEFAULT_BUILDER_CODE,
    ERC_8021_MARKER,
    BuilderCodeError,
    calldata_carries_code,
    encode_builder_code_suffix,
    parse_builder_code_suffix_from_calldata,
    valid_builder_code,
)

# Hand-derived vector for {"a": "bc_app", "s": ["bc_crumbs"]}:
#   CBOR map(2): a:"bc_app", s:["bc_crumbs"]  -> 23 bytes
#   suffix: [cbor 0x17 bytes][len 0017][schema 02][marker x16]
SUFFIX_LITERAL = (
    "a261616662635f6170706173816962635f6372756d6273"  # CBOR (23 bytes)
    "0017"  # cborLength (big-endian)
    "02"  # Schema 2 id
    "80218021802180218021802180218021"  # ercMarker (16 bytes)
)


def test_default_builder_code_is_valid():
    assert valid_builder_code(DEFAULT_BUILDER_CODE)
    assert len(DEFAULT_BUILDER_CODE) <= 32


@pytest.mark.parametrize(
    "code",
    [
        "bc_crumbs",
        "bc_my_app",
        "a",  # 1 char
        "x" * 32,  # 32 chars — the cap
        "bc_app_2",
    ],
)
def test_valid_codes(code):
    assert valid_builder_code(code) is True


@pytest.mark.parametrize(
    "code",
    [
        "",
        "Bc_crumbs",  # uppercase
        "bc-crumbs",  # hyphen
        "bc.crumbs",  # dot
        "x" * 33,  # over the cap
        None,
        42,
    ],
)
def test_invalid_codes(code):
    assert valid_builder_code(code) is False


def test_encode_matches_hand_derived_literal():
    encoded = encode_builder_code_suffix({"a": "bc_app", "s": ["bc_crumbs"]})
    assert encoded == "0x" + SUFFIX_LITERAL


def test_parse_from_synthetic_calldata():
    calldata = "0xdeadbeef" + SUFFIX_LITERAL
    assert parse_builder_code_suffix_from_calldata(calldata) == {
        "a": "bc_app",
        "s": ["bc_crumbs"],
    }


def test_parse_tolerates_uppercase_hex():
    calldata = "0xDEADBEEF" + SUFFIX_LITERAL.upper()
    assert parse_builder_code_suffix_from_calldata(calldata) == {
        "a": "bc_app",
        "s": ["bc_crumbs"],
    }


def test_parse_accepts_bytes_input():
    calldata = bytes.fromhex("deadbeef" + SUFFIX_LITERAL)
    assert parse_builder_code_suffix_from_calldata(calldata)["a"] == "bc_app"


def test_no_suffix_returns_none():
    assert parse_builder_code_suffix_from_calldata("0x1234") is None
    assert parse_builder_code_suffix_from_calldata("0x" + "ab" * 64) is None
    # suffix-shaped tail without the marker
    assert (
        parse_builder_code_suffix_from_calldata("0x" + "00" * 19 + "ff" * 16)
        is None
    )


def test_wrong_schema_id_is_absent_not_error():
    # Same frame with schema id 0x03 -> not an ERC-8021 Schema 2 suffix
    cbor = SUFFIX_LITERAL[:46]
    wrong_schema = (
        "0x1234"
        + cbor
        + len(bytes.fromhex(cbor)).to_bytes(2, "big").hex()
        + "03"
        + ERC_8021_MARKER.hex()
    )
    assert parse_builder_code_suffix_from_calldata(wrong_schema) is None


def test_length_overrun_raises():
    overrun = "0x" + "ffff" + "02" + ERC_8021_MARKER.hex()
    with pytest.raises(BuilderCodeError):
        parse_builder_code_suffix_from_calldata(overrun)


def test_garbage_cbor_raises():
    # Valid frame, but the "CBOR" region is not a map
    cbor = "80"  # empty array
    bad = "0x1234" + cbor + "0001" + "02" + ERC_8021_MARKER.hex()
    with pytest.raises(BuilderCodeError):
        parse_builder_code_suffix_from_calldata(bad)


def test_invalid_code_inside_suffix_raises():
    # s code with an uppercase letter violates the format
    with pytest.raises(BuilderCodeError):
        encode_builder_code_suffix({"s": ["BC_crumbs"]})
    with pytest.raises(BuilderCodeError):
        encode_builder_code_suffix({"a": "bc_my_app", "unknown": "x"})


def test_calldata_carries_code():
    calldata = "0xdeadbeef" + SUFFIX_LITERAL
    assert calldata_carries_code(calldata, "bc_crumbs") is True
    assert calldata_carries_code(calldata, "bc_app") is True  # as the `a` code
    assert calldata_carries_code(calldata, "bc_other") is False
    assert calldata_carries_code("0x1234", "bc_crumbs") is False
    with pytest.raises(ValueError):
        calldata_carries_code(calldata, "not_a_code!")


def test_roundtrip_with_facilitator_codes():
    attribution = {"a": "bc_example_app", "w": "bc_fac", "s": ["bc_crumbs", "bc_sdk"]}
    suffix = encode_builder_code_suffix(attribution)
    assert parse_builder_code_suffix_from_calldata("0x00" + suffix[2:]) == attribution
