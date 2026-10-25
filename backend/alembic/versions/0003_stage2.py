"""Persist scoped chat sessions, messages and verified citations.

Revision ID: 0003_stage2
Revises: 0002_stage1
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_stage2"
down_revision = "0002_stage1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "course_id", sa.String(36), sa.ForeignKey("courses.id"), nullable=False
        ),
        sa.Column("document_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_sessions_owner_id", "chat_sessions", ["owner_id"])
    op.create_index("ix_chat_sessions_course_id", "chat_sessions", ["course_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("retrieval_query", sa.Text()),
        sa.Column("candidate_chunk_ids", sa.JSON()),
        sa.Column("model", sa.String(255)),
        sa.Column("embedding_model", sa.String(255)),
        sa.Column("retrieval_ms", sa.Integer()),
        sa.Column("first_token_ms", sa.Integer()),
        sa.Column("generation_ms", sa.Integer()),
        sa.Column("total_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cost_estimate", sa.Float()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_owner_id", "messages", ["owner_id"])
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_table(
        "citations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chunk_id", sa.String(36), sa.ForeignKey("chunks.id", ondelete="SET NULL")
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
        ),
        sa.Column("label", sa.Integer(), nullable=False),
    )
    op.create_index("ix_citations_owner_id", "citations", ["owner_id"])
    op.create_index("ix_citations_message_id", "citations", ["message_id"])


def downgrade() -> None:
    op.drop_table("citations")
    op.drop_table("messages")
    op.drop_table("chat_sessions")
