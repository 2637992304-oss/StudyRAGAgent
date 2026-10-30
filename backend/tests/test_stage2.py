import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import chat as chat_api
from app.core.auth import create_token, hash_password
from app.core.config import get_settings
from app.db.base import Base
from app.db.models import ChatSession, Chunk, Course, Document, Message, User
from app.db.session import get_db
from app.main import app
from app.providers.llm import Delta


def events(response):
    return [
        (block.split("\n")[0][7:], json.loads(block.split("data: ", 1)[1]))
        for block in response.text.strip().split("\n\n")
    ]


@pytest.fixture
def library(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(chat_api, "get_engine", lambda: engine)

    def db_override():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = db_override

    def scoped_retrieve(db, chat, _query):
        statement = (
            select(Chunk, Document)
            .join(Document, Chunk.document_id == Document.id)
            .where(
                Chunk.owner_id == chat.owner_id,
                Chunk.course_id == chat.course_id,
                Document.owner_id == chat.owner_id,
                Document.course_id == chat.course_id,
                Document.status == "ready",
            )
        )
        if chat.document_ids is not None:
            statement = statement.where(Document.id.in_(chat.document_ids))
        rows = db.execute(statement).all()
        return rows, [chunk.id for chunk, _ in rows]

    monkeypatch.setattr(chat_api, "retrieve", scoped_retrieve)
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(chat_api, "embed_texts", lambda _: [[1.0] + [0.0] * 511])
    users = [str(uuid4()), str(uuid4())]
    courses = [str(uuid4()) for _ in range(3)]
    docs = [str(uuid4()) for _ in range(4)]
    with Session(engine) as db, db.begin():
        for user_id in users:
            db.add(
                User(
                    id=user_id,
                    email=f"{user_id}@example.test",
                    password_hash=hash_password("test"),
                )
            )
        db.flush()
        for course_id, owner in zip(
            courses, [users[0], users[0], users[1]], strict=True
        ):
            db.add(Course(id=course_id, owner_id=owner, name="测试课程"))
        db.flush()
        for doc_id, course_id, owner, status in zip(
            docs,
            [courses[0], courses[0], courses[1], courses[2]],
            [users[0], users[0], users[0], users[1]],
            ["ready", "failed", "ready", "ready"],
            strict=True,
        ):
            db.add(
                Document(
                    id=doc_id,
                    course_id=course_id,
                    owner_id=owner,
                    filename=f"{doc_id}.md",
                    document_type="笔记",
                    mime_type="text/markdown",
                    storage_key=str(uuid4()),
                    sha256="0" * 64,
                    size_bytes=10,
                    status=status,
                )
            )
        db.flush()
        for index, (doc_id, course_id, owner) in enumerate(
            zip(
                docs,
                [courses[0], courses[0], courses[1], courses[2]],
                [users[0], users[0], users[0], users[1]],
                strict=True,
            )
        ):
            db.add(
                Chunk(
                    id=str(uuid4()),
                    document_id=doc_id,
                    course_id=course_id,
                    owner_id=owner,
                    chunk_index=0,
                    text=f"资料 {index} 的独有事实",
                    content_type="body",
                    section_path="第一章",
                    page_start=index + 1,
                    content_hash="1" * 64,
                    embedding_model="BAAI/bge-small-zh-v1.5",
                    parser_version="test",
                    embedding=[1.0] + [0.0] * 511,
                )
            )
    client = TestClient(app)
    headers = [
        {"Authorization": f"Bearer {create_token(user_id)}"} for user_id in users
    ]
    try:
        yield client, headers, courses, docs, users, engine
    finally:
        with Session(engine) as db:
            for chat in db.scalars(
                select(ChatSession).where(ChatSession.owner_id.in_(users))
            ).all():
                db.delete(chat)
            db.flush()
            db.execute(delete(Document).where(Document.owner_id.in_(users)))
            db.execute(delete(Course).where(Course.owner_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.commit()
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_scoped_stream_persistence_and_isolation(library, monkeypatch):
    client, (a, b), courses, docs, users, engine = library

    class FakeLLM:
        def stream(self, messages, tools=None, **kwargs):
            if "引用核对器" in messages[0]["content"]:
                yield Delta('{"supported":true}', finish_reason="stop")
            elif messages[-1]["role"] == "tool":
                payload = messages[-1]["content"]
                assert "资料 0 的独有事实" in payload
                assert "资料 1 的独有事实" not in payload
                assert "资料 2 的独有事实" not in payload
                assert "资料 3 的独有事实" not in payload
                yield Delta(
                    "根据资料，事实如下 [1]。",
                    input_tokens=100,
                    output_tokens=12,
                    finish_reason="stop",
                )
            else:
                yield Delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call1",
                            "function": {
                                "name": "search_course_materials",
                                "arguments": '{"query":"独有事实"}',
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                )

    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: FakeLLM())
    assert (
        client.post(
            "/api/v1/chat/sessions", json={"course_id": courses[2]}, headers=a
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/chat/sessions",
            json={"course_id": courses[0], "document_ids": [docs[3]]},
            headers=a,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/chat/sessions",
            json={"course_id": courses[0], "document_ids": [docs[1]]},
            headers=a,
        ).status_code
        == 404
    )
    created = client.post(
        "/api/v1/chat/sessions",
        json={"course_id": courses[0], "document_ids": [docs[0]]},
        headers=a,
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    assert any(
        item["id"] == session_id
        for item in client.get(
            f"/api/v1/chat/sessions?course_id={courses[0]}", headers=a
        ).json()
    )
    assert (
        client.get(
            f"/api/v1/chat/sessions?course_id={courses[0]}", headers=b
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/chat/sessions/{session_id}/messages", headers=b
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"content": "问题"},
            headers=b,
        ).status_code
        == 404
    )
    response = client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        json={"content": "资料中的事实是什么？"},
        headers=a,
    )
    assert response.status_code == 200
    parsed = events(response)
    assert [name for name, _ in parsed if name != "tool_status"] == [
        "answer_delta",
        "citation",
        "done",
    ]
    citation = next(data for name, data in parsed if name == "citation")
    assert citation["document_id"] == docs[0]
    assert citation["page_start"] == 1
    assert citation["snippet"] == "资料 0 的独有事实"
    assert (
        client.get(
            f"/api/v1/citations/{citation['citation_id']}", headers=b
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/citations/{citation['citation_id']}", headers=a).json()[
            "chunk_id"
        ]
        == citation["chunk_id"]
    )
    history = client.get(
        f"/api/v1/chat/sessions/{session_id}/messages", headers=a
    ).json()
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[1]["citations"][0]["chunk_id"] == citation["chunk_id"]
    with Session(engine) as db:
        saved = db.scalar(
            select(Message).where(
                Message.id == history[1]["id"], Message.owner_id == users[0]
            )
        )
        assert saved.candidate_chunk_ids == [citation["chunk_id"]]
        # The review double omits usage: known generation is not a whole-turn total.
        assert saved.input_tokens is None and saved.output_tokens is None
        assert saved.measurements["usage"]["state"] == "partial"
        assert saved.measurements["usage"]["known_input_tokens"] == 100
        assert saved.measurements["usage"]["known_output_tokens"] == 12
        doc = db.get(Document, docs[0])
        doc.status = "deleting"
        db.commit()
    assert (
        client.get(
            f"/api/v1/citations/{citation['citation_id']}", headers=a
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"content": "资料删除后还能回答吗"},
            headers=a,
        ).status_code
        == 404
    )


def test_no_evidence_and_provider_failure(library, monkeypatch, caplog):
    client, (a, _), courses, _, _, _ = library
    created = client.post(
        "/api/v1/chat/sessions", json={"course_id": courses[0]}, headers=a
    ).json()
    monkeypatch.setattr(chat_api, "retrieve", lambda *_: ([], []))

    class NoEvidenceLLM:
        def stream(self, messages, tools=None, **kwargs):
            if messages[-1]["role"] == "tool":
                yield Delta("【限制】当前资料中没有足够依据。", finish_reason="stop")
            else:
                yield Delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "empty1",
                            "function": {
                                "name": "search_course_materials",
                                "arguments": '{"query":"不存在的答案"}',
                            },
                        }
                    ],
                    finish_reason="tool_calls",
                )

    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: NoEvidenceLLM())

    response = client.post(
        f"/api/v1/chat/sessions/{created['id']}/messages",
        json={"content": "不存在的答案"},
        headers=a,
    )
    assert [name for name, _ in events(response) if name != "tool_status"] == [
        "answer_delta",
        "done",
    ]
    assert "没有足够依据" in response.text
    assert (
        client.get(f"/api/v1/chat/sessions/{created['id']}/messages", headers=a).json()[
            -1
        ]["citations"]
        == []
    )

    class BrokenLLM:
        def stream(self, _, tools=None, **kwargs):
            raise TimeoutError("private upstream detail")
            yield Delta("unreachable")

    monkeypatch.setattr(
        chat_api,
        "retrieve",
        lambda *_: ([(Chunk(id="fake", text="x"), Document(id="fake"))], ["fake"]),
    )
    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: BrokenLLM())
    response = client.post(
        f"/api/v1/chat/sessions/{created['id']}/messages",
        json={"content": "会超时吗"},
        headers=a,
    )
    assert [name for name, _ in events(response) if name != "tool_status"] == ["error"]
    assert events(response)[-1][1]["code"] == "llm_timeout"
    assert "模型响应超时" in response.text
    assert "private upstream detail" not in response.text
    assert "private upstream detail" not in caplog.text
    assert "llm_timeout" in caplog.text
    history = client.get(
        f"/api/v1/chat/sessions/{created['id']}/messages", headers=a
    ).json()
    assert history[-1]["status"] == "failed"
