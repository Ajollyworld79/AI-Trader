"""Initial enterprise trading schema.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from alembic import op
from app.infrastructure import models as _models  # noqa: F401
from app.infrastructure.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
