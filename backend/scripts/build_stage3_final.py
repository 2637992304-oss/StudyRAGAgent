"""Separate final acceptance data, not the embedding feasibility holdout."""

import json

from scripts.build_stage3_closeout import ROOT
from scripts.build_stage3_completion import ChapterParser


def main():
    data = json.loads((ROOT / "data/dataset.json").read_text(encoding="utf-8"))
    target = ROOT / "final_data/sources"
    target.mkdir(parents=True, exist_ok=True)
    terms = dict(
        request_timeout="timeout_limit",
        max_attempts="attempt_budget",
        retry_delay="wait_interval",
        cache_capacity="entry_limit",
        cache_ttl="expiry_window",
        eviction_policy="replacement_rule",
        batch_size="payload_count",
        delivery_order="completion_order",
        duplicate_handling="deduplication_rule",
        commit_rule="transaction_rule",
        visibility_rule="read_visibility",
        retention_rule="audit_window",
    )
    numbers = {
        "19秒": "31秒",
        "6次": "9次",
        "5秒": "7秒",
        "37条": "43条",
        "61秒": "83秒",
        "12条": "16条",
        "61天": "83天",
    }
    names = {
        d["key"]: "final_" + d["key"]
        for d in data["documents"]
        if d["key"].startswith("holdout_")
    }
    names.update(book_7_4="final_book_7_3", book_7_5="final_book_7_6")

    def rewrite(text):
        for a, b in [*terms.items(), *numbers.items()]:
            text = text.replace(a, b)
        return text

    parser = ChapterParser()
    parser.feed((ROOT / "raw/chapter7.html").read_text(encoding="utf-8"))
    parser.flush()
    sections, current = {}, None
    for tag, text in parser.blocks:
        if tag in {"h1", "h2", "h3"} and text.startswith(("7.3 ", "7.6 ")):
            current = text
            sections[current] = []
        elif tag in {"h1", "h2", "h3"}:
            current = None
        elif current:
            sections[current].append(text)
    book_specs = {
        "book_7_4": (
            "7.3",
            "教材说while条件为false时，程序下一步如何执行？",
            "If false, exit the while statement and continue execution "
            "at the next statement.",
        ),
        "book_7_5": (
            "7.6",
            "教材将Newton方法称为algorithm，它解决哪一类问题？",
            "it is a mechanical process for solving a category of problems "
            "(in this case, computing square roots).",
        ),
    }
    for doc in data["documents"]:
        key = doc["key"]
        content = (ROOT / "data/sources" / doc["file"]).read_text(encoding="utf-8")
        if key in book_specs:
            prefix = book_specs[key][0]
            heading = next(s for s in sections if s.startswith(prefix + " "))
            content = "# " + heading + "\n" + "\n".join(sections[heading])
        elif key in names:
            content = rewrite(content)
        doc["key"] = names.get(key, key)
        doc["file"] = doc["key"] + ".md"
        (target / doc["file"]).write_text(content, encoding="utf-8")
    for case in data["cases"]:
        if case["split"] != "holdout":
            continue
        original = case["id"]
        case["id"] = "final_" + original
        case["question"] = rewrite(case["question"])
        if original in book_specs:
            prefix, case["question"], quote = book_specs[original]
            heading = next(s for s in sections if s.startswith(prefix + " "))
            case["expected"] = [
                dict(
                    unit=names[original],
                    document=names[original],
                    section=heading,
                    quote=quote,
                    subquestion=case["question"],
                )
            ]
        else:
            for unit in case["expected"]:
                unit["unit"] = "final_" + rewrite(unit["unit"])
                unit["document"] = names[unit["document"]]
                unit["quote"] = rewrite(unit["quote"])
                unit["subquestion"] = rewrite(unit["subquestion"])
        case["scope"]["documents"] = [names[k] for k in case["scope"]["documents"]]
        case["reference_answer"] = (
            "；".join(u["quote"] for u in case["expected"]) or "所选资料没有依据。"
        )
        case["key_facts"] = [u["quote"] for u in case["expected"]]
    data["version"] = "stage3-final-acceptance-v1"
    data["limitation"] = (
        "New values/identifiers and textbook sections; controlled templates "
        "overlap development. Not representative broad benchmark."
    )
    for case in data["cases"]:
        for unit in case["expected"]:
            source = (target / (unit["document"] + ".md")).read_text(encoding="utf-8")
            assert unit["quote"] in source
    (ROOT / "final_data/dataset.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "Final acceptance: 20 holdout cases, new identifiers/values "
        "and textbook sections"
    )


if __name__ == "__main__":
    main()
