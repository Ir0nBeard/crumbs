"""Minimal ULID generator (RFC-style: 48-bit ms timestamp + 80 bits randomness,
Crockford base32, 26 chars). Python stdlib only — no external ULID dependency.

Format: tttttttttttttttttttttttttt (10 chars time + 16 chars random, Crockford base32).
Canonical string form used throughout: 26 chars, uppercase.
"""
from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # no I, L, O, U

_MASK = 0x1F


def _encode_base32(value: int, length: int) -> str:
    out = ["0"] * length
    for i in range(length - 1, -1, -1):
        out[i] = _CROCKFORD[value & _MASK]
        value >>= 5
    return "".join(out)


def make_ulid(ts_ms: int | None = None, entropy: bytes | None = None) -> str:
    """Return a 26-char ULID string.

    ts_ms: unix milliseconds (default: now). entropy: exactly 10 bytes of
    randomness (default: os.urandom(10)).
    """
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    if entropy is None:
        entropy = os.urandom(10)
    if len(entropy) != 10:
        raise ValueError("entropy must be exactly 10 bytes")
    ts_part = _encode_base32(ts_ms & ((1 << 48) - 1), 10)
    rand_int = int.from_bytes(entropy, "big")
    rand_part = _encode_base32(rand_int, 16)
    return ts_part + rand_part


def ulid_to_ts_ms(ulid: str) -> int:
    """Decode the timestamp portion of a ULID (for tests/inspection)."""
    if len(ulid) != 26:
        raise ValueError("invalid ULID length")
    ts_part = ulid[:10]
    val = 0
    for ch in ts_part:
        idx = _CROCKFORD.index(ch)
        val = (val << 5) | idx
    return val
