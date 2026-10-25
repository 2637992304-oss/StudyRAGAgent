"""Bounded query-only normalization; no source text or generated answers."""

import re


def normalize_query(query: str) -> str:
    # Require database context and both halves of an all-or-nothing expression.
    # Keep this explicit and auditable; don't infer a topic from generic success.
    if re.search(r"数据库|事务|transaction|database", query, re.I):
        query = re.sub(
            r"要么(?:全部成功|全成功|全成|全做)[，,\s]*"
            r"要么(?:全部回滚|全部撤销|全撤|全不做)",
            "全部成功或全部回滚（事务原子性 atomicity）",
            query,
        )
    return query


def query_facets(query: str) -> list[str]:
    """Only explicit question boundaries; never claim semantic decomposition."""
    parts = [
        p.strip(" ，,。？?")
        for p in re.split(r"[；;？?]+|，并(?:说明|解释)|以及", query)
        if p.strip(" ，,。？?")
    ]
    if 1 < len(parts) <= 3 and all(len(p) >= 4 for p in parts):
        return parts
    return [query]
