"""Durable ingestion delivery/leases and visible parse quality. No reprocessing."""

import sqlalchemy as sa

from alembic import op

revision = "0008_ingestion_recovery"
down_revision = "0007_session_titles"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [
        ("execution_token", sa.String(36)),
        ("lease_until", sa.DateTime(timezone=True)),
        ("dispatched_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("ingestion_jobs", sa.Column(name, kind, nullable=True))
    for name in ("attempts", "delivery_attempts"):
        op.add_column(
            "ingestion_jobs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )
    op.add_column(
        "documents",
        sa.Column("parse_warnings", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("documents", sa.Column("parse_quality", sa.String(24), nullable=True))
    op.add_column(
        "documents", sa.Column("parser_version", sa.String(40), nullable=True)
    )


def downgrade():
    for name in ("parser_version", "parse_quality", "parse_warnings"):
        op.drop_column("documents", name)
    for name in (
        "delivery_attempts",
        "attempts",
        "dispatched_at",
        "lease_until",
        "execution_token",
    ):
        op.drop_column("ingestion_jobs", name)
