"""Actual additive migration roundtrip, only under the disposable runner."""

import os
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from app.db.models import (
    ChatSession,
    Course,
    MemoryDependency,
    MemoryState,
    Message,
    User,
)
from app.db.session import get_engine


@pytest.mark.skipif(
    not os.environ.get("STAGE7_VALIDATION_RUN"), reason="disposable PostgreSQL required"
)
def test_0010_roundtrip_preserves_old_messages():
    owner, course, sid, msg = [str(uuid4()) for _ in range(4)]
    with Session(get_engine()) as db, db.begin():
        db.add(
            User(id=owner, email=owner + "@example.test", password_hash="disposable")
        )
        db.flush()
        db.add(Course(id=course, owner_id=owner, name="migration"))
        db.flush()
        db.add(ChatSession(id=sid, owner_id=owner, course_id=course))
        db.flush()
        db.add(
            Message(
                id=msg,
                session_id=sid,
                owner_id=owner,
                role="user",
                content="保留的旧消息",
                status="completed",
            )
        )
    config = Config("alembic.ini")
    try:
        command.downgrade(config, "0009_message_measurements")
        command.upgrade(config, "head")
        command.check(config)
        with Session(get_engine()) as db:
            assert db.get(Message, msg).content == "保留的旧消息"
        with Session(get_engine()) as db, db.begin():
            control = MemoryState(
                owner_id=owner,
                scope="session:" + sid,
                data={"enabled": True, "summary": {"task_goal": []}},
            )
            db.add(control)
            db.flush()
            control.data = {
                **control.data,
                "dependencies": {"coverage": control.id, "scope": None},
            }
            db.add(
                MemoryDependency(
                    owner_id=owner,
                    state_id=control.id,
                    kind="messages",
                    source_id=msg,
                    digest="x" * 64,
                )
            )
            control_id = control.id
        command.downgrade(config, "0010_memory")
        with Session(get_engine()) as db:
            assert db.get(MemoryState, control_id).data["summary"] is None
            assert db.get(Message, msg).content == "保留的旧消息"
        with Session(get_engine()) as db, db.begin():
            control = db.get(MemoryState, control_id)
            control.data = {
                "enabled": True,
                "summary": {"task_goal": []},
                "dependencies": {"messages": {msg: "legacy-digest"}, "scope": None},
            }
        command.upgrade(config, "head")
        command.check(config)
        with Session(get_engine()) as db:
            assert (
                db.get(MemoryState, control_id).data["dependencies"]["messages"][msg]
                == "legacy-digest"
            )
    finally:
        with Session(get_engine()) as db, db.begin():
            db.delete(db.get(Message, msg))
            db.flush()
            db.delete(db.get(ChatSession, sid))
            db.flush()
            db.delete(db.get(Course, course))
            db.flush()
            db.delete(db.get(User, owner))
