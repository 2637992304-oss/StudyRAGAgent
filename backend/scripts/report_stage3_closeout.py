"""Summarize the bounded final round without modifying any observations."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "docs/stage3_closeout"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cell(value):
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def main():
    lines = [
        "# 阶段3：有明确边界的最后一轮收尾",
        "",
        "状态：本轮工程与真实实验收尾；新模型评审与答案仍待用户逐题审核。"
        "生产继续使用dense、BAAI/bge-small-zh-v1.5、vector(512)，未重建用户资料。",
        "",
        "## 范围、复核与冻结",
        "",
        "[标注复核](annotation_review.md)记录用户同意的处理原则：争议项单列、"
        "保留原评分、不直接把标注歧义当作系统事实错误。"
        "factorial(0)已有代码证据，原文字锚点漏计不能当作真实证据缺失。",
        "",
        "实现：章节限额改为可选软偏好；原门槛、Top-K、预算不变。"
        "未覆盖的精确字段可保留同章节正文；其他补充仅在考虑不同章节后、"
        "有新增词面内容且已过门槛时执行。子问题优先按精确字段匹配分配，"
        "共用10个重排名额。生成提示区分通用定义和资料内事实，逐事实引用。",
        "",
        "开发两次：第一次发现字段被错误分配；修复后dev_final验证14道正样本、"
        "2道无答案、4道边界。配置在freeze.json冻结，重排门槛仍为-4。"
        "之后未依据最终保留结果调整检索/生成逻辑、评测算法、参数、标注或分母。"
        "评测脚本仅事后整理格式，冻结原文与AST一致证明见formatting_record.json。",
        "",
        "Embedding预试验与最终验收分开。data/共40题，dev20/预试验holdout20；"
        "final_data/复用同一开发集，另有20道最终案例：14道正样本采用新字段、"
        "新数值和新教材小节，2道负例更换范围，4道边界沿用控制结构。"
        "模板与开发集仍有共性，合成题占多数；不冒充独立真实教材分布。"
        "教材来自Allen B. Downey《Think Python 2e》第7章，CC BY-NC 3.0，"
        "仅非商业并保留署名；自制材料CC0。图片未提取，原HTML见raw/。",
        "",
        "## 冻结后的最终问答验收",
        "",
        "每组20题：14道正样本、2道无答案、4道边界。检索按证据锚点计分，"
        "与答案语义正确性分开。门槛拒答和LLM生成后的拒答也分开。",
        "",
        "| 方案 | 最终证据覆盖 | 门槛错拒 | 门槛无答案拒答 | LLM调用 | 范围违规 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    machine = dict(runs={}, judges={})
    for run in ["holdout_dense", "holdout_candidate", "regression"]:
        path = ROOT / run / "summary.json"
        if not path.exists():
            continue
        summary = next(iter(read(path)["strategies"].values()))
        machine["runs"][run] = summary
        values = [run] + [
            summary[k]
            for k in [
                "evidence_coverage",
                "gate_false_refusal_rate",
                "gate_no_answer_refusal_rate",
                "llm_calls",
                "scope_violations",
            ]
        ]
        lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    lines.extend(
        [
            "",
            "regression为旧7题回归，不并入新题总体指标。",
            "",
            "| 模型评审 | 完整率 | 部分率 | 完整错拒 | 引用支持 | 引用断言数 | 失败 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in ["judge_dense", "judge_candidate", "judge_regression"]:
        path = ROOT / run / "summary.json"
        if not path.exists():
            continue
        r = read(path)
        q = r["quality"]
        machine["judges"][run] = r
        values = (
            [run]
            + [
                q[k]
                for k in [
                    "complete_answer_rate_all_positive",
                    "partial_answer_rate_judged",
                    "full_false_refusal_rate_judged",
                    "model_citation_support_rate",
                    "cited_claim_n",
                ]
            ]
            + [r["errors"]]
        )
        lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    lines.extend(
        [
            "",
            "生成和评审均为deepseek-flash的分开调用，非独立专家验收。"
            "unknown不算引用支持；失败保留在全部正样本分母。"
            "两组最终无答案题均由模型评审判为2/2正确拒答；"
            "候选仅1/2在检索门槛直接拒答，另一题仍调用LLM。"
            "逐子问题、事实已知分母、token与usage均见原始summary/reviews。"
            "未配置价格时费用unknown，评审费用与生成分开。",
            "",
            "## BGE-M3真实1024维对照",
            "",
            "官方模型固定提交5617a9f61b028005a4858fdac845db406aefb181，"
            "独立Sentence Transformers环境、CPU float32、2线程、最大512 token。"
            "首次因漏下官方pytorch_model.bin失败，保留embedding_comparison/；"
            "补齐后embedding_final/预试验成功，embedding_acceptance/为冻结后最终对照。"
            "没有启用sparse或ColBERT。",
            "",
            "同一资料、切块、Top-K5、实时权限和TEMP pgvector精确查询。"
            "每模型仅用dev校准，目标固定为覆盖−2×无答案错误接纳，"
            "同分取更严格门槛，结果落盘后再测holdout。"
            "两道负例很少，校准可泛化性有限。该实验未接LLM或reranker，"
            "不能与上表原默认0.65门槛的端到端结果直接比较。",
            "",
            "| 模型 | 状态 | dev距离门槛 | Recall@5 | 门槛后覆盖 | 错拒 | 查询P95 ms |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    file = ROOT / "embedding_acceptance/comparison.json"
    if file.exists():
        comparison = read(file)
        machine["embedding"] = comparison
        for model in comparison["models"]:
            h = model.get("holdout", {})
            values = [
                model["model"],
                model["status"],
                model.get("gate", {}).get("threshold"),
                h.get("recall"),
                h.get("evidence_coverage"),
                h.get("false_refusal"),
                h.get("warm_retrieval_ms", {}).get("p95"),
            ]
            lines.append("| " + " | ".join(cell(x) for x in values) + " |")
            if "external_metadata" in model:
                m = model["external_metadata"]
                lines.extend(
                    [
                        "",
                        "BGE-M3工作进程峰值内存"
                        f"{m['peak_rss_bytes'] / 1024**3:.2f} GiB；"
                        f"加载{m['load_ms']:.1f}ms，资料编码{m['passage_encode_ms']:.1f}ms。"
                        "峰值含运行时和权重，不是纯模型大小。旧模型进程峰值未测，不能记零。"
                        "文档编码批大小BGE为2、FastEmbed为16，不能直接比较入库吞吐；"
                        "在线查询均单条、相同2线程。BGE查询时间包含真实编码加SQL，"
                        "未把预编码字典查找冒充模型耗时。",
                    ]
                )
    lines.extend(
        [
            "",
            "## 速度与限制",
            "",
            "端到端验收运行有重叠，不作为容量结论；另用隔离公开语料独立测检索，"
            "闭环并发1/2、各24请求，冷启动单列，不含HTTP/LLM排队。"
            "3秒仍是助手实验参考，不是用户设定的SLO。",
        ]
    )
    file = ROOT / "benchmark_final/benchmark.json"
    if file.exists():
        r = read(file)
        machine["benchmark"] = r
        lines.extend(
            ["", f"{r['chunks']}块，CPU `{r['cpu']}`，Embedding/重排各2线程。"]
        )
        for g in r["groups"]:
            lines.append(
                f"- {g['strategy']} 并发{g['concurrency']}，n={g['total_n']}，"
                f"P95={g['wall_ms']['p95']:.1f}ms，错误{g['errors']}。"
            )
    lines.extend(
        [
            "",
            "本次dense并发2明显快于并发1，存在顺序、缓存或系统状态影响的可能，"
            "原因未验证；不能据此宣称并发提升使单次检索加速。"
            "正式速度结果保留原样，不择优删除或无限追加实验。",
            "",
            "## 采用方案与阶段状态",
            "",
            "采用：生产仍为dense/512维，closeout候选只作可选配置。"
            "BGE-M3的有限样本质量收益须与CPU时间、内存、重建及回退成本共同衡量；"
            "尚无全链路/广泛教材/真实用户分布证据，不自动迁移。"
            "没有证据需要迁移Qdrant。",
            "",
            "阶段3工程和这一轮有边界的实验可以收尾；不等于检索完美或新答案已获真人签字。"
            "新human_review.csv保持待审，旧审核结论不继承。"
            "不再将已看过的保留题用于调参后宣称新测试；更广资料与GPU测试归后续优化。",
            "",
        ]
    )
    verification = ROOT / "verification.json"
    if verification.exists():
        v = read(verification)
        lines.append(
            f"最终检查：{v['tests_passed']}项后端测试通过，Ruff/格式/Alembic离线检查通过；"
            f"{v['fixture_manifests']}份评测清单已清理，临时账号剩余{v['remaining_users']}。"
            "检索逻辑、配置、数据与旧审核表核对通过；评测脚本格式例外见"
            "formatting_record.json。仅增加隔离评测依赖锁，无生产依赖或前端变更。"
        )
    (ROOT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "comparison.json").write_text(
        json.dumps(machine, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    readable = ["# 最终验收逐题对照", "", "新运行待人工审核；旧表签认不自动继承。", ""]
    for run in machine["runs"]:
        data_dir = (
            ROOT / "final_data"
            if run != "regression"
            else ROOT.parent / "evaluation_data"
        )
        dataset = read(data_dir / "dataset.json")
        docs = {d["key"]: d for d in dataset["documents"]}
        cases = {c["id"]: c for c in dataset["cases"]}
        jf = ROOT / ("judge_" + run.replace("holdout_", "")) / "reviews.json"
        reviews = {r["id"]: r for r in read(jf)} if jf.exists() else {}
        readable.extend([f"## {run}", ""])
        for row in read(ROOT / run / "cases.json"):
            readable.extend([f"### {row['id']}：{row['question']}", ""])
            for u in cases[row["id"]]["expected"]:
                prefix = "final_data" if run != "regression" else "../evaluation_data"
                link = f"{prefix}/sources/{docs[u['document']]['file']}"
                readable.extend(
                    [
                        f"原文：[{u['document']}]({link}) / {u.get('section')}",
                        "",
                        "> " + u["quote"],
                        "",
                    ]
                )
            readable.extend(
                ["**真实回答**：" + row.get("answer", "权限阻断，未生成"), ""]
            )
            quality = reviews.get(row["id"], {}).get("quality")
            readable.extend(
                ["**模型评审**：`" + json.dumps(quality, ensure_ascii=False) + "`", ""]
            )
            for c in row.get("actual_context", []):
                readable.extend(
                    [
                        f"**实际引用[{c['label']}]**：{c['document']}",
                        "",
                        "> " + c["text"],
                        "",
                    ]
                )
            readable.extend(["**真人确认**：待审，填写本组human_review.csv。", ""])
    (ROOT / "review_readable.md").write_text(
        "\n".join(readable) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
