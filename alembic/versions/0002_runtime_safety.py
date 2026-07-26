"""Persist broker credentials and managed position state.

Revision ID: 0002_runtime_safety
Revises: 0001_initial
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_runtime_safety"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("orders"):
        order_columns = {column["name"] for column in inspector.get_columns("orders")}
        if "signal_id" not in order_columns:
            with op.batch_alter_table(
                "orders",
                naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
            ) as batch_op:
                batch_op.add_column(sa.Column("signal_id", sa.String(length=36), nullable=True))
                batch_op.create_unique_constraint("uq_orders_signal_id", ["signal_id"])
            inspector = sa.inspect(op.get_bind())
            unique_columns = {
                tuple(constraint.get("column_names") or ())
                for constraint in inspector.get_unique_constraints("orders")
            }
            if ("idempotency_key",) not in unique_columns:
                with op.batch_alter_table("orders") as batch_op:
                    batch_op.create_unique_constraint(
                        "uq_orders_idempotency_key", ["idempotency_key"]
                    )

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("broker_credentials"):
        credential_columns = {
            column["name"] for column in inspector.get_columns("broker_credentials")
        }
        missing_lease_columns = []
        if "refresh_lease_owner" not in credential_columns:
            missing_lease_columns.append(
                sa.Column("refresh_lease_owner", sa.String(length=36), nullable=True)
            )
        if "refresh_lease_until" not in credential_columns:
            missing_lease_columns.append(
                sa.Column("refresh_lease_until", sa.DateTime(timezone=True), nullable=True)
            )
        if missing_lease_columns:
            with op.batch_alter_table("broker_credentials") as batch_op:
                for column in missing_lease_columns:
                    batch_op.add_column(column)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("broker_credentials"):
        op.create_table(
            "broker_credentials",
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("encrypted_access_token", sa.Text(), nullable=True),
            sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
            sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("refresh_lease_owner", sa.String(length=36), nullable=True),
            sa.Column("refresh_lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider"),
        )
        op.create_index(
            "ix_broker_credentials_created_at",
            "broker_credentials",
            ["created_at"],
            unique=False,
        )

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("managed_positions"):
        op.create_table(
            "managed_positions",
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("sector", sa.String(length=100), nullable=False),
            sa.Column("uic", sa.Integer(), nullable=False),
            sa.Column("asset_type", sa.String(length=32), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("entry_price", sa.Float(), nullable=False),
            sa.Column("atr", sa.Float(), nullable=False),
            sa.Column("stop_loss", sa.Float(), nullable=False),
            sa.Column("take_profit", sa.Float(), nullable=False),
            sa.Column("high_watermark", sa.Float(), nullable=False),
            sa.Column("broker_entry_order_id", sa.String(length=100), nullable=False),
            sa.Column("broker_stop_order_id", sa.String(length=100), nullable=True),
            sa.Column("broker_take_order_id", sa.String(length=100), nullable=True),
            sa.Column("trailing_active", sa.Boolean(), nullable=False),
            sa.Column("state", sa.String(length=24), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("broker_entry_order_id"),
        )
        op.create_index(
            "ix_managed_positions_created_at",
            "managed_positions",
            ["created_at"],
            unique=False,
        )
        op.create_index(
            "ix_managed_positions_state",
            "managed_positions",
            ["state"],
            unique=False,
        )
        op.create_index(
            "ix_managed_positions_symbol",
            "managed_positions",
            ["symbol"],
            unique=False,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("managed_positions"):
        op.drop_table("managed_positions")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("broker_credentials"):
        op.drop_table("broker_credentials")
    # Keep the nullable signal_id column on downgrade. Fresh 0001 installations
    # may already contain it because that legacy migration reflects metadata at
    # runtime, so removing it would make downgrade destructive and ambiguous.
