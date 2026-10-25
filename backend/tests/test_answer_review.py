"""Image context, bounded repair, source isolation, and protocol regression."""

# ruff: noqa: F811

import json
from collections import deque

import pytest
from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401
from test_stage4 import ask, call, picture, stage4  # noqa: F401
from test_stage5 import QUERY, URL, external, tool  # noqa: F401

from app.answer_review import input_snapshot
from app.db.models import Document, Message
from app.providers.call_options import Execution, profile
from app.providers.llm import Delta

BAD = "图片中查询按部门求和。删除部门时外键自动置空 [1]。"
GOOD = (
    "查询返回工资总额不低于各部门总额平均值的部门。"
    "假设总额为100、200、300，则返回后两组。"
)
ISSUE = {
    "verdict": "needs_revision",
    "issues": [
        {
            "claim": "删除部门时外键自动置空",
            "type": "unsupported_source_claim",
            "reason": "图片只含查询，没有建表或外键约束。",
        }
    ],
}
PASS = {"verdict": "passed", "issues": []}


class Model:
    def __init__(self, answers, reviews=(), repairs=(), on_repair=None, on_review=None):
        self.answers, self.reviews, self.repairs = map(
            deque, (answers, reviews, repairs)
        )
        self.inputs = []
        self.on_repair, self.on_review = on_repair, on_review

    def stream(self, messages, tools=None, **kwargs):
        first = messages[0]["content"]
        stage = (
            "review"
            if "引用核对器" in first
            else "repair"
            if "回答修复器" in first
            else "agent"
        )
        self.inputs.append((stage, messages, kwargs["options"], tools))
        if stage == "review":
            if self.on_review:
                self.on_review(kwargs)
            value = self.reviews.popleft()
            yield Delta(
                value if isinstance(value, str) else json.dumps(value),
                finish_reason="stop",
            )
        elif stage == "repair":
            if self.on_repair:
                self.on_repair(kwargs)
            yield Delta(
                self.repairs.popleft(),
                finish_reason="stop",
                reasoning_content="private-repair-thought"
                if kwargs["options"].thinking
                else "",
            )
        else:
            value = self.answers.popleft()
            yield (
                value
                if isinstance(value, Delta)
                else Delta(value, finish_reason="stop")
            )


def attach(context):
    client, headers, *_, sid = context
    r = client.post(
        f"/api/v1/chat/sessions/{sid}/attachments",
        headers=headers[0],
        files={"file": ("query.png", picture(), "image/png")},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_image_direct_answer_does_not_require_course_or_external_tools(
    stage4, monkeypatch
):
    model = Model([GOOD], [PASS])
    _, rows = ask(
        stage4,
        "图片中的SQL是什么意思",
        model,
        monkeypatch,
        attachment_ids=[attach(stage4)],
    )
    assert rows[-1]["content"] == GOOD
    assert not any(t.get("tool") for t in rows[-1]["agent_trace"])
    assert rows[-1]["agent_trace"][-1]["supported"] is True
    assert [x[0] for x in model.inputs] == ["agent", "review"]
    assert any(p["type"] == "image_url" for p in model.inputs[0][1][-1]["content"])


@pytest.mark.parametrize("thinking", [False, True])
def test_image_repair_gets_original_context_and_rechecks_without_citations(
    stage4, monkeypatch, thinking
):
    model = Model([call(), BAD], [ISSUE, PASS], [GOOD])
    _, rows = ask(
        stage4,
        "图片中的SQL是什么意思",
        model,
        monkeypatch,
        attachment_ids=[attach(stage4)],
        thinking=thinking,
    )
    assert rows[-1]["content"] == GOOD and rows[-1]["stop_reason"] == "answer"
    assert not rows[-1]["citations"]
    assert [s for s, *_ in model.inputs] == [
        "agent",
        "agent",
        "review",
        "repair",
        "review",
    ]
    for stage, messages, options, tools in model.inputs[2:]:
        assert tools is None
        assert any(
            p.get("type") == "image_url"
            for m in messages
            if isinstance(m.get("content"), list)
            for p in m["content"]
        )
        data = json.loads(messages[-1]["content"])
        assert data["question"] == "图片中的SQL是什么意思"
        assert (
            options == profile("agent", thinking)
            if stage == "repair"
            else options == profile("citation_check")
        )
    with Session(stage4[5]) as db:
        stored = db.get(Message, rows[-1]["id"])
        assert not stored.protocol_state["replayable"]
        assert len(stored.protocol_state["validation"]) == 3
        assert stored.protocol_state["validation"][0]["issues"][0]["claim"]
    assert "private-repair-thought" not in json.dumps(rows)
    follow = Model(["继续解释分组。"], [PASS])
    ask(stage4, "继续", follow, monkeypatch)
    text = json.dumps(follow.inputs[0][1], ensure_ascii=False)
    assert (
        GOOD in text and "自动置空" not in text and "private-repair-thought" not in text
    )


def test_repair_with_valid_course_citation_can_pass_without_image(stage4, monkeypatch):
    model = Model([call(), "错的结论 [1]"], [ISSUE, PASS], ["课程事实 [1]。"])
    _, rows = ask(stage4, "按照课程说明", model, monkeypatch)
    assert rows[-1]["content"] == "课程事实 [1]。"
    assert len(rows[-1]["citations"]) == 1


def test_uncited_image_reasoning_error_can_be_repaired(stage4, monkeypatch):
    issue = {
        "verdict": "needs_revision",
        "issues": [
            {
                "claim": "高于平均就是排名前一半",
                "type": "reasoning_error",
                "reason": "平均值与中位数不是同一概念。",
            }
        ],
    }
    model = Model(["高于平均就是排名前一半。"], [issue, PASS], [GOOD])
    _, rows = ask(
        stage4, "解释图片代码", model, monkeypatch, attachment_ids=[attach(stage4)]
    )
    assert rows[-1]["content"] == GOOD
    assert not any(t.get("tool") for t in rows[-1]["agent_trace"])
    assert len([t for t in rows[-1]["agent_trace"] if t.get("check")]) == 2


def test_only_removing_citation_does_not_pass(stage4, monkeypatch):
    model = Model([call(), BAD], [ISSUE], [BAD.replace(" [1]", "")])
    _, rows = ask(
        stage4, "解释图片", model, monkeypatch, attachment_ids=[attach(stage4)]
    )
    assert (
        rows[-1]["stop_reason"] == "unsupported"
        and "自动置空" not in rows[-1]["content"]
    )
    assert [s for s, *_ in model.inputs].count("repair") == 1


def test_second_failure_is_bounded_and_keeps_no_bad_assertion(stage4, monkeypatch):
    model = Model([call(), BAD], [ISSUE, ISSUE], ["换个说法，删部门会置空 [1]。"])
    _, rows = ask(
        stage4, "解释图片", model, monkeypatch, attachment_ids=[attach(stage4)]
    )
    assert rows[-1]["stop_reason"] == "unsupported"
    assert "置空" not in rows[-1]["content"]
    assert [s for s, *_ in model.inputs].count("repair") == 1


@pytest.mark.parametrize(
    "bad_review", ["not json", '{"verdict":"passed","issues":[{}]}']
)
def test_bad_review_protocol_is_unavailable_not_content_failure(
    stage4, monkeypatch, bad_review
):
    model = Model([call(), "课程事实 [1]。"], [bad_review])
    _, rows = ask(stage4, "查询课程", model, monkeypatch)
    assert rows[-1]["stop_reason"] == "verification_unavailable"
    assert rows[-1]["agent_trace"][-1]["supported"] is None
    assert all(stage != "repair" for stage, *_ in model.inputs)


def test_strict_course_never_falls_back_to_image_only_answer(stage4, monkeypatch):
    model = Model([call(), BAD], [ISSUE], [GOOD])
    _, rows = ask(
        stage4,
        "严格根据课程资料解释图片",
        model,
        monkeypatch,
        attachment_ids=[attach(stage4)],
    )
    assert rows[-1]["stop_reason"] == "no_evidence" and GOOD not in rows[-1]["content"]


def test_source_removed_during_review_prevents_repair(stage4, monkeypatch):
    def remove(_):
        with Session(stage4[5]) as db:
            for doc in db.query(Document).filter(Document.owner_id == stage4[4][0]):
                doc.status = "failed"
            db.commit()

    model = Model([call(), BAD], [ISSUE], on_review=remove)
    _, rows = ask(stage4, "课程解释", model, monkeypatch)
    assert rows[-1]["stop_reason"] == "expired_evidence"
    assert all(s != "repair" for s, *_ in model.inputs)


def test_source_removed_during_repair_prevents_further_review(stage4, monkeypatch):
    def remove(_):
        with Session(stage4[5]) as db:
            for doc in db.query(Document).filter(Document.owner_id == stage4[4][0]):
                doc.status = "failed"
            db.commit()

    model = Model([call(), BAD], [ISSUE], [GOOD], on_repair=remove)
    _, rows = ask(
        stage4, "解释图片", model, monkeypatch, attachment_ids=[attach(stage4)]
    )
    assert rows[-1]["stop_reason"] == "expired_evidence"
    assert [s for s, *_ in model.inputs].count("review") == 1


def test_cancellation_in_repair_discards_late_answer(stage4, monkeypatch):
    def cancel(kwargs):
        kwargs["execution"].cancelled.set()

    model = Model([call(), BAD], [ISSUE], [GOOD], on_repair=cancel)
    events, rows = ask(
        stage4, "解释图片", model, monkeypatch, attachment_ids=[attach(stage4)]
    )
    assert events[-1][0] == "error" and rows[-1]["stop_reason"] == "cancelled"
    assert not rows[-1]["content"]


def test_no_image_cannot_relabel_unsupported_source_fact_as_general(
    stage4, monkeypatch
):
    model = Model([call(), BAD], [ISSUE], [GOOD])
    _, rows = ask(stage4, "课程事实", model, monkeypatch)
    assert rows[-1]["stop_reason"] == "unsupported"
    assert GOOD not in rows[-1]["content"]


def test_external_fact_cannot_fall_back_to_uncited_image_answer(external, monkeypatch):
    context, _ = external
    model = Model(
        [
            tool("web_search", {"query": QUERY}, "s"),
            tool("read_web_page", {"url": URL}, "r"),
            "网页断言 [1]。",
        ],
        [ISSUE],
        [GOOD],
    )
    _, rows = ask(
        context,
        "图片结合最新网页",
        model,
        monkeypatch,
        attachment_ids=[attach(context)],
        web_enabled=True,
        web_queries=[QUERY],
    )
    assert rows[-1]["stop_reason"] == "no_evidence"
    assert GOOD not in rows[-1]["content"]


def test_deadline_during_repair_never_accepts_late_answer(stage4):
    from sqlalchemy import select

    from app.agent import checked_answer
    from app.db.models import ChatSession, Chunk
    from app.providers.call_options import ExecutionStopped
    from app.providers.vision import image_block

    now = [0.0]
    execution = Execution(10, clock=lambda: now[0])
    model = Model([], [ISSUE], [GOOD], on_repair=lambda _: now.__setitem__(0, 11))
    with Session(stage4[5]) as db:
        chat = db.get(ChatSession, stage4[-1])
        chunk = db.scalar(
            select(Chunk).where(
                Chunk.owner_id == chat.owner_id, Chunk.course_id == chat.course_id
            )
        )
        state = {
            "answer": BAD,
            "stop_reason": "answer",
            "evidence": [chunk.id],
            "trace": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "input_context": [
                {"role": "user", "content": [image_block(picture(), "image/png")]}
            ],
        }
        with pytest.raises(ExecutionStopped, match="deadline_exceeded"):
            checked_answer(db, chat, state, "auto", model, execution=execution)
        assert [s for s, *_ in model.inputs].count("review") == 1


def test_expired_execution_never_starts_review(stage4, monkeypatch):
    from app.agent import checked_answer
    from app.db.models import ChatSession

    context = stage4
    with Session(context[5]) as db:
        # All deterministic validation still precedes any network side effect.
        chat = db.get(ChatSession, context[-1])
        state = {
            "answer": "x [99]",
            "stop_reason": "answer",
            "evidence": [],
            "trace": [],
        }
        assert (
            checked_answer(db, chat, state, "auto", Model([]), execution=Execution(0))[
                2
            ]
            == "invalid_citation"
        )


def test_input_snapshot_excludes_assistant_protocol_and_keeps_admitted_images():
    image = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,public-fixture"},
    }
    snapshot = input_snapshot(
        [
            {"role": "assistant", "content": "private old unsupported assertion"},
            {"role": "user", "content": [{"type": "text", "text": "原图片"}, image]},
            {"role": "tool", "content": "private protocol"},
            {"role": "user", "content": [{"type": "text", "text": "继续解释"}]},
        ]
    )
    assert len(snapshot) == 2 and image in snapshot[0]["content"]
    assert "private" not in json.dumps(snapshot)
