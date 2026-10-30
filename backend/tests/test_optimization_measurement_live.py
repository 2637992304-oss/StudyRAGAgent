"""Opt-in real PostgreSQL HTTP/pgvector checks; no external model calls."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import chat as chat_api
from app.core.auth import create_token, hash_password
from app.core.config import get_settings
from app.db.models import ChatSession, Chunk, Course, Document, Message, User
from app.db.session import get_db, get_engine
from app.evaluation.runtime import observer
from app.main import app

pytestmark = pytest.mark.skipif(
    os.environ.get("OPT34_LIVE") != "1",
    reason="explicit opt34 disposable services required",
)


@pytest.fixture
def disposable():
    identity = os.environ["OPT34_RUN_ID"]
    prefix = os.environ["OPT34_CONTAINER_PREFIX"]
    assert prefix == "studyrag-opt34-" + identity[:12]
    raw = json.loads(
        subprocess.check_output(["docker", "inspect", prefix + "-pg"], text=True)
    )[0]
    assert raw["Config"]["Labels"]["studyrag.opt34"] == identity
    engine = get_engine()
    assert engine.url.host == "127.0.0.1" and engine.url.username == "opt34"
    assert (
        str(engine.url.port)
        == raw["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
    )
    yield engine


@pytest.mark.parametrize("mode", ["off", "on", "source_deleted"])
def test_current_http_real_pgvector_measurements_and_adjacent_snapshots(
    disposable, monkeypatch, mode
):
    engine = disposable
    settings = get_settings()
    for key, value in dict(
        llm_base_url="https://example.test/v1",
        llm_api_key="fixture-only",
        llm_model="opt34-fixture",
        retrieval_strategy="dense",
        retrieval_top_k=1,
        retrieval_adjacent_context=mode != "off",
        retrieval_pair_headings=False,
        retrieval_verified_tables=False,
    ).items():
        monkeypatch.setattr(settings, key, value)
    owner, course, document, session, seed, neighbor = [str(uuid4()) for _ in range(6)]
    with Session(engine) as db:
        db.add(
            User(
                id=owner,
                email=owner + "@example.test",
                password_hash=hash_password("opt34-disposable-only"),
            )
        )
        db.flush()
        db.add(Course(id=course, owner_id=owner, name="自制资料"))
        db.flush()
        db.add(
            Document(
                id=document,
                owner_id=owner,
                course_id=course,
                filename="self-authored.md",
                document_type="md",
                mime_type="text/markdown",
                storage_key=str(uuid4()),
                sha256="a" * 64,
                size_bytes=80,
                status="ready",
            )
        )
        db.add(
            ChatSession(
                id=session, owner_id=owner, course_id=course, document_ids=[document]
            )
        )
        db.flush()
        for index, (key, body, vector) in enumerate(
            [
                (seed, "青石规则：有效期17秒。", [1.0] + [0.0] * 511),
                (neighbor, "仅校验码相等时允许复用。", [0.0, 1.0] + [0.0] * 510),
            ]
        ):
            db.add(
                Chunk(
                    id=key,
                    owner_id=owner,
                    course_id=course,
                    document_id=document,
                    chunk_index=index,
                    text=body,
                    content_type="body",
                    section_path="青石规则",
                    content_hash="b" * 64,
                    embedding_model=settings.embedding_model,
                    parser_version="opt34-test",
                    embedding=vector,
                )
            )
        db.commit()

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(chat_api, "get_engine", lambda: engine)
    monkeypatch.setattr(
        chat_api, "embed_texts", lambda texts: [[1.0] + [0.0] * 511 for _ in texts]
    )
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "source-query",
                        "function": {
                            "name": "search_course_materials",
                            "arguments": json.dumps({"query": "青石规则"}),
                        },
                    }
                ]
            }
            finish = "tool_calls"
        elif len(requests) == 2:
            delta = {
                "content": "有效期17秒 [1]。"
                + ("校验码相等才可复用 [2]。" if mode != "off" else "")
            }
            finish = "stop"
        else:
            if mode == "source_deleted":
                with Session(engine) as db:
                    db.get(Document, document).status = "deleting"
                    db.commit()
            delta = {"content": '{"supported":true}'}
            finish = "stop"
        events = [
            {"choices": [{"delta": delta, "finish_reason": finish}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        ]
        return httpx.Response(
            200,
            text="".join(
                "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                for event in events
            )
            + "data: [DONE]\n\n",
        )

    client_class = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs),
    )
    captured = []
    token = observer.set(captured.append)
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + create_token(owner)}
            response = client.post(
                f"/api/v1/chat/sessions/{session}/messages",
                headers=headers,
                json={
                    "content": "严格根据资料解释青石规则",
                    "answer_scope": "strict",
                    "web_enabled": False,
                },
            )
            assert response.status_code == 200
            history = client.get(
                f"/api/v1/chat/sessions/{session}/messages", headers=headers
            ).json()
        final = history[-1]
        assert final["measurements"]["logical_calls"] == 3
        assert final["measurements"]["http_attempts"] == len(requests) == 3
        assert final["input_tokens"] == 15 and final["output_tokens"] == 6
        assert final["measurements"]["usage"]["state"] == "complete"
        sources = [
            s for e in captured if e["kind"] == "course_sources" for s in e["sources"]
        ]
        assert {s["chunk_id"] for s in sources} == (
            {seed} if mode == "off" else {seed, neighbor}
        )
        tool_message = next(m for m in requests[1]["messages"] if m["role"] == "tool")
        actual_sources = json.loads(tool_message["content"])["sources"]
        assert actual_sources == sources
        assert [e["purpose"] for e in captured if e["kind"] == "model_input"] == [
            "agent",
            "agent",
            "citation_check",
        ]
        if mode == "source_deleted":
            assert final["stop_reason"] == "expired_evidence" and not final["citations"]
        else:
            assert len(final["citations"]) == (1 if mode == "off" else 2)
            metrics = final["measurements"]
            assert metrics["server_first_body_ms"] >= metrics["model_first_output_ms"]
            assert any(s["stage"] == "persistence" for s in metrics["stages"])
    finally:
        observer.reset(token)
        app.dependency_overrides.pop(get_db, None)


def test_real_postgres_0009_roundtrip_keeps_old_messages(disposable):
    engine = disposable
    owner, course, session, message = [str(uuid4()) for _ in range(4)]
    with Session(engine) as db:
        db.add(
            User(
                id=owner,
                email=owner + "@example.test",
                password_hash=hash_password("opt34-disposable-only"),
            )
        )
        db.flush()
        db.add(Course(id=course, owner_id=owner, name="迁移样本"))
        db.flush()
        db.add(ChatSession(id=session, owner_id=owner, course_id=course))
        db.flush()
        db.add(
            Message(
                id=message,
                owner_id=owner,
                session_id=session,
                role="assistant",
                content="旧版自制回答",
                input_tokens=0,
                output_tokens=0,
            )
        )
        db.commit()
    backend = Path(__file__).resolve().parents[1]
    for target in ("0008_ingestion_recovery", "head"):
        action = "downgrade" if target == "0008_ingestion_recovery" else "upgrade"
        subprocess.run(
            [sys.executable, "-m", "alembic", action, target],
            cwd=backend,
            check=True,
            capture_output=True,
        )
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT content,input_tokens FROM messages WHERE id=:id"),
                {"id": message},
            ).one() == ("旧版自制回答", 0)
    with Session(engine) as db:
        assert db.get(Message, message).measurements is None
    subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=backend,
        check=True,
        capture_output=True,
    )
