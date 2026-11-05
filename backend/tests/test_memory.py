"""Stage 7 real PostgreSQL service and graph tests; model responses are scripted."""

import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import test_stage2
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_stage4 import Scripted, ask, stage4  # noqa: F401

from app.core.config import get_settings
from app.db.models import (
    ChatSession,
    MemoryJob,
    MemoryPreference,
    MemorySuggestion,
    Message,
    now,
)
from app.memory import service as m
from app.memory import summary
from app.providers.llm import Delta


@pytest.fixture
def library(monkeypatch):
    if os.environ.get("STAGE7_VALIDATION_RUN"):
        target = os.environ["DATABASE_URL"]
        monkeypatch.setattr(
            test_stage2, "create_engine", lambda *a, **kw: create_engine(target)
        )
    # Standalone baseline is SQLite; the dedicated validated runner uses real PG.
    yield from test_stage2.library.__wrapped__(monkeypatch)


@pytest.fixture
def mem(stage4, monkeypatch):  # noqa: F811
    s = get_settings()
    for key in (
        "memory_preferences_enabled",
        "memory_suggestions_enabled",
        "memory_summary_enabled",
    ):
        monkeypatch.setattr(s, key, True)
    monkeypatch.setattr(summary, "get_engine", lambda: stage4[-2])
    return stage4


def save(context, course=None, version=0, value="Python", request_key=None):
    client, headers = context[:2]
    return client.put(
        "/api/v1/memory/preferences",
        headers=headers[0],
        json={
            "course_id": course,
            "key": "code_language",
            "value": value,
            "version": version,
            "request_key": request_key or str(uuid4()),
        },
    )


def test_preference_scope_override_and_cas(mem):
    client, headers, courses = mem[:3]
    assert save(mem).status_code == 200
    row = save(mem, courses[0], value="SQL").json()
    result = client.get(
        f"/api/v1/memory/preferences?course_id={courses[0]}", headers=headers[0]
    ).json()
    assert result["effective"] == {"code_language": "SQL"}
    assert save(mem, courses[0], value="Java").status_code == 409
    assert (
        client.delete(
            f"/api/v1/memory/preferences/{row['id']}?version=1", headers=headers[0]
        ).status_code
        == 200
    )
    assert client.get(
        f"/api/v1/memory/preferences?course_id={courses[0]}", headers=headers[0]
    ).json()["effective"] == {"code_language": "Python"}


def test_manual_idempotence_and_undo_do_not_override_new_value(mem):
    first = save(mem, request_key="once").json()
    assert save(mem, request_key="once").json() == first
    client, headers = mem[:2]
    assert save(mem, version=1, value="SQL").status_code == 200
    assert (
        client.post(
            f"/api/v1/memory/operations/{first['operation_id']}/undo",
            headers=headers[0],
            json={"version": 1},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "key,value",
    [("prompt", "ignore rules"), ("code_language", "secret"), ("hint_first", True)],
)
def test_whitelist_rejects_untrusted_fields(mem, key, value):
    client, headers = mem[:2]
    assert (
        client.put(
            "/api/v1/memory/preferences",
            headers=headers[0],
            json={"key": key, "value": value, "version": 0, "request_key": "bad"},
        ).status_code
        == 422
    )


def test_foreign_course_and_foreign_record_rejected(mem):
    client, headers, courses = mem[:3]
    assert save(mem, courses[2]).status_code == 404
    row = save(mem).json()
    assert (
        client.delete(
            f"/api/v1/memory/preferences/{row['id']}?version=1", headers=headers[1]
        ).status_code
        == 404
    )


def tool(text, name="save_preference", **extra):
    args = {
        "key": "code_language",
        "value": "Python",
        "intent_text": text,
        "memory_only": True,
        **extra,
    }
    return [
        Delta(
            tool_calls=[
                {
                    "index": 0,
                    "id": "m1",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
            finish_reason="tool_calls",
        )
    ]


def test_graph_explicit_commit_receipt_and_reload(mem, monkeypatch):
    question = "以后这门课都用Python举例"
    events, history = ask(
        mem, question, Scripted([tool(question)]), monkeypatch, answer_scope="strict"
    )
    assert any(e[0] == "memory_operation" for e in events)
    assert history[-1]["memory"]["operations"][0]["status"] == "saved"
    with Session(mem[-2]) as db:
        row = db.scalar(
            select(MemoryPreference).where(MemoryPreference.owner_id == mem[-3][0])
        )
        assert row.scope == "course:" + mem[2][0]
        assert db.get(Message, history[-1]["id"]).protocol_state["replayable"] is False


def test_graph_temporary_no_tool_does_not_write(mem, monkeypatch):
    ask(
        mem,
        "这题用Python举例",
        Scripted([[Delta("本题示例。", finish_reason="stop")]]),
        monkeypatch,
    )
    with Session(mem[-2]) as db:
        assert not db.scalar(
            select(MemoryPreference).where(MemoryPreference.owner_id == mem[-3][0])
        )


def test_failed_after_staging_does_not_save(mem, monkeypatch):
    question = "以后用Python，顺便解释课程定理"
    args = tool(question, memory_only=False)
    ask(
        mem,
        question,
        Scripted([args, [Delta("【限制】无法完成。", finish_reason="stop")]]),
        monkeypatch,
    )
    with Session(mem[-2]) as db:
        assert not db.scalar(
            select(MemoryPreference).where(MemoryPreference.owner_id == mem[-3][0])
        )


def test_source_delete_invalidates_chat_preference_but_not_manual(mem, monkeypatch):
    question = "以后用Python"
    _, history = ask(mem, question, Scripted([tool(question)]), monkeypatch)
    client, headers, courses = mem[:3]
    assert save(mem, value="SQL").status_code == 200
    assert (
        client.delete(
            f"/api/v1/chat/sessions/{mem[-1]}/messages/{history[0]['id']}",
            headers=headers[0],
        ).status_code
        == 204
    )
    result = client.get(
        f"/api/v1/memory/preferences?course_id={courses[0]}", headers=headers[0]
    ).json()
    assert result["effective"] == {"code_language": "SQL"}


def create_suggestion(context):
    _, _, courses, _, users, engine, sid = context
    with Session(engine) as db, db.begin():
        chat = db.get(ChatSession, sid)
        messages = [
            Message(
                owner_id=users[0],
                session_id=sid,
                role="user",
                content="用Python举例",
                status="completed",
            )
            for _ in range(3)
        ]
        reply = Message(
            owner_id=users[0],
            session_id=sid,
            role="assistant",
            content="示例",
            status="completed",
        )
        db.add_all([*messages, reply])
        db.flush()
        data = {
            "scope": "course:" + courses[0],
            "key": "code_language",
            "value": "Python",
            "sources": [x.id for x in messages],
        }
        return m.suggest(db, users[0], chat, reply.id, data)


def test_suggestion_pending_not_effective_and_confirmation_idempotent(mem):
    card = create_suggestion(mem)
    client, headers, courses = mem[:3]
    url = f"/api/v1/memory/preferences?course_id={courses[0]}"
    assert client.get(url, headers=headers[0]).json()["effective"] == {}
    body = {
        "version": card["version"],
        "preference_version": 0,
        "course_id": courses[0],
        "value": "Python",
        "request_key": "accept-once",
    }
    first = client.post(
        f"/api/v1/memory/suggestions/{card['id']}/accept", headers=headers[0], json=body
    )
    assert first.status_code == 200
    assert (
        client.post(
            f"/api/v1/memory/suggestions/{card['id']}/accept",
            headers=headers[0],
            json=body,
        ).json()
        == first.json()
    )
    assert client.get(url, headers=headers[0]).json()["effective"] == {
        "code_language": "Python"
    }


def test_dismiss_suppresses_and_stale_cards_cannot_accept(mem):
    card = create_suggestion(mem)
    client, headers, courses = mem[:3]
    assert (
        client.post(
            f"/api/v1/memory/suggestions/{card['id']}/dismiss",
            headers=headers[0],
            json={"version": 1, "suppress": True},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/memory/suggestions/{card['id']}/accept",
            headers=headers[0],
            json={
                "version": 1,
                "preference_version": 0,
                "course_id": courses[0],
                "value": "Python",
                "request_key": "late",
            },
        ).status_code
        == 409
    )


def test_suggestion_conflicts_after_manual_change(mem):
    card = create_suggestion(mem)
    assert save(mem, mem[2][0], value="SQL").status_code == 200
    with Session(mem[-2]) as db:
        assert (
            m.suggestion_data(db, db.get(MemorySuggestion, card["id"]))["status"]
            == "invalidated"
        )


def summary_job(context, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "agent_history_messages", 2)
    _, _, _, _, users, engine, sid = context
    with Session(engine) as db, db.begin():
        chat = db.get(ChatSession, sid)
        for i in range(6):
            db.add(
                Message(
                    owner_id=users[0],
                    session_id=sid,
                    role="user" if i % 2 == 0 else "assistant",
                    content="任务：复习事务，先给提示" if i % 2 == 0 else "好",
                    status="completed",
                    created_at=now() + timedelta(seconds=i),
                )
            )
        db.flush()
        summary.configure(db, chat, 0, True)
        return summary.enqueue(db, chat)


class SummaryProvider:
    def stream(self, messages, **kwargs):
        payload = json.loads(messages[-1]["content"])
        ident = payload["messages"][0]["id"]
        yield Delta(
            json.dumps(
                {"task_goal": [{"text": "复习事务，先给提示", "source_ids": [ident]}]}
            ),
            finish_reason="stop",
        )


def test_summary_generation_reload_clear_and_duplicate_delivery(mem, monkeypatch):
    ident = summary_job(mem, monkeypatch)
    assert ident
    summary.run_job(ident, SummaryProvider())
    summary.run_job(ident, SummaryProvider())
    client, headers = mem[:2]
    url = f"/api/v1/chat/sessions/{mem[-1]}/memory"
    result = client.get(url, headers=headers[0]).json()
    assert result["summary"]["task_goal"][0]["text"] == "复习事务，先给提示"
    assert result["covered"] == 4
    paused = client.patch(
        url, headers=headers[0], json={"version": result["version"], "enabled": False}
    ).json()
    assert paused["summary"] == result["summary"]
    with Session(mem[-2]) as db:
        assert summary.valid_summary(db, db.get(ChatSession, mem[-1])) is None
    result = paused
    assert (
        client.delete(url + f"?version={result['version']}", headers=headers[0]).json()[
            "summary"
        ]
        is None
    )
    summary.run_job(ident, SummaryProvider())
    assert client.get(url, headers=headers[0]).json()["summary"] is None


def test_summary_late_result_rejected_after_source_delete(mem, monkeypatch):
    ident = summary_job(mem, monkeypatch)

    class DeleteDuringGeneration(SummaryProvider):
        def stream(self, messages, **kwargs):
            with Session(mem[-2]) as db, db.begin():
                target = json.loads(messages[-1]["content"])["messages"][0]["id"]
                db.delete(db.get(Message, target))
            yield from super().stream(messages, **kwargs)

    summary.run_job(ident, DeleteDuringGeneration())
    with Session(mem[-2]) as db:
        assert db.get(MemoryJob, ident).status == "invalidated"
        assert summary.valid_summary(db, db.get(ChatSession, mem[-1])) is None
        assert db.get(MemoryJob, ident).measurements["attempt"] is not None


def test_summary_invalid_model_is_bounded_and_original_chat_unchanged(mem, monkeypatch):
    ident = summary_job(mem, monkeypatch)
    for _ in range(3):
        summary.run_job(ident, Scripted([[Delta("not-json", finish_reason="stop")]]))
    with Session(mem[-2]) as db:
        row = db.get(MemoryJob, ident)
        assert row.attempts == 2 and row.status == "failed"
        assert row.measurements is not None


def test_disabled_features_preserve_management_delete(mem, monkeypatch):
    row = save(mem).json()
    monkeypatch.setattr(get_settings(), "memory_preferences_enabled", False)
    client, headers = mem[:2]
    assert (
        client.get("/api/v1/memory/preferences", headers=headers[0]).json()["effective"]
        == {}
    )
    assert (
        client.delete(
            f"/api/v1/memory/preferences/{row['id']}?version=1", headers=headers[0]
        ).status_code
        == 200
    )


def test_stale_identity_map_cannot_override_concurrent_preference(mem):
    from fastapi import HTTPException

    owner, engine = mem[-3][0], mem[-2]
    first = save(mem).json()
    with Session(engine) as stale:
        cached = m.preference(stale, owner, "global", "code_language")
        assert cached.version == 1
        assert save(mem, version=1, value="SQL").status_code == 200
        with pytest.raises(HTTPException):
            m.mutate(
                stale,
                owner,
                scope="global",
                key="code_language",
                value="Java",
                version=first["version"],
                request_key="stale-writer",
            )
        stale.rollback()


def test_expired_suggestion_and_natural_confirmation(mem, monkeypatch):
    card = create_suggestion(mem)
    text = "保存这个建议"
    _, history = ask(
        mem,
        text,
        Scripted([tool(text, name="confirm_preference", suggestion_id=card["id"])]),
        monkeypatch,
    )
    assert history[-1]["memory"]["operations"][0]["status"] == "saved"
    with Session(mem[-2]) as db:
        assert db.get(MemorySuggestion, card["id"]).status == "accepted"


def test_expired_suggestion_cannot_accept(mem):
    card = create_suggestion(mem)
    with Session(mem[-2]) as db, db.begin():
        db.get(MemorySuggestion, card["id"]).expires_at = now() - timedelta(seconds=1)
    client, headers, courses = mem[:3]
    response = client.post(
        f"/api/v1/memory/suggestions/{card['id']}/accept",
        headers=headers[0],
        json={
            "version": 1,
            "preference_version": 0,
            "course_id": courses[0],
            "value": "Python",
            "request_key": "expired",
        },
    )
    assert response.status_code == 409


def test_confirm_and_explicit_other_field_are_one_atomic_batch(mem):
    card = create_suggestion(mem)
    with Session(mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, mem[-1])
        source = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="user",
            content="保存这个建议，并且以后详细解释",
            status="completed",
        )
        db.add(source)
        db.flush()
        answer = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="assistant",
            content="",
            status="completed",
            reply_to_id=source.id,
        )
        db.add(answer)
        db.flush()
        staged = []
        for name, args in [
            ("save_preference", {"key": "explanation_detail", "value": "detailed"}),
            (
                "confirm_preference",
                {
                    "key": "code_language",
                    "value": "Python",
                    "suggestion_id": card["id"],
                },
            ),
        ]:
            m.stage_tool(
                db,
                chat,
                source,
                {"id": name, "function": {"name": name, "arguments": json.dumps(args)}},
                staged,
            )
        results = m.commit_staged(db, chat, answer, staged)
        assert len(results) == 2 and all(r["status"] == "saved" for r in results)
        assert m.preferences(db, chat.owner_id, chat.course_id)["effective"] == {
            "code_language": "Python",
            "explanation_detail": "detailed",
        }


def test_agent_supplied_id_cannot_resolve_ambiguous_user_confirmation(mem):
    from fastapi import HTTPException

    card = create_suggestion(mem)
    with Session(mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, mem[-1])
        other_reply = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="assistant",
            content="another suggestion",
            status="completed",
        )
        source = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="user",
            content="保存这个建议",
            status="completed",
        )
        db.add_all([other_reply, source])
        db.flush()
        previous = db.get(MemorySuggestion, card["id"])
        m.suggest(
            db,
            chat.owner_id,
            chat,
            other_reply.id,
            {**previous.data, "key": "hint_first", "value": "true"},
        )
        with pytest.raises(HTTPException) as error:
            m.stage_tool(
                db,
                chat,
                source,
                {
                    "id": "confirm",
                    "function": {
                        "name": "confirm_preference",
                        "arguments": json.dumps(
                            {
                                "key": "code_language",
                                "value": "Python",
                                "suggestion_id": card["id"],
                            }
                        ),
                    },
                },
                [],
            )
        assert error.value.status_code == 409


def test_summary_http_retry_is_owned_by_job_not_transport(mem, monkeypatch):
    import httpx

    from app.providers.call_options import Execution, profile
    from app.providers.llm import OpenAICompatibleLLM

    calls = []
    original = httpx.Client

    def unavailable(request):
        calls.append(request.url)
        return httpx.Response(503, request=request)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(unavailable), **kw),
    )
    with pytest.raises(httpx.HTTPStatusError):
        list(
            OpenAICompatibleLLM().stream(
                [{"role": "user", "content": "自制测试"}],
                options=profile("memory_summary"),
                execution=Execution.start(5),
            )
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("这个建议不错，就帮我记住吧", True),
        ("就按你说的来", True),
        ("可以，接受这个建议", True),
        ("不要记住", False),
        ("如果我说保存会怎样", False),
        ("解释“保存这个建议”", False),
        ("这个建议不错", False),
    ],
)
def test_confirmation_language_fence(text, expected):
    assert m.confirmation_intent(text) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("可以，接受这个建议并记住。", True),
        ("这个建议不错，就帮我记住吧", True),
        ("好的，请保存这个偏好，谢谢", True),
        ("就按你说的来", True),
        ("保存这个建议，另外请解释SQL", False),
        ("不要保存这个建议", False),
        ("解释保存这个建议", False),
    ],
)
def test_pure_confirmation_cannot_swallow_mixed_questions(text, expected):
    assert m.pure_confirmation(text) is expected


def test_unique_confirmation_uses_guarded_graph_tool_without_llm(mem, monkeypatch):
    create_suggestion(mem)
    _, history = ask(mem, "可以，接受这个建议并记住。", Scripted([]), monkeypatch)
    assert history[-1]["stop_reason"] == "answer"
    assert history[-1]["memory"]["operations"][0]["status"] == "saved"
    assert history[-1]["measurements"]["http_attempts"] == 0


@pytest.mark.parametrize(
    "key,value,text,expected",
    [
        ("code_language", "Java", "以后改用Java", True),
        ("code_language", "Python", "以后改用Java", False),
        ("code_language", "Java", "以后用JavaScript", False),
        ("explanation_detail", "brief", "记住我已经掌握所有数据库知识", False),
        ("explanation_detail", "brief", "以后讲得短一点", True),
    ],
)
def test_field_evidence_does_not_invent_or_reverse_preference(
    key, value, text, expected
):
    assert m.field_grounded(key, value, text) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("以后用Java", "Java"),
        ("把Python改成Java", "Java"),
        ("改用Java，不再用Python", "Java"),
        ("用Java替代Python", "Java"),
        ("以后Python或者Java都可以", None),
        ("假如从Python改成Java", None),
    ],
)
def test_code_target_binding(text, expected):
    assert m.code_target(text) == expected


def test_model_schema_does_not_delegate_current_source_binding(mem):
    assert all(
        "intent_text" not in tool["function"]["parameters"]["properties"]
        for tool in m.tool_definitions()
    )


@pytest.mark.parametrize("max_rounds,proposes", [(4, True), (1, False)])
@pytest.mark.parametrize("clarify", [False, True])
def test_single_graph_suggestion_checkpoint_consumes_existing_round(
    mem, monkeypatch, max_rounds, proposes, clarify
):
    from app.agent import run_agent

    monkeypatch.setattr(get_settings(), "agent_max_rounds", max_rounds)
    purposes = []

    class Provider:
        def stream(self, messages, tools=None, *, options=None, **kwargs):
            purposes.append(options.purpose)
            if options.purpose != "memory_suggestion":
                yield Delta(
                    "【需补充】请补充题目" if clarify else "一般解释，仅回答当前问题。",
                    finish_reason="stop",
                )
                return
            data = json.loads(messages[-1]["content"])
            yield from tool(
                "",
                name="propose_preference",
                source_ids=[r["id"] for r in data["source_messages"]],
                memory_only=False,
            )

    with Session(mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, mem[-1])
        rows = [
            Message(
                owner_id=chat.owner_id,
                session_id=chat.id,
                role="user",
                content=f"这题用Python举例：问题{i}",
                status="completed",
                created_at=now() + timedelta(seconds=i),
            )
            for i in range(3)
        ]
        db.add_all(rows)
        db.flush()
        history = [{"role": "user", "content": r.content} for r in rows]
        states = list(
            run_agent(
                db,
                chat,
                rows[-1].content,
                "auto",
                history,
                Provider(),
                lambda *a, **kw: [],
                memory_user_id=rows[-1].id,
            )
        )
        final = states[-1][1]
        assert bool(final["memory_staged"]) is proposes
        assert final["rounds"] == (2 if proposes else 1)
        assert purposes == (["agent", "memory_suggestion"] if proposes else ["agent"])
        assert m.preferences(db, chat.owner_id, chat.course_id)["effective"] == {}


@pytest.mark.parametrize("model_value", ["Java", "Python"])
def test_update_binds_current_user_without_model_quote(mem, monkeypatch, model_value):
    save(mem, mem[2][0])
    question = "更改长期偏好，以后这门课改用Java举例。"
    step = tool(question, name="update_preference", value=model_value)
    for delta in step:
        for call in delta.tool_calls:
            args = json.loads(call["function"]["arguments"])
            args.pop("intent_text")
            call["function"]["arguments"] = json.dumps(args)
    _, history = ask(mem, question, Scripted([step]), monkeypatch)
    with Session(mem[-2]) as db:
        assert m.preferences(db, mem[-3][0], mem[2][0])["effective"] == {
            "code_language": "Java"
        }
    assert history[-1]["memory"]["operations"][0]["status"] == "saved"


def test_staged_batch_rolls_back_every_key_on_late_failure(mem):
    from unittest.mock import patch

    from fastapi import HTTPException

    with Session(mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, mem[-1])
        source = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="user",
            content="以后用Java且详细讲解",
            status="completed",
        )
        answer = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="assistant",
            content="",
            status="completed",
        )
        db.add_all([source, answer])
        db.flush()
        staged = []
        for key, value in [
            ("code_language", "Java"),
            ("explanation_detail", "detailed"),
        ]:
            m.stage_tool(
                db,
                chat,
                source,
                {
                    "id": key,
                    "function": {
                        "name": "save_preference",
                        "arguments": json.dumps({"key": key, "value": value}),
                    },
                },
                staged,
            )
        original = m.mutate

        def fail_second(*args, **kwargs):
            if kwargs["key"] == "explanation_detail":
                raise HTTPException(409, "controlled late conflict")
            return original(*args, **kwargs)

        with patch.object(m, "mutate", fail_second):
            assert m.commit_staged(db, chat, answer, staged)[0]["status"] == "conflict"
        assert m.preferences(db, chat.owner_id, chat.course_id)["effective"] == {}
        receipts = m.history_metadata(db, answer)["operations"]
        assert len(receipts) == 1 and receipts[0]["status"] == "conflict"
        with pytest.raises(HTTPException):
            m.undo(db, chat.owner_id, receipts[0]["operation_id"], 0)


def test_clarification_can_publish_pending_card_but_no_effective_preference(
    mem, monkeypatch
):
    class Provider:
        def stream(self, messages, tools=None, *, options=None, **kwargs):
            if options.purpose == "memory_suggestion":
                data = json.loads(messages[-1]["content"])
                yield from tool(
                    "",
                    name="propose_preference",
                    source_ids=[r["id"] for r in data["source_messages"]],
                    memory_only=False,
                )
            else:
                yield Delta("【需补充】请补充题目条件。", finish_reason="stop")

    with Session(mem[-2]) as db, db.begin():
        for i in range(2):
            db.add(
                Message(
                    owner_id=mem[-3][0],
                    session_id=mem[-1],
                    role="user",
                    content=f"这题用Python举例，问题{i}",
                    status="completed",
                    created_at=now() - timedelta(seconds=10 - i),
                )
            )
    _, history = ask(mem, "这题继续用Python举例", Provider(), monkeypatch)
    assert history[-1]["stop_reason"] == "clarify"
    assert history[-1]["memory"]["suggestions"][0]["status"] == "pending"
    with Session(mem[-2]) as db:
        assert m.preferences(db, mem[-3][0], mem[2][0])["effective"] == {}


def test_summary_rolls_past_dependency_cap_and_targets_invalidation(mem, monkeypatch):
    from app.db.models import MemoryDependency

    monkeypatch.setattr(get_settings(), "memory_summary_dependencies", 2)
    ident = summary_job(mem, monkeypatch)
    summary.run_job(ident, SummaryProvider())
    with Session(mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, mem[-1])
        control = m.state(db, chat.owner_id, "session:" + chat.id)
        assert summary.valid_summary(db, chat)
        sources = list(
            db.scalars(
                select(MemoryDependency).where(MemoryDependency.state_id == control.id)
            )
        )
        assert len(sources) > 2
        other = ChatSession(owner_id=chat.owner_id, course_id=chat.course_id)
        db.add(other)
        db.flush()
        other_control = m.state(db, chat.owner_id, "session:" + other.id, True)
        other_control.data = {
            "enabled": True,
            "summary": {"task_goal": []},
            "dependencies": {"scope": other.document_ids},
        }
        db.flush()
        unrelated = Message(
            owner_id=chat.owner_id,
            session_id=chat.id,
            role="user",
            content="unrelated new turn",
            status="completed",
        )
        db.add(unrelated)
        db.flush()
        unrelated.content = "unrelated edit"
        db.flush()
        assert control.data["summary"] is not None
        db.get(Message, sources[0].source_id).content = "changed covered turn"
        db.flush()
        assert control.data["summary"] is None
        assert summary.valid_summary(db, other) == {"task_goal": []}


def test_summary_oversized_user_turn_reports_pause(mem, monkeypatch):
    monkeypatch.setattr(get_settings(), "memory_summary_chars", 1000)
    ident = summary_job(mem, monkeypatch)
    with Session(mem[-2]) as db, db.begin():
        job = db.get(MemoryJob, ident)
        job.status = "invalidated"
        source = db.get(Message, next(iter(job.data["dependencies"]["messages"])))
        source.content = "user condition " * 2000
        db.flush()
        chat = db.get(ChatSession, mem[-1])
        assert summary.enqueue(db, chat) is None
        assert (
            summary.summary_status(db, chat)["blocked_reason"]
            == "single_turn_exceeds_summary_input_budget"
        )


def test_summary_chains_over_300_messages_with_bounded_payload(mem, monkeypatch):
    from app.db.models import MemoryDependency

    monkeypatch.setattr(get_settings(), "agent_history_messages", 2)
    monkeypatch.setattr(get_settings(), "memory_summary_chars", 1500)
    with Session(mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, mem[-1])
        for i in range(324):
            db.add(
                Message(
                    owner_id=chat.owner_id,
                    session_id=chat.id,
                    role="user" if i % 2 == 0 else "assistant",
                    content="任务：复习事务，先给提示"
                    if i % 2 == 0
                    else "large assistant explanation" * 1000,
                    status="completed",
                    created_at=now() + timedelta(seconds=i),
                )
            )
        db.flush()
        summary.configure(db, chat, 0, True)
        ident = summary.enqueue(db, chat)
    executions = 0
    while ident:
        summary.run_job(ident, SummaryProvider())
        executions += 1
        assert executions < 200
        with Session(mem[-2]) as db:
            jobs = list(
                db.scalars(
                    select(MemoryJob).where(
                        MemoryJob.session_id == mem[-1], MemoryJob.status == "queued"
                    )
                )
            )
            assert len(jobs) <= 1
            ident = jobs[0].id if jobs else None
            if jobs:
                assert (
                    len(json.dumps(jobs[0].data["input"], ensure_ascii=False)) <= 1500
                )
    with Session(mem[-2]) as db, db.begin():
        chat = db.get(ChatSession, mem[-1])
        assert summary.summary_status(db, chat)["covered"] == 322
        source = db.scalar(
            select(MemoryDependency).where(MemoryDependency.kind == "messages")
        )
        assert summary.valid_summary(db, chat)
        db.delete(db.get(Message, source.source_id))
        db.flush()
        assert summary.valid_summary(db, chat) is None
