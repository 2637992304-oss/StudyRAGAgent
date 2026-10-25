import pytest

from app.core.config import Settings
from app.db.models import Chunk, Document
from app.evaluation.metrics import quality_summary
from app.evaluation.trace import retrieval_trace
from app.ingestion.parser import Part, make_chunks
from app.retrieval import (
    Candidate,
    RetrievalResult,
    balanced_pool,
    expanded_query,
    is_heading,
    select_evidence,
)
from scripts.judge_stage3 import validate_review


def item(key, text, section="s", score=2):
    return Candidate(
        Chunk(id=key, text=text, section_path=section, chunk_index=0),
        Document(id="d"),
        rerank_score=score,
    )


def test_pairing_keeps_body_location_and_does_not_attach_unrelated_heading():
    parts = [
        Part("# A", "A"),
        Part("原子性定义", "A"),
        Part("# B", "B"),
        Part("other page", "C", page=2),
    ]
    chunks = make_chunks(parts, pair_headings=True)
    assert chunks[0]["text"] == "# A 原子性定义"
    assert chunks[0]["section_path"] == "A"
    assert not is_heading(Chunk(**chunks[0]))
    assert chunks[1]["text"] == "other page" and chunks[1]["page_start"] == 2
    assert len(make_chunks(parts)) == 4  # Existing indexes/parser calls unchanged.


def test_alias_boundaries_and_configurable_terms_preserve_original_query():
    settings = Settings(_env_file=None)
    assert (
        expanded_query("ACID durability指什么", settings)
        == "ACID durability指什么 持久性"
    )
    assert expanded_query("superdurability_v2", settings) == "superdurability_v2"
    assert expanded_query("持久性 durability", settings) == "持久性 durability"
    settings.retrieval_term_aliases = {"index": ["索引"]}
    assert expanded_query("index_number", settings) == "index_number"


def test_branch_budget_reserves_expansion_without_duplicate_ids():
    rows = [item(str(i), "正文") for i in range(5)]
    selected = balanced_pool([rows[:4], [rows[0]], [rows[4]]], 3)
    assert [x.chunk.id for x in selected] == ["0", "4", "1"]


def test_diversity_backfills_eligible_body_without_relaxing_score_gate():
    rows = [
        item("title", "# s"),
        item("a", "事实1"),
        item("b", "事实2"),
        item("weak", "无关内容", "other", -8),
        item("c", "另一证据", "other"),
    ]
    result = RetrievalResult("rerank", ranked=rows)
    settings = Settings(
        _env_file=None,
        retrieval_top_k=2,
        retrieval_pair_headings=True,
        retrieval_diversify=True,
        retrieval_section_limit=1,
        retrieval_rerank_gate="score",
        retrieval_rerank_min_score=0,
    )
    select_evidence(result, settings)
    assert [x.chunk.id for x in result.evidence] == ["a", "c"]
    assert result.rejected["title"] == "heading_without_body"
    assert result.rejected["b"] == "section_diversity"
    assert result.rejected["weak"] == "rerank_threshold"


def test_trace_distinguishes_candidate_cutoff_from_final_gate_and_preserves_pool():
    a, b = item("a", "fact A"), item("b", "fact B", score=-7)
    expected = [
        {"unit": "a", "document": "doc", "quote": "fact A"},
        {"unit": "b", "document": "doc", "quote": "fact B"},
        {"unit": "c", "document": "doc", "quote": "fact C"},
    ]
    result = RetrievalResult(
        "rerank",
        recalled=[a, b],
        rerank_input=[b],
        ranked=[b],
        branches={"lexical": [a, b]},
        rejected={"b": "rerank_threshold"},
    )
    trace = retrieval_trace(result, {"d": "doc"}, expected, {"a", "b"}, redact=True)
    assert [x["stage"] for x in trace["evidence_losses"]] == [
        "rerank_candidate_cutoff",
        "rerank_threshold",
        "parse_or_chunk_missing",
    ]
    assert len(trace["candidates"]) == 2 and all(
        not x["text"] for x in trace["candidates"]
    )


def test_quality_unknown_and_partial_do_not_inflate_complete_answer_rate():
    row = {
        "answerable": True,
        "id": "a",
        "judge_status": "ok",
        "quality": {
            "task_outcome": "partial",
            "fact_consistency": "supported",
            "claims": [],
            "subquestions": [],
        },
    }
    report = quality_summary([row, {"answerable": True, "judge_status": "error"}])
    assert report["complete_answer_rate_all_positive"] == 0
    assert report["partial_answer_rate_judged"] == 1
    assert report["unknown_or_failed_positive_n"] == 1


def test_judge_cannot_invent_quote_or_pass_missing_subquestion():
    row = {
        "answer": "fact [1]",
        "actual_context": [{"label": 1, "text": "actual fact"}],
    }
    case = {"answerable": True, "expected": [{"unit": "a"}]}
    review = dict(
        task_outcome="complete",
        fact_consistency="supported",
        claims=[
            dict(
                claim="x",
                citation_labels=[1],
                support="supported",
                source_quote="invented",
            )
        ],
        subquestions=[dict(unit="a", outcome="complete", reason="x")],
        reason="x",
    )
    with pytest.raises(ValueError, match="quote"):
        validate_review(review, row, case)
    review["claims"][0]["source_quote"] = "actual fact"
    review["subquestions"][0]["outcome"] = "missing"
    with pytest.raises(ValueError, match="Incomplete"):
        validate_review(review, row, case)


def test_actual_prompt_observer_is_context_local_and_uses_real_prompt():
    from app.api.chat import build_prompt, prompt_observer

    observed = []
    token = prompt_observer.set(
        lambda evidence, messages: observed.append((evidence, messages))
    )
    chunk = Chunk(id="source", text="original body", section_path="s")
    doc = Document(filename="source.md")
    try:
        messages = build_prompt([(chunk, doc)], "question")
    finally:
        prompt_observer.reset(token)
    assert observed[0][1] is messages and "original body" in messages[1]["content"]
    build_prompt([(chunk, doc)], "another question")
    assert len(observed) == 1


def test_new_fixture_splits_are_document_disjoint_and_reference_anchors_map():
    from pathlib import Path
    from types import SimpleNamespace

    from app.evaluation.fixtures import load_dataset
    from app.evaluation.metrics import matched_units
    from app.ingestion.parser import parse_document

    path = (
        Path(__file__).resolve().parents[2] / "docs/stage3_completion_data/dataset.json"
    )
    data = load_dataset(path)
    assert len(data["cases"]) == 50
    assert len({x["id"] for x in data["cases"]}) == 50
    visible = {
        split: {
            doc
            for c in data["cases"]
            if c["split"] == split and "boundary" not in c["categories"]
            for doc in c["scope"]["documents"]
        }
        for split in ["dev", "holdout"]
    }
    assert not visible["dev"] & visible["holdout"]
    chunks = {
        d["key"]: make_chunks(
            parse_document(
                d["file"], (path.parent / "sources" / d["file"]).read_bytes()
            ),
            pair_headings=True,
        )
        for d in data["documents"]
    }
    for case in data["cases"]:
        for unit in case["expected"]:
            assert unit["document"] in case["scope"]["documents"]
            assert any(
                matched_units(
                    SimpleNamespace(chunk=Chunk(**c)), [unit], unit["document"]
                )
                for c in chunks[unit["document"]]
            )


def test_mixed_pdf_ocr_runs_for_image_page_even_when_other_page_has_text(monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from app.ingestion.parser import parse_document

    pytest.importorskip("pypdfium2")
    calls = []

    class OCR:
        def __call__(self, image):
            calls.append(image.size)
            return SimpleNamespace(txts=["unit OCR text"])

    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=OCR))
    path = (
        Path(__file__).resolve().parents[2]
        / "docs/stage3_completion_layout/sources/mixed_scan.pdf"
    )
    parts = parse_document(path.name, path.read_bytes())
    assert len(calls) == 1
    assert [p.page for p in parts] == [1, 2]
    assert "MIXED-2026" in parts[0].text and parts[1].text == "unit OCR text"


def test_human_review_invalidates_on_changed_actual_context_even_if_answer_same(
    tmp_path,
):
    import csv

    from app.evaluation.reporting import write_review

    path = tmp_path / "review.csv"
    row = dict(
        id="q",
        strategy="dense",
        question="Q",
        reference_answer="A",
        answer="A",
        actual_context=[dict(document="d", section="s", text="original")],
        actual_prompt_sha256="first",
    )
    write_review([row], path)
    with path.open(encoding="utf-8-sig", newline="") as file:
        records = list(csv.DictReader(file))
    records[0]["factually_correct"] = "yes"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    row["actual_context"][0]["text"] = "changed"
    row["actual_prompt_sha256"] = "second"
    write_review([row], path)
    with path.open(encoding="utf-8-sig", newline="") as file:
        assert next(csv.DictReader(file))["factually_correct"] == "pending"


def test_fixture_parse_failure_leaves_clean_manifest_without_touching_database(
    tmp_path, monkeypatch
):
    import json

    from app.evaluation import fixtures as module

    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "broken.md").write_bytes(b"\xff")
    monkeypatch.setattr(
        module, "get_engine", lambda: pytest.fail("No DB writes on parser failure")
    )
    with (
        pytest.raises(UnicodeDecodeError),
        module.fixtures(
            {"documents": [dict(key="bad", file="broken.md")]},
            tmp_path,
            900,
            150,
            tmp_path / "dataset.json",
        ),
    ):
        pass
    manifest = json.loads(
        (tmp_path / "fixture_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["cleaned"] and manifest["phase"] == "parsing_before_database_writes"
