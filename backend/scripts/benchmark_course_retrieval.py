"""Isolated course benchmark: model load, first queries and cached repeats apart."""

import argparse
import hashlib
import json
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_engine
from app.evaluation.fixtures import fixtures, load_dataset, scope_for
from app.evaluation.metrics import latency
from app.rerank_cache import score_cache
from app.retrieval import search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2])
    args = parser.parse_args()
    if min(args.limit, args.repeats, *args.concurrency) < 1:
        parser.error("Counts/concurrency must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    dataset = load_dataset(args.dataset)
    cases = [
        c
        for c in dataset["cases"]
        if c["split"] == args.split and "boundary" not in c["categories"]
    ][: args.limit]
    if not cases:
        parser.error("No matching questions")
    settings = get_settings().model_copy(deep=True)
    for field, key in [
        ("retrieval_rerank_candidates", "rerank_count"),
        ("retrieval_rerank_gate", "gate"),
        ("retrieval_rerank_min_score", "rerank_min"),
        ("retrieval_max_distance", "max_distance"),
        ("retrieval_keyword_min_score", "keyword_min"),
        ("reranker_model", "reranker_model"),
        ("reranker_threads", "threads"),
        ("reranker_batch_size", "batch_size"),
        ("retrieval_pair_headings", "pair_headings"),
        ("retrieval_expand_terms", "expand_terms"),
        ("retrieval_balanced_candidates", "balanced_candidates"),
        ("retrieval_diversify", "diversify"),
        ("retrieval_section_limit", "section_limit"),
        ("retrieval_top_k", "top_k"),
        ("retrieval_query_normalization", "query_normalization"),
        ("retrieval_facet_coverage", "facet_coverage"),
        ("retrieval_section_backfill", "section_backfill"),
        ("retrieval_structured_candidates", "structured_candidates"),
        ("retrieval_facet_context", "facet_context"),
    ]:
        if key in config:
            setattr(settings, field, config[key])
    settings.retrieval_term_aliases = {
        **settings.retrieval_term_aliases,
        **config.get("term_aliases", {}),
    }
    settings.retrieval_strategy = "rerank"
    settings.reranker_local_files_only = True
    rows, groups = [], []
    start = time.perf_counter()
    with fixtures(
        dataset,
        args.output,
        config.get("chunk_size", 900),
        config.get("overlap", 150),
        args.dataset,
        pair_headings=config.get("chunk_pair_headings", False),
        pdf_layout=config.get("pdf_layout", False),
    ) as (owners, courses, docs):
        fixture_ms = (time.perf_counter() - start) * 1000

        def run(case):
            scope = scope_for(case, owners, courses, docs)
            with Session(get_engine()) as db:
                result = search(db, scope, case["question"], settings)
            return dict(
                id=case["id"],
                retrieval_ms=result.retrieval_ms,
                rerank_ms=result.rerank_ms,
                hits=result.rerank_cache_hits,
                misses=result.rerank_cache_misses,
                evidence=[x.chunk.id for x in result.evidence],
                refused=not result.evidence,
            )

        settings.rerank_score_cache = False
        load = run(cases[0])
        # Establish uncached reference in the same fixture/IDs, after model load.
        reference = {c["id"]: run(c) for c in cases}
        for concurrency in args.concurrency:
            for phase in ["uncached", "cache_empty", "cache_repeat"]:
                if phase == "cache_empty":
                    score_cache.clear()
                settings.rerank_score_cache = phase != "uncached"
                selected = cases if phase == "cache_empty" else cases * args.repeats
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    observed = list(pool.map(run, selected))
                for row in observed:
                    row.update(
                        phase=phase,
                        concurrency=concurrency,
                        evidence_unchanged=row["evidence"]
                        == reference[row["id"]]["evidence"],
                        refusal_unchanged=row["refused"]
                        == reference[row["id"]]["refused"],
                    )
                rows.extend(observed)
                groups.append(
                    dict(
                        phase=phase,
                        concurrency=concurrency,
                        retrieval_ms=latency([r["retrieval_ms"] for r in observed]),
                        rerank_ms=latency([r["rerank_ms"] for r in observed]),
                        hits=sum(r["hits"] for r in observed),
                        misses=sum(r["misses"] for r in observed),
                        evidence_changes=sum(
                            not r["evidence_unchanged"] for r in observed
                        ),
                        refusal_changes=sum(
                            not r["refusal_unchanged"] for r in observed
                        ),
                    )
                )
                print(phase, concurrency, len(observed), flush=True)
    score_cache.clear()
    payload = dict(
        timestamp=datetime.now(UTC).isoformat(),
        config=config,
        dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        platform=platform.platform(),
        fixture_embedding_and_load_ms=fixture_ms,
        model_load_first_query=load,
        questions=len(cases),
        repeats=args.repeats,
        groups=groups,
        limitations=(
            "Same isolated small course; closed-loop concurrency. "
            "Cache repeats are not first-query improvements. "
            "No HTTP or LLM in this benchmark."
        ),
    )
    (args.output / "benchmark.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "observations.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
