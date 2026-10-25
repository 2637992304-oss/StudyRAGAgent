"""Rebuild the final comparison and readable audit from saved observations."""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "docs/stage3_completion_runs"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    comparisons = []
    for folder in sorted(RUNS.iterdir()):
        file = folder / "summary.json"
        if file.exists():
            for strategy, summary in read(file).get("strategies", {}).items():
                exclusion = {
                    "dev_chunk_pair": "Prototype heading bug; superseded by v2",
                    "dev_full": "Prototype heading bug; superseded by v2",
                }.get(folder.name)
                if folder.name == "dev_original" and strategy != "rerank":
                    exclusion = "Copied rerank distance; use dev_dense_current"
                comparisons.append(
                    dict(
                        run=folder.name,
                        strategy=strategy,
                        valid_for_conclusion=not bool(exclusion),
                        exclusion_reason=exclusion,
                        **summary,
                    )
                )
    (RUNS / "comparison.json").write_text(
        json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 阶段3收尾：真实运行对照",
        "",
        "原始开发试验与无效试验保留；最终结论以冻结后的保留集及final速度测试为准。",
        "",
        "| 运行 | 策略 | n | 原始Recall@5 | 最终证据覆盖 | "
        "完整错拒 | 无答案拒答 | 检索P95 ms |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in comparisons:
        if not r["valid_for_conclusion"]:
            continue
        values = [
            r["run"],
            r["strategy"],
            r["n"],
            r["recall"],
            r["evidence_coverage"],
            r["false_refusal_rate"],
            r["no_answer_refusal_rate"],
            r["retrieval_ms"]["p95"],
        ]
        lines.append(
            "| "
            + " | ".join(f"{x:.4f}" if isinstance(x, float) else str(x) for x in values)
            + " |"
        )
    (RUNS / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    review_lines = [
        "# 冻结保留集与专项：中文审核对照",
        "",
        "模型原始判断保留；真人决定以各组human_review.csv为准。"
        "原始引用来自真实API的提示快照，不再按预检顺序猜测。",
        "",
        "实际提示快照只采集隔离测试账号；普通问答没有开启正文日志。",
        "",
        "审核含义及完整清单见[人工审核说明](../human_review_guide.md)。"
        "同意模型判为错误，不表示RAG回答正确。",
        "",
    ]
    confirmation = RUNS / "user_confirmation.json"
    if confirmation.exists():
        recorded = read(confirmation)
        groups = recorded["groups"]
        review_lines.extend(
            [
                f"用户确认时间：{recorded['reviewed_at']}。"
                f"已登记{sum(g['valid_model_reviews_agreed'] for g in groups)}"
                "条有效模型评审的同意、"
                f"{sum(g['scope_blocks_confirmed'] for g in groups)}条权限阻断确认；"
                f"{sum(g['model_errors_acknowledged'] for g in groups)}"
                "条模型评审失败保留未知。"
                "由Codex按本对话明确授权代填，不代表所有RAG答案正确。",
                "",
            ]
        )
    bundles = [
        (
            "新保留集：原默认语义检索",
            "holdout_dense_e2e",
            "judge_holdout_dense",
            "stage3_completion_data",
        ),
        (
            "新保留集：冻结改进配置",
            "holdout_selected_e2e",
            "judge_holdout_selected",
            "stage3_completion_data",
        ),
        (
            "版面与混合扫描专项",
            "layout_e2e",
            "judge_layout",
            "stage3_completion_layout",
        ),
        (
            "已知问题回归（非新盲测）",
            "regression7_e2e",
            "judge_regression7",
            "evaluation_data",
        ),
    ]
    for title, run, judge, data in bundles:
        path = RUNS / run / "cases.json"
        if not path.exists():
            continue
        dataset = read(ROOT / "docs" / data / "dataset.json")
        cases = {r["id"]: r for r in dataset["cases"]}
        docs = {r["key"]: r for r in dataset["documents"]}
        reviews = (
            {r["id"]: r for r in read(RUNS / judge / "reviews.json")}
            if (RUNS / judge / "reviews.json").exists()
            else {}
        )
        review_lines.extend(["## " + title, ""])
        manual_file = RUNS / run / "human_review.csv"
        manual = {}
        if manual_file.exists():
            with manual_file.open(encoding="utf-8-sig", newline="") as stream:
                manual = {
                    (entry["id"], entry["strategy"]): entry
                    for entry in csv.DictReader(stream)
                }
        for row in read(path):
            case = cases[row["id"]]
            review_lines.extend(
                [
                    f"### {row['id']}：{row['question']}",
                    "",
                    "**原资料与应回答的事实**",
                    "",
                ]
            )
            for unit in case["expected"]:
                spec = docs[unit["document"]]
                relative = f"../{data}/sources/{spec['file']}"
                location = (
                    f"第{unit['page']}页"
                    if unit.get("page")
                    else unit.get("section", "正文")
                )
                review_lines.extend(
                    [
                        f"[{spec['file']}]({relative}) / {location}",
                        "",
                        "> " + unit["quote"],
                        "",
                    ]
                )
            if not case["expected"]:
                review_lines.append(
                    "无答案或权限边界题，需检查完整授权范围；不能用片段存在性代替无答案判断。"
                )
            review_lines.extend(
                ["**参考结果**：" + row["reference_answer"], "", "**真实回答**", ""]
            )
            review_lines.extend(
                "> " + line
                for line in row.get("answer", "未生成，权限范围拒绝").splitlines()
            )
            review_lines.append("")
            observed = reviews.get(row["id"], {})
            if "quality" in observed:
                quality = observed["quality"]
                review_lines.extend(
                    [
                        f"**模型判断**：{quality['task_outcome']}；{quality['reason']}",
                        "",
                    ]
                )
                for claim in quality["claims"]:
                    review_lines.extend(
                        [
                            f"- 断言：{claim['claim']}；"
                            f"引用{claim['citation_labels']}；"
                            f"模型支持判断：{claim['support']}。",
                            "",
                        ]
                    )
            else:
                review_lines.extend(
                    ["**模型判断**：" + observed.get("judge_status", "待评审"), ""]
                )
            for context in row.get("actual_context", []):
                review_lines.extend(
                    [
                        f"**实际引用 [{context['label']}]**：{context['document']} / "
                        + str(
                            context.get("section")
                            or context.get("page_start")
                            or "正文"
                        ),
                        "",
                        "> " + context["text"],
                        "",
                    ]
                )
            decision = manual.get((row["id"], row["strategy"]), {})
            if (
                decision.get("reviewer")
                and decision.get("answer") == row.get("answer", "")
                and decision.get("reference") == row["reference_answer"]
                and decision.get("actual_context_fingerprint", "")
                == (row.get("actual_prompt_sha256") or "")
            ):
                review_lines.extend(
                    [
                        f"**真人确认**：{decision['reviewer']}。{decision['notes']}",
                        "",
                    ]
                )
            else:
                review_lines.extend(
                    [
                        "**真人确认**：待审。请分别记录RAG回答结论和是否同意模型评审"
                        "（同意／部分同意／不同意／无法判断），正式结果写本组human_review.csv"
                        "的审核列与notes。",
                        "",
                    ]
                )
    (RUNS / "review_readable.md").write_text(
        "\n".join(review_lines) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
