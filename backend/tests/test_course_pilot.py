"""Layout fidelity, pure-score cache invalidation and live SQL authorization."""

import math

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import ChatSession, Chunk, Document
from app.db.session import get_engine
from app.ingestion.parser import Part, make_chunks
from app.rerank_cache import ScoreCache, score_cache
from app.retrieval import (
    Candidate,
    RetrievalResult,
    relation_pool,
    scoped_scores,
    search,
)


def test_pdf_layout_keeps_caption_rows_page_and_default_compatibility():
    part = Part("ID    value\n001    10\n002    20\n\nFigure B.1 Data table.", page=7)
    original = make_chunks([part])
    assert original[0]["text"] == "ID value 001 10 002 20 Figure B.1 Data table."
    revised = make_chunks([part], pdf_layout=True)
    assert len(revised) == 1
    assert revised[0]["text"] == "Figure B.1 Data table.\nID value\n001 10\n002 20"
    assert revised[0]["page_start"] == revised[0]["page_end"] == 7
    other = Part("the next page", page=8)
    chunks = make_chunks([part, other], pdf_layout=True)
    assert all(c["page_start"] == c["page_end"] for c in chunks)
    assert chunks[-1]["page_start"] == 8


def test_long_table_windows_repeat_source_header_without_splitting_rows():
    rows = [f"ID{i:03} {i:06}" for i in range(30)]
    part = Part("ID value\n" + "\n".join(rows) + "\n\nFigure B.1 Table.", page=2)
    chunks = make_chunks([part], 120, 20, pdf_layout=True)
    assert len(chunks) > 1
    assert all(c["text"].startswith("Figure B.1 Table.\nID value\n") for c in chunks)
    assert all(len(c["text"]) <= 120 for c in chunks)
    observed = {line for c in chunks for line in c["text"].splitlines()[2:]}
    assert observed == set(rows)
    assert make_chunks([Part("one\ntwo")], pdf_layout=True) == make_chunks(
        [Part("one\ntwo")]
    )


def test_pdf_layout_handles_oversized_lines_and_small_limits():
    for limit in [1, 20, 90]:
        chunks = make_chunks([Part("X" * 200, page=1)], limit, 0, pdf_layout=True)
        assert chunks and all(0 < len(c["text"]) <= limit for c in chunks)
        assert "".join(c["text"] for c in chunks) == "X" * 200


def test_numeric_cache_ttl_lru_zero_and_nonfinite():
    now = [0.0]
    cache = ScoreCache(lambda: now[0])
    cache.put("a", 0.0, 2)
    cache.put("b", -1, 2)
    assert cache.get("a", 10) == 0
    cache.put("c", 1, 2)
    assert cache.get("b", 10) is None
    now[0] = 10
    assert cache.get("a", 10) is None
    with pytest.raises(ValueError):
        cache.put("invalid", math.nan, 2)


def test_relation_reservation_uses_own_caption_not_foreign_key_mentions():
    def item(key, text):
        return Candidate(Chunk(id=key, text=text), Document(id="doc"))

    unrelated = item("other", "create table prereq\nforeign key references course")
    data = item("data", "Figure A.5 The course relation.\ncourse_id title\nC1 X")
    ddl = item("ddl", "create table course\n(course_id varchar(8))")
    fallback = [unrelated, ddl]
    settings = Settings(_env_file=None)
    result = relation_pool(
        "course样例有多少行", [unrelated, ddl, data], fallback, 4, settings
    )
    assert [x.chunk.id for x in result[:2]] == ["data", "ddl"]
    assert len(result) <= 4
    assert (
        relation_pool("absent", [unrelated, ddl, data], fallback, 2, settings)
        == fallback
    )


def test_facet_context_keeps_parent_entities_in_actual_scoring_query():
    from app.retrieval import LIGHT_RERANKER

    class Store:
        def dense(self, vector, limit):
            return [
                Candidate(
                    Chunk(id="x", text="Zhang student ID 42"),
                    Document(id="doc"),
                    distance=0.1,
                )
            ]

        def lexical(self, query, limit):
            return self.dense(None, limit)

    scorer = Scorer()
    query = "Zhang的成绩是什么？请确认学生ID"
    settings = Settings(
        _env_file=None,
        retrieval_strategy="rerank",
        retrieval_facet_coverage=True,
        retrieval_facet_context=True,
        retrieval_rerank_gate="score",
        reranker_model=LIGHT_RERANKER,
    )
    result = search(
        None,
        ChatSession(owner_id="a", course_id="c"),
        query,
        settings,
        store=Store(),
        embed=lambda _: [[1.0]],
        reranker=scorer,
    )
    assert result.facet_scoring_queries
    assert all(
        value.startswith(query + "\nFocus: ")
        for value in result.facet_scoring_queries.values()
    )
    assert all("Zhang" in q for q, _ in scorer.calls)


class Scorer:
    def __init__(self):
        self.calls = []
        self.identity = "weights-session-1"

    def cache_identity(self):
        return self.identity

    def score(self, query, texts):
        self.calls.append((query, list(texts)))
        return [float(len(text)) for text in texts]


def test_cache_recomputes_whole_quantized_group_on_partial_eviction_or_change():
    score_cache.clear()
    settings = Settings(_env_file=None, rerank_score_cache=True)
    scope = ChatSession(owner_id="a", course_id="c", document_ids=["doc"])
    scorer = Scorer()
    items = [
        Candidate(Chunk(id=str(i), text=f"row {i}"), Document(id="doc"))
        for i in range(2)
    ]
    texts = [item.chunk.text for item in items]

    def call():
        result = RetrievalResult("rerank")
        values = scoped_scores(scorer, "q", items, texts, scope, settings, result)
        return values, result

    call()
    assert call()[1].rerank_cache_hits == 2
    with score_cache.lock:
        score_cache.values.popitem(last=False)
    values, result = call()
    assert result.rerank_cache_hits == 0 and result.rerank_cache_misses == 2
    assert scorer.calls[-1][1] == texts  # Never score a miss-only subset.
    texts[0] = "changed scoring window"
    assert call()[1].rerank_cache_misses == 2
    settings.reranker_batch_size = 2
    assert call()[1].rerank_cache_misses == 2
    items.reverse()
    texts.reverse()
    assert call()[1].rerank_cache_misses == 2
    score_cache.clear()


def test_scoped_cache_keys_include_all_input_and_provenance():
    score_cache.clear()
    settings = Settings(_env_file=None, rerank_score_cache=True)
    chunk = Chunk(
        id="c", text="Alpha 42", parser_version="p1", embedding_model="embed1"
    )
    item = Candidate(chunk, Document(id="doc"))
    scope = ChatSession(owner_id="owner", course_id="course", document_ids=["doc"])
    scorer = Scorer()

    def call(query="q", text=None):
        result = RetrievalResult("rerank")
        value = scoped_scores(
            scorer, query, [item], [text or chunk.text], scope, settings, result
        )
        return value, result.rerank_cache_hits

    assert call() == ([8.0], 0)
    assert call() == ([8.0], 1)
    assert call("different")[1] == 0
    assert call(text="Alpha")[1] == 0
    chunk.text = "Alpha 43"  # Same ID/hash column cannot hide changed live text.
    assert call()[1] == 0
    chunk.parser_version = "p2"
    assert call()[1] == 0
    chunk.embedding_model = "embed2"
    assert call()[1] == 0
    scope.owner_id = "other"
    assert call()[1] == 0
    scope.course_id = "different-course"
    assert call()[1] == 0
    scope.document_ids = None
    assert call()[1] == 0
    scorer.identity = "weights-session-2"
    assert call()[1] == 0
    score_cache.clear()


def test_score_cache_never_bypasses_live_pgvector_scope(monkeypatch, tmp_path):
    from app.evaluation import fixtures as module

    vector = [1.0] + [0.0] * 511
    monkeypatch.setattr(module, "embed_texts", lambda texts: [vector for _ in texts])
    source = tmp_path / "sources"
    source.mkdir()
    (source / "test.md").write_text("alpha 42", encoding="utf-8")
    dataset = dict(
        documents=[
            dict(
                key="allowed", file="test.md", owner="a", course="main", status="ready"
            ),
            dict(
                key="foreign", file="test.md", owner="b", course="main", status="ready"
            ),
        ]
    )
    settings = get_settings().model_copy(deep=True)
    settings.retrieval_strategy = "rerank"
    settings.retrieval_rerank_gate = "score"
    settings.retrieval_rerank_min_score = -4
    settings.rerank_score_cache = True
    score_cache.clear()
    scorer = Scorer()
    with module.fixtures(dataset, tmp_path, 900, 150, tmp_path / "dataset.json") as (
        owners,
        courses,
        docs,
    ):
        scope = ChatSession(
            owner_id=owners["a"],
            course_id=courses["a", "main"],
            document_ids=[docs["allowed"]],
        )
        with Session(get_engine()) as db:

            def run():
                return search(
                    db,
                    scope,
                    "alpha",
                    settings,
                    embed=lambda _: [vector],
                    reranker=scorer,
                )

            first = run()
            assert first.rerank_cache_misses == 1
            assert run().rerank_cache_hits == 1
            chunk = db.scalar(select(Chunk).where(Chunk.document_id == docs["allowed"]))
            chunk.text = "alpha 43"
            db.commit()
            assert run().rerank_cache_misses == 1
            db.get(Document, docs["allowed"]).status = "deleted"
            db.commit()
            assert not run().evidence
            assert len(scorer.calls) == 2
            scope.document_ids = [docs["foreign"]]
            assert not run().evidence
            scope.document_ids = []
            assert not run().evidence
            scope.document_ids = [docs["allowed"]]
            db.get(Document, docs["allowed"]).status = "ready"
            db.delete(chunk)
            db.commit()
            assert not run().evidence
            assert len(scorer.calls) == 2
    score_cache.clear()
