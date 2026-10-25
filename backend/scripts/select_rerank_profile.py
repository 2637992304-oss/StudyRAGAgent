"""Compare evidence gates on saved DEV scores; never accepts holdout rows.

Inference latency comes from the actual candidate-depth run, not replay timing.
"""

import argparse
import json
from pathlib import Path
from statistics import mean

from app.core.config import Settings
from app.db.models import Chunk, Document
from app.evaluation.fixtures import DATA, load_dataset
from app.retrieval import Candidate, RetrievalResult, select_evidence


def evaluate(rows, settings, expected):
    coverage, false_refusals, refusals = [], [], []
    for row in rows:
        ranked = [
            Candidate(
                Chunk(id=c["chunk_id"], text=c["text"], section_path=c["section"]),
                Document(id=c["document"]),
                distance=c["distance"],
                keyword_score=c["keyword_score"],
                rerank_score=c["rerank_score"],
            )
            for c in row["ranked"]
        ]
        result = RetrievalResult("rerank", ranked=ranked)
        select_evidence(result, settings, row["question"])
        if row["answerable"]:
            selected = {c.chunk.id for c in result.evidence}
            hit = (
                set().union(
                    *(
                        set(c["units"])
                        for c in row["ranked"]
                        if c["chunk_id"] in selected
                    )
                )
                if selected
                else set()
            )
            coverage.append(len(hit & expected[row["id"]]) / len(expected[row["id"]]))
            false_refusals.append(not result.evidence)
        elif "boundary" not in row["categories"]:
            refusals.append(not result.evidence)
    return {
        "coverage": mean(coverage),
        "false_refusal": mean(false_refusals),
        "no_answer_refusal": mean(refusals),
        "objective": (mean(coverage) + mean(refusals)) / 2,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DATA)
    args = parser.parse_args()
    expected = {
        c["id"]: {u["unit"] for u in c["expected"]}
        for c in load_dataset(args.dataset)["cases"]
    }
    trials = []
    for run in args.runs:
        rows = json.loads((run / "cases.json").read_text(encoding="utf-8"))
        if any(row["split"] != "dev" or row["status"] != "ok" for row in rows):
            raise ValueError("Require complete successful DEV-only inference results")
        rows = [row for row in rows if row["strategy"] == "rerank"]
        if not rows or len({r["id"] for r in rows}) != len(rows):
            raise ValueError("Require unique rerank cases")
        meta = json.loads((run / "summary.json").read_text(encoding="utf-8"))
        p95 = meta["strategies"]["rerank"]["retrieval_ms"]["p95"]
        count = meta["metadata"]["parameters"]["rerank_count"]
        parameters = meta["metadata"]["parameters"]
        for gate in ["legacy", "score", "coverage", "adaptive"]:
            for threshold in [-6.0, -4.0, -3.0, -2.0, 0.0]:
                for strong in [0.0, 2.0] if gate == "adaptive" else [0.0]:
                    settings = Settings(
                        _env_file=None,
                        retrieval_strategy="rerank",
                        retrieval_rerank_gate=gate,
                        retrieval_max_distance=0.3,
                        retrieval_keyword_min_score=3.0,
                        retrieval_rerank_min_score=threshold,
                        retrieval_rerank_strong_score=strong,
                        retrieval_pair_headings=parameters.get("pair_headings", False),
                        retrieval_diversify=parameters.get("diversify", False),
                        retrieval_section_limit=parameters.get("section_limit", 2),
                    )
                    result = evaluate(rows, settings, expected)
                    trials.append(
                        {
                            "run": str(run),
                            "count": count,
                            "gate": gate,
                            "threshold": threshold,
                            "strong_score": strong,
                            "model": meta["metadata"]["reranker"],
                            "parameters": parameters,
                            "p95_ms": p95,
                            **result,
                        }
                    )
    qualified = [
        r
        for r in trials
        if r["p95_ms"] <= 2000
        and r["false_refusal"] <= 0.15
        and r["no_answer_refusal"] >= 0.8
    ]
    ranked = sorted(
        qualified or trials,
        key=lambda r: (
            -r["objective"],
            r["p95_ms"],
            r["false_refusal"],
            {"score": 0, "adaptive": 1, "legacy": 2, "coverage": 3}[r["gate"]],
        ),
    )
    selected = ranked[0]
    payload = {
        "selection_data": str(args.dataset),
        "target_p95_ms": 2000,
        "target_met": bool(qualified),
        "selected": selected,
        "trials": trials,
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    config = {
        "strategies": ["rerank"],
        "rerank_count": selected["count"],
        "gate": selected["gate"],
        "rerank_min": selected["threshold"],
        "strong_score": selected["strong_score"],
        "max_distance": 0.3,
        "keyword_min": 3.0,
        "threads": 2,
        "batch_size": 4,
        "reranker_model": selected["model"],
    }
    for key in (
        "pair_headings",
        "chunk_pair_headings",
        "expand_terms",
        "balanced_candidates",
        "diversify",
        "section_limit",
        "top_k",
        "chunk_size",
        "overlap",
        "threads",
        "batch_size",
        "passage_chars",
    ):
        if key in selected["parameters"]:
            config[key] = selected["parameters"][key]
    args.output.with_name(args.output.stem + "_profile.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    for count in sorted({row["count"] for row in trials}):
        best = max(
            [row for row in trials if row["count"] == count],
            key=lambda row: row["objective"],
        )
        print(count, best, flush=True)
    print("selected", selected, flush=True)


if __name__ == "__main__":
    main()
