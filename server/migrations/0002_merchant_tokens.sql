-- 0002 — per-merchant keyed tokens (item: merchant-scoped API credentials).
-- Only the SHA-256 hash of a token is stored; the plaintext cmk_ value is
-- shown once at issuance. A token is bound to one merchant and conversions
-- authenticated with it are scoped to that merchant's receipts. The optional
-- `origins` JSON list restricts browser-origin (Origin header) use of the
-- token — scoped CORS. NULL/empty origins = no origin restriction
-- (server-to-server use).

CREATE TABLE IF NOT EXISTS merchant_tokens (
    token_id    TEXT PRIMARY KEY,
    mid         TEXT NOT NULL REFERENCES merchants(mid),
    token_hash  TEXT NOT NULL,
    label       TEXT,
    origins     TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    last_used_at TIMESTAMPTZ,
    revoked_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_merchant_tokens_token_hash
    ON merchant_tokens(token_hash);
CREATE INDEX IF NOT EXISTS ix_merchant_tokens_mid
    ON merchant_tokens(mid);
