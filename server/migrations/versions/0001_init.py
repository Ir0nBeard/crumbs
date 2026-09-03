"""0001 init — execute the canonical Postgres DDL (migrations/0001_init.sql).

The SQL file is the single source of truth for the production schema; this
version file just runs it so `alembic upgrade head` works end-to-end.
"""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SQL_PATH = Path(__file__).resolve().parent.parent / "0001_init.sql"


def upgrade() -> None:
    op.execute(_SQL_PATH.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Order matters (children first). Used only for local rollback testing.
    for table in (
        "audit_events", "used_nonces", "revoked_agents", "revoked_journeys",
        "revoked_receipts", "disputes", "payouts", "splits", "conversions",
        "receipts", "journeys", "merchant_programs", "merchants", "agents",
        "agent_owners",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
