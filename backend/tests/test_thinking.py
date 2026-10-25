"""Addendum checks: no provider network; controllable clocks and transports."""
# ruff: noqa: F811

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401
from test_stage4 import Scripted, ask, call, picture, stage4  # noqa: F401

from app.api import chat as chat_api
from app.core.config import get_settings
from app.db.models import Message
from app.providers import llm
from app.providers.call_options import (
    Execution,
    ExecutionStopped,
    IncompleteResponse,
    bounded_call,
    profile,
)
from app.providers.llm import Delta, complete_turn


class Recording(Scripted):
    def __init__(self, turns):
        super().__init__(turns)
        self.options = []

    def stream(self, messages, tools=None, **kwargs):
        self.options.append(kwargs["options"])
        yield from super().stream(messages, tools, **kwargs)


@pytest.mark.parametrize("value", ["true", "false", "1", 1, 0, None, [], {}])
def test_strict_thinking_rejects_coercion(value):
    with pytest.raises(ValidationError):
        chat_api.MessageInput(thinking=value)


@pytest.mark.parametrize(
    "body,expected",
    [({}, False), ({"thinking": False}, False), ({"thinking": True}, True)],
)
def test_request_selection_and_private_history(stage4, monkeypatch, body, expected):
    provider = Recording(
        [[Delta("OK", reasoning_content="private-state", finish_reason="stop")]]
    )
    stream, history = ask(stage4, "hi", provider, monkeypatch, **body)
    assert all(m["thinking"] is expected for m in history)
    assert "private-state" not in json.dumps([stream, history])
    assert "protocol_state" not in json.dumps(history)
    assert provider.options == [profile("agent", expected)]
    with Session(stage4[-2]) as db:
        answer = db.get(Message, history[-1]["id"])
        assert answer.reply_to_id == history[-2]["id"]
        assert (
            answer.protocol_state["messages"][-1]["reasoning_content"]
            == "private-state"
        )
        assert answer.call_config["agent"] == profile("agent", expected).snapshot()


def test_profiles_are_frozen_and_independent():
    with pytest.raises(FrozenInstanceError):
        profile("agent", True).thinking = False
    assert profile("agent", True).max_tokens == 8192
    assert profile("agent").max_tokens == 2048
    assert profile("transcription").thinking is False
    assert profile("citation_check").timeout == 60


def test_wire_profiles_explicit_and_sampling(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_base_url", "https://example.test")
    monkeypatch.setattr(settings, "llm_api_key", "test")
    monkeypatch.setattr(settings, "llm_model", "test")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"OK"},'
            '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
        )

    original = httpx.Client
    monkeypatch.setattr(
        llm.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    for p in [
        profile("agent", True),
        profile("agent"),
        profile("transcription"),
        profile("citation_check"),
    ]:
        complete_turn(
            llm.get_llm_provider(), [{"role": "user", "content": "Q"}], options=p
        )
    assert [p["thinking"]["type"] for p in sent] == [
        "enabled",
        "disabled",
        "disabled",
        "disabled",
    ]
    assert [p["max_tokens"] for p in sent] == [8192, 2048, 4096, 1024]
    assert sent[0]["reasoning_effort"] == "high" and "temperature" not in sent[0]
    assert all("reasoning_effort" not in p for p in sent[1:])


def test_concurrent_request_options_do_not_bleed():
    barrier = threading.Barrier(2)
    records = []

    class Concurrent:
        def stream(self, messages, tools=None, **kwargs):
            barrier.wait(timeout=2)
            records.append((messages[0]["content"], kwargs["options"]))
            yield Delta("done", finish_reason="stop")

    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(
                complete_turn,
                Concurrent(),
                [{"role": "user", "content": str(t)}],
                options=profile("agent", t),
            )
            for t in [True, False]
        ]
        for f in futures:
            assert f.result(timeout=3).message["content"] == "done"
    assert dict(records) == {
        "True": profile("agent", True),
        "False": profile("agent", False),
    }


@pytest.mark.parametrize(
    "stop,expected",
    [("step", "step_timeout"), ("round", "deadline_exceeded"), ("cancel", "cancelled")],
)
def test_silent_transport_stops_with_controlled_clock(stop, expected):
    now = [0.0]
    entered, release = threading.Event(), threading.Event()
    execution = Execution(50, clock=lambda: now[0])

    def work():
        entered.set()
        release.wait(2)
        return "late result"

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(bounded_call, work, execution, 10)
        assert entered.wait(1)
        if stop == "cancel":
            execution.cancelled.set()
        else:
            now[0] = 60 if stop == "round" else 11
        try:
            with pytest.raises(ExecutionStopped) as exc:
                future.result(timeout=1)
            assert exc.value.code == expected
        finally:
            release.set()


def test_no_new_call_after_deadline_or_cancel():
    for execution in [Execution(0), Execution.start(10)]:
        execution.cancelled.set()
        with pytest.raises(ExecutionStopped):
            bounded_call(lambda: pytest.fail("must not execute"), execution, 2)


def test_citation_check_off_after_all_thinking_tool_rounds(stage4, monkeypatch):
    provider = Recording(
        [
            [call("A", id="a"), Delta(reasoning_content="real-a")],
            [call("B", id="b"), Delta(reasoning_content="real-b")],
            [
                Delta(
                    "课程事实 [1]", reasoning_content="real-final", finish_reason="stop"
                )
            ],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "严格根据资料查A和B", provider, monkeypatch, thinking=True)
    assert history[-1]["status"] == "completed"
    assert provider.options[:3] == [profile("agent", True)] * 3
    assert provider.options[3] == profile("citation_check")
    assert provider.inputs[1][-2]["reasoning_content"] == "real-a"
    assert provider.inputs[2][-2]["reasoning_content"] == "real-b"
    assert len(history[-1]["citations"]) == 1


def test_transcription_does_not_inherit_chat_choice(monkeypatch):
    from app.providers.vision import describe_image

    provider = Recording([[Delta("2x + 5 = 17", finish_reason="stop")]])
    monkeypatch.setattr(llm, "get_llm_provider", lambda: provider)
    assert describe_image("sample.png", picture()) == "2x + 5 = 17"
    assert provider.options == [profile("transcription")]


def test_reload_replays_real_protocol_and_mode_switch(stage4, monkeypatch):
    provider = Recording(
        [
            [call()],
            [Delta("资料事实 [1]", reasoning_content="saved", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "检索", provider, monkeypatch, thinking=True)
    for thinking in [True, False, True]:
        followup = Recording(
            [
                [
                    Delta(
                        "继续回答",
                        reasoning_content="new" if thinking else "",
                        finish_reason="stop",
                    )
                ]
            ]
        )
        ask(stage4, "继续解释", followup, monkeypatch, thinking=thinking)
        actual = followup.inputs[0]
        assert any(m.get("reasoning_content") == "saved" for m in actual)
        assert any(m["role"] == "tool" for m in actual)
        assert followup.options == [profile("agent", thinking)]
    client, headers, *_, sid = stage4
    assert (
        client.get(
            f"/api/v1/chat/sessions/{sid}/messages", headers=headers[1]
        ).status_code
        == 404
    )
    # Deleting the message also deletes its only private transcript copy.
    assert (
        client.delete(
            f"/api/v1/chat/sessions/{sid}/messages/{history[-1]['id']}",
            headers=headers[0],
        ).status_code
        == 204
    )
    with Session(stage4[-2]) as db:
        assert db.get(Message, history[-1]["id"]) is None


def test_legacy_history_is_reference_not_fabricated_reasoning(stage4, monkeypatch):
    with Session(stage4[-2]) as db:
        db.add(
            Message(
                owner_id=stage4[4][0],
                session_id=stage4[-1],
                role="assistant",
                content="旧回答：x=6",
            )
        )
        db.commit()
    provider = Recording([[Delta("x=6", finish_reason="stop")]])
    _, history = ask(stage4, "继续", provider, monkeypatch, thinking=True)
    assert history[0]["thinking"] is None
    assert any(
        m["role"] == "user" and "旧回答" in str(m["content"])
        for m in provider.inputs[0]
    )
    assert not any(m.get("reasoning_content") == "" for m in provider.inputs[0])


def test_cancel_marks_database_and_discards_late_answer(stage4, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Slow:
        def stream(self, messages, tools=None, **kwargs):
            entered.set()
            release.wait(2)
            yield Delta("late answer", finish_reason="stop")

    monkeypatch.setattr(chat_api, "get_llm_provider", Slow)
    client, headers, *_, sid = stage4
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(
            client.post,
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"content": "slow", "thinking": True},
            headers=headers[0],
        )
        assert entered.wait(2)
        assert (
            client.post(
                f"/api/v1/chat/sessions/{sid}/cancel", headers=headers[1]
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/chat/sessions/{sid}/cancel", headers=headers[0]
            ).status_code
            == 204
        )
        response = pending.result(timeout=2)
        release.set()
    assert "late answer" not in response.text
    history = client.get(
        f"/api/v1/chat/sessions/{sid}/messages", headers=headers[0]
    ).json()
    assert history[-1]["stop_reason"] == "cancelled"
    assert history[-1]["content"] == ""


def test_truncation_is_not_completed(stage4, monkeypatch):
    _, history = ask(
        stage4,
        "hi",
        Recording([[Delta("partial", finish_reason="length")]]),
        monkeypatch,
        thinking=True,
    )
    assert history[-1]["stop_reason"] == "truncated"
    assert history[-1]["status"] == "failed" and history[-1]["content"] == ""
    with pytest.raises(IncompleteResponse):
        complete_turn(Scripted([[Delta("partial", finish_reason="length")]]), [])


@pytest.mark.parametrize(
    "kind,code",
    [(httpx.ConnectTimeout, "connect_timeout"), (httpx.ReadTimeout, "read_timeout")],
)
def test_transport_timeout_codes(kind, code):
    class Broken:
        def stream(self, *args, **kwargs):
            raise kind("private")
            yield

    with pytest.raises(ExecutionStopped) as exc:
        complete_turn(Broken(), [], options=profile("agent", True))
    assert exc.value.code == code


def test_retry_backoff_respects_cancel_and_keeps_profile(monkeypatch):
    sent = []
    execution = Execution.start(10)
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_base_url", "https://example.test")
    monkeypatch.setattr(settings, "llm_api_key", "test")
    monkeypatch.setattr(settings, "llm_model", "test")

    def handler(request):
        sent.append(json.loads(request.content))
        execution.cancelled.set()  # cancellation DURING 503 backoff
        return httpx.Response(503)

    original = httpx.Client
    monkeypatch.setattr(
        llm.httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(ExecutionStopped) as exc:
        complete_turn(
            llm.get_llm_provider(),
            [],
            options=profile("agent", True),
            execution=execution,
        )
    assert exc.value.code == "cancelled"
    assert len(sent) == 1 and sent[0]["thinking"] == {"type": "enabled"}


def test_whole_deadline_persisted_with_silent_model(stage4, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    now = [0.0]
    execution = Execution(100, clock=lambda: now[0])

    class Silent:
        def stream(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            yield Delta("late", finish_reason="stop")

    # Provide a controllable clock at the admission boundary, no long sleeps.
    monkeypatch.setattr(chat_api, "Execution", lambda *args: execution)
    monkeypatch.setattr(chat_api, "get_llm_provider", Silent)
    client, headers, *_, sid = stage4
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(
            client.post,
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"content": "wait"},
            headers=headers[0],
        )
        assert entered.wait(2)
        now[0] = 101
        response = pending.result(timeout=2)
        release.set()
    assert "deadline_exceeded" in response.text and "late" not in response.text
    history = client.get(
        f"/api/v1/chat/sessions/{sid}/messages", headers=headers[0]
    ).json()
    assert history[-1]["status"] == "failed"
    assert history[-1]["stop_reason"] == "deadline_exceeded"


def test_client_cannot_select_call_profile(stage4, monkeypatch):
    provider = Recording([[Delta("OK", finish_reason="stop")]])
    ask(
        stage4,
        "hi",
        provider,
        monkeypatch,
        purpose="transcription",
        max_tokens=999999,
        timeout=9999,
    )
    assert provider.options == [profile("agent")]


def test_live_pending_not_expired_by_thinking_timeout(stage4, monkeypatch):
    from datetime import UTC, datetime, timedelta

    client, headers, *_, sid = stage4
    with Session(stage4[-2]) as db:
        db.add(
            Message(
                owner_id=stage4[4][0],
                session_id=sid,
                role="assistant",
                content="",
                status="pending",
                created_at=datetime.now(UTC) - timedelta(seconds=200),
                call_config={"deadline_seconds": 360},
            )
        )
        db.commit()
    response = client.post(
        f"/api/v1/chat/sessions/{sid}/messages",
        json={"content": "new"},
        headers=headers[0],
    )
    assert response.status_code == 409


def test_slow_cross_worker_probe_does_not_block_stream(monkeypatch):
    import asyncio

    entered, release_probe, release_answer = (threading.Event() for _ in range(3))

    def probe(_):
        entered.set()
        release_probe.wait(2)
        return False

    def events():
        release_answer.wait(2)
        yield "done"

    monkeypatch.setattr(chat_api, "_was_cancelled", probe)

    async def run():
        async def read():
            return [
                event
                async for event in chat_api.agent_events(
                    events(), Execution.start(5), "id"
                )
            ]

        task = asyncio.create_task(read())
        assert await asyncio.to_thread(entered.wait, 1)
        release_answer.set()
        try:
            assert await asyncio.wait_for(task, 1) == ["done"]
        finally:
            release_probe.set()

    asyncio.run(run())
