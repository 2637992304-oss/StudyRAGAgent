"""Probe runtime support or compare a supported embedding in a TEMP pgvector table.

No production embedding dimension/model is changed. BGE-M3 is explicitly skipped
when the installed FastEmbed runtime does not support it.
"""

import argparse
import hashlib
import json
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
from app.evaluation.fixtures import DATA, fixtures, load_dataset, scope_for
from app.evaluation.metrics import evidence_metrics, matched_units
from app.retrieval import Candidate, PgVectorStore, search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    supported = TextEmbedding.list_supported_models()
    description = next((m for m in supported if m["model"] == args.model), None)
    report = {
        "model": args.model,
        "runtime": "fastembed",
        "description": description,
        "production_dimension_changed": False,
        "results": [],
        "timestamp": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
        "dataset_version": load_dataset()["version"],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "parameters": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
    }
    try:
        if not description:
            report.update(
                status="skipped",
                reason="model_not_supported_by_installed_runtime",
                alternatives=[
                    m["model"] for m in supported if "multilingual" in m["model"]
                ],
            )
            return
        if not args.run:
            report.update(
                status="not_run", reason="feasibility_only; pass --run to execute"
            )
            return
        start = time.perf_counter()
        model = TextEmbedding(
            model_name=args.model,
            cache_dir=str(get_settings().model_cache_dir),
            local_files_only=not args.allow_model_download,
            threads=2,
        )
        report["model_load_ms"] = (time.perf_counter() - start) * 1000
        data = load_dataset()
        settings = get_settings().model_copy(update={"retrieval_strategy": "dense"})
        with fixtures(data, args.output, 900, 150) as (owners, courses, docs):
            inverse = {value: key for key, value in docs.items()}
            # One connection owns the TEMP table throughout the experiment.
            with get_engine().connect() as connection, Session(bind=connection) as db:
                chunks = db.scalars(
                    select(Chunk).where(Chunk.owner_id.in_(owners.values()))
                ).all()
                vectors = list(model.embed([c.text for c in chunks]))
                table = Table(
                    "stage3_alternative_vectors",
                    MetaData(),
                    Column("chunk_id", String(36), primary_key=True),
                    Column("embedding", Vector(description["dim"])),
                    prefixes=["TEMPORARY"],
                    postgresql_on_commit="DROP",
                )
                table.create(connection)
                db.execute(
                    table.insert(),
                    [
                        {"chunk_id": c.id, "embedding": v.tolist()}
                        for c, v in zip(chunks, vectors, strict=True)
                    ],
                )

                class AlternativeStore(PgVectorStore):
                    def dense(self, vector, limit):
                        distance = table.c.embedding.cosine_distance(vector)
                        rows = self.db.execute(
                            self.statement()
                            .join(table, Chunk.id == table.c.chunk_id)
                            .add_columns(distance)
                            .order_by(distance, Chunk.id)
                            .limit(limit)
                        ).all()
                        return [Candidate(c, d, distance=float(s)) for c, d, s in rows]

                def embed_query(texts):
                    # FastEmbed query_embed applies model-specific query prefixes.
                    return [v.tolist() for v in model.query_embed(texts)]

                for case in [c for c in data["cases"] if c["split"] == "dev"][
                    : args.limit
                ]:
                    scope = scope_for(case, owners, courses, docs)
                    result = search(
                        db,
                        scope,
                        case["question"],
                        settings,
                        embed=embed_query,
                        store=AlternativeStore(db, scope, settings),
                    )
                    hits = [
                        matched_units(item, case["expected"], inverse[item.document.id])
                        for item in result.ranked
                    ]
                    report["results"].append(
                        {
                            "id": case["id"],
                            "retrieval_ms": result.retrieval_ms,
                            "metrics": evidence_metrics(
                                {u["unit"] for u in case["expected"]}, hits, 5
                            ),
                        }
                    )
                db.rollback()  # Discard the TEMP vectors with this connection.
        report.update(
            status="ok",
            backend="real_pgvector_temporary_table",
            dimension=description["dim"],
            threshold_calibrated=False,
        )
    except Exception as exc:
        report.update(status="error", error_type=type(exc).__name__)
    finally:
        (args.output / "alternative_embedding.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
