"""New fixed development/holdout fixtures, plus attributed open-textbook chapters."""

import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2] / "docs/stage3_completion_data"


class ChapterParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.buffer, self.tag = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag in {"h1", "h2", "h3", "p", "pre", "li", "dt", "dd"}:
            if self.tag:
                self.flush()
            self.tag = tag

    def handle_data(self, data):
        if self.tag:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == self.tag:
            self.flush()

    def flush(self):
        text = " ".join("".join(self.buffer).split())
        if text:
            self.blocks.append((self.tag, text))
        self.tag, self.buffer = None, []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    (ROOT / "sources").mkdir(parents=True, exist_ok=True)
    (ROOT / "raw").mkdir(exist_ok=True)
    sources, documents, cases = {}, [], []

    def doc(key, text, owner="a", course="main", status="ready", **extra):
        sources[key] = text
        (ROOT / "sources" / (key + ".md")).write_text(text, encoding="utf-8")
        documents.append(
            dict(
                key=key,
                file=key + ".md",
                owner=owner,
                course=course,
                status=status,
                **extra,
            )
        )

    def case(key, split, question, units, answer, categories, scope=None):
        cases.append(
            dict(
                id=key,
                split=split,
                question=question,
                categories=categories,
                scope=scope or dict(owner="a", course="main", documents=None),
                answerable=bool(units),
                expected=units,
                reference_answer=answer,
                key_facts=[x["quote"] for x in units],
                annotation_status="authored_from_source_pending_human_review",
                human_review="pending",
            )
        )

    def unit(doc_key, section, quote, subquestion=None):
        assert quote in sources[doc_key], (doc_key, quote)
        return dict(
            unit=doc_key + ":" + section + ":" + quote[:10],
            document=doc_key,
            section=section,
            quote=quote,
            subquestion=subquestion or section,
        )

    topics = {
        "dev": [
            ("durability", "持久性", "事务提交后，其结果不会因进程退出而丢失。"),
            ("atomicity", "原子性", "一个事务的操作全部成功或全部回滚。"),
            ("encapsulation", "封装", "把相关实现组织在一个明确的接口后面。"),
            ("generalization", "泛化", "用参数表达变化，使同一函数适用更多输入。"),
            ("refactoring", "重构", "在保持外部行为的前提下调整程序结构。"),
            (
                "recursion",
                "递归",
                "函数通过调用自身求解规模更小的同类问题，并需要终止条件。",
            ),
        ],
        "holdout": [
            ("isolation", "隔离性", "并发事务之间的中间状态需要按约定级别隔离。"),
            ("immutable", "不可变", "对象创建后不能原地修改其值。"),
            ("traversal", "遍历", "按顺序访问序列中的每一个元素。"),
            ("slice", "切片", "从序列中取出指定索引范围的子序列。"),
            ("index", "索引", "表示元素在序列中的位置。"),
            ("comparison", "比较", "根据运算符判断两个值的关系。"),
        ],
    }
    for split, items in topics.items():
        prefix = "d" if split == "dev" else "h"
        units = []
        for i, (english, chinese, fact) in enumerate(items):
            key = f"{prefix}_concept_{i}"
            text = (
                f"# {chinese}术语索引\n本节出现英文标识符 {english}，定义见概念正文。\n"
                f"# {chinese}概念正文\n{chinese}是指：{fact}\n"
                f"# {chinese}工程示例\n"
                f"案例编号为{410 + i if split == 'dev' else 720 + i}。"
                "这是自生成教材示例；没有给出任何性能承诺、考试日期或修复时间。\n"
            )
            doc(key, text, license="CC0-1.0")
            u = unit(key, chinese + "概念正文", fact, chinese)
            units.append(u)
            case(
                f"{prefix}01{i}",
                split,
                f"文档中的{english}具体是什么意思？请给出定义。",
                [u],
                fact,
                ["term_separation", "mixed_language"],
            )
        for i in range(3):
            selected = [units[i * 2], units[i * 2 + 1]]
            cn = [items[i * 2][1], items[i * 2 + 1][1]]
            case(
                f"{prefix}02{i}",
                split,
                f"请分别解释{cn[0]}与{cn[1]}，两部分都要回答。",
                selected,
                "；".join(x["quote"] for x in selected),
                ["multi_evidence"],
            )
        for i, q in enumerate(
            [
                "资料承诺的99分位响应时间具体是多少毫秒？",
                "这些定义对应的下次考试是哪一天？",
                "案例发生问题后，供应商保证几小时完成修复？",
            ]
        ):
            case(
                f"{prefix}03{i}",
                split,
                q,
                [],
                "当前所选资料中没有足够依据。",
                ["no_answer", "hard_negative"],
                dict(owner="a", course="main", documents=[f"{prefix}_concept_0"]),
            )
        bad = f"{prefix}_foreign"
        doc(
            bad,
            f"# 私有答案\n{prefix}专属实验室是紫杉楼，禁止跨账号提供。\n",
            owner="b",
            license="CC0-1.0",
        )
        case(
            f"{prefix}040",
            split,
            f"{prefix}专属实验室在哪里？",
            [],
            "范围被拒绝",
            ["boundary"],
            dict(owner="a", course="main", documents=[bad]),
        )

    textbook_manifest = []
    for key, page in [
        ("book_dev", "thinkpython2005.html"),
        ("book_holdout", "thinkpython2009.html"),
    ]:
        url = "https://www.greenteapress.com/thinkpython2/html/" + page
        raw = ROOT / "raw" / ("ch4.html" if key == "book_dev" else "ch8.html")
        if args.download or not raw.exists():
            response = httpx.get(url, timeout=60, follow_redirects=True)
            response.raise_for_status()
            raw.write_bytes(response.content)
        html = raw.read_text(encoding="utf-8")
        chapter = ChapterParser()
        chapter.feed(html)
        started = False
        lines = []
        for tag, value in chapter.blocks:
            if tag == "h1":
                started = True
            if not started:
                continue
            lines.append(("# " if tag in {"h1", "h2", "h3", "dt"} else "") + value)
        text = "\n".join(lines) + "\n"
        doc(
            key,
            text,
            license="CC-BY-NC-3.0",
            source_url=url,
            author="Allen B. Downey",
            transformations=(
                "HTML block text; whitespace normalized; "
                "headings marked #; images not extracted"
            ),
        )
        textbook_manifest.append(
            dict(
                key=key,
                url=url,
                raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                license="CC-BY-NC-3.0",
                author="Allen B. Downey",
            )
        )

    annotations = {
        "dev": [
            (
                55,
                "Wrapping a piece of code up in a function is called encapsulation.",
                "教材中encapsulation是什么意思？",
                "把一段代码包装进函数称为封装。",
            ),
            (
                59,
                "Adding a parameter to a function is called generalization",
                "函数如何通过generalization变得更通用？",
                "通过增加参数，让函数适用于更多情况。",
            ),
            (
                90,
                "rearranging a program to improve interfaces "
                "and facilitate code re-use",
                "refactoring的目的是什么？",
                "重排程序结构以改进接口并方便代码复用。",
            ),
            (
                101,
                "A docstring is a string at the beginning of a function",
                "docstring放在函数什么位置，解释什么？",
                "放在函数开头，用于解释接口。",
            ),
            (
                132,
                "A requirement that should be satisfied by the caller "
                "before a function starts.",
                "precondition要求由谁在什么时候满足？",
                "调用者应在函数开始前满足。",
            ),
            (
                66,
                "they include the parameter names as “keywords”",
                "为什么这种传参方式叫keyword arguments？",
                "因为参数列表里包含参数名作为关键字。",
            ),
        ],
        "holdout": [
            (
                10,
                "the offset of the first letter is zero",
                "字符串第一个字符的索引从几开始？",
                "从0开始。",
            ),
            (
                18,
                "returns the number of characters in a string",
                "内置len用于字符串时返回什么？",
                "返回字符串的字符数。",
            ),
            (
                41,
                "including the first but excluding the last",
                "教材中[n:m]切片包含左右两个端点吗？",
                "包含起点n，不包含终点m。",
            ),
            (
                52,
                "strings are immutable, which means you can’t "
                "change an existing string",
                "Python字符串immutable意味着什么，想改变内容怎么办？",
                "不能修改已有字符串，可以创建一个改变后的新字符串。",
            ),
            (
                58,
                "If the character is not found, the function returns -1.",
                "教材find函数没找到字符时返回多少？",
                "返回-1。",
            ),
            (
                86,
                "returns True if the first appears as a substring in the second",
                "in操作符用于两个字符串，什么时候返回True？",
                "第一个字符串是第二个的子串时。",
            ),
        ],
    }
    for split, entries in annotations.items():
        prefix = "d" if split == "dev" else "h"
        key = "book_" + split
        source_lines = sources[key].splitlines()
        units = []
        for i, (number, quote, question, answer) in enumerate(entries):
            assert quote in source_lines[number - 1], (key, number)
            section = next(
                line[2:]
                for line in reversed(source_lines[:number])
                if line.startswith("# ")
            )
            u = unit(key, section, quote, f"book_fact_{i}")
            u["source_line"] = number
            units.append(u)
            case(
                f"{prefix}10{i}",
                split,
                question,
                [u],
                answer,
                ["open_textbook", "mixed_language"],
                dict(owner="a", course="main", documents=[key]),
            )
        for i in range(2):
            pair = [units[i * 2], units[i * 2 + 1]]
            q = f"请同时回答两个问题：{entries[i * 2][2]} {entries[i * 2 + 1][2]}"
            case(
                f"{prefix}11{i}",
                split,
                q,
                pair,
                entries[i * 2][3] + entries[i * 2 + 1][3],
                ["open_textbook", "multi_evidence"],
                dict(owner="a", course="main", documents=[key]),
            )
        for i, q in enumerate(
            [
                "本章承诺相关代码在十万字符输入下耗时多少毫秒？",
                "本章对应课程的期末考试具体是哪一天？",
            ]
        ):
            case(
                f"{prefix}12{i}",
                split,
                q,
                [],
                "当前所选资料中没有足够依据。",
                ["open_textbook", "no_answer", "hard_negative"],
                dict(owner="a", course="main", documents=[key]),
            )
        extra = (
            [
                (
                    75,
                    "r belongs in the interface because it specifies "
                    "the circle to be drawn",
                    "为什么circle接口应该有r参数，而不宜把n暴露给使用者？",
                    "r指定圆，n是绘制细节。",
                ),
                (
                    103,
                    "all docstrings are triple-quoted strings",
                    "教材约定docstring用哪种引号形式？",
                    "使用三引号字符串。",
                ),
            ]
            if split == "dev"
            else [
                (
                    22,
                    "the six letters are numbered 0 to 5",
                    "banana有六个字符，为何用索引6取最后字符会错？",
                    "从0计数，索引为0到5，应取length-1。",
                ),
                (
                    24,
                    "fruit[-1] yields the last letter",
                    "fruit[-1]与fruit[-2]分别得到哪个字符位置？",
                    "分别得到最后一个和倒数第二个字符。",
                ),
            ]
        )
        for i, (number, quote, question, answer) in enumerate(extra):
            section = next(
                line[2:]
                for line in reversed(source_lines[:number])
                if line.startswith("# ")
            )
            case(
                f"{prefix}13{i}",
                split,
                question,
                [unit(key, section, quote)],
                answer,
                ["open_textbook", "exercise", "long_passage"],
                dict(owner="a", course="main", documents=[key]),
            )
    for item in cases:
        if item["scope"]["documents"] is None:
            prefix = "d" if item["split"] == "dev" else "h"
            item["scope"]["documents"] = [f"{prefix}_concept_{i}" for i in range(6)] + [
                "book_" + item["split"]
            ]
    payload = dict(
        version="stage3-completion-v1",
        license="per-document: CC0-1.0 / CC-BY-NC-3.0",
        provenance=(
            "Authored synthetic contrasts plus attributed Think Python 2e "
            "chapters 4 and 8. Distinct documents/topics by split. "
            "Questions/reference interpretations pending human confirmation."
        ),
        documents=documents,
        cases=cases,
        textbook_manifest=textbook_manifest,
    )
    (ROOT / "dataset.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "provenance.json").write_text(
        json.dumps(textbook_manifest, indent=2), encoding="utf-8"
    )
    print(
        "cases",
        len(cases),
        "dev",
        sum(c["split"] == "dev" for c in cases),
        "holdout",
        sum(c["split"] == "holdout" for c in cases),
    )


if __name__ == "__main__":
    main()
