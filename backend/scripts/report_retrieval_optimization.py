"""Build a comparison from real completed runs, preserving failures separately."""

import argparse
import json
from pathlib import Path

from app.evaluation.metrics import summarize


def paired_public(root):
    miniature = root / "public_holdout95_minilm" / "cases.json"
    bge = root / "public_holdout20_bge" / "cases.json"
    baseline = root / "public200_core_bounded" / "cases.json"
    if not (miniature.exists() and bge.exists() and baseline.exists()):
        return
    mini_rows = json.loads(miniature.read_text(encoding="utf-8"))
    bge_rows = json.loads(bge.read_text(encoding="utf-8"))
    if len(mini_rows) != 95 or len(bge_rows) != 20:
        return  # Do not label an in-progress checkpoint as a completed cohort.
    base_rows = json.loads(baseline.read_text(encoding="utf-8"))
    cohorts = {
        "common20": {row["id"] for row in bge_rows},
        "holdout95": {row["id"] for row in mini_rows},
    }
    result = {}
    for cohort, ids in cohorts.items():
        result[cohort] = {}
        for strategy in ("dense", "keyword", "hybrid"):
            result[cohort][strategy] = summarize(
                [r for r in base_rows if r["id"] in ids and r["strategy"] == strategy]
            )
        result[cohort]["minilm"] = summarize([r for r in mini_rows if r["id"] in ids])
        if cohort == "common20":
            result[cohort]["bge"] = summarize(bge_rows)
    root.joinpath("public_paired.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    lines = [
        "# 同题公开数据对照",
        "",
        "原始段落标签、缩减语料、应用Top-5块；不等于答案准确率。",
        "",
    ]
    for cohort, strategies in result.items():
        lines.extend(
            [
                f"## {cohort}",
                "",
                "| 方案 | n | Recall | Hit | MRR | 最终覆盖 | P95 ms |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for name, row in strategies.items():
            values = [
                name,
                row["n"],
                row["recall"],
                row["hit"],
                row["mrr"],
                row["evidence_coverage"],
                row["retrieval_ms"]["p95"],
            ]
            lines.append(
                "| "
                + " | ".join(
                    f"{v:.4f}" if isinstance(v, float) else str(v) for v in values
                )
                + " |"
            )
        lines.append("")
    root.joinpath("public_paired.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("../docs/retrieval_optimization")
    )
    args = parser.parse_args()
    runs = []
    for folder in sorted(args.root.iterdir()):
        if not folder.is_dir() or not (folder / "summary.json").exists():
            continue
        content = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        for name, metrics in content["strategies"].items():
            runs.append(
                {
                    "run": folder.name,
                    "strategy": name,
                    "dataset": content["metadata"]["dataset_version"],
                    "parameters": content["metadata"]["parameters"],
                    **metrics,
                }
            )
    args.root.joinpath("comparison.json").write_text(
        json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 实测对照",
        "",
        "合成证据单元与公开原始段落标签的分数不能横向比较。",
        "公开子语料成绩不是官方完整语料榜单成绩；答案事实性、引用支持未由这些检索分数证明。",
        "",
        "| 运行 | 方案 | 数据集 | 题数 | Recall@5 | 门槛后覆盖 | "
        "错误拒答 | 无答案拒答 | P95 ms |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in runs:
        values = [
            row["run"],
            row["strategy"],
            row["dataset"],
            row["n"],
            row["recall"],
            row["evidence_coverage"],
            row["false_refusal_rate"],
            row["no_answer_refusal_rate"],
            row["retrieval_ms"]["p95"],
        ]
        lines.append(
            "| "
            + " | ".join(
                f"{x:.4f}"
                if isinstance(x, float)
                else str(x)
                if x is not None
                else "N/A"
                for x in values
            )
            + " |"
        )
    args.root.joinpath("comparison.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    paired_public(args.root)


if __name__ == "__main__":
    main()
