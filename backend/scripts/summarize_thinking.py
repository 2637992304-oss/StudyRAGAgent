"""Analyze frozen outputs; never rerun or replace a failed model answer."""

import argparse
import itertools
import json
import math
import re
import statistics
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    args = parser.parse_args()
    path = Path(args.results)
    data = json.loads(path.read_text(encoding="utf-8"))
    counts = sum(
        all(b - a > 1 for a, b in zip(row, row[1:]))
        for row in itertools.combinations(range(1, 12), 4)
    )
    assert counts == math.comb(8, 4) == 70
    output = {
        "review_identity": (
            "程序确定性核对 + DeepSeek Flash同供应商AI辅助；"
            "Codex复核原回答，非人工独立验收"
        ),
        "numeric_reference": {
            "fact": 23 * 7,
            "derivation_enumerated": counts,
            "wrong_work": (20 + 12) // 4,
            "strict": 9 - 4,
            "image": (44 - 9) // 5,
        },
        "modes": {},
        "failures_preserved": [],
        "engineering_checks": data["checks"],
    }
    lines = [
        "# 冻结最终样本对照",
        "",
        "| 样本 | 关闭：秒/输出tokens/AI正确性 | 开启：秒/输出tokens/AI正确性 |",
        "| --- | --- | --- |",
    ]
    for case in dict.fromkeys(row["id"] for row in data["pairs"]):
        values = []
        for thinking in [False, True]:
            row = next(
                r
                for r in data["pairs"]
                if r["id"] == case and r["thinking"] == thinking
            )
            review = next(
                r
                for r in data["ai_reviews"]
                if r["id"] == case and r["thinking"] == thinking
            )
            judgment = json.loads(review["raw_judgment"])
            values.append(
                f"{row['elapsed_seconds']:.3f} / {row['answer']['output_tokens']} / "
                f"{judgment['correctness']}"
            )
            if judgment["correctness"] < 2:
                output["failures_preserved"].append(
                    {"id": case, "thinking": thinking, "judgment": judgment}
                )
            if case == "derivation":
                boxed = re.findall(r"\\boxed\{(\d+)\}", row["answer"]["content"])
                output.setdefault("derivation_program_check", []).append(
                    {
                        "thinking": thinking,
                        "final_boxed_answer": int(boxed[-1]) if boxed else None,
                        "expected": counts,
                        "correct": bool(boxed and int(boxed[-1]) == counts),
                    }
                )
        lines.append(f"| {case} | {values[0]} | {values[1]} |")
    for thinking in [False, True]:
        rows = [r for r in data["pairs"] if r["thinking"] == thinking]
        judgments = [
            json.loads(r["raw_judgment"])
            for r in data["ai_reviews"]
            if r["thinking"] == thinking
        ]
        output["modes"][str(thinking)] = {
            "n": len(rows),
            "correct_2_of_2": sum(j["correctness"] == 2 for j in judgments),
            "median_seconds": statistics.median(r["elapsed_seconds"] for r in rows),
            "max_seconds": max(r["elapsed_seconds"] for r in rows),
            "input_tokens": sum(r["answer"]["input_tokens"] for r in rows),
            "output_tokens": sum(r["answer"]["output_tokens"] for r in rows),
            "stops": [r["answer"]["stop_reason"] for r in rows],
            "tool_counts": [
                sum(bool(t.get("tool")) for t in r["answer"]["agent_trace"])
                for r in rows
            ],
        }
    output["cost"] = None
    output["limits"] = (
        "每模式6题、单次采样；生成预算2048/8192不同，不能把差异都归因于开关，不外推可靠性或费用。"
    )
    (path.parent / "summary.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (path.parent / "comparison.md").write_text(
        "\n".join(lines) + "\n\n" + output["limits"] + "\n", encoding="utf-8"
    )
    print(json.dumps(output["modes"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
