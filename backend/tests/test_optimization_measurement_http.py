# ruff: noqa: F811 -- shared isolated fixtures
import time

import pytest
from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401
from test_stage4 import Scripted, ask, call, stage4  # noqa: F401

from app.api import chat as chat_api
from app.db.models import Message
from app.providers.llm import Delta


@pytest.mark.parametrize("slow", ["retrieval", "agent", "review", "persistence"])
def test_current_http_measurements_include_all_calls_and_slow_stage(
    stage4, monkeypatch, slow
):
    original = chat_api.retrieve

    def retrieve(*args, **kwargs):
        if slow == "retrieval":
            time.sleep(0.04)
        return original(*args, **kwargs)

    monkeypatch.setattr(chat_api, "retrieve", retrieve)
    commit = Session.commit

    def slow_commit(self):
        if slow == "persistence":
            time.sleep(0.04)
        return commit(self)

    monkeypatch.setattr(Session, "commit", slow_commit)

    class Timed(Scripted):
        def stream(self, *args, **kwargs):
            if kwargs["options"].purpose == (
                "agent"
                if slow == "agent"
                else "citation_check"
                if slow == "review"
                else "unused"
            ):
                time.sleep(0.04)
            yield from super().stream(*args, **kwargs)

    provider = Timed(
        [
            [call("事实"), Delta(input_tokens=3, output_tokens=1)],
            [Delta("事实 [1]", finish_reason="stop", input_tokens=3, output_tokens=1)],
            [
                Delta(
                    '{"supported":true}',
                    finish_reason="stop",
                    input_tokens=3,
                    output_tokens=1,
                )
            ],
        ]
    )
    events, history = ask(stage4, "严格根据资料解释事实", provider, monkeypatch)
    final = history[-1]
    measured = final["measurements"]
    assert measured["logical_calls"] == 3
    assert measured["http_attempts"] == 0
    assert measured["tool_calls"] == 1
    assert measured["usage"]["state"] == "complete"
    assert final["input_tokens"] == 9
    assert final["output_tokens"] == 3
    assert [c["purpose"] for c in measured["calls"]] == ["agent", "agent", "review"]
    assert max(x["elapsed_ms"] for x in measured["stages"] if x["stage"] == slow) >= 35
    assert measured["server_first_body_ms"] >= measured["model_first_output_ms"]
    assert next(
        i for i, (name, _) in enumerate(events) if name == "answer_delta"
    ) > next(i for i, (name, _) in enumerate(events) if name == "tool_status")


def test_old_messages_remain_explicitly_unknown(stage4, monkeypatch):
    client, headers, *_, engine, session = stage4
    with Session(engine) as db:
        chat = db.get(
            __import__("app.db.models", fromlist=["ChatSession"]).ChatSession, session
        )
        db.add(
            Message(
                owner_id=chat.owner_id,
                session_id=session,
                role="assistant",
                content="旧答案",
                input_tokens=0,
                output_tokens=0,
            )
        )
        db.commit()
    message = client.get(
        f"/api/v1/chat/sessions/{session}/messages", headers=headers[0]
    ).json()[-1]
    assert message["measurement_status"] == "legacy_unknown"
    assert message["measurements"] is None


def test_repair_and_recheck_are_counted_once_each(stage4, monkeypatch):
    provider = Scripted(
        [
            [call("事实")],
            [Delta("初稿事实 [1]", finish_reason="stop")],
            [Delta('{"supported":false}', finish_reason="stop")],
            [Delta("修正后的资料事实 [1]", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "严格根据资料解释事实", provider, monkeypatch)
    metrics = history[-1]["measurements"]
    assert [c["purpose"] for c in metrics["calls"]] == [
        "agent",
        "agent",
        "review",
        "repair",
        "recheck",
    ]
    assert metrics["logical_calls"] == 5
    assert metrics["usage"]["state"] == "missing"
    assert metrics["usage"]["input_tokens"] is None
