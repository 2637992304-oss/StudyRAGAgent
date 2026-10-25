"""Real embedding/pgvector/reranker warmed latency at explicit concurrency.

Uses isolated fixtures; repeated queries are capacity probes, not quality scores.
"""

import argparse
import json
import os
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Chunk
from app.db.session import get_engine
from app.evaluation.fixtures import fixtures, load_dataset, scope_for
from app.evaluation.metrics import latency
from app.retrieval import search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    settings = get_settings().model_copy(deep=True)
    for field, key in [
        ("retrieval_rerank_candidates", "rerank_count"),
        ("retrieval_rerank_gate", "gate"),
        ("retrieval_rerank_min_score", "rerank_min"),
        ("reranker_model", "reranker_model"),
        ("reranker_threads", "threads"),
        ("reranker_batch_size", "batch_size"),
        ("retrieval_pair_headings", "pair_headings"),
        ("retrieval_expand_terms", "expand_terms"),
        ("retrieval_balanced_candidates", "balanced_candidates"),
        ("retrieval_diversify", "diversify"),
        ("retrieval_query_normalization", "query_normalization"),
        ("retrieval_facet_coverage", "facet_coverage"),
        ("retrieval_section_backfill", "section_backfill"),
    ]:
        if key in config:
            setattr(settings, field, config[key])
    dataset = load_dataset(args.dataset)
    cases = [c for c in dataset["cases"] if "boundary" not in c["categories"]]
    observations, cold = [], []
    with fixtures(dataset, args.output, 900, 150, args.dataset) as (
        owners,
        courses,
        docs,
    ):
        with Session(get_engine()) as db:
            chunks = db.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.owner_id.in_(owners.values()))
            )

        def run(index, strategy):
            start = time.perf_counter()
            case = cases[index % len(cases)]
            result = {"id": case["id"], "strategy": strategy, "status": "ok"}
            try:
                scoped = scope_for(case, owners, courses, docs)
                with Session(get_engine()) as db:
                    found = search(db, scoped, case["question"], settings)
                    result.update(
                        retrieval_ms=found.retrieval_ms,
                        rerank_ms=found.rerank_ms,
                        rerank_count=found.rerank_count,
                    )
            except Exception as exc:
                result.update(status="error", error_type=type(exc).__name__)
            result["wall_ms"] = (time.perf_counter() - start) * 1000
            return result

        for strategy in ["dense", "rerank"]:
            settings.retrieval_strategy = strategy
            for field, key in [
                ("retrieval_pair_headings", "pair_headings"),
                ("retrieval_expand_terms", "expand_terms"),
                ("retrieval_balanced_candidates", "balanced_candidates"),
                ("retrieval_diversify", "diversify"),
                ("retrieval_query_normalization", "query_normalization"),
                ("retrieval_facet_coverage", "facet_coverage"),
                ("retrieval_section_backfill", "section_backfill"),
            ]:
                setattr(
                    settings,
                    field,
                    bool(config.get(key)) if strategy == "rerank" else False,
                )
            cold.append(run(0, strategy))
            for concurrency in args.concurrency:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    rows = list(
                        pool.map(lambda i: run(i, strategy), range(args.requests))
                    )
                for row in rows:
                    row["concurrency"] = concurrency
                observations.extend(rows)
                print(
                    strategy,
                    concurrency,
                    len(rows),
                    sum(r["status"] == "error" for r in rows),
                    flush=True,
                )
    groups = []
    for strategy in ["dense", "rerank"]:
        for concurrency in args.concurrency:
            rows = [
                r
                for r in observations
                if r["strategy"] == strategy and r["concurrency"] == concurrency
            ]
            target = 1000 if strategy == "dense" else 3000
            stats = latency([r["wall_ms"] for r in rows if r["status"] == "ok"])
            groups.append(
                dict(
                    strategy=strategy,
                    concurrency=concurrency,
                    total_n=len(rows),
                    errors=sum(r["status"] != "ok" for r in rows),
                    wall_ms=stats,
                    target_p95_ms=target,
                    target_met=not any(r["status"] != "ok" for r in rows)
                    and stats["p95"] is not None
                    and stats["p95"] <= target,
                )
            )
    payload = dict(
        timestamp=datetime.now(UTC).isoformat(),
        target_origin="assistant_defined_experiment_not_user_acceptance_or_production_SLO",
        platform=platform.platform(),
        cpu=platform.processor(),
        logical_cpus=os.cpu_count(),
        threads_per_model=settings.reranker_threads,
        embedding_threads=settings.embedding_threads,
        chunks=chunks,
        documents=len(dataset["documents"]),
        source_model=settings.embedding_model,
        reranker=settings.reranker_model,
        fixture_chunk_pairing=False,
        note=(
            "Existing-layout large corpus; runtime pairing only for rerank. "
            "Dense retains original flags. Targets predeclared in source. "
            "Closed-loop concurrency, no queue/HTTP/LLM; n is small, not a SLO."
        ),
        cold=cold,
        groups=groups,
    )
    (args.output / "observations.json").write_text(
        json.dumps(observations, indent=2), encoding="utf-8"
    )
    (args.output / "benchmark.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
