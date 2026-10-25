"""Reaggregate saved raw observations using current metric definitions."""

import argparse
import json
from pathlib import Path

from app.evaluation.metrics import refusal_observation, summarize
from app.evaluation.reporting import write_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    comparisons = []
    for path in sorted(args.root.iterdir()):
        if not path.is_dir() or not (path / "cases.json").exists():
            continue
        observations = json.loads((path / "cases.json").read_text(encoding="utf-8"))
        for row in observations:
            if row["status"] == "ok":
                row["gate_refused"] = not row.get("evidence_ids")
            if row["status"] == "ok" and "answer" in row:
                row.update(
                    refusal_observation(
                        row["answer"],
                        "no_evidence" if not row.get("llm_calls") else "completed",
                    )
                )
        (path / "cases.json").write_text(
            json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
        write_review(observations, path / "human_review.csv")
        summary["strategies"] = {
            name: summarize([row for row in observations if row["strategy"] == name])
            for name in sorted({row["strategy"] for row in observations})
        }
        summary["aggregation"] = "stage3-v1-final; recomputed from original cases.json"
        (path / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# Run comparison",
            "",
            "Factuality/citation semantic support: pending human review.",
            "Refusal*: retrieval gate in retrieval runs; "
            "explicit text rules in E2E runs.",
            "",
            "| Strategy | n | errors | Recall@K | Hit@K | MRR | Coverage after gate | "
            "False refusal | No-answer refusal* | Warm P95 ms |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for strategy, row in summary["strategies"].items():
            comparisons.append(
                {
                    "run": path.name,
                    "strategy": strategy,
                    "parameters": summary["metadata"]["parameters"],
                    **row,
                }
            )
            values = [
                strategy,
                row["n"],
                row["errors"],
                row["recall"],
                row["hit"],
                row["mrr"],
                row["evidence_coverage"],
                row["false_refusal_rate"],
                row["no_answer_refusal_rate"],
                row["retrieval_ms"]["p95"],
            ]
            lines.append(
                "| "
                + " | ".join(
                    str(v) if not isinstance(v, float) else f"{v:.4f}" for v in values
                )
                + " |"
            )
        (path / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.root / "comparison.json").write_text(
        json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for row in comparisons:
        print(
            row["run"],
            row["strategy"],
            row["n"],
            row["errors"],
            row["recall"],
            row["evidence_coverage"],
            row["false_refusal_rate"],
            row["no_answer_refusal_rate"],
        )


if __name__ == "__main__":
    main()
