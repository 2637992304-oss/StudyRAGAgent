"""Formal PostgreSQL conversion/constraints on a dedicated disposable database."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from app.db.models import (
    Attachment,
    ChatSession,
    Course,
    MemoryItem,
    MemoryItemSource,
    MemoryPreference,
    Message,
    User,
)
from app.db.session import get_engine
from app.memory.legacy_text import content
from app.memory.service import VALUES


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").endswith("/text_migration"),
    reason="dedicated disposable migration database required",
)
def test_all_legacy_values_tombstones_and_pg_constraints():
    engine = get_engine()
    assert engine.dialect.name == "postgresql"
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "0011_memory_dependencies")
    records, owners = [], []
    try:
        with Session(engine) as db, db.begin():
            for key, values in VALUES.items():
                for value in sorted(values):
                    owner, ident = str(uuid4()), str(uuid4())
                    owners.append(owner)
                    db.add(
                        User(
                            id=owner,
                            email=owner + "@migration.example.test",
                            password_hash="unused",
                        )
                    )
                    db.flush()
                    db.add(
                        MemoryPreference(
                            id=ident,
                            owner_id=owner,
                            scope="global",
                            key=key,
                            value=value,
                            version=3,
                            source_kind="manual",
                            sources=[],
                            deleted=value == "C++",
                        )
                    )
                    records.append((ident, key, value))
            owner, ident = str(uuid4()), str(uuid4())
            owners.append(owner)
            db.add(
                User(
                    id=owner,
                    email=owner + "@migration.example.test",
                    password_hash="unused",
                )
            )
            db.flush()
            db.add(
                MemoryPreference(
                    id=ident,
                    owner_id=owner,
                    scope="global",
                    key="hint_first",
                    value="true",
                    version=2,
                    source_kind="explicit_chat",
                    sources=[str(uuid4())],
                )
            )
            invalid = ident
            owner, ident = str(uuid4()), str(uuid4())
            owners.append(owner)
            db.add(
                User(
                    id=owner,
                    email=owner + "@migration.example.test",
                    password_hash="unused",
                )
            )
            db.flush()
            course = Course(owner_id=owner, name="旧来源迁移")
            db.add(course)
            db.flush()
            chat = ChatSession(owner_id=owner, course_id=course.id)
            db.add(chat)
            db.flush()
            message = Message(
                owner_id=owner,
                session_id=chat.id,
                role="user",
                content="记住以后先给提示",
                status="completed",
            )
            db.add(message)
            db.flush()
            attachment = Attachment(
                owner_id=owner,
                session_id=chat.id,
                message_id=message.id,
                filename="synthetic.png",
                mime_type="image/png",
                storage_key=str(uuid4()),
                sha256="f" * 64,
                size_bytes=1,
                width=1,
                height=1,
            )
            db.add(attachment)
            db.flush()
            attachment_id = attachment.id
            db.add(
                MemoryPreference(
                    id=ident,
                    owner_id=owner,
                    scope="global",
                    key="hint_first",
                    value="true",
                    version=4,
                    source_kind="explicit_chat",
                    sources=[message.id],
                )
            )
            sourced = ident
        command.upgrade(config, "head")
        command.check(config)
        with Session(engine) as db:
            from app.memory.text_service import sources_live

            assert db.get(MemoryItem, invalid).status == "invalidated"
            copied = db.get(MemoryItem, sourced)
            assert sources_live(db, copied)
            assert {"messages", "sessions", "policies", "attachments"} <= set(
                db.scalars(select(MemoryItemSource.kind))
            )
            db.delete(db.get(Attachment, attachment_id))
            db.commit()
            assert not sources_live(db, copied)
            for ident, key, value in records:
                item = db.get(MemoryItem, ident)
                assert item.version == 3 and item.content == content(key, value)
                assert item.status == ("deleted" if value == "C++" else "active")
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(
                    text("UPDATE memory_items SET status='invented' WHERE id=:id"),
                    {"id": records[0][0]},
                )
            db.rollback()
        command.downgrade(config, "0012_text_memory_storage")
        command.upgrade(config, "head")
        command.check(config)
        with Session(engine) as db:
            assert (
                len(
                    list(
                        db.scalars(
                            select(MemoryItem).where(MemoryItem.owner_id.in_(owners))
                        )
                    )
                )
                == 16
            )
    finally:
        command.upgrade(config, "head")
        with Session(engine) as db, db.begin():
            for model in (Attachment, Message, ChatSession, Course):
                db.execute(delete(model).where(model.owner_id.in_(owners)))
            for owner in owners:
                row = db.get(User, owner)
                if row:
                    db.delete(row)
