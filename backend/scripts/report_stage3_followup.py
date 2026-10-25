"""Rebuild follow-up conclusions from saved real observations, not model guesses."""

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "docs/stage3_followup"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def display(value):
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def main():
    lines = [
        "# 阶段3后续优化：真实结果",
        "",
        "2026-10-20。保持生产默认dense、BAAI/bge-small-zh-v1.5、vector(512)。",
        "新配置仅为可选实验。先修复/开发验证、冻结，再在新保留题上对照；"
        "旧题仅作回归。本轮新答案没有沿用用户对旧四张表的确认。",
        "",
        "## 实现与边界",
        "",
        "- q002：正确正文已进重排输入，但口语问法使MiniLM评分低于-4。"
        "新增数据库语境下、有双侧表达的有界术语规范化；原问题保留，"
        "规范化仅用于检索/重排，不把改写当作回答证据。未降低门槛。",
        "- 显式分号/问号等最多拆3项；各项候选按名次参与分配。"
        "每个候选只对其最匹配分支重排一次，共用10个名额。"
        "各项优先保留已过同模型门槛的证据，仍受Top-K、范围、章节与预算限制。"
        "这不是通用语义分解，也不保证每项都能检出。",
        "- 可选生成提示逐项回答、逐结论引用，缺证据只拒答相应项。"
        "没有用检索分数代替引用语义评审。",
        "- 本轮真实发现：book_6_5的锚点正文被section_diversity排除；"
        "不能把原始Top-5完整覆盖1.0误写成最终证据全部覆盖。"
        "候选的holdout_1_2在已提供buffer正文时仍拒答；"
        "holdout_3_1及holdout_3_2存在部分回答/额外证据不足断言。"
        "引用支持率未改善，不能宣称已解决引用问题。",
        "- 标注限制：buffer题把容量23条作为具体含义，未提供通用概念定义，"
        "任务问法与参考之间存在歧义；模型可能混淆概念完整性和来源内事实。"
        "原始评分与分母完整保留，不事后修改保留集或删题抬分；需人工复核后另版标注。",
        "",
        "## 新数据与冻结",
        "",
        "48题：dev24、holdout24；每集17道正样本、3道无答案、4道边界。"
        "44道CC0控制题，另4道来自Allen B. Downey《Think Python 2e》第6章，"
        "CC BY-NC 3.0（非商业、署名）。开发/保留的可见文档不相交，教材按章节小节拆分。"
        "原HTML在raw/，来源及授权在data/dataset.json；教材图未提取。"
        "新题规模小，控制题占多数，所选文档范围窄；不能外推真实教材总体准确率。",
        "",
        "开发门槛验证见dev_final/；配置/数据/核心实现冻结见freeze.json。"
        "dev_candidate是增补教材前22题的初步开发验证，不用于最终同题对照。"
        "标注引句经过程序核验，但新参考解释与模型语义评分仍待人工审核。",
        "",
        "## 新保留集：同题对照",
        "",
        "| 方案 | 证据覆盖 | 原始Top-5完整覆盖 | 门槛错拒 | "
        "门槛无答案拒答 | LLM调用 | 范围违规 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for run in ["holdout_dense", "holdout_candidate"]:
        file = ROOT / run / "summary.json"
        if not file.exists():
            continue
        summary = next(iter(read(file)["strategies"].values()))
        values = [run] + [
            summary[k]
            for k in [
                "evidence_coverage",
                "complete",
                "gate_false_refusal_rate",
                "gate_no_answer_refusal_rate",
                "llm_calls",
                "scope_violations",
            ]
        ]
        lines.append("| " + " | ".join(display(x) for x in values) + " |")
    lines.extend(
        [
            "",
            "检索指标按证据锚点计算；正样本17题，负样本3题，边界4题分别统计。",
            "",
            "| 模型辅助评审 | 完整回答/全部正样本 | 部分回答/已评正样本 | "
            "完整错拒 | 事实一致性 | 引用支持 | 带引用断言数 | 评审失败 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in ["judge_dense", "judge_candidate", "judge_regression"]:
        file = ROOT / run / "summary.json"
        if not file.exists():
            continue
        r = read(file)
        q = r["quality"]
        values = [
            run,
            q["complete_answer_rate_all_positive"],
            q["partial_answer_rate_judged"],
            q["full_false_refusal_rate_judged"],
            q["model_fact_consistency_rate"],
            q["model_citation_support_rate"],
            q["cited_claim_n"],
            r["errors"],
        ]
        lines.append("| " + " | ".join(display(x) for x in values) + " |")
    lines.extend(
        [
            "",
            "生成与评审均为deepseek-flash的分开调用，存在同模型偏差，非专家签字。"
            "完整性与引用支持分开，unknown不算支持；失败及缺失保留在全部正样本分母。"
            "事实一致性为模型给出已知判断的条件率，known分母另见各judge/summary.json。"
            "新人工审核表位于holdout_dense/、holdout_candidate/及regression/的human_review.csv，"
            "均待用户确认；旧四张已确认表保持原样。",
            "",
            "## 分阶段损失",
            "",
            "计数为每题每个期望证据单元的损失，不同问题引用同一正文仍各计一次。",
        ]
    )
    losses = {}
    paths = {
        "旧q002等回归": ROOT.parent
        / "stage3_completion_runs/regression7_e2e/cases.json",
        "旧冻结保留": ROOT.parent
        / "stage3_completion_runs/holdout_selected_e2e/cases.json",
        "新保留原默认": ROOT / "holdout_dense/cases.json",
        "新保留候选": ROOT / "holdout_candidate/cases.json",
        "修复回归": ROOT / "regression/cases.json",
    }
    for name, path in paths.items():
        if path.exists():
            counts = Counter(
                unit["stage"]
                for row in read(path)
                for unit in row.get("trace", {}).get("evidence_losses", [])
            )
            losses[name] = dict(counts)
            lines.append(f"- {name}：`{dict(counts)}`")
    (ROOT / "stage_losses.json").write_text(
        json.dumps(losses, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines.extend(
        [
            "",
            "## 仅替换Embedding：独立TEMP表",
            "",
            "同一配对切块、Top-K5、余弦距离、相同范围及精确pgvector查询；"
            "模型维度隔离于TEMP表，不改生产索引。每个模型仅用dev选择距离门槛，"
            "固定目标为证据覆盖−2×无答案错误接纳率，同分选更严格门槛。"
            "选择结果先落盘再查holdout。这里没有混合检索/重排，"
            "且采用配对切块，不能和原默认端到端数字直接作单因素比较。",
            "",
            "| 模型 | 状态 | 维度 | dev门槛 | holdout原始Recall | "
            "门槛后覆盖 | 错拒 | 无答案拒答 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    file = ROOT / "embedding_comparison/comparison.json"
    if file.exists():
        for r in read(file)["models"]:
            h = r.get("holdout", {})
            values = [
                r["model"],
                r["status"],
                r.get("dimension"),
                r.get("gate", {}).get("threshold"),
                h.get("recall"),
                h.get("evidence_coverage"),
                h.get("false_refusal"),
                h.get("no_answer_refusal"),
            ]
            lines.append("| " + " | ".join(display(x) for x in values) + " |")
    lines.extend(
        [
            "",
            "BGE-M3不受现有FastEmbed运行时支持，因此明确跳过，没有新增实测或自动切换。"
            "384维模型即使本小集有收益，也不能据此迁移生产；尚缺更广教材、表格、"
            "噪声扫描及端到端多模型验证。维度不是独立质量开关。",
            "",
            "## 速度与成本",
            "",
            "速度在独立基准运行中测量，闭环并发1/2、各24请求；不包含LLM或HTTP排队。"
            "冷启动单列；3秒仍是助手实验参考，非用户验收标准或生产SLO。",
        ]
    )
    for run in ["benchmark_public", "benchmark_facets"]:
        file = ROOT / run / "benchmark.json"
        if file.exists():
            r = read(file)
            lines.append(
                f"- {run}：{r['chunks']}块，CPU `{r['cpu']}`，"
                f"模型线程{r['threads_per_model']}；"
            )
            for group in r["groups"]:
                lines.append(
                    f"  - {group['strategy']} 并发{group['concurrency']}："
                    f"n={group['total_n']}，P95={group['wall_ms']['p95']}ms，"
                    f"错误{group['errors']}。"
                )
    lines.extend(
        [
            "",
            "端到端token/调用和延迟见各run/summary.json；服务端usage优先，价格未配置时费用unknown。"
            "模型评审成本另存，不混入生成。没有以小样本P95推断生产容量。",
            "",
            "## 决策",
            "",
            "本轮保留dense与512维生产默认，候选配置保持可选。"
            "修复q002并不等于所有口语查询都解决；新增控制集收益不代表真实分布稳定收益。"
            "继续优先验证更广的真实文档、引用语义及并发；没有证据要求迁移Qdrant。",
        ]
    )
    verification = ROOT / "verification.json"
    if verification.exists():
        v = read(verification)
        lines.extend(
            [
                "",
                "## 最终检查",
                "",
                f"后端{v['backend_tests']['passed']}项测试通过，"
                f"{v['backend_tests']['warnings']}条现有弃用提示；"
                "Ruff、格式与Alembic离线SQL生成通过。"
                f"{v['fixture_manifests']}份隔离清单已清理，临时账号剩余"
                f"{v['remaining_fixture_users']}；冻结哈希一致、旧四份审核表未改动。"
                "没有新增依赖、修改前端或自动重建用户资料。",
                "",
                "[重跑命令与数据授权](README.md) · "
                "[逐题原文/回答/实际引用](review_readable.md) · "
                "[机器检查记录](verification.json)",
            ]
        )
    (ROOT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    readable = [
        "# 新运行：逐题来源、回答与引用",
        "",
        "本轮均待人工审核，未沿用上一轮用户签认。",
        "",
    ]
    for run, judge, dataset_path in [
        ("holdout_dense", "judge_dense", ROOT / "data/dataset.json"),
        ("holdout_candidate", "judge_candidate", ROOT / "data/dataset.json"),
        (
            "regression",
            "judge_regression",
            ROOT.parent / "evaluation_data/dataset.json",
        ),
    ]:
        file = ROOT / run / "cases.json"
        if not file.exists():
            continue
        dataset = read(dataset_path)
        cases = {x["id"]: x for x in dataset["cases"]}
        docs = {x["key"]: x for x in dataset["documents"]}
        reviews_file = ROOT / judge / "reviews.json"
        reviews = (
            {x["id"]: x for x in read(reviews_file)} if reviews_file.exists() else {}
        )
        readable.extend([f"## {run}", ""])
        for row in read(file):
            readable.extend([f"### {row['id']}：{row['question']}", ""])
            for unit in cases[row["id"]]["expected"]:
                prefix = "data" if run != "regression" else "../evaluation_data"
                source = f"{prefix}/sources/{docs[unit['document']]['file']}"
                readable.extend(
                    [
                        f"原文：[{unit['document']}]({source}) / "
                        f"{unit.get('section', '')}",
                        "",
                        "> " + unit["quote"],
                        "",
                    ]
                )
            readable.extend(
                ["**真实回答**：" + row.get("answer", "权限阻断，未生成"), ""]
            )
            review = reviews.get(row["id"], {})
            readable.extend(
                [
                    "**模型评审**：`"
                    + json.dumps(
                        review.get("quality", review.get("judge_status")),
                        ensure_ascii=False,
                    )
                    + "`",
                    "",
                ]
            )
            for context in row.get("actual_context", []):
                readable.extend(
                    [
                        f"**实际引用[{context['label']}]**：{context['document']}",
                        "",
                        "> " + context["text"],
                        "",
                    ]
                )
            readable.extend(["**真人确认**：待审，填写本组human_review.csv。", ""])
    (ROOT / "review_readable.md").write_text(
        "\n".join(readable) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
