"""Disposable in-memory SQLite checks, not PostgreSQL migration acceptance."""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import (
    Course,
    MemoryIndexJob,
    MemoryItem,
    MemoryItemRevision,
    User,
)
from app.memory.text_schema import TextMemoryInput
from app.memory.text_service import apply_manual_batch, owned, revise


def test_derived_memory_invalidates_when_parent_changes(store):
    from app.memory import text_service as service

    parent = revise(store, "alice", TextMemoryInput(content="实验耗时7秒"))
    operation = apply_manual_batch(
        store, "alice", [{"input": TextMemoryInput(content="实验结果备忘")}], "derived"
    )
    child = owned(store, "alice", operation.data["after"][0]["id"])
    service.bind_sources(
        store,
        "alice",
        operation,
        [],
        "explicit_request",
        dependencies=[
            {
                "kind": "memory_items",
                "id": parent.id,
                "digest": service.dependency_digest("memory_items", parent),
            }
        ],
    )
    assert service.sources_live(store, child)
    revise(
        store,
        "alice",
        TextMemoryInput(content="实验结果已纠正"),
        ident=parent.id,
        version=1,
    )
    assert not service.sources_live(store, child)


def test_permissions_filter_before_relevant_top_eight(store):
    from app.memory.text_service import rank

    for i in range(10):
        revise(store, "alice", TextMemoryInput(content=f"目标 {i}"))
    allowed = revise(store, "alice", TextMemoryInput(content="目标 仅允许这一条"))
    rows = rank(store, "alice", None, "目标", allowed=lambda row: row.id == allowed.id)
    assert [row.id for row in rows] == [allowed.id]


def test_disabling_parent_use_blocks_derived_memory(store):
    from app.memory import service as legacy
    from app.memory import text_service as service

    parent = revise(
        store, "alice", TextMemoryInput(content="父记忆", course_id="course")
    )
    operation = apply_manual_batch(
        store, "alice", [{"input": TextMemoryInput(content="派生内容")}], "derived-use"
    )
    child = owned(store, "alice", operation.data["after"][0]["id"])
    service.bind_sources(
        store,
        "alice",
        operation,
        [],
        "explicit_request",
        dependencies=[
            {
                "kind": "memory_items",
                "id": parent.id,
                "digest": service.dependency_digest("memory_items", parent),
            }
        ],
    )
    assert service.sources_live(store, child)
    control = legacy.settings(store, "alice", "course:course")
    legacy.change_settings(store, "alice", "course", control["version"], {"use": False})
    assert not service.sources_live(store, child)


def test_summary_rebuild_can_progress_past_obsolete_answer_memory(store):
    from app.db.models import ChatSession, Message
    from app.memory import summary

    item = revise(store, "alice", TextMemoryInput(content="原要求", course_id="course"))
    chat = ChatSession(owner_id="alice", course_id="course")
    store.add(chat)
    store.flush()
    answer = Message(
        owner_id="alice",
        session_id=chat.id,
        role="assistant",
        content="重建时不进入摘要输入的旧助手正文",
        status="completed",
        protocol_state={"memory_items": [{"id": item.id, "version": item.version}]},
    )
    store.add(answer)
    store.flush()
    original = summary.dependencies(store, chat, [answer])
    assert summary.valid_dependencies(store, chat, original)
    revise(
        store,
        "alice",
        TextMemoryInput(content="新要求", course_id="course"),
        ident=item.id,
        version=1,
    )
    assert not summary.valid_dependencies(store, chat, original)
    rebuilt = summary.dependencies(store, chat, [answer])
    assert rebuilt["memory_items"] == {}
    assert summary.valid_dependencies(store, chat, rebuilt)
    answer.protocol_state = {"memory_items": [{"id": item.id, "version": item.version}]}
    store.flush()
    current = summary.dependencies(store, chat, [answer])
    assert current["memory_items"]
    from app.memory import service as legacy

    control = legacy.settings(store, "alice", "course:course")
    legacy.change_settings(store, "alice", "course", control["version"], {"use": False})
    assert not summary.valid_dependencies(store, chat, current)
    assert summary.dependencies(store, chat, [answer])["memory_items"] == {}


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(get_settings(), "memory_preferences_enabled", True)
    monkeypatch.setattr(get_settings(), "memory_engine", "text")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                User(id="alice", email="alice@example.test", password_hash="unused"),
                User(id="bob", email="bob@example.test", password_hash="unused"),
            ]
        )
        db.flush()
        db.add(Course(id="course", owner_id="alice", name="自制测试"))
        db.commit()
        yield db
    engine.dispose()


def test_revision_and_outbox_share_caller_rollback(store):
    item = revise(store, "alice", TextMemoryInput(content="以后结合实验讲解"))
    assert item.version == 1
    assert store.scalar(select(func.count()).select_from(MemoryIndexJob)) == 1
    store.rollback()
    for model in (MemoryItem, MemoryItemRevision, MemoryIndexJob):
        assert store.scalar(select(func.count()).select_from(model)) == 0


def test_edit_retains_id_and_creates_new_revision(store):
    item = revise(store, "alice", TextMemoryInput(content="先讲直觉"))
    ident = item.id
    store.commit()
    updated = revise(
        store,
        "alice",
        TextMemoryInput(content="先讲直觉，再给定义"),
        ident=ident,
        version=1,
    )
    store.commit()
    assert updated.id == ident
    assert updated.version == 2
    revisions = list(
        store.scalars(select(MemoryItemRevision).order_by(MemoryItemRevision.version))
    )
    assert [r.data["content"] for r in revisions] == ["先讲直觉", "先讲直觉，再给定义"]
    with pytest.raises(HTTPException) as exc:
        revise(
            store, "alice", TextMemoryInput(content="旧请求"), ident=ident, version=1
        )
    assert exc.value.status_code == 409


def test_owner_and_course_boundaries(store):
    item = revise(
        store, "alice", TextMemoryInput(content="我的学习目标", course_id="course")
    )
    store.commit()
    with pytest.raises(HTTPException) as exc:
        owned(store, "bob", item.id)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        revise(store, "bob", TextMemoryInput(content="越权", course_id="course"))
    assert exc.value.status_code == 404


def test_idempotency_and_changed_payload_rejected(store):
    changes = [{"input": TextMemoryInput(content="完成课程实验")}]
    first = apply_manual_batch(store, "alice", changes, "same")
    store.commit()
    replay = apply_manual_batch(store, "alice", changes, "same")
    assert replay.id == first.id
    assert store.scalar(select(func.count()).select_from(MemoryItem)) == 1
    with pytest.raises(HTTPException) as exc:
        apply_manual_batch(
            store, "alice", [{"input": TextMemoryInput(content="其他内容")}], "same"
        )
    assert exc.value.status_code == 409


def test_late_conflict_rolls_back_whole_batch(store):
    item = revise(store, "alice", TextMemoryInput(content="原要求"))
    ident = item.id
    store.commit()
    with pytest.raises(HTTPException):
        apply_manual_batch(
            store,
            "alice",
            [
                {"input": TextMemoryInput(content="不能留下的第一项")},
                {"id": ident, "version": 0, "input": TextMemoryInput(content="冲突")},
            ],
            "conflict",
        )
    assert store.scalar(select(func.count()).select_from(MemoryItem)) == 1
    assert store.scalar(select(func.count()).select_from(MemoryItemRevision)) == 1
