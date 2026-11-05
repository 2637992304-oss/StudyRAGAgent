"""Text mode ASGI + scripted single graph; isolated PG via existing runner."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_memory import library, mem, stage4  # noqa: F401
from test_stage4 import Scripted, ask

from app.core.config import get_settings
from app.db.models import (
    Attachment,
    ChatSession,
    Chunk,
    Document,
    MemoryItem,
    MemoryItemSource,
    MemorySuggestion,
    Message,
    now,
)
from app.memory import text_graph, text_service
from app.providers.llm import Delta


@pytest.fixture
def text_mem(mem, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "memory_engine", "text")
    return mem


def tool(name, **args):
    return [
        Delta(
            tool_calls=[
                {
                    "index": 0,
                    "id": str(uuid4()),
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
            finish_reason="tool_calls",
        )
    ]


def create(context, content="先讲直觉，再给定义", **extra):
    client, headers, courses = context[:3]
    return client.post(
        "/api/v1/memory/items",
        headers=headers[0],
        json={
            "content": content,
            "course_id": courses[0],
            "version": 0,
            "request_key": str(uuid4()),
            **extra,
        },
    )


def test_manual_api_text_and_boundary(text_mem):
    response = create(text_mem, "😀" * 1000)
    assert response.status_code == 200, response.text
    assert response.json()["schema_version"] == 2
    assert create(text_mem, "😀" * 1001).status_code == 422


def test_manual_api_edit_delete_undo(text_mem):
    client, headers = text_mem[:2]
    item = create(text_mem).json()["items"][0]
    updated = client.put(
        f"/api/v1/memory/items/{item['id']}",
        headers=headers[0],
        json={
            "content": "数学推导详细，其他简短",
            "course_id": item["course_id"],
            "version": item["version"],
            "request_key": str(uuid4()),
        },
    )
    assert updated.status_code == 200, updated.text
    ident = updated.json()["operation_id"]
    assert (
        client.post(
            f"/api/v1/memory/text-operations/{ident}/undo", headers=headers[0]
        ).status_code
        == 200
    )
    items = client.get(
        f"/api/v1/memory/items?course_id={item['course_id']}", headers=headers[0]
    ).json()["items"]
    assert items[0]["content"] == item["content"]
    removed = client.request(
        "DELETE",
        f"/api/v1/memory/items/{item['id']}",
        headers=headers[0],
        json={"version": items[0]["version"], "request_key": str(uuid4())},
    )
    assert removed.status_code == 200


def test_graph_save_free_text_receipt_history(text_mem, monkeypatch):
    events, history = ask(
        text_mem,
        "记住：以后先讲直觉，再给定义。",
        Scripted(
            [
                tool(
                    "save_memory",
                    content="先讲直觉，再给定义。",
                    memory_only=True,
                    use_mode="always",
                )
            ]
        ),
        monkeypatch,
    )
    assert any(event[0] == "memory_item_operation" for event in events), events
    assert history[-1]["memory"]["operations"][0]["schema_version"] == 2
    with Session(text_mem[-2]) as db:
        row = db.scalar(select(MemoryItem))
        assert row.content == "先讲直觉，再给定义。"
        assert row.write_basis == "explicit_request"


def test_graph_update_free_text(text_mem, monkeypatch):
    item = create(text_mem, "回答简短", use_mode="always").json()["items"][0]
    events, history = ask(
        text_mem,
        "以后回答改成详细。",
        Scripted(
            [
                tool(
                    "update_memory",
                    id=item["id"],
                    version=item["version"],
                    content="回答详细",
                    use_mode="always",
                    memory_only=True,
                )
            ]
        ),
        monkeypatch,
    )
    assert any(event[0] == "memory_item_operation" for event in events), events
    assert history[-1]["memory"]["operations"][0]["items"][0]["content"] == "回答详细"


def test_source_delete_invalidates_text(text_mem, monkeypatch):
    _, history = ask(
        text_mem,
        "记住：我计划十二月完成课程。",
        Scripted(
            [tool("save_memory", content="用户计划十二月完成课程。", memory_only=True)]
        ),
        monkeypatch,
    )
    client, headers, courses = text_mem[:3]
    client.delete(
        f"/api/v1/chat/sessions/{text_mem[-1]}/messages/{history[0]['id']}",
        headers=headers[0],
    )
    assert (
        client.get(
            f"/api/v1/memory/items?course_id={courses[0]}", headers=headers[0]
        ).json()["items"]
        == []
    )


def test_off_mode_rejects_new_writes(text_mem, monkeypatch):
    monkeypatch.setattr(get_settings(), "memory_engine", "off")
    assert create(text_mem).status_code == 409


def test_cross_account_text_rejected(text_mem):
    client, headers = text_mem[:2]
    item = create(text_mem).json()["items"][0]
    assert (
        client.get(f"/api/v1/memory/items/{item['id']}", headers=headers[1]).status_code
        == 404
    )


def test_failed_answer_does_not_commit_text(text_mem, monkeypatch):
    ask(
        text_mem,
        "记住先讲直觉，顺便解释课程定理",
        Scripted(
            [
                tool("save_memory", content="先讲直觉", memory_only=False),
                [Delta("【限制】无法完成。", finish_reason="stop")],
            ]
        ),
        monkeypatch,
    )
    with Session(text_mem[-2]) as db:
        assert db.scalar(select(MemoryItem)) is None
        assert db.scalar(select(MemorySuggestion)) is None


def candidate(context, *, multiple=False, target=None):
    with Session(context[-2]) as db, db.begin():
        chat = db.get(ChatSession, context[-1])
        rows = []
        for i in range(3):
            row = Message(
                owner_id=chat.owner_id,
                session_id=chat.id,
                role="user",
                content="这次用生活例子解释。"
                if not multiple
                else "请整理以下超长要求：" + "保留条件和例外。" * 150,
                status="completed",
                created_at=now() + timedelta(seconds=i),
            )
            db.add(row)
            rows.append(row)
        db.flush()
        assistant = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="assistant",
            content="候选",
            status="completed",
        )
        db.add(assistant)
        db.flush()
        args = {"content": "以后用生活例子解释。", "source_ids": [r.id for r in rows]}
        if target:
            args.update(id=target["id"], version=target["version"])
        if multiple:
            args.update(
                organizing=True,
                items=[
                    {"content": "数学推导详细，其他简短。"},
                    {"content": "先给直觉，再给定义。"},
                ],
            )
        staged = []
        text_graph.stage_tool(
            db,
            chat,
            rows[-1],
            {
                "id": str(uuid4()),
                "function": {"name": "propose_memory", "arguments": json.dumps(args)},
            },
            staged,
        )
        events = text_graph.commit_staged(db, chat, assistant, staged)
        return events[0]


def test_pending_edit_confirm_is_atomic_and_idempotent(text_mem):
    client, headers = text_mem[:2]
    card = candidate(text_mem)
    assert card["status"] == "pending"
    body = {
        "version": card["version"],
        "items": [
            {"content": "以后优先使用课程实验举例。", "course_id": text_mem[2][0]}
        ],
        "request_key": str(uuid4()),
    }
    route = f"/api/v1/memory/text-suggestions/{card['id']}/accept"
    response = client.post(route, headers=headers[0], json=body)
    assert response.status_code == 200, response.text
    replay = client.post(route, headers=headers[0], json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == response.json()["operation_id"]
    with Session(text_mem[-2]) as db:
        assert db.scalar(select(MemoryItem)).content == body["items"][0]["content"]


def test_unique_natural_confirmation_without_model(text_mem, monkeypatch):
    from app.api import chat as chat_api

    original_error = chat_api.generation_error

    def diagnostic(exc):
        if isinstance(exc, TypeError):
            raise exc
        return original_error(exc)

    monkeypatch.setattr(chat_api, "generation_error", diagnostic)
    candidate(text_mem)
    events, _ = ask(text_mem, "可以，接受这个建议并记住。", Scripted([]), monkeypatch)
    assert any(e[0] == "memory_item_operation" for e in events), events


def test_long_split_stays_pending_then_saves_all(text_mem):
    client, headers = text_mem[:2]
    card = candidate(text_mem, multiple=True)
    with Session(text_mem[-2]) as db:
        assert db.scalar(select(MemoryItem)) is None
    response = client.post(
        f"/api/v1/memory/text-suggestions/{card['id']}/accept",
        headers=headers[0],
        json={
            "version": card["version"],
            "items": card["items"],
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 2


def test_changed_source_invalidates_pending(text_mem):
    card = candidate(text_mem)
    with Session(text_mem[-2]) as db, db.begin():
        row = db.get(MemorySuggestion, card["id"])
        source = db.get(Message, row.data["sources"][0])
        source.content = "已经更改的原文"
    client, headers = text_mem[:2]
    response = client.post(
        f"/api/v1/memory/text-suggestions/{card['id']}/accept",
        headers=headers[0],
        json={
            "version": card["version"],
            "items": card["items"],
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 409


def test_attachment_and_document_dependencies_prevent_scope_escape(text_mem):
    with Session(text_mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, text_mem[-1])
        user = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="user",
            content="记住我指定的内容，所有课程生效",
            status="completed",
        )
        assistant = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="assistant",
            content="回执",
            status="completed",
        )
        db.add_all([user, assistant])
        db.flush()
        attachment = Attachment(
            owner_id=chat.owner_id,
            session_id=chat.id,
            message_id=user.id,
            filename="synthetic.png",
            mime_type="image/png",
            storage_key=str(uuid4()),
            sha256="a" * 64,
            size_bytes=1,
            width=1,
            height=1,
        )
        db.add(attachment)
        db.flush()
        chunk = db.scalar(select(Chunk).where(Chunk.course_id == chat.course_id))
        staged = []
        text_graph.stage_tool(
            db,
            chat,
            user,
            {
                "id": str(uuid4()),
                "function": {
                    "name": "save_memory",
                    "arguments": json.dumps(
                        {
                            "scope": "global",
                            "content": "用户指定备忘",
                            "memory_only": True,
                        }
                    ),
                },
            },
            staged,
            evidence=[chunk.id],
        )
        text_graph.commit_staged(db, chat, assistant, staged)
        item = db.scalar(select(MemoryItem))
        assert text_service.sources_live(db, item)
        assert text_service.allowed_for_chat(db, item, chat)
        other = ChatSession(owner_id=chat.owner_id, course_id=text_mem[2][1])
        db.add(other)
        db.flush()
        assert not text_service.allowed_for_chat(db, item, other)
        kinds = set(db.scalars(select(MemoryItemSource.kind)))
        assert {
            "messages",
            "attachments",
            "documents",
            "chunks",
            "sessions",
            "answers",
        } <= kinds
        doc = db.get(Document, chunk.document_id)
        doc.status = "failed"
        db.flush()
        assert not text_service.sources_live(db, item)


def test_summary_tracks_memory_versions(text_mem):
    from app.memory import summary
    from app.memory.text_schema import TextMemoryInput

    with Session(text_mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, text_mem[-1])
        item = text_service.revise(
            db, chat.owner_id, TextMemoryInput(content="先讲直觉")
        )
        answer = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="assistant",
            content="按照偏好讲解",
            status="completed",
            protocol_state={"memory_items": [{"id": item.id, "version": 1}]},
        )
        db.add(answer)
        db.flush()
        deps = summary.dependencies(db, chat, [answer])
        assert summary.valid_dependencies(db, chat, deps)
        text_service.revise(
            db,
            chat.owner_id,
            TextMemoryInput(content="改为先讲定义"),
            ident=item.id,
            version=1,
        )
        assert not summary.valid_dependencies(db, chat, deps)
        rebuilt = summary.dependencies(db, chat, [answer])
        assert rebuilt["memory_items"] == {}
        assert summary.valid_dependencies(db, chat, rebuilt)


def test_publication_does_not_claim_uncommitted_save():
    answer = "我已经记住了。下面解释事务。"
    assert "没有成功提交" in text_graph.publication_guard(answer, [])
    events = [{"type": "memory_item_suggestion", "status": "pending"}]
    assert "确认前" in text_graph.publication_guard(answer, events)
    assert (
        text_graph.publication_guard(
            answer, [{"type": "memory_item_operation", "status": "saved"}]
        )
        == answer
    )


def test_graph_cannot_expand_scope_without_global_request(text_mem):
    with Session(text_mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, text_mem[-1])
        current = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="user",
            content="以后先讲直觉",
            status="completed",
        )
        db.add(current)
        db.flush()
        staged = []
        result = text_graph.stage_tool(
            db,
            chat,
            current,
            {
                "id": str(uuid4()),
                "function": {
                    "name": "save_memory",
                    "arguments": json.dumps({"content": "先讲直觉", "scope": "global"}),
                },
            },
            staged,
        )
        assert result["scope"] == "course:" + chat.course_id
        assert staged[0]["input"]["course_id"] == chat.course_id


def test_edited_suggestion_conflict_is_measured_and_not_saved(text_mem, monkeypatch):
    from app.db.models import MemoryOperation
    from app.memory import text_conflicts

    existing = create(text_mem, "默认回答简短").json()["items"][0]
    card = candidate(text_mem)
    provider = Scripted(
        [
            [
                Delta(
                    json.dumps(
                        {
                            "conflicts": [
                                {
                                    "index": 0,
                                    "target_id": existing["id"],
                                    "reason": "与简短要求冲突，请明确编辑原条目",
                                }
                            ]
                        }
                    ),
                    finish_reason="stop",
                )
            ]
        ]
    )
    monkeypatch.setattr(text_conflicts, "get_engine", lambda: text_mem[-2])
    monkeypatch.setattr(text_conflicts, "get_llm_provider", lambda: provider)
    client, headers = text_mem[:2]
    body = {
        "version": card["version"],
        "items": [{"content": "以后所有讲解详细", "course_id": text_mem[2][0]}],
        "request_key": str(uuid4()),
    }
    for _ in range(2):
        response = client.post(
            f"/api/v1/memory/text-suggestions/{card['id']}/accept",
            headers=headers[0],
            json=body,
        )
        assert response.status_code == 409, response.text
        assert "冲突" in response.json()["detail"]
    assert len(provider.inputs) == 1
    with Session(text_mem[-2]) as db:
        assert len(list(db.scalars(select(MemoryItem)))) == 1
        assert db.get(MemorySuggestion, card["id"]).status == "pending"
        operation = db.scalar(
            select(MemoryOperation).where(
                MemoryOperation.request_key == "text:conflict:" + body["request_key"]
            )
        )
        assert operation.data["usage"]["attempt"]["logical_calls"] == 1


def test_changed_candidate_cannot_silently_overwrite_old_topic(text_mem, monkeypatch):
    from app.memory import text_conflicts

    existing = create(text_mem, "默认回答简短").json()["items"][0]
    card = candidate(text_mem, target=existing)
    monkeypatch.setattr(
        text_conflicts,
        "get_llm_provider",
        lambda: pytest.fail("model cannot authorize replacement"),
    )
    client, headers = text_mem[:2]
    response = client.post(
        f"/api/v1/memory/text-suggestions/{card['id']}/accept",
        headers=headers[0],
        json={
            "version": card["version"],
            "items": [
                {"content": "学习目标是通过考试", "course_id": existing["course_id"]}
            ],
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 409
    assert "明确确认替换" in response.json()["detail"]
    with Session(text_mem[-2]) as db:
        assert db.get(MemoryItem, existing["id"]).content == existing["content"]
