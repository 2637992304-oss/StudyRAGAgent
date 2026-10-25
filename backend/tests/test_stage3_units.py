from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.db.models import ChatSession, Chunk, Document
from app.evaluation.fixtures import DATA, load_dataset
from app.evaluation.metrics import (
    evidence_metrics,
    latency,
    matched_units,
    refusal_observation,
    summarize,
)
from app.evaluation.reporting import write_review
from app.ingestion.parser import Part, make_chunks, parse_document
from app.retrieval import Candidate, bm25, rrf, search, tokens


def candidate(key, text="主键 primary_key 唯一标识记录", **kwargs):
    return Candidate(Chunk(id=key, text=text), Document(id="doc"), **kwargs)


def test_tokenization_preserves_identifiers_and_chinese_bigrams():
    result = tokens("主键 course_id B_tree 512")
    assert {"主", "键", "主键", "course_id", "b_tree", "512"} <= set(result)
    scores = bm25("course_id 主键", ["主键 course_id", "主修课程说明", ""])
    assert scores[0] > scores[1] >= scores[2] == 0


def test_rrf_uses_rank_not_incompatible_raw_scores_and_deduplicates():
    a = candidate("a", distance=0.2)
    b = candidate("b", keyword_score=1000)
    a2 = candidate("a", keyword_score=2)
    result = rrf([[a, a], [b, a2]], 60)
    assert [item.chunk.id for item in result] == ["a", "b"]
    assert result[0].fusion_score == pytest.approx(1 / 61 + 1 / 62)
    assert result[0].distance == 0.2 and result[0].keyword_score == 2


class Store:
    def dense(self, vector, limit):
        return [
            candidate("a", distance=0.8),
            candidate("b", text="外键 foreign_key 引用别的表", distance=0.2),
        ][:limit]

    def lexical(self, query, limit):
        return [candidate("a", keyword_score=5)][:limit]


def test_strategy_specific_thresholds_rerank_and_context_budget():
    settings = Settings(_env_file=None, retrieval_strategy="hybrid")
    args = (None, ChatSession(), "主键", settings)
    result = search(*args, embed=lambda _: [[1]], store=Store())
    assert {item.chunk.id for item in result.evidence} == {"a", "b"}
    settings.retrieval_strategy = "dense"
    result = search(*args, embed=lambda _: [[1]], store=Store())
    assert [item.chunk.id for item in result.evidence] == ["b"]
    settings.retrieval_strategy = "rerank"
    seen = []

    class Ranker:
        def score(self, query, texts):
            seen.extend(texts)
            return [-2, 3]

    result = search(*args, embed=lambda _: [[1]], store=Store(), reranker=Ranker())
    assert len(seen) == 2
    assert [item.chunk.id for item in result.evidence] == ["b"]
    settings.retrieval_max_chars = 1
    result = search(*args, embed=lambda _: [[1]], store=Store(), reranker=Ranker())
    assert not result.evidence and "context_budget" in result.rejected.values()


def test_failed_reranker_never_silently_uses_dense_scores():
    class BrokenRanker:
        def score(self, *_):
            raise RuntimeError("unavailable")

    with pytest.raises(RuntimeError):
        search(
            None,
            ChatSession(),
            "query",
            Settings(_env_file=None, retrieval_strategy="rerank"),
            embed=lambda _: [[1]],
            store=Store(),
            reranker=BrokenRanker(),
        )


def test_evidence_units_do_not_double_count_overlap_and_negative_denominators():
    result = evidence_metrics({"a", "b"}, [{"a"}, {"a"}, {"b"}], 2)
    assert result == {"recall": 0.5, "hit": 1, "mrr": 1, "complete": 0}
    assert evidence_metrics(set(), [{"a"}], 5)["recall"] is None
    assert evidence_metrics({"a"}, [set(), {"a"}], 5)["mrr"] == 0.5
    assert latency([])["p95"] is None
    assert latency([1, 2, 3])["p95"] == 3
    summary = summarize([{"status": "error", "scope_violations": 1}])
    assert summary["errors"] == 1 and summary["scope_violations"] == 1
    assert summary["recall"] is None and summary["cost"] is None


def test_anchor_remapping_requires_document_and_section_and_complete_quote():
    units = [{"unit": "x", "document": "d", "section": "s", "quote": "主键 唯一"}]
    c = SimpleNamespace(chunk=Chunk(text="主键唯一", section_path="s"))
    assert matched_units(c, units, "d") == {"x"}
    assert matched_units(c, units, "other") == set()
    c.chunk.section_path = "other"
    assert matched_units(c, units, "d") == set()


def test_chunk_settings_preserve_page_and_overlap_and_validate():
    parts = [Part(text="0123456789abcdefghij", page=7)]
    chunks = make_chunks(parts, 10, 3)
    assert chunks[0]["text"][-3:] == chunks[1]["text"][:3]
    assert all(c["page_start"] == c["page_end"] == 7 for c in chunks)
    with pytest.raises(ValueError):
        make_chunks(parts, 10, 10)


def test_dataset_coverage_and_references():
    data = load_dataset()
    cases = data["cases"]
    assert len(cases) >= 50 and len({c["id"] for c in cases}) == len(cases)
    for category in [
        "synonym",
        "term",
        "exercise",
        "multi_evidence",
        "no_answer",
        "boundary",
    ]:
        assert sum(category in c["categories"] for c in cases) >= 5
    assert {c["split"] for c in cases} == {"dev", "holdout"}
    for case in cases:
        assert case["human_review"] == "pending"
        if "multi_evidence" in case["categories"]:
            assert len(case["expected"]) >= 2


def test_all_baseline_reference_anchors_exist_in_parsed_sources():
    data = load_dataset()
    documents = {
        spec["key"]: make_chunks(
            parse_document(
                spec["file"], (DATA.parent / "sources" / spec["file"]).read_bytes()
            )
        )
        for spec in data["documents"]
    }
    for case in data["cases"]:
        for unit in case["expected"]:
            assert any(
                matched_units(
                    SimpleNamespace(chunk=Chunk(**chunk)), [unit], unit["document"]
                )
                for chunk in documents[unit["document"]]
            ), (case["id"], unit)


def test_explicit_refusal_and_partial_refusal_are_distinct_from_gate():
    assert refusal_observation("不知道。资料没有提供日期。", "completed")["refused"]
    assert refusal_observation("I don't know.", "completed")["refused"]
    assert (
        refusal_observation("当前所选资料中没有足够依据。", "no_evidence")[
            "refusal_basis"
        ]
        == "server_gate"
    )
    partial = refusal_observation("UDP 不保证顺序。事务定义的证据不足。", "completed")
    assert partial["partial_refusal"] and not partial["refused"]
    assert not refusal_observation("样本表有 3 条数据 [1]。", "completed")["refused"]


def test_audit_export_preserves_manual_decisions(tmp_path):
    import csv

    path = tmp_path / "review.csv"
    row = {"id": "q001", "strategy": "dense", "question": "Q", "reference_answer": "A"}
    write_review([row], path)
    with path.open(encoding="utf-8-sig", newline="") as file:
        records = list(csv.DictReader(file))
    records[0]["reviewer"] = "human reviewer"
    records[0]["factually_correct"] = "yes"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    write_review([row], path)
    with path.open(encoding="utf-8-sig", newline="") as file:
        record = next(csv.DictReader(file))
    assert record["reviewer"] == "human reviewer"
    assert record["factually_correct"] == "yes"
