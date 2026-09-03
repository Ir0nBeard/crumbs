"""Server configuration — pydantic-settings, env-gated.

Only non-secret defaults live here. Real deployments MUST set values via
environment or .env (see .env.example). NO secrets are committed.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CRUMBS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API ---------------------------------------------------------------
    app_name: str = "crumbs-attribution-ledger"
    api_prefix: str = "/v1"
    debug: bool = False

    # --- Database ----------------------------------------------------------
    # SQLite for local dev/tests; Postgres in production (see migrations/).
    database_url: str = "sqlite:///./crumbs_dev.db"

    # --- Redis (used-nonce store + rate limits) ------------------------------
    # Empty -> in-memory fallback (local dev only; NOT for production).
    redis_url: str = ""

    # --- Signing ------------------------------------------------------------
    # Comma-separated kid:hex-key pairs. kid=1 is the default issuance key.
    # If unset, a throwaway dev key is generated at startup (logs a warning;
    # receipts won't verify across restarts — fine for local only).
    signing_keys: str = ""
    default_kid: int = 1

    # --- Budgets (journey-bound; docs/ATTRIBUTION_PROTOCOL.md §5) --------------
    budget_max_conversions: int = 5
    budget_max_merchants: int = 10
    budget_max_cart_value_usd: int = 200000  # $2,000 in minor units (cents)
    # Static FX table used ONLY for cross-currency budget normalization.
    # Stub: extend with a real rate source before production. Unknown currency -> 1.0.
    fx_usd_rates: str = "USD:1.0,EUR:1.09,GBP:1.27"

    # --- Fraud / velocity (docs/ATTRIBUTION_PROTOCOL.md §5) --------------------
    nonce_grace_seconds: int = 48 * 3600  # used-nonce retention beyond TTL
    issuance_rate_limit: int = 120        # journey starts per ip per hour
    conversion_rate_limit: int = 300      # conversion posts per ip per hour
    self_referral_window_seconds: int = 3600
    self_referral_max_conversions: int = 10  # per (agent, merchant) per window
    conversion_padding_tolerance_bps: int = 1000  # 10% cart-value tolerance

    # --- Consent (docs/ATTRIBUTION_PROTOCOL.md §6) -----------------------------
    # Stubbed provider integration: if set, the server calls the CMP to
    # re-validate consent signals. Empty -> trust the client signal (MVP).
    cmp_verify_url: str = ""

    # --- Payouts -------------------------------------------------------------
    # Stub rail gating: actual x402/CDP or Stripe Connect settlement is NOT
    # implemented (see CHANGELOG.md — honest stub list). Scheduling records
    # only — no float. Defaults to FALSE (fail-closed): flip explicitly to
    # enable.
    payouts_enabled: bool = False
    default_owner_share_bps: int = 2000  # 20% of net commission to agent owner

    # --- Admin ---------------------------------------------------------------
    # If empty, admin endpoints (revocation) are DISABLED (401).
    admin_token: str = ""

    # --- Webhook --------------------------------------------------------------
    webhook_default_ttl_seconds: int = 3600
    webhook_tolerance_seconds: int = 300  # replay window for signed webhooks

    # --- Receipt TTL -----------------------------------------------------------
    receipt_ttl_seconds: int = 30 * 24 * 3600  # 30 days (docs/ATTRIBUTION_PROTOCOL.md §2)

    # --- Merchant auth ---------------------------------------------------------
    # Optional gate on /v1/conversions: if set, require X-Crumbs-Key header.
    # Real per-merchant keyed tokens are a post-v0.1 item (see CHANGELOG.md).
    merchant_api_key: str = ""

    @property
    def parsed_signing_keys(self) -> dict[int, bytes]:
        """Parse CRUMBS_SIGNING_KEYS "1:<hex>,2:<hex>" -> {kid: bytes}."""
        out: dict[int, bytes] = {}
        for part in (self.signing_keys or "").split(","):
            part = part.strip()
            if not part:
                continue
            kid_str, _, hex_key = part.partition(":")
            try:
                out[int(kid_str)] = bytes.fromhex(hex_key.strip())
            except ValueError:
                raise ValueError(f"invalid signing key entry: {part!r}")
        return out

    @property
    def parsed_fx(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for part in (self.fx_usd_rates or "").split(","):
            part = part.strip()
            if not part:
                continue
            cur, _, rate = part.partition(":")
            out[cur.strip().upper()] = float(rate)
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
