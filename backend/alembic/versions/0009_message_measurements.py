"""Versioned usage and monotonic timings; old records remain unknown."""

import sqlalchemy as sa

from alembic import op

revision = "0009_message_measurements"
down_revision = "0008_ingestion_recovery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("measurements", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("messages", "measurements")
