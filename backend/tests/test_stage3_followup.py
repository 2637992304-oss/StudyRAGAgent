import pytest

from app.core.config import Settings
from app.db.models import Chunk, Document
from app.query_plan import normalize_query, query_facets
from app.retrieval import Candidate, RetrievalResult, search, select_evidence
from scripts.compare_stage3_embeddings import calibration


def test_normalization_requires_database_context_and_both_halves():
    assert "atomicity" in normalize_query("数据库事务要么全成要么全撤")
    assert "atomicity" in normalize_query("事务要么全做要么全不做")
    assert normalize_query("考试要么全成要么全撤") == "考试要么全成要么全撤"
    assert normalize_query("数据库如何全部成功") == "数据库如何全部成功"


def test_facets_are_bounded_explicit_questions_not_inferred_answers():
    assert query_facets("解释原子性；解释持久性") == ["解释原子性", "解释持久性"]
    assert query_facets("what is atomicity?") == ["what is atomicity?"]
    text = "aaaa;bbbb;cccc;dddd"
    assert query_facets(text) == [text]


def candidate(key, score, facet):
    return Candidate(
        Chunk(id=key, text="body", section_path=key),
        Document(id="d"),
        rerank_score=score,
        facet=facet,
    )


def test_facet_reservation_keeps_gate_and_does_not_admit_weak_evidence():
    rows = [
        candidate("a", 3, "first"),
        candidate("b", 2, "first"),
        candidate("weak", -8, "second"),
        candidate("c", 0, "second"),
    ]
    result = RetrievalResult("rerank", ranked=rows, facets=["first", "second"])
    settings = Settings(
        _env_file=None,
        retrieval_facet_coverage=True,
        retrieval_rerank_gate="score",
        retrieval_top_k=2,
        retrieval_rerank_min_score=-4,
        retrieval_diversify=True,
    )
    select_evidence(result, settings)
    assert [x.chunk.id for x in result.evidence] == ["a", "c"]
    assert "weak" not in {x.chunk.id for x in result.evidence}


def test_multi_question_rerank_budget_is_shared_not_multiplied():
    class Store:
        def dense(self, vector, limit):
            return [candidate(str(i), 0, None) for i in range(12)]

        def lexical(self, query, limit):
            return [candidate(str(i), 0, None) for i in reversed(range(12))]

    class Scorer:
        calls = []

        def score(self, query, texts):
            self.calls.append((query, len(texts)))
            return [1.0] * len(texts)

    scorer = Scorer()
    settings = Settings(
        _env_file=None,
        retrieval_strategy="rerank",
        retrieval_facet_coverage=True,
        retrieval_balanced_candidates=True,
        retrieval_rerank_candidates=6,
    )
    result = search(
        None,
        None,
        "解释原子性；解释持久性",
        settings,
        store=Store(),
        reranker=scorer,
        embed=lambda _: [[1]],
    )
    assert sum(n for _, n in scorer.calls) == result.rerank_count == 6
    assert len(scorer.calls) == 2


def test_embedding_calibration_excludes_boundary_and_rejects_holdout():
    rows = [
        dict(
            split="dev",
            answerable=True,
            expected=["a"],
            categories=[],
            candidates=[dict(distance=0.3, units=["a"])],
        ),
        dict(
            split="dev",
            answerable=False,
            expected=[],
            categories=["no_answer"],
            candidates=[dict(distance=0.5, units=[])],
        ),
        dict(
            split="dev",
            answerable=False,
            expected=[],
            categories=["boundary"],
            candidates=[],
        ),
    ]
    selected, _ = calibration(rows)
    assert selected["threshold"] == 0.3 and selected["false_accept"] == 0
    rows[0]["split"] = "holdout"
    with pytest.raises(ValueError, match="Only development"):
        calibration(rows)
