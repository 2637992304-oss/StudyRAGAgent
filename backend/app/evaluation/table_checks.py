"""Program checks are distinct from independent semantic/human review."""

from app.table_evidence import cell_key


def check_case(case, expected=None):
    errors, checked_rows, checked_joins = [], 0, 0
    context = {x["chunk_id"]: x["text"] for x in case.get("actual_context", [])}
    documents = case.get("trace", {}).get("table_verification", {}).get("documents", [])
    plans = [p for p in documents if p.get("status") == "verified_rows"]
    answer = case.get("answer", "")
    for plan in plans:
        for row in plan["rows"]:
            checked_rows += 1
            if not any(
                row["source_quote"] in context.get(i, "") for i in row["source_ids"]
            ):
                errors.append("row_not_in_actual_context")
            if " ".join(row["source_quote"].split()) != " ".join(
                row["values"].values()
            ):
                errors.append("cell_partition_mismatch")
            if case.get("answer_method") == "verified_table_v1" and not all(
                f"{k}={v}" in answer for k, v in row["values"].items()
            ):
                errors.append("rendered_value_missing")
        for join in plan["joins"]:
            checked_joins += 1
            if not join["matches"]:
                errors.append("join_without_matched_keys")
            for match in join["matches"]:
                left = tuple(cell_key(match["from_values"][k]) for k in join["columns"])
                right = tuple(
                    cell_key(match["to_values"][k]) for k in join["references"]
                )
                if left != right or any(value.casefold() == "null" for value in left):
                    errors.append("invalid_complete_join_key")
                if not any(i in context for i in match["from_source_ids"]) or not any(
                    i in context for i in match["to_source_ids"]
                ):
                    errors.append("join_row_source_missing")
    rows = [r for p in plans for r in p["rows"]]
    gold_checks = []
    for check in expected or []:
        kind = check.get("kind", "row")
        if kind == "row":
            matched = [
                r
                for r in rows
                if r["table"] == check["table"]
                and all(
                    cell_key(r["values"].get(k, "")) == cell_key(v)
                    for k, v in check["where"].items()
                )
            ]
            passed = len(matched) == 1 and all(
                cell_key(matched[0]["values"].get(k, "")) == cell_key(v)
                for k, v in check.get("values", {}).items()
            )
        elif kind == "count":
            matched = [
                c
                for p in plans
                for c in p.get("counts", [])
                if c["table"] == check["table"]
            ]
            passed = len(matched) == 1 and matched[0]["count"] == check["count"]
        elif kind == "join":
            passed = any(
                j["from"] == check["from"]
                and j["to"] == check["to"]
                and j["columns"] == check["columns"]
                and j["references"] == check["references"]
                for p in plans
                for j in p["joins"]
            )
        else:
            raise ValueError("Unknown deterministic check kind")
        gold_checks.append({"kind": kind, "passed": passed})
        if not passed:
            errors.append("expected_fact_not_verified")
    return {
        "id": case["id"],
        "strategy": case["strategy"],
        "program_path": case.get("answer_method") == "verified_table_v1",
        "rows_checked": checked_rows,
        "joins_checked": checked_joins,
        "expected_checks": gold_checks,
        "errors": errors,
        "status": "fail" if errors else "pass" if plans else "not_applicable",
    }
