"""Stage 4 private image attachments and agent execution metadata."""

import sqlalchemy as sa

from alembic import op

revision = "0004_stage4"
down_revision = "0003_stage2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("agent_trace", sa.JSON(), nullable=True))
    op.add_column("messages", sa.Column("stop_reason", sa.String(40), nullable=True))
    op.add_column("messages", sa.Column("answer_scope", sa.String(16), nullable=True))
    op.create_table(
        "attachments",
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
            nullable=True,
        ),
        sa.Column(
            "saved_document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False),
        sa.Column("storage_key", sa.String(36), unique=True, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("owner_id", "session_id", "message_id"):
        op.create_index(f"ix_attachments_{column}", "attachments", [column])


def downgrade():
    op.drop_table("attachments")
    for column in ("answer_scope", "stop_reason", "agent_trace"):
        op.drop_column("messages", column)
