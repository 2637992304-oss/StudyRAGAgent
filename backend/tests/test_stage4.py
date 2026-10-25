"""Stage 4 deterministic graph, provider protocol, attachments and isolation tests."""

import io
import json
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import delete
from sqlalchemy.orm import Session
from test_stage2 import events, library  # noqa: F401

from app.agent import resolve_scope
from app.api import chat as chat_api
from app.db.models import Attachment, Message
from app.providers.llm import Delta, complete_turn
from app.providers.vision import inspect_image


@pytest.fixture
def stage4(library, tmp_path, monkeypatch):  # noqa: F811
    client, headers, courses, docs, users, engine = library
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    session = client.post(
        "/api/v1/chat/sessions", json={"course_id": courses[0]}, headers=headers[0]
    ).json()["id"]
    yield client, headers, courses, docs, users, engine, session
    with Session(engine) as db:
        db.execute(delete(Attachment))
        db.commit()


def picture(fmt="PNG"):
    data = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(data, format=fmt)
    return data.getvalue()


class Scripted:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.inputs = []

    def stream(self, messages, tools=None, **kwargs):
        self.inputs.append(messages)
        yield from next(self.turns)


def call(query="事实", name="search_course_materials", id="t1", **extra):
    return Delta(
        tool_calls=[
            {
                "index": 0,
                "id": id,
                "function": {
                    "name": name,
                    "arguments": json.dumps({"query": query, **extra}),
                },
            }
        ],
        finish_reason="tool_calls",
    )


def ask(stage4, content, provider, monkeypatch, **body):
    client, headers, *_, session = stage4
    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: provider)
    response = client.post(
        f"/api/v1/chat/sessions/{session}/messages",
        json={"content": content, **body},
        headers=headers[0],
    )
    assert response.status_code == 200
    return events(response), client.get(
        f"/api/v1/chat/sessions/{session}/messages", headers=headers[0]
    ).json()


def test_complete_turn_aggregates_multiple_calls_and_hides_reasoning():
    provider = Scripted(
        [
            [
                Delta(reasoning_content="internal"),
                Delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "a",
                            "function": {
                                "name": "search_course_",
                                "arguments": '{"que',
                            },
                        },
                        {
                            "index": 1,
                            "id": "b",
                            "function": {
                                "name": "search_course_materials",
                                "arguments": '{"query":"B"}',
                            },
                        },
                    ]
                ),
                Delta(
                    tool_calls=[
                        {
                            "index": 0,
                            "function": {"name": "materials", "arguments": 'ry":"A"}'},
                        }
                    ]
                ),
                Delta(finish_reason="tool_calls"),
            ]
        ]
    )
    turn = complete_turn(provider, [], [])
    assert turn.message["tool_calls"][0]["function"]["arguments"] == '{"query":"A"}'
    assert len(turn.message["tool_calls"]) == 2
    assert turn.message["content"] == ""
    assert turn.message["reasoning_content"] == "internal"


@pytest.mark.parametrize("finish", [None, "length", "content_filter"])
def test_incomplete_turn_never_executes(finish):
    with pytest.raises(RuntimeError):
        complete_turn(Scripted([[Delta(text="partial", finish_reason=finish)]]), [])


@pytest.mark.parametrize(
    "fmt,suffix", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")]
)
def test_image_actual_format(fmt, suffix):
    mime, w, h = inspect_image(picture(fmt), suffix)
    assert mime.startswith("image/") and (w, h) == (100, 80)
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        inspect_image(b"not an image", suffix)
    with pytest.raises(HTTPException):
        inspect_image(picture("PNG"), ".jpg")


def test_direct_answer_without_rag(stage4, monkeypatch):
    def forbidden(*args):
        raise AssertionError("direct answer must not retrieve")

    monkeypatch.setattr(chat_api, "retrieve", forbidden)
    parsed, history = ask(
        stage4, "你好", Scripted([[Delta("你好！", finish_reason="stop")]]), monkeypatch
    )
    assert history[-1]["content"] == "你好！"
    assert history[-1]["stop_reason"] == "answer"
    assert history[-1]["citations"] == []


def test_strict_cannot_bypass_evidence(stage4, monkeypatch):
    _, history = ask(
        stage4,
        "严格根据课程资料回答",
        Scripted([[Delta("我凭记忆猜测课程结论。", finish_reason="stop")]]),
        monkeypatch,
    )
    assert history[-1]["stop_reason"] == "no_evidence"
    assert "猜测" not in history[-1]["content"]


@pytest.mark.parametrize(
    "bad", [{"name": "unknown"}, {"owner_id": "foreign"}, {"query": ""}]
)
def test_tool_argument_rejection(stage4, monkeypatch, bad):
    provider = Scripted(
        [[call(**bad)], [Delta("【限制】没有可用信息", finish_reason="stop")]]
    )
    _, history = ask(stage4, "请查询课程事实", provider, monkeypatch)
    assert history[-1]["agent_trace"][0]["status"] == "rejected"


def test_empty_retry_then_clarify(stage4, monkeypatch):
    monkeypatch.setattr(chat_api, "retrieve", lambda *args: ([], []))
    provider = Scripted(
        [
            [call("A")],
            [call("B", id="t2")],
            [Delta("unreachable", finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "查询缺失资料", provider, monkeypatch)
    assert history[-1]["stop_reason"] == "limit"
    assert len(history[-1]["agent_trace"]) == 2


def test_duplicate_does_not_execute_again(stage4, monkeypatch):
    seen = []
    monkeypatch.setattr(
        chat_api, "retrieve", lambda *args: (seen.append(args[-1]) or ([], []))
    )
    _, history = ask(
        stage4, "查一下", Scripted([[call("A")], [call("A", id="t2")]]), monkeypatch
    )
    assert seen == ["A"]
    assert history[-1]["agent_trace"][1]["status"] == "rejected"
    assert history[-1]["stop_reason"] == "limit"


def test_retrieval_failure_and_provider_failure(stage4, monkeypatch):
    def broken(*args):
        raise TimeoutError("private data")

    monkeypatch.setattr(chat_api, "retrieve", broken)
    _, history = ask(
        stage4,
        "查课件",
        Scripted([[call()], [Delta("【限制】检索失败", finish_reason="stop")]]),
        monkeypatch,
    )
    assert history[-1]["agent_trace"][0]["status"] == "timeout"
    provider = Scripted([[Delta("uncommitted", finish_reason=None)]])
    parsed, history = ask(stage4, "再试一次", provider, monkeypatch)
    assert parsed[-1][0] == "error"
    assert history[-1]["status"] == "failed" and history[-1]["content"] == ""


def test_invalid_citation_drops_entire_claim(stage4, monkeypatch):
    _, history = ask(
        stage4,
        "课程结论",
        Scripted([[Delta("虚构事实 [99]", finish_reason="stop")]]),
        monkeypatch,
    )
    assert history[-1]["stop_reason"] == "invalid_citation"
    assert "虚构事实" not in history[-1]["content"]


def test_ai_rejected_claim_not_retained(stage4, monkeypatch):
    provider = Scripted(
        [
            [call()],
            [Delta("未经支持的结论 [1]", finish_reason="stop")],
            [Delta('{"supported":false}', finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "查询资料", provider, monkeypatch)
    assert history[-1]["stop_reason"] == "unsupported"
    assert "未经支持" not in history[-1]["content"]


def test_attachment_history_scope_and_remove(stage4, monkeypatch):
    client, (a, b), courses, docs, users, engine, sid = stage4
    uploaded = client.post(
        f"/api/v1/chat/sessions/{sid}/attachments",
        files={"file": ("q.png", picture(), "image/png")},
        headers=a,
    )
    assert uploaded.status_code == 201
    aid = uploaded.json()["id"]
    assert (
        client.get(f"/api/v1/chat/attachments/{aid}/file", headers=b).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/chat/attachments/{aid}/save", json={}, headers=b
        ).status_code
        == 404
    )
    other = client.post(
        "/api/v1/chat/sessions", json={"course_id": courses[1]}, headers=a
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/chat/sessions/{other}/messages",
            json={"content": "x", "attachment_ids": [aid]},
            headers=a,
        ).status_code
        == 404
    )
    provider = Scripted([[Delta("【需补充】请说明要做什么。", finish_reason="stop")]])
    _, history = ask(stage4, "", provider, monkeypatch, attachment_ids=[aid])
    assert history[0]["attachments"][0]["id"] == aid
    assert any(
        p.get("type") == "image_url"
        for m in provider.inputs[0]
        if m["role"] == "user"
        for p in m["content"]
    )
    follow = Scripted(
        [
            [Delta("这是之前的图片。", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    _, follow_history = ask(stage4, "这张图呢", follow, monkeypatch)
    assert follow_history[-1]["content"] == "这是之前的图片。"
    assert any(
        p.get("type") == "image_url"
        for m in follow.inputs[0]
        if m["role"] == "user"
        for p in m["content"]
    )
    assert (
        client.get(f"/api/v1/courses/{courses[0]}/documents", headers=a).json()[0]["id"]
        in docs
    )
    assert (
        client.delete(f"/api/v1/chat/attachments/{aid}", headers=a).status_code == 204
    )
    assert (
        client.get(f"/api/v1/chat/attachments/{aid}/file", headers=a).status_code == 404
    )


def test_save_copy_and_session_delete(stage4, monkeypatch):
    client, (a, _), courses, _, _, engine, sid = stage4
    from app.workers.tasks import ingest_task

    monkeypatch.setattr(ingest_task, "delay", lambda *_: None)
    uploaded = client.post(
        f"/api/v1/chat/sessions/{sid}/attachments",
        files={"file": ("q.png", picture(), "image/png")},
        headers=a,
    ).json()
    aid = uploaded["id"]
    saved = client.post(
        f"/api/v1/chat/attachments/{aid}/save", json={}, headers=a
    ).json()
    assert (
        client.post(f"/api/v1/chat/attachments/{aid}/save", json={}, headers=a).json()[
            "document_id"
        ]
        == saved["document_id"]
    )
    assert client.delete(f"/api/v1/chat/sessions/{sid}", headers=a).status_code == 204
    assert (
        client.get(f"/api/v1/documents/{saved['document_id']}/file", headers=a).content
        == picture()
    )
    assert (
        client.delete(
            f"/api/v1/documents/{saved['document_id']}", headers=a
        ).status_code
        == 204
    )


def test_scope_intent():
    assert resolve_scope("严格根据我的课件解释", None) == "strict"
    assert resolve_scope("再详细一点", None, "strict") == "strict"
    assert resolve_scope("允许使用通用知识", None, "strict") == "auto"


def test_multiple_calls_one_turn(stage4, monkeypatch):
    first = call("A")
    second = call("B", id="t2")
    second.tool_calls[0]["index"] = 1
    combined = Delta(
        tool_calls=first.tool_calls + second.tool_calls, finish_reason="tool_calls"
    )
    provider = Scripted(
        [
            [combined],
            [Delta("根据课程事实 [1]", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "请核对两个事实", provider, monkeypatch)
    assert len([t for t in history[-1]["agent_trace"] if t.get("tool")]) == 2
    assert len(history[-1]["citations"]) == 1


def test_delete_message_cleans_files(stage4, monkeypatch):
    client, (a, b), *_, sid = stage4
    item = client.post(
        f"/api/v1/chat/sessions/{sid}/attachments",
        files={"file": ("x.png", picture(), "image/png")},
        headers=a,
    ).json()
    _, history = ask(
        stage4,
        "看这张图",
        Scripted([[Delta("【需补充】请说明要求", finish_reason="stop")]]),
        monkeypatch,
        attachment_ids=[item["id"]],
    )
    mid = history[0]["id"]
    assert (
        client.delete(
            f"/api/v1/chat/sessions/{sid}/messages/{mid}", headers=b
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/chat/sessions/{sid}/messages/{mid}", headers=a
        ).status_code
        == 204
    )
    assert (
        client.get(f"/api/v1/chat/attachments/{item['id']}/file", headers=a).status_code
        == 404
    )


def test_round_limit_and_stale_admission(stage4, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.core.config import get_settings

    client, headers, _, _, users, engine, sid = stage4
    with Session(engine) as db:
        db.add(
            Message(
                id=str(uuid4()),
                owner_id=users[0],
                session_id=sid,
                role="assistant",
                content="",
                status="pending",
                created_at=datetime.now(UTC) - timedelta(minutes=16),
            )
        )
        db.commit()
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    _, history = ask(stage4, "查课程", Scripted([[call()]]), monkeypatch)
    assert history[0]["stop_reason"] == "interrupted"
    assert history[-1]["stop_reason"] == "limit"


def test_tool_timeout_ignores_late_result(stage4, monkeypatch):
    import time

    from app.core.config import get_settings

    def slow(*args):
        time.sleep(0.08)
        return [], []

    monkeypatch.setattr(get_settings(), "retrieval_timeout", 0.01)
    monkeypatch.setattr(chat_api, "retrieve", slow)
    _, history = ask(
        stage4,
        "查一下",
        Scripted([[call()], [Delta("【限制】检索超时", finish_reason="stop")]]),
        monkeypatch,
    )
    assert history[-1]["agent_trace"][0]["status"] == "timeout"
    assert history[-1]["citations"] == []


def test_disconnect_closes_pending_state(stage4, monkeypatch):
    import time

    client, headers, _, _, users, engine, sid = stage4
    mid = str(uuid4())
    with Session(engine) as db:
        db.add(
            Message(
                id=mid,
                owner_id=users[0],
                session_id=sid,
                role="assistant",
                content="",
                status="pending",
            )
        )
        db.commit()
    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: Scripted([[call()]]))
    iterator = chat_api.stream_agent(
        users[0], sid, mid, "查资料", "auto", time.monotonic()
    )
    assert "tool_status" in next(iterator)
    iterator.close()
    with Session(engine) as db:
        message = db.get(Message, mid)
        assert message.status == "failed" and message.stop_reason == "cancelled"


def test_reused_call_id_rejected_even_for_new_query(stage4, monkeypatch):
    seen = []
    monkeypatch.setattr(
        chat_api, "retrieve", lambda *args: (seen.append(args[-1]) or ([], []))
    )
    _, history = ask(
        stage4,
        "查询资料",
        Scripted([[call("A", id="same")], [call("B", id="same")]]),
        monkeypatch,
    )
    assert seen == ["A"]
    assert history[-1]["agent_trace"][1]["status"] == "rejected"


def test_code_indices_are_not_citations(stage4, monkeypatch):
    text = "Python中，`items[0]`返回首项。\n```python\nx = [1]\n```"
    _, history = ask(
        stage4,
        "解释列表索引",
        Scripted([[Delta(text, finish_reason="stop")]]),
        monkeypatch,
    )
    assert history[-1]["content"] == text
    assert history[-1]["citations"] == []
