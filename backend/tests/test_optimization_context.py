from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_stage2 import library  # noqa: F401

from app.db.models import ChatSession, Chunk, Document
from app.ingestion.context import adjacent_sources
from app.ingestion.parser import Part, make_chunks


@pytest.mark.parametrize(
    "blocked",
    [
        "none",
        "answer",
        "section",
        "slide",
        "foreign",
        "unselected",
        "deleted",
        "budget",
    ],
)
def test_adjacent_sources_recheck_boundaries_and_scope(library, blocked):  # noqa: F811
    _, _, courses, docs, users, engine = library
    with Session(engine) as db:
        chunk = db.scalar(select(Chunk).where(Chunk.document_id == docs[0]))
        doc = db.get(Document, docs[0])
        chunk.page_start = None
        candidate = Chunk(
            id=str(uuid4()),
            document_id=doc.id,
            owner_id=users[0],
            course_id=courses[0],
            chunk_index=1,
            text="附加条件",
            section_path=chunk.section_path,
            content_hash="x" * 64,
            embedding_model=chunk.embedding_model,
            parser_version="opt34-test",
            embedding=[1.0] + [0.0] * 511,
        )
        if blocked == "answer":
            candidate.text = "答案：42"
        if blocked == "section":
            candidate.section_path = "别章"
        if blocked == "slide":
            candidate.slide_no = 2
        if blocked == "foreign":
            candidate.owner_id = users[1]
        if blocked == "deleted":
            doc.status = "deleted"
        db.add(candidate)
        db.commit()
        chat = ChatSession(
            owner_id=users[0],
            course_id=courses[0],
            document_ids=[] if blocked == "unselected" else [doc.id],
        )
        rows = adjacent_sources(
            db, chat, [(chunk, doc)], len(chunk.text) if blocked == "budget" else 900
        )
        assert [c.id for c, _ in rows] == (
            [chunk.id, candidate.id] if blocked == "none" else [chunk.id]
        )


def test_short_aggregation_retains_boundaries_and_original_content():
    parts = [
        Part("定义", "一"),
        Part("必要条件", "一"),
        Part("1. 练习", "一"),
        Part("答案：42", "一"),
        Part("另一章", "二"),
        Part("PDF正文", page=1),
        Part("PDF下一段", page=1),
    ]
    chunks = make_chunks(parts, aggregate_short=True)
    assert chunks[0]["text"] == "定义 必要条件"
    assert chunks[1]["content_type"] == "question"
    assert chunks[2]["content_type"] == "answer"
    assert len(chunks) == 6
    assert chunks[-1]["text"] == "PDF下一段"


def test_heading_pairing_never_discards_trailing_or_consecutive_headings():
    chunks = make_chunks(
        [Part("# A", "A"), Part("# B", "B"), Part("正文", "B"), Part("# C", "C")],
        pair_headings=True,
    )
    assert " ".join(c["text"] for c in chunks) == "# A # B 正文 # C"


def test_aggregation_does_not_cross_slide_or_table_or_size():
    chunks = make_chunks(
        [
            Part("a" * 150, slide=1),
            Part("b" * 150, slide=1),
            Part("c", slide=2),
            Part("表头\n行", slide=2, table=True),
        ],
        max_chars=200,
        aggregate_short=True,
    )
    assert all(len(c["text"]) <= 200 for c in chunks)
    assert len(chunks) == 4


def test_explicit_english_question_hint_answer_are_separate():
    chunks = make_chunks(
        [
            Part("Question: calculate"),
            Part("Hint: use rule"),
            Part("Answer: 42"),
            Part("ordinary note"),
        ],
        aggregate_short=True,
    )
    assert [c["content_type"] for c in chunks] == [
        "question",
        "explanation",
        "answer",
        "body",
    ]


@pytest.mark.parametrize("suffix", [".md", ".txt", ".docx", ".pptx"])
def test_actual_formats_keep_heading_body_and_numbers(suffix):
    from app.ingestion.parser import parse_document
    from scripts.evaluate_optimization_context import specimen

    parts = parse_document(
        "self-authored" + suffix,
        specimen(suffix, "自制标题", ["定义值为29。", "限制条件为零下无效。"]),
    )
    assert parts[0].heading
    for flags in ({}, {"aggregate_short": True}, {"pair_headings": True}):
        chunks = make_chunks(parts, **flags)
        text = " ".join(c["text"] for c in chunks)
        assert all(word in text for word in ("自制标题", "29", "零下无效"))
        if suffix == ".pptx":
            assert {c["slide_no"] for c in chunks} == {1}
