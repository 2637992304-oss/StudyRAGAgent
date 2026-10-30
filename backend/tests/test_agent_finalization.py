# ruff: noqa: F811 -- shared isolated fixtures
from test_stage2 import library  # noqa: F401
from test_stage4 import Scripted, ask, call, stage4  # noqa: F401

from app.core.config import get_settings
from app.providers.llm import Delta


def test_last_tool_has_one_tool_disabled_final_answer_and_review(stage4, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    monkeypatch.setattr(get_settings(), "agent_finalize_enabled", True)

    class Provider(Scripted):
        def stream(self, messages, tools=None, **kwargs):
            if len(self.inputs) == 1:
                assert tools is None
                assert kwargs["options"].allow_tools is False
            yield from super().stream(messages, tools, **kwargs)

    provider = Provider(
        [
            [call("事实")],
            [Delta("资料支持的事实 [1]", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "严格根据资料说明事实", provider, monkeypatch)
    final = history[-1]
    assert final["content"] == "资料支持的事实 [1]"
    assert [c["purpose"] for c in final["measurements"]["calls"]] == [
        "agent",
        "finalize",
        "review",
    ]
    assert len(final["citations"]) == 1


def test_finalization_cannot_execute_another_tool(stage4, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    monkeypatch.setattr(get_settings(), "agent_finalize_enabled", True)
    provider = Scripted([[call("事实")], [call("新的检索", id="forbidden-final-tool")]])
    _, history = ask(stage4, "严格根据资料说明事实", provider, monkeypatch)
    final = history[-1]
    assert len(provider.inputs) == 2
    assert final["measurements"]["tool_calls"] == 1
    assert final["stop_reason"] == "finalize_tool_request"
    assert final["citations"] == []


def test_no_evidence_never_spends_a_finalization_call(stage4, monkeypatch):
    from app.api import chat as chat_api

    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    monkeypatch.setattr(get_settings(), "agent_finalize_enabled", True)
    monkeypatch.setattr(chat_api, "retrieve", lambda *_: ([], []))
    provider = Scripted([[call("未知资料")]])
    _, history = ask(stage4, "严格根据资料说明事实", provider, monkeypatch)
    assert len(provider.inputs) == 1
    assert history[-1]["stop_reason"] == "no_evidence"
    assert history[-1]["citations"] == []


def test_source_revoked_before_finalization_does_not_start_model(stage4, monkeypatch):
    from sqlalchemy.orm import Session

    from app.api import chat as chat_api
    from app.db.models import Document

    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    monkeypatch.setattr(get_settings(), "agent_finalize_enabled", True)
    original = chat_api.retrieve

    def retrieve(db, chat, query):
        rows, ids = original(db, chat, query)
        # Adversarial adapter: returns previously read rows but source is now gone.
        result = [(chunk, doc) for chunk, doc in rows]
        with Session(db.get_bind()) as other:
            other.get(Document, result[0][1].id).status = "deleting"
            other.commit()
        return result, ids

    monkeypatch.setattr(chat_api, "retrieve", retrieve)
    provider = Scripted([[call("事实")]])
    _, history = ask(stage4, "严格根据资料说明事实", provider, monkeypatch)
    assert len(provider.inputs) == 1
    assert history[-1]["stop_reason"] in {"expired_evidence", "no_evidence"}
    assert history[-1]["citations"] == []


def test_finalization_repair_and_recheck_use_reserved_call_path(stage4, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    monkeypatch.setattr(get_settings(), "agent_finalize_enabled", True)
    monkeypatch.setattr(get_settings(), "context_budget_enabled", True)
    provider = Scripted(
        [
            [call("事实")],
            [Delta("原始事实 [1]", finish_reason="stop")],
            [Delta('{"supported":false}', finish_reason="stop")],
            [Delta("修正后的事实 [1]", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    _, history = ask(stage4, "严格根据资料说明事实", provider, monkeypatch)
    metrics = history[-1]["measurements"]
    assert [c["purpose"] for c in metrics["calls"]] == [
        "agent",
        "finalize",
        "review",
        "repair",
        "recheck",
    ]
    assert history[-1]["content"] == "修正后的事实 [1]"
    assert (
        len([e for e in metrics["context_events"] if e["kind"] == "context_budget"])
        == 5
    )


def test_cancel_after_tool_never_starts_finalization(stage4, monkeypatch):
    from app.api import chat as chat_api

    monkeypatch.setattr(get_settings(), "agent_finalize_enabled", True)
    monkeypatch.setattr(get_settings(), "agent_max_rounds", 1)
    original = chat_api.retrieve

    def retrieve(*args):
        result = original(*args)
        with chat_api._ACTIVE_LOCK:
            assert len(chat_api._ACTIVE) == 1
            next(iter(chat_api._ACTIVE.values()))[1].cancelled.set()
        return result

    monkeypatch.setattr(chat_api, "retrieve", retrieve)
    provider = Scripted([[call("事实")]])
    _, history = ask(stage4, "严格根据资料说明事实", provider, monkeypatch)
    assert len(provider.inputs) == 1
    assert history[-1]["stop_reason"] == "cancelled"
    assert not history[-1]["citations"]


def test_insufficient_time_leaves_review_boundary_intact(stage4):
    from sqlalchemy.orm import Session

    from app.agent import run_agent
    from app.db.models import ChatSession
    from app.providers.call_options import Execution

    client, headers, courses, docs, users, engine, sid = stage4
    settings = get_settings()
    from unittest.mock import patch

    provider = Scripted([])
    with patch.object(settings, "agent_finalize_enabled", True), Session(engine) as db:
        chat = db.get(ChatSession, sid)
        updates = list(
            run_agent(
                db,
                chat,
                "问题",
                "strict",
                [{"role": "user", "content": "问题"}],
                provider,
                lambda *_: ([], []),
                execution=Execution.start(100),
            )
        )
    assert updates[-1][1]["stop_reason"] == "finalization_time_insufficient"
    assert not provider.inputs
