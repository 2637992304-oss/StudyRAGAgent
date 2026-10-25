"""Persist conversation titles and retain names for existing conversations."""

import sqlalchemy as sa

from alembic import op

revision = "0007_session_titles"
down_revision = "0006_external"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chat_sessions",
        sa.Column("title", sa.String(120), nullable=False, server_default="新对话"),
    )
    op.add_column(
        "chat_sessions",
        sa.Column(
            "title_renamed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.execute(
        sa.text(r"""
        UPDATE chat_sessions SET title = COALESCE((
            SELECT CASE WHEN trim(m.content) = '' THEN '图片问答'
                ELSE left(regexp_replace(trim(m.content), '\s+', ' ', 'g'), 20) END
            FROM messages m
            WHERE m.session_id = chat_sessions.id
              AND m.owner_id = chat_sessions.owner_id AND m.role = 'user'
            ORDER BY m.created_at, m.id LIMIT 1
        ), '新对话')
    """)
    )


def downgrade():
    op.drop_column("chat_sessions", "title_renamed")
    op.drop_column("chat_sessions", "title")
