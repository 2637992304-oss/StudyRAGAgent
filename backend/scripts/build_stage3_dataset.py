"""Build versioned, self-authored public evaluation material (no LLM labels)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "docs" / "evaluation_data"

# Each fact is an authored definition of the synthetic course, not a real textbook.
TOPICS = [
    (
        "database",
        "数据库",
        "主键唯一标识表中的一行，且不能为 NULL。",
        "外键引用另一张表的键，用于维护参照完整性。",
        "表里每条记录靠什么区分？",
        "primary_key 可以为 NULL 吗？",
        "试题：两行能使用相同的主键吗？",
        "不能，主键必须唯一。",
    ),
    (
        "transaction",
        "事务",
        "原子性表示一个事务的操作全部成功或全部回滚。",
        "持久性表示事务提交后，其结果不会因进程退出而丢失。",
        "一组数据库操作如何做到要么全成要么全撤？",
        "ACID 中的 durability 指什么？",
        "试题：转账扣款成功但入账失败，应怎样处理该事务？",
        "全部回滚。",
    ),
    (
        "index",
        "索引",
        "B_tree 索引按键有序组织数据，支持范围查找。",
        "索引需要额外存储，并增加写入时的维护开销。",
        "怎样加速按数值区间查找？",
        "B_tree 为什么适合 range query？",
        "试题：索引会完全没有写入成本吗？",
        "不会，索引增加写入维护开销。",
    ),
    (
        "network",
        "网络",
        "TCP 提供可靠且有序的字节流传输。",
        "UDP 不保证报文到达或到达顺序。",
        "哪种协议能保证字节按顺序送到？",
        "UDP 是否保证 delivery？",
        "试题：资料要求传输可靠且有序，应选 TCP 还是 UDP？",
        "TCP。",
    ),
    (
        "cache",
        "缓存",
        "LRU 在容量不足时淘汰最近最少使用的条目。",
        "TTL 是缓存条目的生存时间，过期后应失效。",
        "缓存满了，最久没被访问的数据该如何处理？",
        "TTL 到期后条目是否有效？",
        "试题：LRU 缓存满时，应淘汰刚刚访问的条目吗？",
        "不应，淘汰最近最少使用的条目。",
    ),
    (
        "algorithm",
        "算法",
        "二分查找要求输入序列已经有序。",
        "二分查找的时间复杂度为 O(log_n)。",
        "把查找范围反复折半前，数据需要满足什么条件？",
        "binary_search 的 complexity 是多少？",
        "试题：能直接对无序数组使用二分查找吗？",
        "不能，必须先保证有序。",
    ),
    (
        "isolation",
        "隔离",
        "读已提交隔离级别禁止读取其他事务尚未提交的数据。",
        "可重复读要求同一事务重复读取同一行时结果保持一致。",
        "怎样避免读到别人还没提交的修改？",
        "read_committed 允许 dirty_read 吗？",
        "试题：读已提交能否读取其他事务的未提交更新？",
        "不能。",
    ),
    (
        "security",
        "安全",
        "密码应保存带随机盐的不可逆哈希，而非明文。",
        "最小权限原则要求账号只获得完成工作所需的权限。",
        "用户口令怎样保存更合适？",
        "least_privilege 如何分配权限？",
        "试题：数据库中应直接保存用户明文密码吗？",
        "不应，应保存带随机盐的哈希。",
    ),
    (
        "retrieval",
        "检索",
        "Recall 表示相关证据中被检索到的比例。",
        "Precision 表示检索结果中相关证据所占的比例。",
        "应该找到的证据里实际找到了多少，用什么衡量？",
        "Precision 的分母是什么？",
        "试题：应召回四条证据，实际召回三条，Recall 是多少？",
        "3/4，即 0.75。",
    ),
    (
        "chunking",
        "切块",
        "切块重叠用于保留相邻片段边界的上下文。",
        "片段过小可能拆散表头和数据，造成证据语义不完整。",
        "资料切开之后，如何保留交界处的语义？",
        "chunk_overlap 的作用是什么？",
        "试题：将表头和每个单元格分别切成孤立片段合适吗？",
        "不合适，会破坏表格语义。",
    ),
]


def build():
    ROOT.mkdir(parents=True, exist_ok=True)
    sources = ROOT / "sources"
    sources.mkdir(exist_ok=True)
    docs, cases = [], []

    def document(key, content, **extra):
        filename = key + ".md"
        (sources / filename).write_text(content, encoding="utf-8")
        docs.append(
            {
                "key": key,
                "file": filename,
                "owner": "a",
                "course": "main",
                "status": "ready",
                "source": "self-authored CC0 synthetic course",
                **extra,
            }
        )

    def case(category, question, expected, answer, scope=None, facts=None, **extra):
        i = len(cases)
        cases.append(
            {
                "id": f"q{i + 1:03}",
                "split": "holdout" if i % 2 else "dev",
                "categories": [category],
                "question": question,
                "scope": {"owner": "a", "course": "main", "documents": scope},
                "answerable": bool(expected),
                "expected": expected,
                "reference_answer": answer,
                "key_facts": facts or [answer],
                "annotation_status": "programmatic_from_authored_fixture",
                "human_review": "pending",
                **extra,
            }
        )

    units = []
    for key, title, first, second, synonym, term, exam, answer in TOPICS:
        context = (
            f"本节讨论{title}的定义及应用。答题应以给出的定义为依据，"
            "区分概念的前提条件与适用范围，不能将一种机制的性质直接套用到另一种机制。"
        )
        aliases = {
            "database": "primary_key NULL",
            "transaction": "ACID durability",
            "index": "B_tree range query",
            "network": "TCP UDP delivery",
            "cache": "LRU TTL",
            "algorithm": "binary_search complexity O(log_n)",
            "isolation": "read_committed dirty_read",
            "security": "least_privilege",
            "retrieval": "Recall Precision",
            "chunking": "chunk_overlap",
        }[key]
        document(
            key,
            f"# {title}定义\n{context}{first} 术语：{aliases}。\n"
            f"# {title}补充\n{context}{second}\n"
            f"# {title}练习\n{exam} 参考答案：{answer}\n",
        )
        a = {
            "unit": key + ":definition",
            "document": key,
            "section": title + "定义",
            "quote": first,
        }
        b = {
            "unit": key + ":extra",
            "document": key,
            "section": title + "补充",
            "quote": second,
        }
        e = {
            "unit": key + ":exercise",
            "document": key,
            "section": title + "练习",
            "quote": answer,
        }
        units.append((a, b, e))
    for index, item in enumerate(TOPICS):
        key, _, first, _, synonym, _, _, _ = item
        case("synonym", synonym, [units[index][0]], first)
    for index, item in enumerate(TOPICS):
        key, _, first, second, _, term, _, _ = item
        use_second = index in {1, 3, 4, 5, 7, 8}
        case(
            "term",
            term,
            [units[index][int(use_second)]],
            second if use_second else first,
        )
    for index, item in enumerate(TOPICS):
        case("exercise", item[6], [units[index][2]], item[7])
    for index, item in enumerate(TOPICS):
        next_index = (index + 2) % len(TOPICS)
        other = TOPICS[next_index]
        case(
            "multi_evidence",
            f"分别说明{item[1]}定义和{other[1]}补充中的核心结论。",
            [units[index][0], units[next_index][1]],
            item[2] + other[3],
            facts=[item[2], other[3]],
        )
    for index, item in enumerate(TOPICS):
        case(
            "no_answer",
            f"{item[1]}课程下次考试的具体日期是什么？",
            [],
            "当前所选资料中没有足够依据。",
            scope=[item[0]],
        )
    roles = [
        ("foreign", {"owner": "b"}),
        ("other_course", {"course": "other"}),
        ("excluded", {}),
        ("failed", {"status": "failed"}),
        ("deleted", {"status": "deleted"}),
    ]
    for role, extra in roles:
        document(
            role, f"# 边界\n{role} 专属答案：琥珀编号 7319，实验室在北楼。", **extra
        )
        for question in (
            f"{role} 专属答案的琥珀编号是多少？",
            f"{role} 专属答案的实验室在哪里？",
        ):
            case(
                "boundary",
                question,
                [],
                "当前所选资料中没有足够依据。",
                scope=["database", role] if role != "excluded" else ["database"],
                boundary_kind=role,
            )
    document(
        "table",
        "# Schema\nThe course relation has 4 attributes: "
        "course_id, title, dept_name, credits.\n"
        "# Sample rows\ncourse_id | title | dept_name | credits\n"
        "CS101 | Intro | CS | 4\nCS202 | Networks | CS | 3\n"
        "MA101 | Calculus | Math | 4\nThe sample course relation contains 3 rows.\n",
    )
    for name, question, section, quote in [
        ("schema", "course relation里面有多少个属性？", "Schema", "4 attributes"),
        ("rows", "course relation样本表里面有多少条数据？", "Sample rows", "3 rows"),
    ]:
        case(
            "table",
            question,
            [
                {
                    "unit": "table:" + name,
                    "document": "table",
                    "section": section,
                    "quote": quote,
                }
            ],
            "4" if name == "schema" else "3",
            scope=["table"],
        )
    document("formula", "# 公式\n本课程中等差数列求和公式为 S_n = n*(a_1+a_n)/2。\n")
    case(
        "formula",
        "等差数列的求和公式是什么？",
        [
            {
                "unit": "formula:sum",
                "document": "formula",
                "section": "公式",
                "quote": "S_n = n*(a_1+a_n)/2",
            }
        ],
        "S_n = n*(a_1+a_n)/2",
        scope=["formula"],
    )
    dataset = {
        "version": "stage3-v1",
        "license": "CC0-1.0",
        "provenance": "Authored synthetic educational fixtures; not user documents.",
        "documents": docs,
        "cases": cases,
    }
    (ROOT / "dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Built {len(cases)} cases, {len(docs)} documents")


if __name__ == "__main__":
    build()
