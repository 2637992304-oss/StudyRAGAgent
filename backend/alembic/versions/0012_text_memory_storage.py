"""Add text-memory storage; legacy data conversion is a separate cutover step."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0012_text_memory_storage"
down_revision = "0011_memory_dependencies"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("write_basis", sa.String(length=24), nullable=False),
        sa.Column("applies_when", sa.Text(), nullable=True),
        sa.Column("exceptions", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("use_mode", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memory_items_course_id", "memory_items", ["course_id"])
    op.create_index("ix_memory_items_owner_id", "memory_items", ["owner_id"])
    op.create_index("ix_memory_items_status", "memory_items", ["status"])
    op.create_table(
        "memory_item_revisions",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("memory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("item_id", "version"),
    )
    op.create_index(
        "ix_memory_item_revisions_item_id", "memory_item_revisions", ["item_id"]
    )
    op.create_table(
        "memory_item_sources",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "revision_id",
            sa.String(length=36),
            sa.ForeignKey("memory_item_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("revision_id", "kind", "source_id"),
    )
    op.create_index(
        "ix_memory_item_sources_revision_id", "memory_item_sources", ["revision_id"]
    )
    op.create_index(
        "ix_memory_item_sources_source_id", "memory_item_sources", ["source_id"]
    )
    op.create_table(
        "memory_item_relations",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("memory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.String(length=36),
            sa.ForeignKey("memory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.UniqueConstraint("item_id", "target_id", "kind"),
    )
    op.create_index(
        "ix_memory_item_relations_item_id", "memory_item_relations", ["item_id"]
    )
    op.create_table(
        "memory_item_embeddings",
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("memory_items.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
    )
    op.create_table(
        "memory_index_jobs",
        sa.Column("id", sa.String(length=36), nullable=False, primary_key=True),
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("memory_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.UniqueConstraint("item_id", "version"),
    )
    op.create_index("ix_memory_index_jobs_item_id", "memory_index_jobs", ["item_id"])
    op.create_index("ix_memory_index_jobs_status", "memory_index_jobs", ["status"])


def downgrade():
    op.drop_index("ix_memory_index_jobs_item_id", table_name="memory_index_jobs")
    op.drop_index("ix_memory_index_jobs_status", table_name="memory_index_jobs")
    op.drop_table("memory_index_jobs")
    op.drop_table("memory_item_embeddings")
    op.drop_index(
        "ix_memory_item_relations_item_id", table_name="memory_item_relations"
    )
    op.drop_table("memory_item_relations")
    op.drop_index(
        "ix_memory_item_sources_revision_id", table_name="memory_item_sources"
    )
    op.drop_index("ix_memory_item_sources_source_id", table_name="memory_item_sources")
    op.drop_table("memory_item_sources")
    op.drop_index(
        "ix_memory_item_revisions_item_id", table_name="memory_item_revisions"
    )
    op.drop_table("memory_item_revisions")
    op.drop_index("ix_memory_items_course_id", table_name="memory_items")
    op.drop_index("ix_memory_items_owner_id", table_name="memory_items")
    op.drop_index("ix_memory_items_status", table_name="memory_items")
    op.drop_table("memory_items")
