"""Request-scoped thinking preferences and private tool protocol history."""

import sqlalchemy as sa

from alembic import op

revision = "0005_thinking"
down_revision = "0004_stage4"
branch_labels = None
depends_on = None


def upgrade():
    # No defaults/backfill: NULL explicitly identifies legacy messages.
    op.add_column("messages", sa.Column("thinking", sa.Boolean(), nullable=True))
    op.add_column("messages", sa.Column("call_config", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("protocol_state", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("reply_to_id", sa.String(36), nullable=True))


def downgrade():
    for name in ("reply_to_id", "protocol_state", "call_config", "thinking"):
        op.drop_column("messages", name)
