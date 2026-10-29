# ruff: noqa: F811 -- imported shared pytest fixtures are intentional.
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401
from test_stage4 import Scripted, ask, call, stage4  # noqa: F401

from app.api import chat as chat_api
from app.core.config import get_settings
from app.db.models import Chunk, Document
from app.evaluation.runtime import CourseRetrieval, observer
from app.providers.llm import Delta


def test_stage3_evaluator_uses_current_http_inputs(library, monkeypatch):  # noqa: F811
    from app.db.models import ChatSession
    from scripts.evaluate_stage3 import end_to_end

    client, headers, courses, _, users, engine = library
    provider = Scripted(
        [
            [call("严格根据资料解释事实")],
            [Delta("事实 [1]", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    )
    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: provider)
    with Session(engine) as db:
        result = end_to_end(
            client,
            {
                "question": "严格根据资料解释事实",
                "categories": ["term"],
                "key_facts": ["事实"],
                "_other_token": headers[1]["Authorization"].split()[1],
            },
            ChatSession(owner_id=users[0], course_id=courses[0]),
            headers[0],
            db,
            None,
        )
    assert result["context_capture"] == "captured"
    assert result["actual_context"] and result["citations"][0]["matches_actual_prompt"]
    assert result["model_turns"] == 3 and len(result["model_input_hashes"]) == 3
    assert result["llm_calls"] == 0  # Scripted transport made no real HTTP request.


def test_current_http_multiturn_observation(stage4, monkeypatch):  # noqa: F811
    seen = []
    token = observer.set(seen.append)
    try:
        provider = Scripted(
            [
                [call("first")],
                [call("second", id="t2")],
                [Delta("事实 [1]", finish_reason="stop")],
                [Delta('{"supported":true}', finish_reason="stop")],
            ]
        )
        events, history = ask(stage4, "严格根据资料解释", provider, monkeypatch)
    finally:
        observer.reset(token)
    sources = [e["sources"] for e in seen if e["kind"] == "course_sources"]
    assert len(sources) == 2 and sources[0] == sources[1]
    assert sources[0][0]["label"] == 1
    assert len([e for e in seen if e["kind"] == "model_input"]) == 4
    assert seen[-1]["kind"] == "final"
    assert events[-1][1]["answer_method"] == "llm"
    assert history[-1]["citations"][0]["chunk_id"] == sources[0][0]["chunk_id"]
    assert "protocol_state" not in history[-1]


@pytest.mark.parametrize(
    "case", ["verified", "rewritten", "disabled", "missing_proof", "deleted"]
)
def test_program_answer_current_http_no_fabricated_zero_calls(
    stage4,
    monkeypatch,
    case,  # noqa: F811
):  # noqa: F811
    client, headers, _, docs, _, engine, _ = stage4
    monkeypatch.setattr(get_settings(), "retrieval_verified_tables", case != "disabled")
    with Session(engine) as db:
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == docs[0]))
        chunk.text = (
            "Figure 1 The instructor relation.\nID name salary\n70001 Ada 60000"
        )
        db.commit()
        cid = chunk.id
    plan = {
        "status": "verified_rows",
        "rows": [
            {
                "table": "instructor",
                "values": {"name": "Ada", "salary": "60000"},
                "source_ids": [cid],
            }
        ],
        "joins": [],
        "keys": {},
        "counts": [],
    }

    if case == "missing_proof":
        plan["joins"] = [{"source_ids": ["missing-proof"]}]

    def retrieve(db, chat, query):
        row = db.execute(
            select(Chunk, Document).join(Document).where(Chunk.id == cid)
        ).one()
        return CourseRetrieval(
            [row], [cid], {"documents": [plan]}, question="严格根据资料查询Ada的薪水"
        )

    monkeypatch.setattr(chat_api, "retrieve", retrieve)
    turns = [
        [call("Ada salary" if case == "rewritten" else "严格根据资料查询Ada的薪水")]
    ]
    if case in {"disabled", "missing_proof"}:
        turns += [
            [Delta("Ada的薪水是60000 [1]", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    provider = Scripted(turns)
    seen = []

    def capture(event):
        seen.append(event)
        if case == "deleted" and event["kind"] == "table_verification":
            with Session(engine) as db, db.begin():
                db.get(Document, docs[0]).status = "deleting"

    token = observer.set(capture)
    try:
        events, history = ask(
            stage4, "严格根据资料查询Ada的薪水", provider, monkeypatch
        )
    finally:
        observer.reset(token)
    expected_method = (
        "verified_table_v1"
        if case in {"verified", "rewritten"}
        else "blocked"
        if case == "deleted"
        else "llm"
    )
    assert events[-1][1]["answer_method"] == expected_method
    if case == "deleted":
        assert (
            history[-1]["stop_reason"] == "expired_evidence"
            and not history[-1]["citations"]
        )
    else:
        assert "60000" in history[-1]["content"] and history[-1]["citations"]
    calls = 1 if case in {"verified", "rewritten", "deleted"} else 3
    assert len(provider.inputs) == calls
    assert len([e for e in seen if e["kind"] == "model_input"]) == calls


def test_observation_contexts_do_not_share_inputs():
    from concurrent.futures import ThreadPoolExecutor

    from app.providers.llm import complete_turn

    def run(question):
        seen = []
        token = observer.set(seen.append)
        try:
            complete_turn(
                Scripted([[Delta("ok", finish_reason="stop")]]),
                [{"role": "user", "content": question}],
            )
        finally:
            observer.reset(token)
        return seen[0]["messages"][0]["content"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ["first request", "second request"])) == [
            "first request",
            "second request",
        ]


def test_generated_facets_remain_user_data(stage4, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "retrieval_facet_coverage", True)
    monkeypatch.setattr(chat_api, "query_facets", lambda q: [q, "another facet"])
    provider = Scripted([[Delta("【需补充】请明确概念", finish_reason="stop")]])
    ask(stage4, "TOKEN_INSTRUCTION_123", provider, monkeypatch)
    first_input = provider.inputs[0]
    assert "TOKEN_INSTRUCTION_123" not in first_input[0]["content"]
    assert any("TOKEN_INSTRUCTION_123" in str(m["content"]) for m in first_input[1:])
