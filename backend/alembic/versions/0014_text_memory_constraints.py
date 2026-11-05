"""Enforce text-memory state and scope invariants in PostgreSQL."""

from alembic import op

revision = "0014_text_memory_constraints"
down_revision = "0013_text_memory_legacy"
branch_labels = None
depends_on = None

CONSTRAINTS = {
    "ck_memory_item_version": "version >= 1",
    "ck_memory_item_status": (
        "status IN ('active','superseded','deleted','invalidated')"
    ),
    "ck_memory_item_use_mode": "use_mode IN ('always','relevant')",
    "ck_memory_item_basis": (
        "write_basis IN ('manual','explicit_request','user_confirmed')"
    ),
    "ck_memory_item_scope": (
        "(scope_type='global' AND course_id IS NULL) OR "
        "(scope_type='course' AND course_id IS NOT NULL)"
    ),
    "ck_memory_item_dates": (
        "valid_from IS NULL OR expires_at IS NULL OR valid_from < expires_at"
    ),
}


def upgrade():
    for name, expression in CONSTRAINTS.items():
        op.create_check_constraint(name, "memory_items", expression)


def downgrade():
    for name in reversed(CONSTRAINTS):
        op.drop_constraint(name, "memory_items", type_="check")
