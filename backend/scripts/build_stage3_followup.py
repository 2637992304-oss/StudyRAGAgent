"""Build new CC0 fixtures without changing any previously reviewed data."""

import hashlib
import json
import re
from pathlib import Path

import httpx

from scripts.build_stage3_completion import ChapterParser

ROOT = Path(__file__).resolve().parents[2] / "docs/stage3_followup"


def main():
    target = ROOT / "data"
    (target / "sources").mkdir(parents=True, exist_ok=True)
    documents, cases = [], []
    topics = {
        "dev": [
            (
                "事务",
                "atomicity",
                "原子性",
                "一个事务的操作全部成功或全部回滚。",
                "durability",
                "持久性",
                "事务提交后的结果不会因进程退出而丢失。",
            ),
            (
                "接口",
                "idempotence",
                "幂等性",
                "重复执行同一请求与执行一次具有相同效果。",
                "retry",
                "重试",
                "临时失败后可以再次发送请求，但必须考虑重复执行。",
            ),
            (
                "权限",
                "authentication",
                "认证",
                "认证核验访问者的身份。",
                "authorization",
                "授权",
                "授权判断访问者是否可以执行某项操作。",
            ),
            (
                "缓存",
                "TTL",
                "存活时间",
                "本样本的缓存记录在写入后47秒过期。",
                "eviction",
                "淘汰",
                "本样本容量不足时先淘汰最近最少使用的记录。",
            ),
            (
                "评估",
                "precision",
                "精确率",
                "精确率是检出结果中相关结果的比例。",
                "recall",
                "召回率",
                "召回率是全部相关结果中被检出的比例。",
            ),
        ],
        "holdout": [
            (
                "事务实验",
                "atomicity",
                "原子性",
                "一次事务内的更新作为整体提交，失败时整体回滚。",
                "isolation",
                "隔离性",
                "并发事务相互隔离，具体可见性由隔离级别决定。",
            ),
            (
                "流控",
                "backpressure",
                "背压",
                "消费者处理不过来时通知生产者降低发送速率。",
                "buffer",
                "缓冲",
                "本样本队列最多暂存23条消息。",
            ),
            (
                "索引",
                "primary_key",
                "主键",
                "主键唯一标识一行且不允许空值。",
                "foreign_key",
                "外键",
                "外键维护引用关系，不保证引用列在本表唯一。",
            ),
            (
                "传输",
                "TCP",
                "可靠传输",
                "TCP提供有序的字节流传输。",
                "UDP",
                "数据报",
                "UDP不保证数据报到达或到达顺序。",
            ),
            (
                "运行",
                "timeout",
                "超时",
                "本样本单次调用的等待上限是13秒。",
                "deadline",
                "截止时间",
                "截止时间约束整个操作必须完成的时间点。",
            ),
        ],
    }

    def document(key, text, owner="a", course="main", status="ready"):
        (target / "sources" / f"{key}.md").write_text(text, encoding="utf-8")
        documents.append(
            dict(
                key=key,
                file=f"{key}.md",
                owner=owner,
                course=course,
                status=status,
                license="CC0-1.0",
            )
        )

    def case(key, split, question, expected, scope, categories):
        cases.append(
            dict(
                id=key,
                split=split,
                question=question,
                categories=categories,
                scope=dict(owner="a", course="main", documents=scope),
                answerable=bool(expected),
                expected=expected,
                reference_answer="；".join(u["quote"] for u in expected)
                or "所选资料没有依据。",
                key_facts=[u["quote"] for u in expected],
                annotation_status="source_authored_anchor_program_verified_semantics_pending_human",
                human_review="pending",
            )
        )

    for split, rows in topics.items():
        for index, (topic, en1, cn1, fact1, en2, cn2, fact2) in enumerate(rows):
            key = f"{split}_{index}"
            sections = [
                f"{cn1}术语索引",
                f"{cn1}概念正文",
                f"{cn2}术语索引",
                f"{cn2}概念正文",
            ]
            text = (
                f"# {sections[0]}\n本章出现英文标识 {en1}；定义见后文。\n\n"
                f"# {sections[1]}\n{topic}主题：{en1}。{fact1}\n\n"
                f"# {sections[2]}\n本章出现英文标识 {en2}；定义见后文。\n\n"
                f"# {sections[3]}\n{topic}主题：{en2}。{fact2}\n"
            )
            document(key, text)
            units = [
                dict(
                    unit=f"{key}:{n}",
                    document=key,
                    section=sections[1 if n == 0 else 3],
                    quote=fact,
                    subquestion=f"{cn}含义",
                )
                for n, (cn, fact) in enumerate([(cn1, fact1), (cn2, fact2)])
            ]
            questions = [
                (f"{topic}资料中的{en1}是什么意思？", units[:1], ["mixed_terms"]),
                (f"解释{cn1}；解释{cn2}。", units, ["multi_evidence"]),
                (f"{en2}在本资料的具体含义是什么？", units[1:], ["definition_body"]),
            ]
            if index == 0:
                questions[0] = (
                    "数据库事务如何做到要么全成要么全撤？"
                    if split == "dev"
                    else "这份数据库事务实验里，怎样实现要么全做要么全不做？",
                    units[:1],
                    ["paraphrase", "atomicity_regression_family"],
                )
            for n, (question, expected, categories) in enumerate(questions):
                case(f"{key}_{n}", split, question, expected, [key], categories)
            if index < 3:
                case(
                    f"{key}_missing",
                    split,
                    f"{topic}资料有没有给出管理员邮箱以及部署服务器的IP地址？",
                    [],
                    [key],
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
            key = f"{split}_blocked_{n}"
            document(key, "# 受限资料\n专属校验数字为98731。", owner, course, status)
            case(key, split, "专属校验数字是多少？", [], [key], ["boundary"])

    # A previously unused chapter, with disjoint section documents for dev/test.
    # CC BY-NC source is kept separate from our CC0 material.
    url = "https://greenteapress.com/thinkpython2/html/thinkpython2007.html"
    raw = ROOT / "raw" / "thinkpython_ch6.html"
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
        if tag in {"h1", "h2", "h3"} and re.match(r"6\.\d+", text):
            current = text
            sections[current] = []
        elif current:
            sections[current].append(text)
    specs = [
        (
            "dev",
            "6.1",
            "return语句执行后，还会执行后面的代码吗？",
            "As soon as a return statement runs, the function terminates "
            "without executing any subsequent statements.",
        ),
        (
            "dev",
            "6.2",
            "incremental development的目标是什么？",
            "The goal of incremental development is to avoid long debugging "
            "sessions by adding and testing only a small amount of code at a time.",
        ),
        (
            "holdout",
            "6.5",
            "factorial(0)应该返回什么？",
            "This definition says that the factorial of 0 is 1",
        ),
        (
            "holdout",
            "6.6",
            "阅读函数调用时，leap of faith指什么？",
            "you assume that the function works correctly "
            "and returns the right result.",
        ),
    ]
    for split, prefix, question, quote in specs:
        heading = next(h for h in sections if h.startswith(prefix + " "))
        key = "book_" + prefix.replace(".", "_")
        text = "# " + heading + "\n" + "\n".join(sections[heading])
        document(key, text)
        documents[-1].update(
            license="CC-BY-NC-3.0",
            author="Allen B. Downey",
            source=url,
            title="Think Python 2e, Chapter 6",
            adaptation="HTML text extraction; figures omitted",
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
            ["public_textbook", "mixed_terms", "long_paragraph"],
        )
    for row in cases:
        for unit in row["expected"]:
            assert unit["quote"] in (
                target / "sources" / f"{unit['document']}.md"
            ).read_text(encoding="utf-8")
    payload = dict(
        version="stage3-followup-v1",
        relevance_granularity="evidence_anchor",
        provenance=(
            "CC0 controlled fixtures plus attributed CC-BY-NC-3.0 textbook sections"
        ),
        documents=documents,
        cases=cases,
    )
    (target / "dataset.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    profile = json.loads(
        (ROOT.parent / "stage3_completion_runs/frozen_profile.json").read_text(
            encoding="utf-8"
        )
    )
    profile.update(query_normalization=True, facet_coverage=True)
    # This is a candidate, not a production default or a successful experiment.
    (ROOT / "candidate.json").write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )
    print(
        len(cases),
        "new questions; dataset_sha256",
        hashlib.sha256((target / "dataset.json").read_bytes()).hexdigest(),
    )


if __name__ == "__main__":
    main()
