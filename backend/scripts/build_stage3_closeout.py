"""One bounded final suite: new explicit fact questions, disjoint dev/holdout."""

import hashlib
import json
import re
from pathlib import Path

import httpx

from scripts.build_stage3_completion import ChapterParser

ROOT = Path(__file__).resolve().parents[2] / "docs/stage3_closeout"


def main():
    (ROOT / "data/sources").mkdir(parents=True, exist_ok=True)
    documents, cases = [], []

    def doc(key, text, **kwargs):
        (ROOT / "data/sources" / f"{key}.md").write_text(text, encoding="utf-8")
        documents.append(
            dict(
                key=key,
                file=f"{key}.md",
                owner="a",
                course="main",
                status="ready",
                license="CC0-1.0",
                **kwargs,
            )
        )

    def case(key, split, question, units, scope, categories):
        cases.append(
            dict(
                id=key,
                split=split,
                question=question,
                answerable=bool(units),
                expected=units,
                reference_answer="；".join(u["quote"] for u in units)
                or "所选资料没有依据。",
                scope=dict(owner="a", course="main", documents=scope),
                categories=categories,
                key_facts=[u["quote"] for u in units],
                annotation_status="source_authored_program_verified_pending_human",
                human_review="pending",
            )
        )

    for split, values in [
        ("dev", [11, 4, 3, 17, 29, 8]),
        ("holdout", [19, 6, 5, 37, 61, 12]),
    ]:
        specs = [
            (
                "调用",
                "request_timeout",
                f"请求等待上限为{values[0]}秒。",
                "max_attempts",
                f"最多尝试{values[1]}次。",
                "retry_delay",
                f"失败后等待{values[2]}秒再重试。",
            ),
            (
                "缓存",
                "cache_capacity",
                f"最多缓存{values[3]}条记录。",
                "cache_ttl",
                f"记录写入后{values[4]}秒过期。",
                "eviction_policy",
                "容量不足时淘汰最早写入的记录。",
            ),
            (
                "消息",
                "batch_size",
                f"每批发送{values[5]}条消息。",
                "delivery_order",
                "发送顺序不代表消费者的完成顺序。",
                "duplicate_handling",
                "消费者使用消息ID去重，重复消息不再次扣款。",
            ),
            (
                "存储",
                "commit_rule",
                "同一事务的更新全部提交或全部回滚。",
                "visibility_rule",
                "提交前的修改对其他事务不可见。",
                "retention_rule",
                f"审计记录保留{values[4]}天。",
            ),
        ]
        keys = [f"{split}_spec_{i}" for i in range(len(specs))]
        for i, spec in enumerate(specs):
            topic, *facts = spec
            key, heading = keys[i], f"{topic}样本参数"
            units = []
            text = ""
            for n in range(3):
                term, fact = facts[n * 2 : n * 2 + 2]
                quote = f"{term}：{fact}"
                text += f"# {heading}\n{quote}\n\n"
                units.append(
                    dict(
                        unit=f"{key}:{term}",
                        document=key,
                        section=heading,
                        quote=quote,
                        subquestion=term,
                    )
                )
            doc(key, text)
            scope = keys if i % 2 else [key]
            case(
                f"{key}_single",
                split,
                f"根据{topic}样本，{facts[0]}记录的具体规则是什么？",
                units[:1],
                scope,
                ["mixed_terms", "explicit_fact"],
            )
            case(
                f"{key}_pair",
                split,
                f"{topic}样本中{facts[2]}是什么规则；{facts[4]}是什么规则？",
                units[1:],
                scope,
                ["multi_evidence"],
            )
            case(
                f"{key}_three",
                split,
                f"请分别列出{topic}样本的{facts[0]}、{facts[2]}、{facts[4]}规则。",
                units,
                scope,
                ["three_evidence", "same_section"],
            )
        for n in range(2):
            case(
                f"{split}_missing_{n}",
                split,
                [
                    "这些样本的服务商承诺故障赔偿比例是多少？",
                    "样本缓存的CPU利用率达到多少才开始淘汰？",
                ][n],
                [],
                keys,
                ["no_answer", "hard_negative"],
            )
        for n, (owner, course, status) in enumerate(
            [
                ("b", "main", "ready"),
                ("a", "other", "ready"),
                ("a", "main", "processing"),
                ("a", "main", "deleted"),
            ]
        ):
            key = f"{split}_boundary_{n}"
            doc(key, "# 受限\n专属实验数字为79312。")
            documents[-1].update(owner=owner, course=course, status=status)
            case(key, split, "专属实验数字是多少？", [], [key], ["boundary"])

    url = "https://greenteapress.com/thinkpython2/html/thinkpython2008.html"
    raw = ROOT / "raw/chapter7.html"
    raw.parent.mkdir(exist_ok=True)
    if not raw.exists():
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        raw.write_bytes(response.content)
    parser = ChapterParser()
    parser.feed(raw.read_text(encoding="utf-8"))
    parser.flush()
    sections, current = {}, None
    for tag, text in parser.blocks:
        if tag in {"h1", "h2", "h3"} and re.match(r"7\.\d+", text):
            current = text
            sections[current] = []
        elif current:
            sections[current].append(text)
    for split, prefix, question, quote in [
        (
            "dev",
            "7.1",
            "Python重新赋值会让现有变量发生什么变化？",
            "A new assignment makes an existing variable refer to a new value "
            "(and stop referring to the old value).",
        ),
        (
            "dev",
            "7.2",
            "能否直接更新一个还不存在的变量？教材说明的原因是什么？",
            "Python evaluates the right side before it assigns a value to x",
        ),
        (
            "holdout",
            "7.4",
            "教材的输入循环中，用户输入done时哪条语句让循环退出？",
            "If the user types done, the break statement exits the loop.",
        ),
        (
            "holdout",
            "7.5",
            "教材建议用什么差值条件代替浮点数直接相等来停止平方根迭代？",
            "if abs(y-x) < epsilon: break",
        ),
    ]:
        heading = next(h for h in sections if h.startswith(prefix + " "))
        key = "book_" + prefix.replace(".", "_")
        doc(key, "# " + heading + "\n" + "\n".join(sections[heading]))
        documents[-1].update(
            license="CC-BY-NC-3.0",
            author="Allen B. Downey",
            source=url,
            adaptation="HTML text; images omitted",
        )
        case(
            key,
            split,
            question,
            [
                dict(
                    unit=key,
                    document=key,
                    section=heading,
                    quote=quote,
                    subquestion=question,
                )
            ],
            [key],
            ["public_textbook", "long_paragraph"],
        )
    for row in cases:
        for unit in row["expected"]:
            content = (ROOT / "data/sources" / f"{unit['document']}.md").read_text(
                encoding="utf-8"
            )
            assert re.sub(r"\s+", "", unit["quote"]) in re.sub(r"\s+", "", content)
    dataset = dict(
        version="stage3-bounded-closeout-v1",
        documents=documents,
        cases=cases,
        relevance_granularity="evidence_anchor",
        provenance="CC0 authored fixtures and CC-BY-NC-3.0 Think Python chapter7",
    )
    file = ROOT / "data/dataset.json"
    file.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    profile = json.loads(
        (ROOT.parent / "stage3_followup/candidate.json").read_text(encoding="utf-8")
    )
    profile.update(section_backfill=True, grounded_answers=True)
    (ROOT / "candidate.json").write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )
    print(len(cases), "new cases", hashlib.sha256(file.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
