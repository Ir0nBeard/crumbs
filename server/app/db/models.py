"""SQLAlchemy models — Crumbs attribution ledger (spec D.2).

All tables exist in the Postgres migration (server/migrations/0001_init.sql).
The models are dialect-neutral (String/Integer/etc.) so the test suite can run
against SQLite; production runs the Postgres DDL.

Conventions:
  * ids are TEXT (ULID-prefixed strings) — no UUID dependency
  * money is INTEGER minor units + ISO currency TEXT — never floats
  * status fields are TEXT with documented allowed values
  * audit_events is APPEND-ONLY by design (no update path in the codebase)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Receipt(Base):
    __tablename__ = "receipts"

    rid: Mapped[str] = mapped_column(String(40), primary_key=True)
    jid: Mapped[str] = mapped_column(String(40), ForeignKey("journeys.jid"), index=True)
    aid: Mapped[str] = mapped_column(String(40), ForeignKey("agents.aid"), index=True)
    mid: Mapped[str] = mapped_column(String(40), ForeignKey("merchants.mid"), index=True)
    v: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sf: Mapped[str] = mapped_column(String(8), nullable=False)
    nc: Mapped[str] = mapped_column(String(32), nullable=False)
    iat: Mapped[int] = mapped_column(BigInteger, nullable=False)
    exp: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    kid: Mapped[int] = mapped_column(Integer, nullable=False)
    sig: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    journey: Mapped["Journey"] = relationship(back_populates="receipts")


class Journey(Base):
    __tablename__ = "journeys"

    jid: Mapped[str] = mapped_column(String(40), primary_key=True)
    aid: Mapped[str] = mapped_column(String(40), ForeignKey("agents.aid"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    surface: Mapped[str] = mapped_column(String(8), nullable=False)
    consent_basis: Mapped[str] = mapped_column(String(16), nullable=False, default="explicit")
    consent_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ua_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Budget counters (spec A.6.2)
    conversions_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    merchants_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cart_value_used_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    max_merchants: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_cart_value_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=200000)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    receipts: Mapped[list["Receipt"]] = relationship(back_populates="journey")


class Agent(Base):
    __tablename__ = "agents"

    aid: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("agent_owners.owner_id"), nullable=True, index=True
    )
    registry_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # IAB Agent Registry linkage (spec D.2) — stub field, not yet populated
    iab_registry_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentOwner(Base):
    __tablename__ = "agent_owners"

    owner_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # KYC / identity / payout account fields — STUB (no real data in MVP)
    payout_rail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payout_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Merchant(Base):
    __tablename__ = "merchants"

    mid: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # STUB (P3 C-M3): v0.1 keeps the webhook signing secret here for local dev;
    # production MUST move it to a secret manager (KMS/encrypted vault) and
    # store only a reference. The dev default is rejected outside SQLite.
    webhook_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    programs: Mapped[list["MerchantProgram"]] = relationship(back_populates="merchant")


class MerchantProgram(Base):
    __tablename__ = "merchant_programs"

    program_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mid: Mapped[str] = mapped_column(String(40), ForeignKey("merchants.mid"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    commission_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    network_take_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=1500)
    consent_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="required")
    self_referral_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="forbid"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    merchant: Mapped["Merchant"] = relationship(back_populates="programs")


class Conversion(Base):
    """orders / conversions (spec D.2) — one row per stamped conversion."""

    __tablename__ = "conversions"
    __table_args__ = (
        UniqueConstraint("rid", "oid", name="uq_conversions_rid_oid"),
        Index("ix_conversions_cid_status", "cid", "order_status"),
    )

    cid: Mapped[str] = mapped_column(String(40), primary_key=True)  # "c_" + ULID
    rid: Mapped[str] = mapped_column(String(40), ForeignKey("receipts.rid"), index=True)
    jid: Mapped[str] = mapped_column(String(40), ForeignKey("journeys.jid"), index=True)
    mid: Mapped[str] = mapped_column(String(40), ForeignKey("merchants.mid"), index=True)
    oid: Mapped[str] = mapped_column(String(128), nullable=False)
    cart_value_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    crb: Mapped[int] = mapped_column(Integer, nullable=False)
    ntb: Mapped[int] = mapped_column(Integer, nullable=False)
    order_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending|finalized|cancelled|refunded
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cart_mismatch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payout_scheduled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Split(Base):
    __tablename__ = "splits"

    split_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    cid: Mapped[str] = mapped_column(String(40), ForeignKey("conversions.cid"), index=True)
    party: Mapped[str] = mapped_column(String(16), nullable=False)  # agent|owner|network
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    pct_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    payout_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payout(Base):
    __tablename__ = "payouts"

    pid: Mapped[str] = mapped_column(String(40), primary_key=True)  # "p_" + ULID
    rail: Mapped[str] = mapped_column(String(32), nullable=False)  # x402|stripe_connect
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="scheduled"
    )  # scheduled|settled|failed
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # STUB: settlement execution (x402/CDP facilitator, Stripe Connect) is NOT
    # implemented in v0.1 — scheduling records only, NO float held (spec E.1).


class Dispute(Base):
    __tablename__ = "disputes"

    did: Mapped[str] = mapped_column(String(40), primary_key=True)
    cid: Mapped[str] = mapped_column(String(40), ForeignKey("conversions.cid"), index=True)
    party: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RevokedReceipt(Base):
    __tablename__ = "revoked_receipts"

    rid: Mapped[str] = mapped_column(String(40), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    by: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")


class RevokedJourney(Base):
    __tablename__ = "revoked_journeys"

    jid: Mapped[str] = mapped_column(String(40), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    by: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")


class RevokedAgent(Base):
    __tablename__ = "revoked_agents"

    aid: Mapped[str] = mapped_column(String(40), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    by: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")


class UsedNonce(Base):
    """Persistent used-nonce log (Redis bloom at scale; table = durable fallback)."""

    __tablename__ = "used_nonces"

    rid: Mapped[str] = mapped_column(String(40), primary_key=True)
    nc: Mapped[str] = mapped_column(String(32), nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    """Append-only audit log — every state change (spec D.4)."""

    __tablename__ = "audit_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
