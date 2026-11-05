"""Normalize summary provenance; preserve all 0010 summaries and messages."""

import sqlalchemy as sa

from alembic import op

revision = "0011_memory_dependencies"
down_revision = "0010_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_dependencies",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "state_id",
            sa.String(36),
            sa.ForeignKey("memory_states.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("state_id", "kind", "source_id"),
    )
    for column in ("owner_id", "state_id", "source_id"):
        op.create_index(
            "ix_memory_dependencies_" + column, "memory_dependencies", [column]
        )


def downgrade():
    # Normalized summaries are invalidated, never left with missing provenance.
    table = sa.table(
        "memory_states",
        sa.column("id", sa.String),
        sa.column("data", sa.JSON),
        sa.column("epoch", sa.Integer),
        sa.column("version", sa.Integer),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).mappings():
        if row["data"].get("dependencies", {}).get("coverage"):
            data = {**row["data"], "summary": None, "dependencies": {}}
            connection.execute(
                table.update()
                .where(table.c.id == row["id"])
                .values(data=data, epoch=row["epoch"] + 1, version=row["version"] + 1)
            )
    op.drop_table("memory_dependencies")
