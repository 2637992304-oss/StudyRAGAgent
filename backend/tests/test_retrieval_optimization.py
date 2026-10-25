from app.core.config import Settings
from app.db.models import Chunk, Document
from app.retrieval import (
    Candidate,
    FastEmbedReranker,
    RetrievalResult,
    deduplicate,
    passage_window,
    select_evidence,
)


def item(key, text="relevant evidence", section="chapter", doc="doc", **kwargs):
    return Candidate(
        Chunk(id=key, text=text, section_path=section), Document(id=doc), **kwargs
    )


def test_score_gate_does_not_veto_good_reranking_with_dense_distance():
    c = item("a", distance=0.9, keyword_score=0.2, rerank_score=3)
    result = RetrievalResult("rerank", ranked=[c])
    settings = Settings(_env_file=None, retrieval_rerank_gate="legacy")
    select_evidence(result, settings)
    assert not result.evidence
    settings.retrieval_rerank_gate = "score"
    select_evidence(result, settings)
    assert result.evidence == [c]
    c.rerank_score = -8
    select_evidence(result, settings)
    assert not result.evidence and result.rejected["a"] == "rerank_threshold"


def test_structural_coverage_guard_is_explicit_and_does_not_check_single_fact():
    c = item("a", rerank_score=4)
    result = RetrievalResult("rerank", ranked=[c])
    settings = Settings(_env_file=None, retrieval_rerank_gate="coverage")
    select_evidence(result, settings, query="分别说明TCP和UDP")
    assert not result.evidence and result.rejected["a"] == "coverage_guard"
    result.ranked.append(item("b", section="other", rerank_score=3))
    select_evidence(result, settings, query="分别说明TCP和UDP")
    assert len(result.evidence) == 2
    result.ranked = [c]
    select_evidence(result, settings, query="什么是TCP？")
    assert result.evidence == [c]


def test_exact_dedup_keeps_other_sources_numbers_and_negations():
    a = item("a", text="sample 3 rows")
    rows, removed = deduplicate(
        [
            a,
            item("b", text="sample   3 rows"),
            item("c", text="sample 4 rows"),
            item("d", text="sample 3 rows", doc="other"),
            item("e", text="not sample 3 rows"),
        ]
    )
    assert [row.chunk.id for row in rows] == ["a", "c", "d", "e"]
    assert removed == {"b": "a"}


def test_length_bucketing_restores_scores_and_keeps_text(monkeypatch):
    from app import retrieval

    seen = []

    class Model:
        def token_count(self, pairs):
            return len(pairs[0][1])

        def rerank(self, query, documents, batch_size):
            seen.extend(documents)
            assert batch_size == 4
            return [float(len(doc)) for doc in documents]

    monkeypatch.setattr(retrieval, "cross_encoder", lambda *args: Model())
    values = ["longer document", "a", "middle"]
    assert FastEmbedReranker(Settings(_env_file=None)).score("query", values) == [
        15.0,
        1.0,
        6.0,
    ]
    assert seen == ["a", "middle", "longer document"]


def test_scoring_window_keeps_prefix_and_query_region_without_rewriting_source():
    source = (
        "HEADER course_id title credits "
        + "无关背景。" * 100
        + " UNIQUE_MARKER 样本包含3条数据。"
    )
    window = passage_window("UNIQUE_MARKER 样本数据", source, 200)
    assert len(window) <= 200 and window.startswith("HEADER")
    assert "UNIQUE_MARKER" in window
    assert passage_window("query", source, 0) == source


def test_budget_and_top_k_rejections_are_distinct():
    rows = [item("a", text="x" * 100, rerank_score=4), item("b", rerank_score=3)]
    result = RetrievalResult("rerank", ranked=rows)
    settings = Settings(
        _env_file=None,
        retrieval_top_k=1,
        retrieval_max_chars=10,
        retrieval_rerank_gate="score",
    )
    select_evidence(result, settings)
    assert result.rejected == {"a": "context_budget", "b": "outside_top_k"}


def test_adaptive_gate_recovers_strong_evidence_but_rejects_weak_matches():
    rows = [
        item("strong", distance=0.9, keyword_score=0.2, rerank_score=3),
        item("medium", distance=0.9, keyword_score=0.2, rerank_score=-2),
        item("supported", distance=0.2, keyword_score=0.2, rerank_score=-2),
        item("weak", distance=0.2, keyword_score=5, rerank_score=-9),
    ]
    settings = Settings(
        _env_file=None,
        retrieval_rerank_gate="adaptive",
        retrieval_max_distance=0.99,
        retrieval_keyword_min_score=3,
        retrieval_rerank_min_score=-6,
    )
    result = RetrievalResult("rerank", ranked=rows)
    select_evidence(result, settings)
    assert [c.chunk.id for c in result.evidence] == ["strong", "supported"]


def test_embedding_batch_is_bounded(monkeypatch):
    from app.providers import embeddings

    calls = []

    class Vector:
        def tolist(self):
            return [1.0] + [0.0] * 511

    class Model:
        def embed(self, texts, batch_size):
            calls.append(batch_size)
            return [Vector() for _ in texts]

    monkeypatch.setattr(embeddings, "_model", lambda name: Model())
    monkeypatch.setattr(
        embeddings,
        "get_settings",
        lambda: Settings(_env_file=None, embedding_batch_size=8),
    )
    assert len(embeddings.embed_texts(["a", "b"])) == 2
    assert calls == [8]


def test_custom_multilingual_registration_is_once_and_uses_official_int8(monkeypatch):
    from fastembed.rerank import cross_encoder as provider

    from app import retrieval

    registered, calls = [], []

    class Encoder:
        @classmethod
        def list_supported_models(cls):
            return registered

        @classmethod
        def add_custom_model(cls, **kwargs):
            calls.append(kwargs)
            registered.append({"model": kwargs["model"]})

        def __init__(self, **kwargs):
            self.settings = kwargs

    monkeypatch.setattr(provider, "TextCrossEncoder", Encoder)
    retrieval.cross_encoder.cache_clear()

    first = retrieval.cross_encoder(retrieval.LIGHT_RERANKER, "cache", True, 2)
    retrieval.cross_encoder(retrieval.LIGHT_RERANKER, "cache", True, 4)
    assert len(calls) == 1
    assert calls[0]["model_file"] == "onnx/model_quint8_avx2.onnx"
    assert first.settings["local_files_only"] is True
    retrieval.cross_encoder.cache_clear()


def test_evaluation_cache_changes_with_source_and_is_not_for_user_storage(tmp_path):
    from app.evaluation.fixtures import vector_cache_path

    path = tmp_path / ".eval-cache" / "dataset.json"
    before = vector_cache_path([{"text": "three rows"}], path)
    after = vector_cache_path([{"text": "four rows"}], path)
    assert before != after
    assert (
        vector_cache_path([{"text": "three rows"}], tmp_path / "private.json") is None
    )
