"""Deterministically copy legacy preferences; never call a model or activate mode."""

import hashlib
import json
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0013_text_memory_legacy"
down_revision = "0012_text_memory_storage"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    old = sa.Table("memory_preferences", metadata, autoload_with=connection)
    messages = sa.Table("messages", metadata, autoload_with=connection)
    items = sa.Table("memory_items", metadata, autoload_with=connection)
    revisions = sa.Table("memory_item_revisions", metadata, autoload_with=connection)
    sources = sa.Table("memory_item_sources", metadata, autoload_with=connection)
    jobs = sa.Table("memory_index_jobs", metadata, autoload_with=connection)
    sessions = sa.Table("chat_sessions", metadata, autoload_with=connection)
    attachments = sa.Table("attachments", metadata, autoload_with=connection)
    grants = sa.Table("repository_grants", metadata, autoload_with=connection)
    for row in connection.execute(sa.select(old)).mappings():
        if connection.scalar(sa.select(items.c.id).where(items.c.id == row["id"])):
            continue
        value, key = row["value"], row["key"]
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
        elif key == "hint_first":
            content = {
                "true": "解题时先给提示，再根据用户需要展开答案。",
                "false": "解题时可以直接展开答案，无需先给提示。",
            }[value]
        else:
            raise RuntimeError("Unsupported legacy memory key; conversion aborted")
        evidence = []
        dependencies = {}
        valid = True
        for ident in row["sources"] or []:
            msg = (
                connection.execute(sa.select(messages).where(messages.c.id == ident))
                .mappings()
                .first()
            )
            if (
                not msg
                or msg["owner_id"] != row["owner_id"]
                or msg["role"] != "user"
                or msg["status"] != "completed"
            ):
                valid = False
                continue
            digest = hashlib.sha256(
                json.dumps(
                    [
                        msg["id"],
                        msg["session_id"],
                        msg["owner_id"],
                        msg["role"],
                        msg["content"],
                    ],
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            evidence.append((ident, digest))
            chat = (
                connection.execute(
                    sa.select(sessions).where(sessions.c.id == msg["session_id"])
                )
                .mappings()
                .first()
            )
            if not chat or chat["owner_id"] != row["owner_id"]:
                valid = False
                continue
            specs = [
                (
                    "sessions",
                    chat["id"],
                    [chat["owner_id"], chat["course_id"], chat["document_ids"]],
                ),
                ("policies", msg["id"], msg["tool_policy"]),
            ]
            for attachment in connection.execute(
                sa.select(attachments).where(attachments.c.message_id == ident)
            ).mappings():
                specs.append(
                    (
                        "attachments",
                        attachment["id"],
                        [attachment["owner_id"], attachment["sha256"]],
                    )
                )
            for grant_id in (msg["tool_policy"] or {}).get("grant_ids", []):
                grant = (
                    connection.execute(sa.select(grants).where(grants.c.id == grant_id))
                    .mappings()
                    .first()
                )
                if (
                    not grant
                    or not grant["active"]
                    or grant["owner_id"] != row["owner_id"]
                ):
                    valid = False
                    continue
                specs.append(
                    (
                        "grants",
                        grant_id,
                        [
                            grant["owner_id"],
                            grant["session_id"],
                            grant["repository"],
                            grant["ref"],
                            grant["active"],
                        ],
                    )
                )
            for kind, source_id, value in specs:
                dependencies[(kind, source_id)] = hashlib.sha256(
                    json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
        data = dict(
            id=row["id"],
            owner_id=row["owner_id"],
            course_id=None
            if row["scope"] == "global"
            else row["scope"].removeprefix("course:"),
            scope_type="global" if row["scope"] == "global" else "course",
            content=content,
            status="deleted"
            if row["deleted"]
            else "active"
            if valid
            else "invalidated",
            version=row["version"],
            write_basis="manual"
            if row["source_kind"] == "manual"
            else "user_confirmed"
            if row["source_kind"] == "confirmed"
            else "explicit_request",
            tags=[],
            use_mode="always",
            applies_when=None,
            exceptions=None,
            valid_from=None,
            expires_at=None,
            created_at=row["updated_at"],
            updated_at=row["updated_at"],
        )
        connection.execute(items.insert().values(**data))
        revision_id = str(uuid4())
        snapshot = {
            k: v
            for k, v in data.items()
            if k not in {"owner_id", "created_at", "updated_at"}
        }
        connection.execute(
            revisions.insert().values(
                id=revision_id,
                item_id=row["id"],
                version=row["version"],
                data=snapshot,
                created_at=row["updated_at"],
            )
        )
        for ident, digest in evidence:
            connection.execute(
                sources.insert().values(
                    id=str(uuid4()),
                    revision_id=revision_id,
                    kind="messages",
                    source_id=ident,
                    digest=digest,
                )
            )
        for (kind, source_id), digest in dependencies.items():
            connection.execute(
                sources.insert().values(
                    id=str(uuid4()),
                    revision_id=revision_id,
                    kind=kind,
                    source_id=source_id,
                    digest=digest,
                )
            )
        if data["status"] == "active":
            connection.execute(
                jobs.insert().values(
                    id=str(uuid4()),
                    item_id=row["id"],
                    version=row["version"],
                    status="queued",
                    attempts=0,
                    data={},
                )
            )


def downgrade():
    # Copy is deliberately preserved. Dropping 0012 deletes the new memory store;
    # that destructive action is not a normal behavioral rollback.
    pass
