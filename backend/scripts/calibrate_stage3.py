"""Calibrate only saved DEV scores using the production evidence gate."""

import argparse
import itertools
import json
from pathlib import Path
from statistics import mean

from app.core.config import Settings
from app.db.models import Chunk, Document
from app.evaluation.fixtures import load_dataset
from app.retrieval import Candidate, RetrievalResult, select_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--gate", choices=["legacy", "score", "coverage"], default="legacy"
    )
    args = parser.parse_args()
    cases = {case["id"]: case for case in load_dataset()["cases"]}
    metadata = [
        json.loads((path / "summary.json").read_text(encoding="utf-8"))["metadata"]
        for path in args.runs
    ]
    signatures = {
        (
            m["dataset_sha256"],
            m["embedding"],
            m["parameters"]["top_k"],
            m["parameters"]["chunk_size"],
            m["parameters"]["overlap"],
        )
        for m in metadata
    }
    if len(signatures) != 1:
        raise ValueError("Calibration input datasets/models/chunking/top-k must match")
    top_k = metadata[0]["parameters"]["top_k"]
    rows = [
        row
        for path in args.runs
        for row in json.loads((path / "cases.json").read_text(encoding="utf-8"))
        if row["status"] == "ok"
    ]
    if any(row["split"] != "dev" for row in rows):
        raise ValueError("Calibration refuses holdout data")
    if len({(r["strategy"], r["id"]) for r in rows}) != len(rows):
        raise ValueError(
            "Duplicate strategy/case; choose one compatible baseline per strategy"
        )
    results = {}
    for strategy in sorted({row["strategy"] for row in rows}):
        group = [row for row in rows if row["strategy"] == strategy]
        trials = []
        for distance, keyword, rerank in itertools.product(
            [0.3, 0.4, 0.5, 0.65],
            [1.0, 3.0, 6.0, 10.0],
            [-6.0, -4.0, -3.0, -2.0, 0.0, 2.0],
        ):
            if strategy == "dense" and (keyword != 1 or rerank != 0):
                continue
            if strategy in {"keyword", "hybrid"} and rerank != 0:
                continue
            if strategy == "keyword" and distance != 0.65:
                continue
            settings = Settings(
                _env_file=None,
                retrieval_strategy=strategy,
                retrieval_max_distance=distance,
                retrieval_keyword_min_score=keyword,
                retrieval_rerank_min_score=rerank,
                retrieval_top_k=top_k,
                retrieval_rerank_gate=args.gate,
            )
            coverage, false_refusals, correct_refusals = [], [], []
            for row in group:
                candidates = [
                    Candidate(
                        Chunk(id=item["chunk_id"], text=item["text"]),
                        Document(),
                        distance=item["distance"],
                        keyword_score=item["keyword_score"],
                        fusion_score=item["fusion_score"],
                        rerank_score=item["rerank_score"],
                    )
                    for item in row["ranked"]
                ]
                result = RetrievalResult(strategy, ranked=candidates)
                select_evidence(result, settings, query=row["question"])
                if row["answerable"]:
                    # Preserve the evidence-unit denominator via original Recall and
                    # all expected units from the versioned dataset.
                    case = cases[row["id"]]
                    expected = {u["unit"] for u in case["expected"]}
                    ids = {c.chunk.id for c in result.evidence}
                    hits = (
                        set().union(
                            *(
                                set(c["units"])
                                for c in row["ranked"]
                                if c["chunk_id"] in ids
                            )
                        )
                        if ids
                        else set()
                    )
                    coverage.append(len(hits & expected) / len(expected))
                    false_refusals.append(not result.evidence)
                elif "boundary" not in row["categories"]:
                    correct_refusals.append(not result.evidence)
            positive_coverage = mean(coverage)
            correct_refusal = mean(correct_refusals)
            trials.append(
                {
                    "max_distance": distance,
                    "keyword_min": keyword,
                    "rerank_min": rerank,
                    "evidence_coverage": positive_coverage,
                    "false_refusal": mean(false_refusals),
                    "no_answer_refusal": correct_refusal,
                    "objective": (positive_coverage + correct_refusal) / 2,
                }
            )
        trials.sort(
            key=lambda t: (
                -t["objective"],
                t["false_refusal"],
                t["max_distance"],
                t["keyword_min"],
                t["rerank_min"],
            )
        )
        results[strategy] = {
            "selected": trials[0],
            "gate": args.gate,
            "trials": trials,
            "selection": "maximize mean(evidence_coverage, no_answer_refusal); "
            "tie: lower false refusal, then deterministic thresholds",
            "data": "dev_only; synthetic small-sample calibration",
            "provenance": {
                "runs": [str(p) for p in args.runs],
                "dataset_sha256": metadata[0]["dataset_sha256"],
                "embedding": metadata[0]["embedding"],
                "top_k": top_k,
                "chunk_size": metadata[0]["parameters"]["chunk_size"],
                "overlap": metadata[0]["parameters"]["overlap"],
            },
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name, result in results.items():
        print(name, result["selected"])


if __name__ == "__main__":
    main()
