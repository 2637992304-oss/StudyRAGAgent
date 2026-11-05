"""Stage 7 memory; additive tables, old messages remain unchanged."""

import sqlalchemy as sa

from alembic import op

revision = "0010_memory"
down_revision = "0009_message_measurements"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_states",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("owner_id", "scope"),
    )
    op.create_index("ix_memory_states_owner_id", "memory_states", ["owner_id"])
    op.create_table(
        "memory_preferences",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("value", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "scope", "key"),
    )
    op.create_index(
        "ix_memory_preferences_owner_id", "memory_preferences", ["owner_id"]
    )
    op.create_table(
        "memory_suggestions",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "dedup_key"),
    )
    op.create_index(
        "ix_memory_suggestions_owner_id", "memory_suggestions", ["owner_id"]
    )
    op.create_index(
        "ix_memory_suggestions_session_id", "memory_suggestions", ["session_id"]
    )
    op.create_table(
        "memory_operations",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("request_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("owner_id", "request_key"),
    )
    op.create_index("ix_memory_operations_owner_id", "memory_operations", ["owner_id"])
    op.create_index(
        "ix_memory_operations_session_id", "memory_operations", ["session_id"]
    )
    op.create_table(
        "memory_jobs",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("work_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("measurements", sa.JSON(), nullable=True),
        sa.UniqueConstraint("session_id", "work_key"),
    )
    op.create_index("ix_memory_jobs_owner_id", "memory_jobs", ["owner_id"])
    op.create_index("ix_memory_jobs_status", "memory_jobs", ["status"])
    op.create_index("ix_memory_jobs_session_id", "memory_jobs", ["session_id"])


def downgrade():
    op.drop_table("memory_jobs")
    op.drop_table("memory_operations")
    op.drop_table("memory_suggestions")
    op.drop_table("memory_preferences")
    op.drop_table("memory_states")
