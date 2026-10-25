"""Real same-chunk embedding comparison with development-only model gates.

All alternate vectors live in connection-local temporary pgvector tables.
No production dimensions, model settings, user documents or defaults are changed.
"""

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from fastembed import TextEmbedding
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Chunk
from app.db.session import get_engine
from app.evaluation.fixtures import fixtures, load_dataset, scope_for
from app.evaluation.metrics import average, evidence_metrics, latency, matched_units
from app.retrieval import Candidate, PgVectorStore
from scripts.evaluate_stage3 import scope_ok


def calibration(rows):
    """Fixed objective: evidence coverage minus twice negative false acceptance."""
    if any(r.get("split") != "dev" for r in rows):
        raise ValueError("Only development rows may calibrate model gates")
    trials = []
    for step in range(1, 101):
        threshold = step / 100
        positives, false_accepts = [], []
        for row in rows:
            kept = [r for r in row["candidates"] if r["distance"] <= threshold]
            if row["answerable"]:
                covered = (
                    set().union(*(set(r["units"]) for r in kept)) if kept else set()
                )
                positives.append(len(covered) / len(row["expected"]))
            elif "boundary" not in row["categories"]:
                false_accepts.append(float(bool(kept)))
        recall = average(positives)
        fpr = average(false_accepts)
        if recall is None or fpr is None:
            raise ValueError(
                "Calibration requires positive and no-answer development cases"
            )
        trials.append(
            dict(
                threshold=threshold,
                coverage=recall,
                false_accept=fpr,
                objective=recall - 2 * fpr,
            )
        )
    return max(
        trials, key=lambda r: (r["objective"], -r["false_accept"], -r["threshold"])
    ), trials


def summarize(rows, threshold):
    positive, negative, boundaries = [], [], []
    for row in rows:
        kept = [r for r in row["candidates"] if r["distance"] <= threshold]
        row["gate_refused"] = not kept
        row["metrics"] = evidence_metrics(
            set(row["expected"]), [set(r["units"]) for r in row["candidates"]], 5
        )
        row["evidence_metrics"] = evidence_metrics(
            set(row["expected"]), [set(r["units"]) for r in kept], 5
        )
        if row["answerable"]:
            positive.append(row)
        elif "boundary" in row["categories"]:
            boundaries.append(row)
        else:
            negative.append(row)
    return dict(
        n=len(rows),
        positive_n=len(positive),
        negative_n=len(negative),
        boundary_n=len(boundaries),
        recall=average(r["metrics"]["recall"] for r in positive),
        evidence_coverage=average(r["evidence_metrics"]["recall"] for r in positive),
        false_refusal=average(float(r["gate_refused"]) for r in positive),
        no_answer_refusal=average(float(r["gate_refused"]) for r in negative),
        scope_violations=sum(r["scope_violations"] for r in rows),
        warm_retrieval_ms=latency([r["ms"] for r in rows[1:]]),
        cold_query_ms=rows[0]["ms"] if rows else None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--models",
        nargs="+",
        default=[
            "BAAI/bge-small-zh-v1.5",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "BAAI/bge-m3",
        ],
    )
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--bge-python", type=Path)
    parser.add_argument("--bge-revision", help="Pinned Hugging Face commit SHA")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data = load_dataset(args.dataset)
    descriptions = {r["model"]: r for r in TextEmbedding.list_supported_models()}
    report = dict(
        timestamp=datetime.now(UTC).isoformat(),
        dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        platform=platform.platform(),
        fastembed=importlib.metadata.version("fastembed"),
        execution="real_embedding_real_pgvector_TEMP_exact_search",
        chunk_size=900,
        overlap=150,
        pair_headings=True,
        top_k=5,
        dimension_and_production_defaults_changed=False,
        models=[],
    )
    with fixtures(data, args.output, 900, 150, args.dataset, pair_headings=True) as (
        owners,
        courses,
        docs,
    ):
        inverse = {value: key for key, value in docs.items()}
        with get_engine().connect() as connection, Session(bind=connection) as db:
            chunks = db.scalars(
                select(Chunk)
                .where(Chunk.owner_id.in_(owners.values()))
                .order_by(Chunk.id)
            ).all()
            report["chunks"] = len(chunks)
            for index, name in enumerate(args.models):
                description = descriptions.get(name)
                entry = dict(model=name, results=[])
                report["models"].append(entry)
                try:
                    external = None
                    if name == "BAAI/bge-m3" and args.bge_python:
                        if not args.bge_revision:
                            raise ValueError("External BGE-M3 requires pinned revision")
                        request = args.output / "bge_request.json"
                        request.write_text(
                            json.dumps(
                                dict(
                                    passages=[c.text for c in chunks],
                                    queries=list(
                                        dict.fromkeys(
                                            c["question"] for c in data["cases"]
                                        )
                                    ),
                                ),
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        output = args.output / "bge_vectors.json"
                        external_started = time.perf_counter()
                        completed = subprocess.run(
                            [
                                str(args.bge_python.resolve()),
                                "-X",
                                "utf8",
                                str(Path(__file__).with_name("bge_m3_worker.py")),
                                "--input",
                                str(request.resolve()),
                                "--output",
                                str(output.resolve()),
                                "--cache",
                                str((Path(".eval-cache") / "bge-m3-model").resolve()),
                                "--revision",
                                args.bge_revision,
                            ],
                            capture_output=True,
                            timeout=1200,
                            encoding="utf-8",
                            errors="replace",
                        )
                        # Store only exception type / return code, never inherited
                        # credentials or arbitrary downloaded-code output.
                        if completed.returncode:
                            entry["worker_returncode"] = completed.returncode
                            entry["worker_errors"] = [
                                line
                                for line in completed.stdout.splitlines()
                                if line.startswith("BGE worker error:")
                            ]
                            raise RuntimeError("Isolated BGE worker failed")
                        entry["external_wall_ms"] = (
                            time.perf_counter() - external_started
                        ) * 1000
                        external = json.loads(output.read_text(encoding="utf-8"))
                        expected_sha = hashlib.sha256(
                            json.dumps(
                                [c.text for c in chunks], ensure_ascii=False
                            ).encode()
                        ).hexdigest()
                        if external["passages_sha256"] != expected_sha:
                            raise ValueError("External corpus fingerprint mismatch")
                        description = {"dim": 1024}
                        entry["runtime"] = "isolated_sentence_transformers"
                        entry["external_metadata"] = {
                            k: v
                            for k, v in external.items()
                            if k
                            not in {
                                "passage_vectors",
                                "query_vectors",
                                "query_encode_ms",
                            }
                        }
                    if not description:
                        entry.update(
                            status="skipped",
                            reason="not_supported_by_installed_fastembed_runtime",
                        )
                        continue
                    start = time.perf_counter()
                    model = (
                        None
                        if external
                        else TextEmbedding(
                            model_name=name,
                            threads=2,
                            cache_dir=str(get_settings().model_cache_dir),
                            local_files_only=not args.allow_model_download,
                        )
                    )
                    entry["load_ms"] = (
                        external["load_ms"]
                        if external
                        else (time.perf_counter() - start) * 1000
                    )
                    entry["dimension"] = description["dim"]
                    table = Table(
                        f"stage3_embedding_followup_{index}",
                        MetaData(),
                        Column("chunk_id", String(36), primary_key=True),
                        Column("embedding", Vector(description["dim"])),
                        prefixes=["TEMPORARY"],
                    )
                    table.create(connection)
                    start = time.perf_counter()
                    vectors = (
                        external["passage_vectors"]
                        if external
                        else list(
                            model.passage_embed([c.text for c in chunks], batch_size=16)
                        )
                    )
                    if len(vectors) != len(chunks) or any(
                        len(v) != description["dim"]
                        or not any(v)
                        or not all(math.isfinite(float(x)) for x in v)
                        for v in vectors
                    ):
                        raise ValueError("Invalid model vectors")
                    db.execute(
                        table.insert(),
                        [
                            dict(chunk_id=c.id, embedding=v if external else v.tolist())
                            for c, v in zip(chunks, vectors, strict=True)
                        ],
                    )
                    entry["document_embedding_and_insert_ms"] = (
                        time.perf_counter() - start
                    ) * 1000
                    for split in ["dev", "holdout"]:
                        rows = []
                        for case in [c for c in data["cases"] if c["split"] == split]:
                            scoped = scope_for(case, owners, courses, docs)
                            start = time.perf_counter()
                            # Preserve current baseline query behavior. Other models
                            # use their declared query prefix through query_embed.
                            method = (
                                None
                                if external
                                else (
                                    model.embed
                                    if name == "BAAI/bge-small-zh-v1.5"
                                    else model.query_embed
                                )
                            )
                            vector = (
                                external["query_vectors"][case["question"]]
                                if external
                                else next(iter(method([case["question"]]))).tolist()
                            )
                            distance = table.c.embedding.cosine_distance(vector)
                            statement = (
                                PgVectorStore(db, scoped, get_settings())
                                .statement()
                                .join(table, Chunk.id == table.c.chunk_id)
                                .add_columns(distance)
                                .order_by(distance, Chunk.id)
                                .limit(5)
                            )
                            hits = [
                                Candidate(c, d, distance=float(s))
                                for c, d, s in db.execute(statement).all()
                            ]
                            if any(not scope_ok(x, scoped) for x in hits):
                                raise PermissionError(
                                    "Scoped temporary store leaked a candidate"
                                )
                            rows.append(
                                dict(
                                    id=case["id"],
                                    split=split,
                                    answerable=case["answerable"],
                                    categories=case["categories"],
                                    expected=[u["unit"] for u in case["expected"]],
                                    ms=(time.perf_counter() - start) * 1000
                                    + (
                                        external["query_encode_ms"][case["question"]]
                                        if external
                                        else 0
                                    ),
                                    scope_violations=0,
                                    candidates=[
                                        dict(
                                            document=inverse[x.document.id],
                                            distance=x.distance,
                                            units=sorted(
                                                matched_units(
                                                    x,
                                                    case["expected"],
                                                    inverse[x.document.id],
                                                )
                                            ),
                                        )
                                        for x in hits
                                    ],
                                )
                            )
                        if split == "dev":
                            selected, trials = calibration(rows)
                            entry["gate"] = selected
                            # Persist calibration before executing any holdout query.
                            (
                                args.output / f"model_{index}_calibration.json"
                            ).write_text(
                                json.dumps(
                                    dict(
                                        dataset_sha256=report["dataset_sha256"],
                                        model=name,
                                        selected=selected,
                                        trials=trials,
                                        selection_split="dev",
                                        objective=(
                                            "coverage - 2 * negative_false_accept; "
                                            "tie prefer lower threshold"
                                        ),
                                    ),
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        entry[split] = summarize(rows, entry["gate"]["threshold"])
                        entry["results"].extend(rows)
                    table.drop(connection)
                    entry["status"] = "ok"
                except Exception as exc:
                    entry.update(status="error", error_type=type(exc).__name__)
                    # A database error aborts the transaction. Don't mask it with
                    # apparent successful comparisons from the same connection.
                    if connection.invalidated or not connection.in_transaction():
                        break
                finally:
                    (args.output / "comparison.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                print(name, entry["status"], flush=True)
            db.rollback()  # Connection-local TEMP tables disappear on close.
    (args.output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
