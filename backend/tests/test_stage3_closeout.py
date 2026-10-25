from app.api.chat import build_prompt
from app.core.config import Settings, get_settings
from app.db.models import Chunk, Document
from app.retrieval import Candidate, RetrievalResult, search, select_evidence


def row(key, text, section, score):
    return Candidate(
        Chunk(id=key, text=text, section_path=section),
        Document(id="doc", filename="test.md"),
        rerank_score=score,
    )


def test_soft_section_limit_considers_other_sections_before_novel_backfill():
    rows = [
        row("a", "timeout equals eleven seconds", "one", 3),
        row("duplicate", "timeout equals eleven seconds", "one", 2.5),
        row("b", "retry attempts limited to four", "one", 2),
        row("c", "cache eviction follows FIFO order", "two", 1),
        row("weak", "irrelevant third rule", "three", -9),
    ]
    settings = Settings(
        _env_file=None,
        retrieval_strategy="rerank",
        retrieval_rerank_gate="score",
        retrieval_rerank_min_score=-4,
        retrieval_diversify=True,
        retrieval_section_limit=1,
        retrieval_top_k=3,
        retrieval_section_backfill=True,
    )
    result = RetrievalResult("rerank", ranked=rows)
    select_evidence(result, settings)
    assert [x.chunk.id for x in result.evidence] == ["a", "c", "b"]
    assert result.rejected["duplicate"] == "section_redundant"
    assert result.rejected["weak"] == "rerank_threshold"
    settings.retrieval_section_backfill = False
    select_evidence(result, settings)
    assert [x.chunk.id for x in result.evidence] == ["a", "c"]


def test_backfill_cannot_exceed_context_budget_or_admit_below_gate():
    rows = [
        row("a", "one fact", "one", 2),
        row("b", "another three different facts", "one", 1),
    ]
    settings = Settings(
        _env_file=None,
        retrieval_rerank_gate="score",
        retrieval_section_backfill=True,
        retrieval_diversify=True,
        retrieval_section_limit=1,
        retrieval_max_chars=10,
    )
    result = RetrievalResult("rerank", ranked=rows)
    select_evidence(result, settings)
    assert [x.chunk.id for x in result.evidence] == ["a"]
    assert result.rejected["b"] == "context_budget"


def test_grounded_prompt_keeps_sources_and_question_unmodified(monkeypatch):
    monkeypatch.setattr(get_settings(), "retrieval_grounded_answers", True)
    chunk = Chunk(id="c", text="timeout: 19 seconds", section_path="config")
    doc = Document(id="d", filename="rules.md")
    messages = build_prompt([(chunk, doc)], "What timeout is recorded?")
    assert "不要求资料先给出通用完整定义" in messages[0]["content"]
    assert "<source>\ntimeout: 19 seconds\n</source>" in messages[1]["content"]
    assert "What timeout is recorded?" in messages[1]["content"]


def test_requested_identifier_can_reserve_third_same_section_fact_without_weak_gate():
    rows = [
        row("a", "timeout_seconds equals 11", "one", 3),
        row("b", "retry_count equals 4", "one", 2),
        row("c", "retention_days equals 29", "one", 1),
        row("weak", "other_rule equals 9", "one", -9),
    ]
    settings = Settings(
        _env_file=None,
        retrieval_rerank_gate="score",
        retrieval_diversify=True,
        retrieval_section_limit=2,
        retrieval_section_backfill=True,
        retrieval_top_k=3,
    )
    result = RetrievalResult("rerank", ranked=rows)
    select_evidence(result, settings, "timeout_seconds retry_count retention_days")
    assert [x.chunk.id for x in result.evidence] == ["a", "b", "c"]


def test_exact_identifier_assignment_beats_generic_branch_rank():
    rows = [
        row("a", "visibility_rule before commit", "one", 1),
        row("b", "retention_rule 29 days", "one", 1),
    ]

    class Store:
        def dense(self, vector, limit):
            return rows

        def lexical(self, query, limit):
            return rows

    class Scorer:
        def score(self, query, texts):
            for text in texts:
                assert query.split()[0] in text
            return [1.0] * len(texts)

    settings = Settings(
        _env_file=None,
        retrieval_strategy="rerank",
        retrieval_facet_coverage=True,
        retrieval_rerank_gate="score",
        retrieval_balanced_candidates=True,
        retrieval_rerank_candidates=2,
    )
    result = search(
        None,
        None,
        "visibility_rule rule? retention_rule rule?",
        settings,
        store=Store(),
        embed=lambda _: [[1]],
        reranker=Scorer(),
    )
    assert len(result.evidence) == 2
