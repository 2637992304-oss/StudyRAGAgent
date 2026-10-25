"""Deterministic reduced-corpus public benchmark; original qrels, no LLM labels.

Raw content stays in ignored .eval-cache; manifest identifies the frozen subset.
This is not the official full-corpus T2Retrieval score.
"""

import argparse
import hashlib
import html
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

API = "https://datasets-server.huggingface.co"


def parquet_subset(args, rng):
    import pyarrow.parquet as pq

    directory = args.output / "parquet"
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for dataset in ["C-MTEB/T2Retrieval", "C-MTEB/T2Retrieval-qrels"]:
        response = httpx.get(f"{API}/parquet", params={"dataset": dataset}, timeout=30)
        response.raise_for_status()
        for spec in response.json()["parquet_files"]:
            path = directory / (
                "qrels.parquet"
                if dataset.endswith("-qrels")
                else spec["split"] + ".parquet"
            )
            if not path.exists() or path.stat().st_size != spec["size"]:
                print("Downloading", spec["split"], spec["size"], flush=True)
                with httpx.stream(
                    "GET", spec["url"], follow_redirects=True, timeout=45
                ) as stream:
                    stream.raise_for_status()
                    with path.with_suffix(".part").open("wb") as target:
                        for chunk in stream.iter_bytes(1024 * 1024):
                            target.write(chunk)
                path.with_suffix(".part").replace(path)
            files[path.stem] = path
    queries = rng.sample(pq.read_table(files["queries"]).to_pylist(), args.queries)
    queries.sort(key=lambda q: int(q["id"]))
    selected = {q["id"] for q in queries}
    qrels = [
        r
        for r in pq.read_table(files["qrels"]).to_pylist()
        if r["qid"] in selected and r["score"] > 0
    ]
    relevant = {r["pid"] for r in qrels}
    ids = pq.read_table(files["corpus"], columns=["id"]).column("id").to_pylist()
    distractors = rng.sample(
        [pid for pid in ids if pid not in relevant], args.distractors
    )
    wanted = relevant | set(distractors)
    corpus = {
        r["id"]: r
        for r in pq.read_table(
            files["corpus"], filters=[("id", "in", sorted(wanted))]
        ).to_pylist()
    }
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files.values()
    }
    print("Selected", len(queries), "queries and", len(corpus), "passages", flush=True)
    return queries, qrels, corpus, hashes


def request(endpoint, dataset, split, **kwargs):
    params = {
        "dataset": dataset,
        "config": "default",
        "split": split,
        "offset": 0,
        "length": 100,
        **kwargs,
    }
    for attempt in range(3):
        try:
            response = httpx.get(f"{API}/{endpoint}", params=params, timeout=45)
            response.raise_for_status()
            payload = response.json()
            if payload.get("partial"):
                raise ValueError("Refusing partial public dataset response")
            if any(r.get("truncated_cells") for r in payload.get("rows", [])):
                raise ValueError("Refusing truncated public dataset rows")
            return payload
        except (httpx.HTTPError, ValueError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def filtered(dataset, split, column, ids):
    if not ids:
        return []
    if any(not re.fullmatch(r"\d+", str(value)) for value in ids):
        raise ValueError("Only numeric source IDs are allowed")
    where = " OR ".join(f"\"{column}\"='{value}'" for value in ids)
    first = request("filter", dataset, split, where=where)
    rows = [r["row"] for r in first["rows"]]
    for offset in range(100, first["num_rows_total"], 100):
        rows.extend(
            r["row"]
            for r in request("filter", dataset, split, where=where, offset=offset)[
                "rows"
            ]
        )
    return rows


def pages(dataset, split, offsets):
    with ThreadPoolExecutor(max_workers=4) as pool:
        chunks = list(
            pool.map(
                lambda offset: request("rows", dataset, split, offset=offset), offsets
            )
        )
    return [r["row"] for chunk in chunks for r in chunk["rows"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".eval-cache/t2retrieval"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("../docs/retrieval_optimization/t2_manifest.json"),
    )
    parser.add_argument("--queries", type=int, default=200)
    parser.add_argument("--distractors", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument(
        "--parquet",
        action="store_true",
        help="Download official files; requires pyarrow",
    )
    args = parser.parse_args()
    rng = random.Random(args.seed)
    dataset_name = "C-MTEB/T2Retrieval"
    if args.parquet:
        queries, qrels, corpus, upstream_hashes = parquet_subset(args, rng)
        query_pages = []
    else:
        queries, qrels, corpus, query_pages = viewer_subset(args, rng, dataset_name)
        upstream_hashes = {}
    expected = {
        q["id"]: sorted({r["pid"] for r in qrels if r["qid"] == q["id"]})
        for q in queries
    }
    if any(not pids for pids in expected.values()):
        raise ValueError("Selected query lacks positive source qrels")
    if {r["pid"] for r in qrels} - corpus.keys():
        raise ValueError("Missing relevant source passages")
    finish(args, queries, qrels, corpus, expected, query_pages, upstream_hashes)


def viewer_subset(args, rng, dataset_name):
    total = request("rows", dataset_name, "queries", length=1)["num_rows_total"]
    query_pages = rng.sample(list(range(0, total - 100, 100)), min(20, total // 100))
    query_pool = pages(dataset_name, "queries", query_pages)
    queries = rng.sample(query_pool, args.queries)
    queries.sort(key=lambda q: int(q["id"]))
    batches = [queries[i : i + 25] for i in range(0, len(queries), 25)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        labels = list(
            pool.map(
                lambda batch: filtered(
                    dataset_name + "-qrels", "dev", "qid", [q["id"] for q in batch]
                ),
                batches,
            )
        )
    qrels = [r for group in labels for r in group if r["score"] > 0]
    expected = {
        q["id"]: sorted({r["pid"] for r in qrels if r["qid"] == q["id"]})
        for q in queries
    }
    if any(not pids for pids in expected.values()):
        raise ValueError("Selected query lacks positive source qrels")
    pids = sorted({r["pid"] for r in qrels}, key=int)
    with ThreadPoolExecutor(max_workers=4) as pool:
        groups = list(
            pool.map(
                lambda batch: filtered(dataset_name, "corpus", "id", batch),
                [pids[i : i + 25] for i in range(0, len(pids), 25)],
            )
        )
    corpus = {row["id"]: row for group in groups for row in group}
    if set(pids) - corpus.keys():
        raise ValueError("Missing relevant source passages")
    total_corpus = request("rows", dataset_name, "corpus", length=1)["num_rows_total"]
    offsets = rng.sample(
        list(range(0, total_corpus - 100, 100)), min(20, total_corpus // 100)
    )
    distractors = [
        row for row in pages(dataset_name, "corpus", offsets) if row["id"] not in corpus
    ]
    for row in rng.sample(distractors, min(args.distractors, len(distractors))):
        corpus[row["id"]] = row
    return queries, qrels, corpus, query_pages


def finish(args, queries, qrels, corpus, expected, query_pages, upstream_hashes):
    dataset_name = "C-MTEB/T2Retrieval"
    # Keep queries sharing any positive passage in the same split.
    parent = {q["id"]: q["id"] for q in queries}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    passage_owner = {}
    for qid, relevant in expected.items():
        for pid in relevant:
            if pid in passage_owner:
                a, b = find(qid), find(passage_owner[pid])
                parent[max(a, b, key=int)] = min(a, b, key=int)
            else:
                passage_owner[pid] = qid
    args.output.mkdir(parents=True, exist_ok=True)
    sources = args.output / "sources"
    sources.mkdir(exist_ok=True)
    documents, cases = [], []
    hashes = {}
    for pid, row in sorted(corpus.items(), key=lambda x: int(x[0])):
        # Strip markup once, identically for every strategy; keep full passage.
        content = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", row["text"])).split())
        filename = f"passage_{pid}.txt"
        (sources / filename).write_text(content, encoding="utf-8")
        hashes[pid] = hashlib.sha256(row["text"].encode()).hexdigest()
        documents.append(
            {
                "key": pid,
                "file": filename,
                "owner": "a",
                "course": "main",
                "status": "ready",
                "source": dataset_name,
            }
        )
    for q in queries:
        group = find(q["id"])
        split = (
            "dev"
            if int(hashlib.sha256(f"{args.seed}:{group}".encode()).hexdigest(), 16) % 2
            else "holdout"
        )
        cases.append(
            {
                "id": "t2_" + q["id"],
                "split": split,
                "categories": ["public_retrieval"],
                "question": q["text"],
                "scope": {"owner": "a", "course": "main", "documents": None},
                "answerable": True,
                "expected": [
                    {"unit": pid, "document": pid, "quote": ""}
                    for pid in expected[q["id"]]
                ],
                "reference_answer": "not_available_retrieval_only",
                "key_facts": [],
                "annotation_status": "original_positive_qrels",
                "human_review": "not_applicable",
            }
        )
    payload = {
        "version": "t2-reduced-v1",
        "relevance_granularity": "original_passage_not_chunk_span",
        "provenance": dataset_name,
        "documents": documents,
        "cases": cases,
    }
    destination = args.output / "dataset.json"
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "timestamp": datetime.now(UTC).isoformat(),
        "source": dataset_name,
        "qrels_source": dataset_name + "-qrels",
        "seed": args.seed,
        "sampling": "seeded queries from full split"
        if args.parquet
        else "20 seeded query pages",
        "requested_queries": args.queries,
        "requested_distractors": args.distractors,
        "upstream_parquet_sha256": upstream_hashes,
        "query_ids": [q["id"] for q in queries],
        "query_pages": query_pages,
        "qrels": qrels,
        "passage_ids": sorted(corpus, key=int),
        "source_sha256": hashes,
        "dataset_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "dev": sum(c["split"] == "dev" for c in cases),
        "holdout": sum(c["split"] == "holdout" for c in cases),
        "caveats": [
            "reduced corpus, not official leaderboard",
            "no no-answer labels",
            "qrels identify whole passages, not exact evidence spans",
            "raw content cached locally; dataset source terms apply",
        ],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Prepared {len(cases)} queries, {len(corpus)} passages", flush=True)


if __name__ == "__main__":
    main()
