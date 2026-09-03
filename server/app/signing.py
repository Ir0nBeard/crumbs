"""HMAC signing service with kid key rotation (spec A.2 / A.6.5).

Keys are held server-side only. kid identifies the key version; receipts are
signed with the CURRENT issuance kid, and verification accepts any known kid
so rotation does not invalidate live receipts. Retire old kids by removing
them from CRUMBS_SIGNING_KEYS after the max TTL + grace has passed.
"""
from __future__ import annotations

import os
import secrets

from .config import get_settings


class SigningService:
    def __init__(self, keys: dict[int, bytes] | None = None, default_kid: int | None = None):
        settings = get_settings()
        parsed = keys if keys is not None else settings.parsed_signing_keys
        if not parsed:
            # Throwaway dev key — local only. Receipts do not survive restarts.
            parsed = {settings.default_kid: secrets.token_bytes(32)}
        self._keys: dict[int, bytes] = dict(parsed)
        self.default_kid: int = default_kid or min(self._keys.keys())
        if self.default_kid not in self._keys:
            raise ValueError(f"default_kid {self.default_kid} not present in keys")

    # -- issuance ------------------------------------------------------------
    def current_kid(self) -> int:
        return self.default_kid

    def key_for(self, kid: int) -> bytes | None:
        return self._keys.get(kid)

    def rotate(self, new_key: bytes | None = None) -> int:
        """Add a new kid (max(existing)+1) and make it the issuance kid."""
        new_kid = max(self._keys.keys()) + 1
        self._keys[new_kid] = new_key or secrets.token_bytes(32)
        self.default_kid = new_kid
        return new_kid

    # -- signing ---------------------------------------------------------------
    def sign(self, payload: dict) -> dict:
        """Sign payload (must NOT contain sig) and return it with sig added."""
        from .core.receipt import sign_payload

        payload = dict(payload)
        payload["kid"] = self.default_kid
        payload["sig"] = sign_payload({k: v for k, v in payload.items() if k != "sig"},
                                      self._keys[self.default_kid])
        return payload

    def key_id_of(self, payload: dict) -> int:
        try:
            return int(payload.get("kid", self.default_kid))
        except (TypeError, ValueError):
            return -1  # malformed kid -> treated as unknown

    def verify(self, payload: dict) -> bool:
        """Verify payload['sig'] against the key named by payload['kid']."""
        return self.verify_detail(payload)[0]

    def verify_detail(self, payload: dict) -> tuple[bool, str]:
        """Verify and explain failure: (ok, "unknown_kid" | "bad_signature" |
        "malformed"). Type confusion on kid/exp never raises (P3 C-M1)."""
        from .core.receipt import verify_signature

        kid = self.key_id_of(payload)
        if kid < 0:
            return False, "malformed"
        key = self._keys.get(kid)
        if key is None:
            return False, "unknown_kid"
        try:
            ok = verify_signature(payload, key)
        except (TypeError, ValueError, AttributeError):
            return False, "malformed"
        return (ok, "bad_signature") if not ok else (True, "ok")


def generate_key_hex() -> str:
    """Helper: produce a CRUMBS_SIGNING_KEYS entry value (for .env setup)."""
    return os.urandom(32).hex()
