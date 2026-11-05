"""Project historical cards to text protocol; preserve original payloads."""

import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "0015_text_memory_suggestions"
down_revision = "0014_text_memory_constraints"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    cards = sa.Table("memory_suggestions", metadata, autoload_with=connection)
    preferences = sa.Table("memory_preferences", metadata, autoload_with=connection)
    messages = sa.Table("messages", metadata, autoload_with=connection)
    for row in connection.execute(sa.select(cards)).mappings():
        data = row["data"]
        if data.get("schema_version") == 2:
            continue
        key, value = data["key"], data["value"]
        if key == "response_language":
            content = {"zh": "默认使用中文讲解。", "en": "默认使用英文讲解。"}[value]
        elif key == "code_language":
            content = f"默认使用{value}编写代码示例。"
        elif key == "explanation_detail":
            content = {
                "brief": "默认保持讲解简短。",
                "standard": "默认使用适中的讲解详细程度。",
                "detailed": "默认详细讲解。",
            }[value]
        else:
            content = {
                "true": "解题时先给提示，再根据用户需要展开答案。",
                "false": "解题时可以直接展开答案，无需先给提示。",
            }[value]
        target = connection.scalar(
            sa.select(preferences.c.id).where(
                preferences.c.owner_id == row["owner_id"],
                preferences.c.scope == data["scope"],
                preferences.c.key == key,
            )
        )
        digests, valid = {}, True
        for ident in data["sources"]:
            message = (
                connection.execute(sa.select(messages).where(messages.c.id == ident))
                .mappings()
                .first()
            )
            if (
                not message
                or message["owner_id"] != row["owner_id"]
                or message["status"] != "completed"
                or message["role"] != "user"
            ):
                valid = False
                continue
            digests[ident] = hashlib.sha256(
                json.dumps(
                    [
                        message["id"],
                        message["session_id"],
                        message["owner_id"],
                        message["role"],
                        message["content"],
                    ],
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
        item = {
            "content": content,
            "course_id": None
            if data["scope"] == "global"
            else data["scope"].removeprefix("course:"),
            "use_mode": "always",
        }
        projected = {
            "schema_version": 2,
            "legacy_payload": data,
            "name": "propose_memory",
            "input": item,
            "items": [item],
            "id": target,
            "version": data["preference_version"],
            "scope": data["scope"],
            "epoch": data["epoch"],
            "global_epoch": data["global_epoch"],
            "sources": data["sources"],
            "source_digests": digests,
        }
        connection.execute(
            cards.update()
            .where(cards.c.id == row["id"])
            .values(data=projected, status=row["status"] if valid else "invalidated")
        )


def downgrade():
    connection = op.get_bind()
    cards = sa.Table("memory_suggestions", sa.MetaData(), autoload_with=connection)
    for row in connection.execute(sa.select(cards)).mappings():
        data = row["data"]
        if data.get("legacy_payload"):
            connection.execute(
                cards.update()
                .where(cards.c.id == row["id"])
                .values(data=data["legacy_payload"])
            )
        elif data.get("schema_version") == 2:
            connection.execute(
                cards.update()
                .where(cards.c.id == row["id"])
                .values(status="invalidated")
            )
