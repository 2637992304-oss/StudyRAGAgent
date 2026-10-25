"""Conservative, bounded table reading. No generated SQL, inferred FK or LLM truth.

Supported input is a captioned, line-preserving sample relation plus CREATE TABLE
DDL in the same document. Ambiguous cells, conflicting keys and incomplete DDL
fail closed. Ordinary prose/OCR remains on the existing RAG path.
"""

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field


def canonical(value):
    return re.sub(r"[\s_]", "", value).casefold()


def cell_key(value):
    """Do not conflate A_B, AB or A B when comparing data values."""
    return " ".join(value.split())


CAPTION = re.compile(
    r"(?im)^Figure\s+[A-Z]?\.?\d+(?:\.\d+)*\s+The\s+([a-z_ ]+?)\s+relation\.?$"
)
# A semicolon terminates a DDL block; nested type/check parentheses are allowed.
CREATE = re.compile(r"(?is)create table\s+([a-z_ ]+?)\s*\((.*?);\s*")
COLUMN = re.compile(
    r"(?im)^\s*\(?([a-z_][a-z_ ]*?)\s+"
    r"(varchar|char|numeric|integer|int|decimal|time)\s*(?:\((\d+)(?:,\d+)?\))?"
)
KEY = re.compile(r"(?is)primary key\s*\(([^)]+)\)")
FK = re.compile(
    r"(?is)foreign key\s*\(([^)]+)\)\s*references\s+([a-z_]+)"
    r"\s*(?:\(([^)]+)\))?"
)


@dataclass
class Schema:
    name: str
    columns: list[tuple[str, str, int]]
    primary: tuple[str, ...]
    foreign: list[tuple[tuple[str, ...], str, tuple[str, ...]]]
    sources: set[str]


@dataclass
class Row:
    table: str
    values: dict[str, str]
    sources: set[str] = field(default_factory=set)
    raw: str = ""


class Catalog:
    def __init__(self, items):
        self.items = {x.chunk.id: x for x in items}
        self.schemas = {}
        self.rows = defaultdict(list)
        self.errors = []
        self.table_sources = defaultdict(set)
        self.bad_tables = set()
        pages = defaultdict(list)
        for item in items:
            pages[item.chunk.page_start, item.chunk.slide_no].append(item)
        for group in pages.values():
            lines, line_sources = [], []
            for item in sorted(group, key=lambda x: x.chunk.chunk_index):
                current = item.chunk.text.splitlines()
                # Merge overlapping windows without counting duplicated rows twice.
                overlap = 0
                for size in range(1, min(len(lines), len(current)) + 1):
                    if lines[-size:] == current[:size]:
                        overlap = size
                for index in range(overlap):
                    line_sources[len(lines) - overlap + index].add(item.chunk.id)
                lines.extend(current[overlap:])
                line_sources.extend({item.chunk.id} for _ in current[overlap:])
            text = "\n".join(lines)
            spans, offset = [], 0
            for line, sources in zip(lines, line_sources, strict=True):
                spans.append((offset, offset + len(line), sources))
                offset += len(line) + 1
            for match in CREATE.finditer(text):
                name, body = canonical(match[1]), match[2]
                columns = [
                    (canonical(c[1]), c[2].lower(), int(c[3] or 0))
                    for c in COLUMN.finditer(body)
                ]
                pk = KEY.search(body)
                if not pk or not columns:
                    continue
                if len({column[0] for column in columns}) != len(columns):
                    self.bad_tables.add(name)
                    self.errors.append(f"ambiguous_column_identifiers:{name}")
                    continue
                primary = tuple(canonical(v) for v in pk[1].split(","))
                if not set(primary) <= {c[0] for c in columns}:
                    continue
                foreign = [
                    (
                        tuple(canonical(v) for v in f[1].split(",")),
                        canonical(f[2]),
                        tuple(canonical(v) for v in f[3].split(",")) if f[3] else (),
                    )
                    for f in FK.finditer(body)
                ]
                complete = [
                    item.chunk.id
                    for item in group
                    if match[0].strip() in item.chunk.text
                ]
                # Repeated primary-key lines in another table are not provenance.
                sources = (
                    {complete[0]}
                    if complete
                    else set().union(
                        *(
                            ids
                            for start, end, ids in spans
                            if start < match.end()
                            and end > match.start()
                            and end > start
                        )
                    )
                )
                schema = Schema(name, columns, primary, foreign, sources)
                previous = self.schemas.get(name)
                if previous and (
                    previous.columns != columns
                    or previous.primary != primary
                    or previous.foreign != foreign
                ):
                    self.bad_tables.add(name)
                    self.errors.append(f"conflicting_schema:{name}")
                else:
                    self.schemas[name] = schema
        pending = []
        for item in items:
            match = CAPTION.search(item.chunk.text)
            if match:
                name = canonical(match[1])
                if name in self.schemas:
                    pending.append((name, item))
                    self.table_sources[name].add(item.chunk.id)
        # Build unambiguous domains first (e.g. building, then department/name).
        domains = defaultdict(set)
        for _ in range(5):
            unresolved = []
            for name, item in pending:
                schema = self.schemas[name]
                header = "".join(c[0] for c in schema.columns)
                lines = item.chunk.text.splitlines()
                found = False
                for line in lines:
                    if canonical(line) == header:
                        found = True
                        continue
                    if not found or not line.strip() or CAPTION.match(line):
                        continue
                    parsed = parse_cells(line, schema.columns, domains)
                    if parsed is None:
                        unresolved.append((name, item, line))
                        continue
                    values = dict(
                        zip((c[0] for c in schema.columns), parsed, strict=True)
                    )
                    key = tuple(cell_key(values[k]) for k in schema.primary)
                    if any(value.casefold() == "null" for value in key):
                        self.bad_tables.add(name)
                        self.errors.append(f"null_primary_key:{name}")
                        continue
                    existing = next(
                        (
                            r
                            for r in self.rows[name]
                            if tuple(cell_key(r.values[k]) for k in schema.primary)
                            == key
                        ),
                        None,
                    )
                    if existing:
                        if existing.values != values:
                            self.bad_tables.add(name)
                            self.errors.append(f"conflicting_primary_key:{name}")
                        else:
                            existing.sources.add(item.chunk.id)
                    else:
                        self.rows[name].append(Row(name, values, {item.chunk.id}, line))
                    for column, value in values.items():
                        domains[column].add(cell_key(value))
            if not unresolved:
                break
            pending = list(
                {
                    (name, item.chunk.id): (name, item) for name, item, _ in unresolved
                }.values()
            )
        for name, _, _ in unresolved:
            self.bad_tables.add(name)
        for name in self.bad_tables:
            self.rows.pop(name, None)
        self.errors.extend(
            f"ambiguous_or_unparsed_rows:{n}"
            for n in sorted({n for n, _, _ in unresolved})
        )

    def links(self):
        for name, schema in self.schemas.items():
            for local, target, remote in schema.foreign:
                other = self.schemas.get(target)
                if (
                    other is None
                    or name in self.bad_tables
                    or target in self.bad_tables
                ):
                    continue
                remote = remote or other.primary
                if (
                    len(local) != len(remote)
                    or remote != other.primary
                    or not set(local) <= {c[0] for c in schema.columns}
                ):
                    continue
                yield name, local, target, remote


def parse_cells(line, columns, domains):
    words = line.split()
    solutions = []

    def walk(index, offset, cells, score):
        if len(solutions) > 512:
            return
        if index == len(columns):
            if offset == len(words):
                solutions.append((score, cells))
            return
        name, kind, length = columns[index]
        remaining = len(columns) - index - 1
        for end in range(offset + 1, len(words) - remaining + 1):
            value = " ".join(words[offset:end])
            if kind in {"numeric", "integer", "int", "decimal"}:
                if not re.fullmatch(r"-?\d+(?:\.\d+)?|null", value, re.I):
                    continue
            elif length and len(value) > length and value.casefold() != "null":
                continue
            if kind in {"varchar", "char"} and length <= 8 and end != offset + 1:
                continue
            match = cell_key(value) in domains.get(name, ())
            walk(index + 1, end, [*cells, value], score + (10 if match else 0))

    walk(0, 0, [], 0)
    if not solutions or len(solutions) > 512:
        return None
    best = max(s[0] for s in solutions)
    winners = {tuple(s[1]) for s in solutions if s[0] == best}
    return list(next(iter(winners))) if len(winners) == 1 else None


COLUMN_HINTS = {
    "工资": "salary",
    "薪水": "salary",
    "预算": "budget",
    "容量": "capacity",
    "成绩": "grade",
    "学分": "credits",
    "主键": "primary",
    "外键": "foreign",
}

# Explicit language-to-field hints; these are schema intent, never reference
# answers. Missing values are named so unrelated available numbers cannot be
# mistaken for an answer (e.g. salary is not a bonus).
FIELD_REQUESTS = {
    "工资": {"salary"},
    "薪水": {"salary"},
    "预算": {"budget"},
    "容量": {"capacity"},
    "姓名": {"name"},
    "成绩": {"grade"},
    "学分": {"credits", "totcred"},
    "奖金": {"bonus"},
    "出生年份": {"birthyear"},
    "出生日期": {"birthdate"},
    "生日": {"birthdate"},
    "年龄": {"age"},
    "入学年份": {"admissionyear"},
    "电话号码": {"phone"},
    "手机号": {"phone"},
    "货币单位": {"currency"},
    "汇率": {"exchangerate"},
    "bonus": {"bonus"},
    "birthday": {"birthdate"},
    "phone": {"phone"},
    "currency": {"currency"},
    "exchange rate": {"exchangerate"},
}


def mentions(query, name):
    return bool(
        re.search(r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])", query, re.I)
    )


def table_plan(items, seed_ids, query, aliases=None, max_hops=3, max_rows=40):
    """Plan from confirmed first-hop rows, with source-declared complete FK joins.

    A catalog is deliberately document-local: identical IDs in separate books
    cannot silently be joined. Every returned row has a reproducible source line.
    """
    if len({item.document.id for item in items}) > 1:
        return {"status": "cross_document_join_blocked"}, set()
    catalog = Catalog(items)
    expanded = query
    for term, values in (aliases or {}).items():
        if term.casefold() in query.casefold():
            expanded += " " + " ".join(values)
    requested = {n for n in catalog.schemas if mentions(expanded, n)}
    wanted_columns = {v for k, v in COLUMN_HINTS.items() if k in query}
    column_owners = defaultdict(set)
    for name, schema in catalog.schemas.items():
        for column in schema.columns:
            column_owners[column[0]].add(name)
    wanted_columns |= {
        c[0]
        for s in catalog.schemas.values()
        for c in s.columns
        if mentions(query, c[0]) and len(column_owners[c[0]]) == 1
    }
    targets = requested | {
        n
        for n, s in catalog.schemas.items()
        if any(c[0] in wanted_columns for c in s.columns)
    }
    anchors = set(re.findall(r"[a-z]+-\d+|\d{5,}", query, re.I))
    anchors = {canonical(v) for v in anchors}
    seeds = []
    named_seeds = []
    for name, rows in catalog.rows.items():
        for row in rows:
            if not row.sources & seed_ids:
                continue
            values = {canonical(v) for v in row.values.values()}
            literal = anchors & values
            name_match = any(
                (len(v) >= 3 or (len(v) == 2 and name in requested and c == "name"))
                and re.search(r"[a-zA-Z]", v)
                and mentions(query, v)
                for c, v in row.values.items()
                if c == "name"
                or (
                    c in catalog.schemas[name].primary
                    and c not in {"courseid", "prereqid", "semester", "year", "secid"}
                )
            )
            if not literal and not name_match:
                continue
            if (
                requested
                and name not in requested
                and not name_match
                and not any("-" in a for a in literal)
            ):
                continue
            if not row_filters(row, query):
                continue
            seeds.append(row)
            if name_match:
                named_seeds.append(row)
    if named_seeds:
        seeds = named_seeds
    counts = []
    if re.search(r"样例|sample", query, re.I) and re.search(
        r"多少|几条|计数|count|how many", query, re.I
    ):
        explicit_tables = {name for name in requested if mentions(query, name)}
        for name in sorted(explicit_tables or requested):
            if name in catalog.rows and name not in catalog.bad_tables:
                sources = catalog.table_sources[name]
                if sources & seed_ids:
                    counts.append(
                        {
                            "table": name,
                            "count": len(catalog.rows[name]),
                            "scope": "complete_parsed_sample_table_only",
                            "source_ids": sorted(sources),
                            "primary": list(catalog.schemas[name].primary),
                        }
                    )
    if not seeds and not counts:
        return {"status": "no_confirmed_row", "errors": catalog.errors}, set()
    # Prefer the explicit starting entity (student ID must not seed instructor ID).
    explicit = [n for n in requested if mentions(query, n)]
    for word, names in (aliases or {}).items():
        if word in query:
            explicit.extend(n for n in names if canonical(n) in requested)
    positions = {}
    for n in explicit:
        positions[canonical(n)] = min(
            [m.start() for m in re.finditer(re.escape(n), query, re.I)]
            + [
                query.index(k)
                for k, vs in (aliases or {}).items()
                if k in query and n in vs
            ]
            + [len(query)]
        )
    if anchors and any(re.fullmatch(r"\d{5,}", a) for a in anchors) and positions:
        first = min(positions, key=lambda n: (positions[n], len(catalog.rows[n])))
        primary_seeds = [r for r in seeds if r.table == first]
        if primary_seeds:
            seeds = primary_seeds
    links = list(catalog.links())
    graph = defaultdict(list)
    for link in links:
        a, _, b, _ = link
        graph[a].append((b, link, False))
        graph[b].append((a, link, True))
    requested_seeds = [row for row in seeds if row.table in requested]
    if requested_seeds:
        seeds = requested_seeds
    elif seeds and requested:

        def distance(name):
            queue = deque([(name, 0)])
            seen = {name}
            while queue:
                node, depth = queue.popleft()
                if node in requested:
                    return depth
                if depth < max_hops:
                    for nxt, _, _ in graph[node]:
                        if nxt not in seen:
                            seen.add(nxt)
                            queue.append((nxt, depth + 1))
            return max_hops + 1

        best = min(distance(row.table) for row in seeds)
        seeds = [row for row in seeds if distance(row.table) == best]
    chosen, used_links, source_ids = {}, [], set()
    join_matches = defaultdict(list)
    for count in counts:
        source_ids.update(count["source_ids"])
        source_ids.update(catalog.schemas[count["table"]].sources)

    def add(row):
        key = (row.table, tuple(row.values.items()))
        chosen[key] = row
        source_ids.add(sorted(row.sources)[0])

    for seed in seeds:
        add(seed)
        if len(chosen) > max_rows:
            return {"status": "row_limit", "errors": catalog.errors}, set()
        for target in sorted(targets):
            queue = deque([(seed.table, [])])
            paths = []
            while queue:
                node, steps = queue.popleft()
                if paths and len(steps) > len(paths[0]):
                    break
                if node == target:
                    paths.append(steps)
                    continue
                if len(steps) >= max_hops:
                    continue
                for nxt, link, reverse in graph[node]:
                    if nxt not in {seed.table, *(s[0] for s in steps)}:
                        queue.append((nxt, [*steps, (nxt, link, reverse)]))
            if not paths:
                continue
            # Sharing a parent (e.g. department) does not identify an advisor.
            # An unrequested shared-parent fan-out cannot establish that role.
            paths = [
                path
                for path in paths
                if not any(
                    not left[2] and right[2] and left[0] not in targets
                    for left, right in zip(path, path[1:])
                )
            ]
            if not paths:
                continue

            def path_cost(path):
                return sum(step[0] not in targets for step in path)

            best_cost = min(path_cost(path) for path in paths)
            paths = [path for path in paths if path_cost(path) == best_cost]
            for path in paths:
                current = [seed]
                for nxt, link, reverse in path:
                    a, local, b, remote = link
                    left, right = (remote, local) if reverse else (local, remote)
                    following = []
                    for row in current:
                        key = tuple(cell_key(row.values[k]) for k in left)
                        if any(value.casefold() == "null" for value in key):
                            continue
                        for other in catalog.rows[nxt]:
                            if key == tuple(
                                cell_key(other.values[k]) for k in right
                            ) and row_filters(other, query):
                                following.append(other)
                                add(other)
                                source, destination = (
                                    (other, row) if reverse else (row, other)
                                )
                                proof = {
                                    "from_values": {k: source.values[k] for k in local},
                                    "to_values": {
                                        k: destination.values[k] for k in remote
                                    },
                                    "from_source_ids": sorted(source.sources),
                                    "to_source_ids": sorted(destination.sources),
                                }
                                if proof not in join_matches[link]:
                                    join_matches[link].append(proof)
                    if following:
                        if link not in used_links:
                            used_links.append(link)
                        source_ids.update(catalog.schemas[a].sources)
                        source_ids.update(catalog.schemas[b].sources)
                    current = following
                    if len(chosen) > max_rows:
                        return {"status": "row_limit", "errors": catalog.errors}, set()
    for n in requested:
        if n in catalog.schemas and re.search(
            r"主键|外键|\bkey\b|关联|依据", query, re.I
        ):
            source_ids.update(catalog.schemas[n].sources)
    rows = [
        {
            "table": r.table,
            "values": r.values,
            "source_ids": sorted(r.sources),
            "source_quote": r.raw,
        }
        for r in chosen.values()
    ]
    return {
        "status": "verified_rows",
        "rows": rows,
        "counts": counts,
        "joins": [
            {
                "from": a,
                "columns": list(local),
                "to": b,
                "references": list(remote),
                "matches": join_matches[a, local, b, remote],
                "source_ids": sorted(
                    catalog.schemas[a].sources | catalog.schemas[b].sources
                ),
            }
            for a, local, b, remote in used_links
        ],
        "keys": {
            n: {
                "primary": list(catalog.schemas[n].primary),
                "source_ids": sorted(catalog.schemas[n].sources),
            }
            for n in requested
            if re.search(r"主键|外键|\bkey\b|关联|依据", query, re.I)
        },
        "errors": catalog.errors,
        "unverified_items": [
            term
            for term, columns in FIELD_REQUESTS.items()
            if mentions(query, term)
            and not columns & {k for row in rows for k in row["values"]}
        ],
        "limits": {"hops": max_hops, "rows": max_rows},
    }, source_ids


def row_filters(row, query):
    course_ids = {canonical(v) for v in re.findall(r"[a-z]+-\d+", query, re.I)}
    if (
        course_ids
        and "courseid" in row.values
        and not (row.table == "course" and re.search(r"先修|prereq", query, re.I))
        and canonical(row.values["courseid"]) not in course_ids
    ):
        return False
    seasons = r"Fall|Spring|Summer|Winter"
    pairs = {
        (season.casefold(), year)
        for season, year in re.findall(
            rf"(?<![a-z])({seasons})(?![a-z])\s*((?:19|20)\d{{2}})(?!\d)", query, re.I
        )
    }
    pairs |= {
        (season.casefold(), year)
        for year, season in re.findall(
            rf"(?<!\d)((?:19|20)\d{{2}})(?!\d)[年的\s]*({seasons})(?![a-z])",
            query,
            re.I,
        )
    }
    if pairs and "semester" in row.values and "year" in row.values:
        if (row.values["semester"].casefold(), row.values["year"]) not in pairs:
            return False
    for column in ("year", "semester"):
        if column not in row.values:
            continue
        values = (
            re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", query)
            if column == "year"
            else re.findall(rf"(?<![a-z])(?:{seasons})(?![a-z])", query, re.I)
        )
        if values and canonical(row.values[column]) not in {
            canonical(v) for v in values
        }:
            return False
    section = re.search(r"(?:section|sec(?:tion)?\s*id|小班)\s*([0-9]+)", query, re.I)
    if section and "secid" in row.values and row.values["secid"] != section[1]:
        return False
    return True


def render_verified(plan, evidence):
    """Render checked facts directly: a model cannot swap a room or invent a row.

    This is a transparent structured result, not a claim that every natural-language
    subquestion was answered. Unsupported inferences remain explicitly unknown.
    """
    if plan.get("status") != "verified_rows":
        return None
    labels = {item.chunk.id: i for i, item in enumerate(evidence, 1)}

    def citations(ids):
        return "".join(f"[{labels[v]}]" for v in ids if v in labels)

    lines = ["以下是程序逐行核对的样例数据及关联依据（不是全校实际统计）："]
    for count in plan.get("counts", []):
        lines.append(
            f"- {count['table']} 当前可解析样例按主键去重后共有 {count['count']} 条；"
            "不是全校实际总数。" + citations(count["source_ids"])
        )
    for row in plan["rows"]:
        refs = citations(row["source_ids"])
        if not refs:
            return None
        lines.append(
            f"- {row['table']}: "
            + "; ".join(f"{k}={v}" for k, v in row["values"].items())
            + f"。{refs}"
        )
    for join in plan["joins"]:
        lines.append(
            f"- 已核对完整外键：{join['from']}({', '.join(join['columns'])})"
            f" → {join['to']}({', '.join(join['references'])})；"
            "按所有列相等关联，null 不参与连接。" + citations(join["source_ids"])
        )
    for name, key in plan["keys"].items():
        lines.append(
            f"- {name} 主键：({', '.join(key['primary'])})。"
            + citations(key["source_ids"])
        )
    if any(
        canonical(v) == "null" for row in plan["rows"] for v in row["values"].values()
    ):
        refs = {
            v
            for row in plan["rows"]
            if any(value.casefold() == "null" for value in row["values"].values())
            for v in row["source_ids"]
        }
        lines.append(
            "- null 表示该样例未提供值，不能解释成 0 或零分。" + citations(sorted(refs))
        )
    if plan.get("unverified_items"):
        lines.append(
            "- 本题无法确定的项目："
            + "、".join(plan["unverified_items"])
            + "。当前核对的表格没有提供这些项目的依据，无法回答，"
            "不能用其他字段的数值替代。"
        )
    lines.append(
        "以上仅支持已列出的样例事实。其他所问内容若没有列出的直接依据，无法确定。"
    )
    return "\n".join(lines)
