-- Crumbs attribution ledger — initial schema (Postgres)
-- Canonical DDL; the SQLAlchemy models mirror this (dialect-neutral for tests).
-- Migration style: alembic-compatible (run via alembic upgrade head, or psql -f).
-- Money is INTEGER minor units + ISO currency TEXT — never floats.
-- audit_events is APPEND-ONLY by design.

CREATE TABLE IF NOT EXISTS agent_owners (
    owner_id      TEXT PRIMARY KEY,
    display_name  TEXT,
    payout_rail   TEXT,
    payout_ref    TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agents (
    aid             TEXT PRIMARY KEY,
    name            TEXT,
    owner_id        TEXT REFERENCES agent_owners(owner_id),
    registry_ref    TEXT,
    iab_registry_id TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    revoked         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agents_owner_id ON agents(owner_id);

CREATE TABLE IF NOT EXISTS merchants (
    mid             TEXT PRIMARY KEY,
    name            TEXT,
    owner_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    -- v0.1 dev: webhook signing secret lives here (guarded dev default).
    -- Production: store a secret-manager reference only (v0.1 local dev keeps
    -- the webhook secret on the row; the dev default is rejected outside SQLite).
    webhook_secret  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_merchants_owner_id ON merchants(owner_id);

CREATE TABLE IF NOT EXISTS merchant_programs (
    program_id          TEXT PRIMARY KEY,
    mid                 TEXT NOT NULL REFERENCES merchants(mid),
    name                TEXT NOT NULL DEFAULT 'default',
    commission_rate_bps INTEGER NOT NULL,
    network_take_bps    INTEGER NOT NULL DEFAULT 1500,
    consent_policy      TEXT NOT NULL DEFAULT 'required',
    self_referral_policy TEXT NOT NULL DEFAULT 'forbid',
    status              TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS ix_merchant_programs_mid ON merchant_programs(mid);

CREATE TABLE IF NOT EXISTS journeys (
    jid                  TEXT PRIMARY KEY,
    aid                  TEXT NOT NULL REFERENCES agents(aid),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    surface              TEXT NOT NULL,
    consent_basis        TEXT NOT NULL DEFAULT 'explicit',
    consent_ref          TEXT,
    ip_hash              TEXT,
    ua_hash              TEXT,
    conversions_used     INTEGER NOT NULL DEFAULT 0,
    merchants_used       INTEGER NOT NULL DEFAULT 0,
    cart_value_used_usd  INTEGER NOT NULL DEFAULT 0,
    max_conversions      INTEGER NOT NULL DEFAULT 5,
    max_merchants        INTEGER NOT NULL DEFAULT 10,
    max_cart_value_usd   INTEGER NOT NULL DEFAULT 200000,
    status               TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS ix_journeys_aid ON journeys(aid);

CREATE TABLE IF NOT EXISTS receipts (
    rid        TEXT PRIMARY KEY,
    jid        TEXT NOT NULL REFERENCES journeys(jid),
    aid        TEXT NOT NULL REFERENCES agents(aid),
    mid        TEXT NOT NULL REFERENCES merchants(mid),
    v          INTEGER NOT NULL,
    sf         TEXT NOT NULL,
    nc         TEXT NOT NULL,
    iat        BIGINT NOT NULL,
    exp        BIGINT NOT NULL,
    kid        INTEGER NOT NULL,
    sig        TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'issued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_receipts_jid ON receipts(jid);
CREATE INDEX IF NOT EXISTS ix_receipts_aid ON receipts(aid);
CREATE INDEX IF NOT EXISTS ix_receipts_mid ON receipts(mid);
CREATE INDEX IF NOT EXISTS ix_receipts_exp ON receipts(exp);

CREATE TABLE IF NOT EXISTS conversions (
    cid                     TEXT PRIMARY KEY,
    rid                     TEXT NOT NULL REFERENCES receipts(rid),
    jid                     TEXT NOT NULL REFERENCES journeys(jid),
    mid                     TEXT NOT NULL REFERENCES merchants(mid),
    oid                     TEXT NOT NULL,
    cart_value_minor_units  BIGINT NOT NULL,
    currency                TEXT NOT NULL,
    crb                     INTEGER NOT NULL,
    ntb                     INTEGER NOT NULL,
    order_status            TEXT NOT NULL DEFAULT 'pending',
    verified_at             TIMESTAMPTZ,
    cart_mismatch           BOOLEAN NOT NULL DEFAULT FALSE,
    payout_scheduled        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_conversions_rid_oid UNIQUE (rid, oid)
);
CREATE INDEX IF NOT EXISTS ix_conversions_rid ON conversions(rid);
CREATE INDEX IF NOT EXISTS ix_conversions_jid ON conversions(jid);
CREATE INDEX IF NOT EXISTS ix_conversions_mid ON conversions(mid);
CREATE INDEX IF NOT EXISTS ix_conversions_cid_status ON conversions(cid, order_status);

CREATE TABLE IF NOT EXISTS splits (
    split_id            TEXT PRIMARY KEY,
    cid                 TEXT NOT NULL REFERENCES conversions(cid),
    party               TEXT NOT NULL,
    amount_minor_units  BIGINT NOT NULL,
    currency            TEXT NOT NULL,
    pct_bps             INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'scheduled',
    payout_id           TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_splits_cid ON splits(cid);
CREATE INDEX IF NOT EXISTS ix_splits_payout_id ON splits(payout_id);

CREATE TABLE IF NOT EXISTS payouts (
    pid          TEXT PRIMARY KEY,
    rail         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'scheduled',
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at   TIMESTAMPTZ,
    tx_hash      TEXT
);

CREATE TABLE IF NOT EXISTS disputes (
    did           TEXT PRIMARY KEY,
    cid           TEXT NOT NULL REFERENCES conversions(cid),
    party         TEXT NOT NULL,
    reason        TEXT NOT NULL,
    evidence_refs TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    resolution    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_disputes_cid ON disputes(cid);

CREATE TABLE IF NOT EXISTS revoked_receipts (
    rid    TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    by     TEXT NOT NULL DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS revoked_journeys (
    jid    TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    by     TEXT NOT NULL DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS revoked_agents (
    aid    TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    by     TEXT NOT NULL DEFAULT 'admin'
);

-- Durable used-nonce fallback (Redis is the hot path; table mirrors it)
CREATE TABLE IF NOT EXISTS used_nonces (
    rid        TEXT PRIMARY KEY,
    nc         TEXT NOT NULL,
    used_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Append-only audit log
CREATE TABLE IF NOT EXISTS audit_events (
    event_id    BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_events_event_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS ix_audit_events_entity_id ON audit_events(entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_created_at ON audit_events(created_at);
