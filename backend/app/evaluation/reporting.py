"""Human audit export preserving any existing reviewer decisions."""

import csv
from pathlib import Path


def write_review(rows: list[dict], path: Path):
    reviewed_fields = [
        "factually_correct",
        "citation_support",
        "refusal_correct",
        "reviewer",
        "notes",
    ]
    previous = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as source:
            previous = {(r["id"], r["strategy"]): r for r in csv.DictReader(source)}
    fields = [
        "id",
        "strategy",
        "question",
        "reference",
        "answer",
        "sources",
        "evidence",
        "reference_sources",
        "actual_context_fingerprint",
        "factually_correct",
        "citation_support",
        "refusal_correct",
        "failure_stage",
        "reviewer",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            ids = set(row.get("evidence_ids", []))
            evidence = [
                item for item in row.get("ranked", []) if item["chunk_id"] in ids
            ]
            actual = row.get("actual_context")
            if actual is not None:
                evidence = actual
            record = {
                "id": row["id"],
                "strategy": row["strategy"],
                "question": row["question"],
                "reference": row["reference_answer"],
                "answer": row.get("answer", ""),
                "sources": "\n".join(
                    f"{item['document']} / {item.get('section')}" for item in evidence
                ),
                "evidence": "\n\n".join(
                    f"[{i}] {item['text']}" for i, item in enumerate(evidence, 1)
                ),
                "failure_stage": row.get("failure_stage", ""),
                "reference_sources": row.get("reference_sources", ""),
                "actual_context_fingerprint": row.get("actual_prompt_sha256", ""),
            }
            old = previous.get((row["id"], row["strategy"]), {})
            if (
                old.get("answer", "") != record["answer"]
                or old.get("reference", "") != record["reference"]
                or old.get("evidence", "") != record["evidence"]
                or old.get("actual_context_fingerprint", "")
                != record["actual_context_fingerprint"]
            ):
                old = {}  # A changed answer needs a fresh human decision.
            for field in reviewed_fields:
                record[field] = old.get(
                    field, "" if field in {"reviewer", "notes"} else "pending"
                )
            writer.writerow(record)
