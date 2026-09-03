"""Signed-attribution receipt v1 — format, signing, verification.

Format reference: docs/ATTRIBUTION_PROTOCOL.md (public). This module is the
canonical implementation of the receipt wire format.
A receipt is an ENTITLEMENT (evidence of a future commission claim), NOT stored
value: it carries ids, surface, TTL and a nonce — never cart value, order id or
payout details (those are stamped at conversion, server-side).

Payload fields (canonical JSON, JCS per RFC 8785, flat object of str/int):
  v    int    format version (1)
  rid  str    receipt id  "rct_" + ULID
  jid  str    journey id  "jrn_" + ULID
  aid  str    agent id    "ag_" + base32
  mid  str    merchant id "m_" + base32
  oid  str    order id (EMPTY at issuance; stamped at conversion)
  cv   int    cart value in minor units (0 at issuance; stamped at conversion)
  cur  str    ISO 4217 currency
  crb  int    commission rate in basis points
  ntb  int    network take in basis points (10-20% of commission)
  sf   str    surface: browser | api | chat
  nc   str    nonce: 16 random bytes, base64url (22 chars, unpadded)
  iat  int    issued-at unix seconds
  exp  int    expiry unix seconds (default TTL 30d)
  kid  int    HMAC key version
  sig  str    HMAC-SHA256 over canonical(payload-without-sig), first 32 bytes,
              base64url (43 chars) — appended as the LAST key so that the signed
              input is canonical(payload) with sig removed.

Total wire size ~300 bytes, well under the 1 KB cookie design rule.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from .jcs import canonical_json
from .ulid import make_ulid

FORMAT_VERSION = 1
DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days (docs/ATTRIBUTION_PROTOCOL.md §2)
SURFACES = ("browser", "api", "chat")

# Id prefixes (docs/ATTRIBUTION_PROTOCOL.md §2)
P_RECEIPT = "rct_"
P_JOURNEY = "jrn_"
P_AGENT = "ag_"
P_MERCHANT = "m_"


def new_nonce() -> str:
    """16 random bytes, base64url unpadded -> 22 chars."""
    return base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode("ascii")


def new_agent_id() -> str:
    """ag_ + 21 chars base32 randomness (~24 chars total)."""
    raw = base64.b32encode(os.urandom(13)).decode("ascii").rstrip("=").lower()
    return P_AGENT + raw


def new_merchant_id() -> str:
    raw = base64.b32encode(os.urandom(13)).decode("ascii").rstrip("=").lower()
    return P_MERCHANT + raw


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def build_receipt_payload(
    *,
    jid: str,
    aid: str,
    mid: str,
    cur: str,
    crb: int,
    ntb: int,
    sf: str,
    kid: int,
    rid: str | None = None,
    oid: str = "",
    cv: int = 0,
    nc: str | None = None,
    iat: int | None = None,
    exp: int | None = None,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> dict:
    """Construct the unsigned receipt payload (all fields except `sig`)."""
    if sf not in SURFACES:
        raise ValueError(f"surface must be one of {SURFACES}, got {sf!r}")
    if not (0 <= crb <= 10000):
        raise ValueError("crb must be in basis points 0..10000")
    if not (0 <= ntb <= 10000):
        raise ValueError("ntb must be in basis points 0..10000")
    if len(cur) != 3 or not cur.isalpha():
        raise ValueError(f"cur must be a 3-letter ISO 4217 code, got {cur!r}")
    if not mid.startswith(P_MERCHANT) or not aid.startswith(P_AGENT):
        raise ValueError("mid/aid must use the spec prefixes")
    now = int(time.time()) if iat is None else iat
    return {
        "v": FORMAT_VERSION,
        "rid": rid or (P_RECEIPT + make_ulid()),
        "jid": jid,
        "aid": aid,
        "mid": mid,
        "oid": oid,
        "cv": cv,
        "cur": cur,
        "crb": crb,
        "ntb": ntb,
        "sf": sf,
        "nc": nc or new_nonce(),
        "iat": now,
        "exp": exp if exp is not None else now + ttl,
        "kid": kid,
    }


def sign_payload(payload: dict, key: bytes) -> str:
    """HMAC-SHA256(key, canonical(payload)), first 32 bytes, base64url (43 chars)."""
    canonical = canonical_json(payload)
    digest = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).digest()
    return _b64url(digest[:32])


def sign_receipt(payload: dict, key: bytes) -> str:
    """Return the full receipt string: JCS-canonical JSON with `sig` appended.

    The canonical string is the signed payload WITHOUT sig; the final wire
    representation re-canonicalizes with sig included (sig sorts last, so the
    signed substring is identical).
    """
    unsigned = {k: v for k, v in payload.items() if k != "sig"}
    sig = sign_payload(unsigned, key)
    full = dict(unsigned)
    full["sig"] = sig
    return canonical_json(full)


def parse_receipt(receipt: str) -> dict:
    """Parse + JCS-validate a receipt string into a payload dict.

    Raises ValueError on malformed input (including type confusion — floats,
    bools in integer fields — so callers never see TypeError/500s).
    """
    import json

    try:
        obj = json.loads(receipt)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("receipt is not valid JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("receipt must be a JSON object")
    required = {"v", "rid", "jid", "aid", "mid", "oid", "cv", "cur",
                "crb", "ntb", "sf", "nc", "iat", "exp", "kid", "sig"}
    missing = required - set(obj.keys())
    if missing:
        raise ValueError(f"receipt missing fields: {sorted(missing)}")
    _validate_field_types(obj)
    # Structural validation
    if obj["v"] != FORMAT_VERSION:
        raise ValueError(f"unsupported receipt version {obj['v']}")
    if not obj["rid"].startswith(P_RECEIPT) or len(obj["rid"]) != len(P_RECEIPT) + 26:
        raise ValueError("malformed rid")
    if not obj["jid"].startswith(P_JOURNEY) or len(obj["jid"]) != len(P_JOURNEY) + 26:
        raise ValueError("malformed jid")
    if obj["sf"] not in SURFACES:
        raise ValueError("malformed surface")
    # Ensure the wire form is canonical (defends against non-canonical duplicates)
    if canonical_json(obj) != receipt:
        raise ValueError("receipt is not JCS-canonical")
    return obj


_INT_FIELDS = ("v", "cv", "crb", "ntb", "iat", "exp", "kid")
_STR_FIELDS = ("rid", "jid", "aid", "mid", "oid", "cur", "sf", "nc", "sig")


def _validate_field_types(obj: dict) -> None:
    """Reject type confusion BEFORE canonicalization (bool is a subclass of int
    in Python, so it must be excluded explicitly)."""
    for f in _INT_FIELDS:
        v = obj.get(f)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"field {f!r} must be an integer")
    for f in _STR_FIELDS:
        if not isinstance(obj.get(f), str):
            raise ValueError(f"field {f!r} must be a string")
    if len(obj["cur"]) != 3:
        raise ValueError("field 'cur' must be a 3-letter ISO 4217 code")
    if not 0 <= obj["crb"] <= 10000 or not 0 <= obj["ntb"] <= 10000:
        raise ValueError("crb/ntb out of basis-point range")


def verify_signature(payload: dict, key: bytes) -> bool:
    """Constant-time HMAC check. payload must contain `sig`."""
    sig = payload.get("sig")
    if not isinstance(sig, str):
        return False
    expected = sign_payload({k: v for k, v in payload.items() if k != "sig"}, key)
    return hmac.compare_digest(expected, sig)


def receipt_expired(payload: dict, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    return now >= int(payload["exp"])
