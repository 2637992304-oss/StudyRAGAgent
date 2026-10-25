"""Typed external evidence and session-scoped repository authorization."""

import sqlalchemy as sa

from alembic import op

revision = "0006_external"
down_revision = "0005_thinking"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("tool_policy", sa.JSON(), nullable=True))
    op.create_table(
        "repository_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("ref", sa.String(255), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "external_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    for table, fields in (
        ("repository_grants", ("owner_id", "session_id")),
        ("external_sources", ("owner_id", "session_id", "message_id")),
    ):
        for field in fields:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    op.drop_table("external_sources")
    op.drop_table("repository_grants")
    op.drop_column("messages", "tool_policy")
