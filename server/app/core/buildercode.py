"""ERC-8021 Schema 2 builder codes + x402 builder-code extension support.

Implements the wire mechanics needed to *verify* on-chain attribution for a
settled x402 payment. The x402 builder-code extension appends an ERC-8021
Schema 2 suffix to a settlement transaction's calldata:

    [cborData][cborLength (2B, big-endian)][schemaId 0x02][ercMarker (16B)]

where ``ercMarker`` is the constant bytes ``80218021802180218021802180218021``
and ``cborData`` is a small CBOR map with optional text keys:

    a  app code     — the application/resource server in the payment path
    w  wallet code  — the facilitator settling the payment on-chain
    s  service code(s) — attribution codes from the payment path
       (client/server/facilitator each have a dedicated reservation;
        the on-chain cap is 11 service codes total)

Every code matches ``^[a-z0-9_]{1,32}$`` (the ERC-8021 builder-code format).

Crumbs registers ``bc_crumbs`` as a service code on the client payment path.
The ledger uses this module to prove a recorded settlement's calldata really
carries that code before accepting an on-chain settlement proof — a stored
``tx_hash`` without verifiable ``bc_crumbs`` attribution is an attestation,
not a proof, and is labelled as such.

The CBOR codec is deliberately minimal (zero dependencies, like the rest of
the core): it understands only the types the suffix can legally carry (text
strings, arrays of text strings, small positive ints, maps) and fails loudly
on anything else — a truncated or hostile suffix must never decode silently.
"""
from __future__ import annotations

import re

# ERC-8021 builder-code format (x402 builder-code extension).
BUILDER_CODE_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# Crumbs' registered service code (fits the format; 9 chars).
DEFAULT_BUILDER_CODE = "bc_crumbs"

# ERC-8021 Schema 2 suffix framing.
ERC_8021_MARKER = bytes.fromhex("80218021802180218021802180218021")
SCHEMA_2_ID = 0x02
_FRAME_BYTES = len(ERC_8021_MARKER) + 1 + 2  # marker + schemaId + cborLength

_SUFFIX_KEYS = ("a", "w", "s")


class BuilderCodeError(ValueError):
    """Raised when an ERC-8021 suffix is present but structurally invalid."""


def valid_builder_code(code: str) -> bool:
    """True when *code* matches the ERC-8021 builder-code format."""
    return isinstance(code, str) and bool(BUILDER_CODE_RE.match(code))


# --- minimal CBOR codec (spec-limited; see module docstring) -----------------


def _cbor_head(major: int, arg: int) -> bytes:
    if arg < 24:
        return bytes([(major << 5) | arg])
    if arg < 256:
        return bytes([(major << 5) | 24, arg])
    if arg < 65536:
        return bytes([(major << 5) | 25]) + arg.to_bytes(2, "big")
    return bytes([(major << 5) | 26]) + arg.to_bytes(4, "big")


def _cbor_encode(value) -> bytes:
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _cbor_head(3, len(raw)) + raw
    if isinstance(value, list):
        return _cbor_head(4, len(value)) + b"".join(_cbor_encode(v) for v in value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"unsupported CBOR value: {value!r}")
    if value >= 0:
        return _cbor_head(0, value)
    raise TypeError(f"unsupported negative CBOR int: {value!r}")


def _cbor_encode_map(attribution: dict) -> bytes:
    if not isinstance(attribution, dict):
        raise TypeError("attribution must be a dict")
    unknown = set(attribution) - set(_SUFFIX_KEYS)
    if unknown:
        raise BuilderCodeError(f"unknown ERC-8021 keys: {sorted(unknown)}")
    for key in ("a", "w"):
        if key in attribution and not valid_builder_code(attribution[key]):
            raise BuilderCodeError(f"invalid {key} builder code: {attribution[key]!r}")
    services = attribution.get("s", [])
    if not isinstance(services, list) or any(
        not valid_builder_code(code) for code in services
    ):
        raise BuilderCodeError(f"invalid s service codes: {services!r}")
    body = bytearray()
    for key in sorted(attribution):
        body += _cbor_encode(key)
        body += _cbor_encode(attribution[key])
    return _cbor_head(5, len(attribution)) + bytes(body)


def _cbor_read_len(info: int, buf: bytes, pos: int):
    if info < 24:
        return info, pos
    width = {24: 1, 25: 2, 26: 4}.get(info)
    if width is None:
        raise ValueError(f"unsupported CBOR length info {info}")
    if pos + width > len(buf):
        raise ValueError("truncated CBOR length")
    return int.from_bytes(buf[pos : pos + width], "big"), pos + width


def _cbor_decode_one(buf: bytes, pos: int):
    if pos >= len(buf):
        raise ValueError("truncated CBOR")
    ib = buf[pos]
    pos += 1
    major, info = ib >> 5, ib & 0x1F
    if major == 0:  # positive int
        return _cbor_read_len(info, buf, pos)
    if major == 3:  # text string
        n, pos = _cbor_read_len(info, buf, pos)
        raw = buf[pos : pos + n]
        if len(raw) < n:
            raise ValueError("truncated CBOR text")
        return raw.decode("utf-8"), pos + n
    if major == 4:  # array
        n, pos = _cbor_read_len(info, buf, pos)
        out = []
        for _ in range(n):
            item, pos = _cbor_decode_one(buf, pos)
            out.append(item)
        return out, pos
    if major == 5:  # map
        n, pos = _cbor_read_len(info, buf, pos)
        out: dict = {}
        for _ in range(n):
            key, pos = _cbor_decode_one(buf, pos)
            if not isinstance(key, str):
                raise ValueError("CBOR map key is not text")
            out[key], pos = _cbor_decode_one(buf, pos)
        return out, pos
    raise ValueError(f"unsupported CBOR major type {major}")


# --- public API --------------------------------------------------------------


def encode_builder_code_suffix(attribution: dict) -> str:
    """Encode an ERC-8021 Schema 2 calldata suffix (hex, 0x-prefixed).

    *attribution* is a dict with any of the keys ``a`` (str), ``w`` (str)
    and ``s`` (list[str]); every code must match the builder-code format.
    The returned hex string is what a facilitator appends to a settlement
    transaction's calldata.
    """
    cbor = _cbor_encode_map(attribution)
    suffix = (
        cbor
        + len(cbor).to_bytes(2, "big")
        + bytes([SCHEMA_2_ID])
        + ERC_8021_MARKER
    )
    return "0x" + suffix.hex()


def _to_bytes(calldata) -> bytes:
    if isinstance(calldata, bytes):
        return calldata
    if not isinstance(calldata, str):
        raise TypeError("calldata must be hex str or bytes")
    hexed = calldata[2:] if calldata[:2].lower() == "0x" else calldata
    try:
        return bytes.fromhex(hexed)
    except ValueError as exc:
        raise BuilderCodeError(f"calldata is not valid hex: {exc}") from exc


def parse_builder_code_suffix_from_calldata(calldata) -> dict | None:
    """Extract ``{a?, w?, s?}`` from calldata, or None when no suffix is present.

    Raises :class:`BuilderCodeError` when a suffix IS present but is
    structurally invalid (wrong length, malformed CBOR, bad codes) — a
    truncated or hostile suffix must never decode silently.
    """
    data = _to_bytes(calldata)
    if len(data) < _FRAME_BYTES:
        return None
    if data[-len(ERC_8021_MARKER) :] != ERC_8021_MARKER:
        return None
    if data[-len(ERC_8021_MARKER) - 1] != SCHEMA_2_ID:
        return None  # a different schema id — not ours, treat as absent
    cbor_len = int.from_bytes(data[-_FRAME_BYTES:-len(ERC_8021_MARKER) - 1], "big")
    start = len(data) - _FRAME_BYTES - cbor_len
    if start < 0:
        raise BuilderCodeError("ERC-8021 suffix length overruns calldata")
    cbor_data = data[start : start + cbor_len]
    try:
        attribution, end = _cbor_decode_one(cbor_data, 0)
    except ValueError as exc:
        raise BuilderCodeError(f"malformed ERC-8021 CBOR: {exc}") from exc
    if end != len(cbor_data):
        raise BuilderCodeError("trailing bytes after ERC-8021 CBOR map")
    if not isinstance(attribution, dict):
        raise BuilderCodeError("ERC-8021 payload is not a map")
    for key in ("a", "w"):
        if key in attribution and not valid_builder_code(attribution[key]):
            raise BuilderCodeError(f"invalid {key} builder code in suffix")
    services = attribution.get("s", [])
    if not isinstance(services, list) or any(
        not valid_builder_code(code) for code in services
    ):
        raise BuilderCodeError("invalid s service codes in suffix")
    return {key: attribution[key] for key in _SUFFIX_KEYS if key in attribution}


def calldata_carries_code(calldata, code: str) -> bool:
    """True when the calldata's ERC-8021 suffix carries *code* as ``a`` or ``s``."""
    if not valid_builder_code(code):
        raise ValueError(f"not a builder code: {code!r}")
    attribution = parse_builder_code_suffix_from_calldata(calldata)
    if not attribution:
        return False
    return attribution.get("a") == code or code in attribution.get("s", [])
