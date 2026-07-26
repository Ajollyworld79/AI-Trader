"""Persist earnings calendars and deterministic market regimes.

Revision ID: 0003_market_intelligence
Revises: 0002_runtime_safety
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_market_intelligence"
down_revision = "0002_runtime_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("earnings_events"):
        op.create_table(
            "earnings_events",
            sa.Column("external_id", sa.String(length=128), nullable=False),
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("report_date", sa.Date(), nullable=False),
            sa.Column("fiscal_date_ending", sa.Date(), nullable=True),
            sa.Column("estimate", sa.Float(), nullable=True),
            sa.Column("currency", sa.String(length=8), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("external_id"),
        )
        op.create_index(
            "ix_earnings_events_created_at",
            "earnings_events",
            ["created_at"],
            unique=False,
        )
        op.create_index(
            "ix_earnings_events_report_date",
            "earnings_events",
            ["report_date"],
            unique=False,
        )
        op.create_index(
            "ix_earnings_events_symbol",
            "earnings_events",
            ["symbol"],
            unique=False,
        )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("market_regimes"):
        op.create_table(
            "market_regimes",
            sa.Column("benchmark_symbol", sa.String(length=32), nullable=False),
            sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
            sa.Column("regime", sa.String(length=32), nullable=False),
            sa.Column("close", sa.Float(), nullable=False),
            sa.Column("volatility", sa.Float(), nullable=False),
            sa.Column("adx", sa.Float(), nullable=False),
            sa.Column("data_quality_ok", sa.Boolean(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "benchmark_symbol",
                "as_of",
                name="uq_market_regime_series",
            ),
        )
        op.create_index(
            "ix_market_regimes_as_of",
            "market_regimes",
            ["as_of"],
            unique=False,
        )
        op.create_index(
            "ix_market_regimes_benchmark_symbol",
            "market_regimes",
            ["benchmark_symbol"],
            unique=False,
        )
        op.create_index(
            "ix_market_regimes_created_at",
            "market_regimes",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("market_regimes"):
        op.drop_table("market_regimes")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("earnings_events"):
        op.drop_table("earnings_events")
