"""Per-merchant keyed tokens — scoped credentials for /v1/conversions.

Replaces the v0.1 single shared ``CRUMBS_MERCHANT_API_KEY`` as the
recommended merchant credential:

  * a token is bound to exactly ONE merchant (``tok_`` id, SHA-256 hash
    stored, plaintext ``cmk_`` value shown once at issuance);
  * conversions authenticated with it are scoped to that merchant's
    receipts (token.mid must equal the receipt's mid);
  * optional per-token ``origins`` (JSON list of https origins) restrict
    browser-origin use of the token — a request carrying an ``Origin``
    header outside the list is rejected (scoped CORS). Requests without
    an Origin header (server-to-server) are unaffected;
  * tokens are revocable (soft-delete status flip) and audited.

The legacy shared key remains accepted for back-compatibility while
merchants migrate; set ``CRUMBS_REQUIRE_MERCHANT_TOKENS=true`` to make
per-merchant tokens mandatory (the legacy key is then rejected).

See docs/ATTRIBUTION_PROTOCOL.md §4.3.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.ulid import make_ulid
from ..db.models import MerchantToken
from ..db.session import audit

TOKEN_PREFIX = "cmk_"
TOKEN_ID_PREFIX = "tok_"


class MerchantAuthError(Exception):
    """Raised by the conversion auth gate; mapped to HTTP in routes."""

    def __init__(self, code: str, message: str, status_code: int = 403):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def hash_token(token: str) -> str:
    """SHA-256 hex of the plaintext token — the only form stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_origins(raw: str | None) -> list[str]:
    """Parse a stored JSON origins list; malformed storage fails closed to [].

    Callers treat an empty list as "no origin restriction".
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [o for o in parsed if isinstance(o, str)]


def validate_origin(origin: str) -> str:
    """Validate a caller-supplied origin string; raises ValueError.

    Origins must be absolute http(s) URLs with a host and no path/query/
    fragment (a browser Origin header shape). Trailing slashes are
    stripped so "https://shop.example.com/" == "https://shop.example.com".
    """
    if not origin:
        raise ValueError("origin must not be empty")
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"origin must be an absolute http(s) URL: {origin!r}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError(f"origin must not carry a path, query, or fragment: {origin!r}")
    return parts.scheme + "://" + parts.netloc


def normalize_origins(origins: list[str] | None) -> list[str]:
    """Validate + normalize a request list; sorted, de-duplicated."""
    if not origins:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for o in origins:
        norm = validate_origin(o)
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return sorted(out)


def issue_token(
    db: Session,
    *,
    mid: str,
    label: str | None = None,
    origins: list[str] | None = None,
    actor: str = "admin",
) -> tuple[MerchantToken, str]:
    """Create a per-merchant token; returns (row, plaintext shown once)."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    norm = normalize_origins(origins)
    row = MerchantToken(
        token_id=TOKEN_ID_PREFIX + make_ulid(),
        mid=mid,
        token_hash=hash_token(plaintext),
        label=(label or None),
        origins=json.dumps(norm) if norm else None,
        status="active",
    )
    db.add(row)
    db.flush()
    audit(db, "merchant_token_issued", "merchant_token", row.token_id,
          actor=actor, payload={"mid": mid, "label": row.label,
                                "origins": norm, "scoped": bool(norm)})
    db.commit()
    return row, plaintext


def revoke_token(db: Session, *, token_id: str, reason: str | None = None,
                 actor: str = "admin") -> MerchantToken:
    """Revoke a token (soft delete). Raises MerchantAuthError when unknown."""
    row = db.get(MerchantToken, token_id)
    if row is None:
        raise MerchantAuthError("TOKEN_NOT_FOUND", f"no merchant token {token_id}", 404)
    if row.status == "revoked":
        return row
    row.status = "revoked"
    row.revoked_at = _now()
    audit(db, "merchant_token_revoked", "merchant_token", row.token_id,
          actor=actor, payload={"mid": row.mid, "reason": reason})
    db.commit()
    return row


def token_public_view(row: MerchantToken) -> dict:
    """Serializable view WITHOUT the hash or plaintext."""
    return {
        "token_id": row.token_id,
        "merchant_id": row.mid,
        "label": row.label,
        "origins": _parse_origins(row.origins),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


def list_tokens(db: Session, mid: str) -> list[dict]:
    rows = db.execute(
        select(MerchantToken)
        .where(MerchantToken.mid == mid)
        .order_by(MerchantToken.created_at)
    ).scalars().all()
    return [token_public_view(r) for r in rows]


def _touch(row: MerchantToken, db: Session) -> None:
    row.last_used_at = _now()
    db.commit()


def authenticate_conversion(
    db: Session,
    *,
    key: str | None,
    settings,
    receipt_mid: str | None,
    origin: str | None,
) -> MerchantToken | None:
    """Gate POST /v1/conversions.

    Resolution order (see module docstring):
      1. X-Crumbs-Key matches a stored per-merchant token -> scoped authz
         (active, mid == receipt merchant, Origin within the token's
         allowlist when one is set).
      2. No token match but the legacy shared key matches
         (CRUMBS_MERCHANT_API_KEY) -> accepted ONLY when
         CRUMBS_REQUIRE_MERCHANT_TOKENS is false (deprecated path).
      3. Otherwise -> MerchantAuthError (401/403).

    Returns the authenticated MerchantToken row (or None for the legacy
    key path / open mode when no key is configured).
    """
    import secrets as _secrets

    if key:
        row = db.execute(
            select(MerchantToken).where(MerchantToken.token_hash == hash_token(key))
        ).scalar_one_or_none()
        if row is not None:
            if row.status != "active":
                raise MerchantAuthError("TOKEN_REVOKED",
                                        "merchant token is revoked", 403)
            # Scoping: the token's merchant must be the receipt's merchant.
            if receipt_mid is not None and row.mid != receipt_mid:
                raise MerchantAuthError(
                    "TOKEN_MERCHANT_MISMATCH",
                    f"token belongs to merchant {row.mid}, not {receipt_mid}", 403)
            # Scoped CORS: browser-origin requests must come from an allowed
            # origin when the token carries an origin allowlist.
            allowed = _parse_origins(row.origins)
            if origin and allowed and origin not in allowed:
                raise MerchantAuthError(
                    "ORIGIN_NOT_ALLOWED",
                    f"origin {origin} is not allowed for this merchant token", 403)
            _touch(row, db)
            return row
        # Legacy shared-key path (deprecated; see config.merchant_api_key).
        if settings.merchant_api_key and _secrets.compare_digest(
            key, settings.merchant_api_key
        ):
            if getattr(settings, "require_merchant_tokens", False):
                raise MerchantAuthError(
                    "MERCHANT_TOKEN_REQUIRED",
                    "per-merchant tokens required "
                    "(CRUMBS_REQUIRE_MERCHANT_TOKENS=true)", 401)
            return None
        raise MerchantAuthError("UNAUTHORIZED", "bad merchant key", 401)

    # No key supplied.
    if getattr(settings, "require_merchant_tokens", False):
        raise MerchantAuthError(
            "MERCHANT_TOKEN_REQUIRED",
            "X-Crumbs-Key header with a per-merchant token is required", 401)
    if settings.merchant_api_key:
        raise MerchantAuthError(
            "MERCHANT_KEY_REQUIRED",
            "X-Crumbs-Key header is required (merchant key configured)", 401)
    return None  # open mode (no merchant auth configured)
