"""0002 — per-merchant keyed tokens (migrations/0002_merchant_tokens.sql)."""
from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_SQL_PATH = Path(__file__).resolve().parent.parent / "0002_merchant_tokens.sql"


def upgrade() -> None:
    op.execute(_SQL_PATH.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS merchant_tokens CASCADE")
