"""Real pgvector retrieval + optional production HTTP/SSE evaluation.

Run from backend: python -m scripts.evaluate_stage3 --help
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.auth import create_token
from app.core.config import get_settings
from app.db.models import Chunk, Document, Message
from app.db.session import get_engine
from app.evaluation.fixtures import DATA, cleanup, fixtures, load_dataset, scope_for
from app.evaluation.metrics import (
    evidence_metrics,
    matched_units,
    refusal_observation,
    summarize,
)
from app.evaluation.reporting import write_review
from app.evaluation.trace import retrieval_trace
from app.main import app
from app.rerank_cache import score_cache
from app.retrieval import Candidate, PgVectorStore, search


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["dense", "keyword", "hybrid", "rerank"],
        choices=["dense", "keyword", "hybrid", "rerank"],
    )
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--dataset", type=Path, default=DATA)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="+", help="Evaluate only these stable case ids")
    parser.add_argument(
        "--calibration",
        type=Path,
        help="Frozen per-strategy development calibration JSON",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument("--keyword-min", type=float, default=1.0)
    parser.add_argument("--rerank-min", type=float, default=0.0)
    parser.add_argument(
        "--reranker-model", help="Explicit reranker model override for comparison"
    )
    parser.add_argument("--rerank-count", type=int, default=20)
    parser.add_argument("--gate", choices=["legacy", "score", "coverage", "adaptive"])
    parser.add_argument("--strong-score", type=float, default=0.0)
    parser.add_argument("--support-distance", type=float, default=0.3)
    parser.add_argument("--support-keyword", type=float, default=3.0)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--no-bucketing", action="store_true")
    parser.add_argument("--keep-duplicates", action="store_true")
    parser.add_argument("--passage-chars", type=int, default=0)
    parser.add_argument("--pair-headings", action="store_true")
    parser.add_argument("--chunk-pair-headings", action="store_true")
    parser.add_argument("--pdf-layout", action="store_true")
    parser.add_argument("--score-cache", action="store_true")
    parser.add_argument("--table-reasoning", action="store_true")
    parser.add_argument("--first-answer-reference-seconds", type=float)
    parser.add_argument("--structured-candidates", action="store_true")
    parser.add_argument("--facet-context", action="store_true")
    parser.add_argument("--verified-tables", action="store_true")
    parser.add_argument("--term-aliases", type=json.loads)
    parser.add_argument("--expand-terms", action="store_true")
    parser.add_argument("--balanced-candidates", action="store_true")
    parser.add_argument("--diversify", action="store_true")
    parser.add_argument("--section-limit", type=int, default=2)
    parser.add_argument("--query-normalization", action="store_true")
    parser.add_argument("--facet-coverage", action="store_true")
    parser.add_argument("--section-backfill", action="store_true")
    parser.add_argument("--grounded-answers", action="store_true")
    parser.add_argument(
        "--redact-passages",
        action="store_true",
        help="Export source hashes/lengths instead of public passage text",
    )
    parser.add_argument("--max-distance", type=float, default=0.65)
    parser.add_argument("--end-to-end", action="store_true")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument(
        "--project-llm-config",
        action="store_true",
        help="Ignore inherited LLM_* overrides in this process; use project .env",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--config", type=Path, help="JSON CLI defaults; explicit CLI args override"
    )
    parser.add_argument(
        "--pricing",
        type=Path,
        help="JSON pricing with model/date/source/currency and per-million rates",
    )
    parser.add_argument(
        "--cleanup", type=Path, help="Clean only accounts in a previous manifest"
    )
    import sys

    if "--config" in sys.argv:
        config = Path(sys.argv[sys.argv.index("--config") + 1])
        parser.set_defaults(**json.loads(config.read_text(encoding="utf-8")))
    args = parser.parse_args()
    if args.output is None:
        parser.error("--output is required (or provide output in --config)")
    if not args.strategies or set(args.strategies) - {
        "dense",
        "keyword",
        "hybrid",
        "rerank",
    }:
        parser.error("invalid strategies")
    if args.keyword_min <= 0 or not 0 <= args.max_distance <= 2:
        parser.error(
            "keyword minimum must be positive; cosine distance must be in [0, 2]"
        )
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.top_k < 1 or not 0 <= args.overlap < args.chunk_size:
        parser.error("invalid top-k/chunk/overlap")
    if (
        args.first_answer_reference_seconds is not None
        and args.first_answer_reference_seconds <= 0
    ):
        parser.error("First-answer reference must be positive")
    args.output = Path(args.output)
    return args


def scope_ok(item, scope):
    return (
        item.chunk.owner_id == scope.owner_id
        and item.document.owner_id == scope.owner_id
        and item.chunk.course_id == scope.course_id
        and item.document.course_id == scope.course_id
        and item.document.status == "ready"
        and (scope.document_ids is None or item.document.id in scope.document_ids)
    )


def end_to_end(
    client, case, scope, headers, db, pricing, *, document_keys=None, redact=False
):
    start = time.perf_counter()
    session = client.post(
        "/api/v1/chat/sessions",
        headers=headers,
        json={"course_id": scope.course_id, "document_ids": scope.document_ids},
    )
    if session.status_code != 201:
        if case["categories"] == ["boundary"] and session.status_code == 404:
            return {
                "api_scope_rejected": True,
                "llm_calls": 0,
                "cost": 0.0 if pricing else None,
                "citations": [],
                "refused": True,
                "total_ms": (time.perf_counter() - start) * 1000,
            }
        raise ValueError("Session creation failed")
    actual_context = []
    prompt_hash = None

    from app.evaluation.runtime import observer

    model_inputs, requests, input_hashes, feature_status = [], [], [], []
    captured = False

    def capture(event):
        nonlocal prompt_hash, captured
        if event["kind"] == "http_attempt":
            requests.append(event["purpose"])
        elif event["kind"] in {"features", "table_verification"}:
            feature_status.append(
                {k: v for k, v in event.items() if k != "verification"}
            )
        elif event["kind"] == "model_input":
            model_inputs.append(event["purpose"])
            prompt_hash = hashlib.sha256(
                json.dumps(event["messages"], ensure_ascii=False).encode()
            ).hexdigest()
            input_hashes.append({"purpose": event["purpose"], "sha256": prompt_hash})
        elif event["kind"] == "course_sources":
            captured = True
            for source in event["sources"]:
                chunk, document = (
                    db.get(Chunk, source["chunk_id"]),
                    db.get(Document, source["document_id"]),
                )
                if (
                    not chunk
                    or not document
                    or not scope_ok(Candidate(chunk, document), scope)
                ):
                    raise PermissionError("Actual prompt scope violation")
                if source["text"] != chunk.text:
                    raise ValueError("Actual tool source mismatch")
                existing = next(
                    (x for x in actual_context if x["label"] == source["label"]), None
                )
                record = {
                    "label": source["label"],
                    "chunk_id": chunk.id,
                    "document_id": document.id,
                    "document": (document_keys or {}).get(
                        document.id, document.filename
                    ),
                    "section": chunk.section_path,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "slide_no": chunk.slide_no,
                    "text": "" if redact else source["text"],
                    "text_sha256": hashlib.sha256(source["text"].encode()).hexdigest(),
                }
                if existing and existing != record:
                    raise ValueError("Source label changed during request")
                if not existing:
                    actual_context.append(record)

    token = observer.set(capture)
    try:
        response = client.post(
            f"/api/v1/chat/sessions/{session.json()['id']}/messages",
            headers=headers,
            json={
                "content": case["question"],
                "answer_scope": "strict",
                "web_enabled": False,
            },
        )
    finally:
        observer.reset(token)
    if response.status_code != 200:
        raise ValueError("Question request failed")
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
        if data:
            events.append((name, json.loads(data)))
    terminal = [(name, data) for name, data in events if name in {"done", "error"}]
    if not terminal or terminal[-1][0] != "done":
        code = (
            terminal[-1][1].get("code", "missing_done") if terminal else "missing_done"
        )
        return {
            "status": "error",
            "error_type": code,
            "llm_calls": len(requests),
            "total_ms": (time.perf_counter() - start) * 1000,
        }
    final = terminal[-1][1]
    answer = "".join(data["text"] for name, data in events if name == "answer_delta")
    citations = []
    for name, data in events:
        if name != "citation":
            continue
        own = client.get(f"/api/v1/citations/{data['citation_id']}", headers=headers)
        foreign_headers = {"Authorization": f"Bearer {case['_other_token']}"}
        foreign = client.get(
            f"/api/v1/citations/{data['citation_id']}", headers=foreign_headers
        )
        document = db.get(Document, data["document_id"])
        chunk = db.get(Chunk, data["chunk_id"])
        snapshot = next(
            (x for x in actual_context if x["label"] == data["label"]), None
        )
        if not document or not chunk or not scope_ok(Candidate(chunk, document), scope):
            raise PermissionError("Actual citation scope violation")
        if (
            not snapshot
            or snapshot["chunk_id"] != chunk.id
            or snapshot["text_sha256"]
            != hashlib.sha256(chunk.text.encode()).hexdigest()
        ):
            raise ValueError("Actual prompt/citation mismatch")
        citations.append(
            {
                "label": data["label"],
                "document_id": data["document_id"],
                "chunk_id": chunk.id,
                "text": snapshot["text"],
                "text_sha256": snapshot["text_sha256"],
                "matches_actual_prompt": True,
                "accessible": own.status_code == 200,
                "foreign_denied": foreign.status_code == 404,
                "in_scope": bool(
                    document
                    and scope_ok(
                        Candidate(db.get(Chunk, data["chunk_id"]), document), scope
                    )
                ),
                "semantic_support": "pending_human_review",
            }
        )
    message = db.get(Message, final["message_id"])
    refusal = refusal_observation(answer, final.get("status", "completed"))
    usage_in, usage_out = message.input_tokens, message.output_tokens
    program_answer = final.get("answer_method") == "verified_table_v1"
    cost = None
    if pricing and pricing["model"] == get_settings().llm_model:
        if usage_in is not None and usage_out is not None:
            cost = (
                usage_in * pricing["input_per_million"]
                + usage_out * pricing["output_per_million"]
            ) / 1_000_000
    cited_labels = {int(label) for label in re.findall(r"\[(\d+)\]", answer)}
    return {
        "answer": answer,
        "actual_context": actual_context,
        "actual_prompt_sha256": hashlib.sha256(
            json.dumps(input_hashes, sort_keys=True).encode()
        ).hexdigest()
        if input_hashes
        else None,
        "model_input_hashes": input_hashes,
        "feature_status": feature_status,
        "sse_terminal": final.get("status", "completed"),
        **refusal,
        "first_answer_ms": message.first_token_ms,
        "total_ms": message.total_ms,
        "input_tokens": usage_in,
        "output_tokens": usage_out,
        "usage_source": "not_applicable_program"
        if program_answer and not requests
        else "provider"
        if usage_in is not None
        else "unknown",
        "answer_method": final.get("answer_method", "llm"),
        "cost": cost,
        "llm_calls": len(requests),
        "model_turns": len(model_inputs),
        "context_capture": "captured" if captured else "no_course_tool",
        "citations": citations,
        "unknown_citation_labels": sorted(
            cited_labels - {c["label"] for c in citations}
        ),
        "generation_refusal_review": "pending_human_review",
        "key_fact_substring_matches": [
            fact.lower() in answer.lower() for fact in case["key_facts"]
        ],
    }


def main():
    args = arguments()
    if args.cleanup:
        cleanup(args.cleanup)
        return
    if args.project_llm_config:
        import os

        for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_THINKING_MODE"):
            os.environ.pop(name, None)
        get_settings.cache_clear()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    original = settings.model_copy(deep=True)
    settings.retrieval_top_k = args.top_k
    settings.retrieval_keyword_min_score = args.keyword_min
    settings.retrieval_rerank_min_score = args.rerank_min
    settings.retrieval_rerank_candidates = args.rerank_count
    settings.reranker_threads = args.threads
    settings.reranker_batch_size = args.batch_size
    settings.reranker_length_bucketing = not args.no_bucketing
    settings.retrieval_deduplicate = not args.keep_duplicates
    settings.reranker_passage_chars = args.passage_chars
    settings.retrieval_pair_headings = args.pair_headings
    settings.retrieval_expand_terms = args.expand_terms
    settings.retrieval_balanced_candidates = args.balanced_candidates
    settings.retrieval_diversify = args.diversify
    settings.retrieval_section_limit = args.section_limit
    settings.retrieval_query_normalization = args.query_normalization
    settings.retrieval_facet_coverage = args.facet_coverage
    settings.retrieval_section_backfill = args.section_backfill
    settings.retrieval_grounded_answers = args.grounded_answers
    settings.retrieval_table_reasoning = args.table_reasoning
    settings.rerank_score_cache = args.score_cache
    settings.retrieval_structured_candidates = args.structured_candidates
    settings.retrieval_facet_context = args.facet_context
    settings.retrieval_verified_tables = args.verified_tables
    if args.term_aliases:
        settings.retrieval_term_aliases = {
            **settings.retrieval_term_aliases,
            **args.term_aliases,
        }
    settings.retrieval_rerank_gate = args.gate or "legacy"
    settings.retrieval_rerank_strong_score = args.strong_score
    settings.retrieval_rerank_support_distance = args.support_distance
    settings.retrieval_rerank_support_keyword = args.support_keyword
    settings.retrieval_max_distance = args.max_distance
    settings.reranker_local_files_only = not args.allow_model_download
    if args.reranker_model:
        settings.reranker_model = args.reranker_model
    dataset = load_dataset(args.dataset)
    cases = [
        c for c in dataset["cases"] if args.split == "all" or c["split"] == args.split
    ]
    if args.ids:
        cases = [case for case in cases if case["id"] in args.ids]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        raise ValueError("No matching evaluation cases")
    calibrated = (
        json.loads(args.calibration.read_text(encoding="utf-8"))
        if args.calibration
        else None
    )
    pricing = (
        json.loads(args.pricing.read_text(encoding="utf-8")) if args.pricing else None
    )
    if pricing and not all(
        key in pricing
        for key in (
            "model",
            "date",
            "source",
            "currency",
            "input_per_million",
            "output_per_million",
        )
    ):
        raise ValueError("Incomplete pricing provenance")
    version = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    metadata = {
        "timestamp": datetime.now(UTC).isoformat(),
        "dataset_version": dataset["version"],
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "relevance_granularity": dataset.get(
            "relevance_granularity", "evidence_anchor"
        ),
        "code_version": version.stdout.strip()
        if version.returncode == 0
        else "unavailable",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "embedding": settings.embedding_model,
        "reranker": settings.reranker_model,
        "llm": settings.llm_model if args.end_to_end else None,
        "execution": "real_embedding_real_pgvector"
        + (
            "_real_http_sse_program_or_llm"
            if args.end_to_end and args.verified_tables
            else "_real_http_sse_llm"
            if args.end_to_end
            else ""
        ),
        "parameters": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "pricing": pricing,
        "human_review": "pending",
        "cold_definition": "first query per strategy; "
        "embedding load is in fixture time; "
        "reranker load/download is in its first query",
        "performance_reference": {
            "first_answer_ms": args.first_answer_reference_seconds * 1000
            if args.first_answer_reference_seconds is not None
            else None,
            "origin": "explicit CLI/config input; not a production SLO",
            "concurrency": 1,
            "scope": "this dataset; no capacity conclusion",
        },
        "score_cache_api_policy": "clear before API call; repeats benchmarked apart"
        if args.score_cache
        else "disabled",
        "skips": [],
    }
    metadata["calibration"] = calibrated
    metadata["package_versions"] = {
        name: importlib.metadata.version(name)
        for name in ("fastembed", "onnxruntime", "pgvector", "sqlalchemy", "httpx")
    }
    metadata["model_cache_revisions"] = {
        folder.name: sorted(file.stem for file in (folder / "trees").glob("*.json"))
        for folder in settings.model_cache_dir.glob("models--*")
    }
    metadata["source_sha256"] = {
        spec["file"]: hashlib.sha256(
            (args.dataset.parent / "sources" / spec["file"]).read_bytes()
        ).hexdigest()
        for spec in dataset["documents"]
    }
    metadata["implementation_sha256"] = {
        str(file.relative_to(Path(__file__).resolve().parents[1])): hashlib.sha256(
            file.read_bytes()
        ).hexdigest()
        for folder in ("app", "scripts")
        for file in (Path(__file__).resolve().parents[1] / folder).rglob("*.py")
    }
    rows, summaries = [], {}
    cold_start = time.perf_counter()
    try:
        with fixtures(
            dataset,
            output,
            args.chunk_size,
            args.overlap,
            args.dataset,
            pair_headings=args.chunk_pair_headings,
            pdf_layout=args.pdf_layout,
        ) as (
            owners,
            courses,
            docs,
        ):
            metadata["fixture_embedding_and_load_ms"] = (
                time.perf_counter() - cold_start
            ) * 1000
            inverse = {value: key for key, value in docs.items()}
            with Session(get_engine()) as db, TestClient(app) as client:
                metadata["fixture_chunk_count"] = db.scalar(
                    select(func.count())
                    .select_from(Chunk)
                    .where(Chunk.owner_id.in_(owners.values()))
                )
                metadata["database_version"] = db.execute(
                    text("select version()")
                ).scalar()
                metadata["pgvector_version"] = db.execute(
                    text("select extversion from pg_extension where extname='vector'")
                ).scalar()
                metadata["indexes"] = (
                    db.execute(
                        text(
                            "select indexname, indexdef from pg_indexes "
                            "where tablename='chunks'"
                        )
                    )
                    .mappings()
                    .all()
                )
                metadata["indexes"] = [dict(item) for item in metadata["indexes"]]
                metadata["plan"] = db.execute(
                    text(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                        "SELECT c.id FROM chunks c "
                        "JOIN documents d ON c.document_id=d.id WHERE c.owner_id=:uid "
                        "AND c.course_id=:cid AND d.owner_id=:uid AND d.course_id=:cid "
                        "AND d.status='ready' AND c.embedding_model=:model "
                        "ORDER BY c.embedding <=> CAST(:vec AS vector) LIMIT :k"
                    ),
                    {
                        "uid": owners["a"],
                        "cid": courses["a", "main"],
                        "model": settings.embedding_model,
                        "vec": str([1.0] + [0.0] * 511),
                        "k": args.top_k,
                    },
                ).scalar()
                for strategy in args.strategies:
                    settings.retrieval_strategy = strategy
                    if calibrated:
                        selected = calibrated[strategy]["selected"]
                        settings.retrieval_max_distance = selected["max_distance"]
                        settings.retrieval_keyword_min_score = selected["keyword_min"]
                        settings.retrieval_rerank_min_score = selected["rerank_min"]
                        settings.retrieval_rerank_gate = args.gate or calibrated[
                            strategy
                        ].get("gate", "legacy")
                    strategy_rows = []
                    for i, case in enumerate(cases):
                        scope = scope_for(case, owners, courses, docs)
                        row = {
                            "id": case["id"],
                            "strategy": strategy,
                            "split": case["split"],
                            "question": case["question"],
                            "categories": case["categories"],
                            "answerable": case["answerable"],
                            "status": "ok",
                            "cold": i == 0,
                            "reference_answer": case["reference_answer"],
                            "reference_sources": "\n".join(
                                f"{unit['document']} / "
                                f"{unit.get('section', unit.get('page', ''))}: "
                                + (
                                    "[redacted]"
                                    if args.redact_passages
                                    else unit["quote"]
                                )
                                for unit in case["expected"]
                            ),
                            "cost": None,
                        }
                        try:
                            result = search(db, scope, case["question"], settings)
                            violations = sum(
                                not scope_ok(item, scope) for item in result.ranked
                            )
                            if violations:
                                # Never serialize unexpected source content.
                                raise PermissionError("Retrieval scope violation")
                            hits = [
                                matched_units(
                                    item, case["expected"], inverse[item.document.id]
                                )
                                for item in result.ranked
                            ]
                            expected = {unit["unit"] for unit in case["expected"]}
                            evidence_hits = (
                                set().union(
                                    *(
                                        matched_units(
                                            item,
                                            case["expected"],
                                            inverse[item.document.id],
                                        )
                                        for item in result.evidence
                                    )
                                )
                                if result.evidence
                                else set()
                            )
                            row.update(
                                {
                                    "metrics": evidence_metrics(
                                        expected, hits, args.top_k
                                    ),
                                    "evidence_coverage": len(evidence_hits)
                                    / len(expected)
                                    if expected
                                    else None,
                                    "scope_violations": 0,
                                    "retrieval_ms": result.retrieval_ms,
                                    "rerank_ms": result.rerank_ms,
                                    "stages_ms": {
                                        "dense": result.dense_ms,
                                        "keyword": result.keyword_ms,
                                        "fusion": result.fusion_ms,
                                        "selection": result.selection_ms,
                                        "verified_tables": result.table_ms,
                                    },
                                    "rerank_count": result.rerank_count,
                                    "rerank_input_chars": result.rerank_input_chars,
                                    "rerank_cache_hits": result.rerank_cache_hits,
                                    "rerank_cache_misses": result.rerank_cache_misses,
                                    "gate_policy": result.gate_policy,
                                    "coverage_status": result.coverage_status,
                                    "duplicates": result.duplicates,
                                    "windowed_ids": result.windowed_ids,
                                    "refused": not result.evidence,
                                    "gate_refused": not result.evidence,
                                    "refusal_basis": "retrieval_gate",
                                    "ranked": [
                                        {
                                            "document": inverse[item.document.id],
                                            "chunk_id": item.chunk.id,
                                            "section": item.chunk.section_path,
                                            "page_start": item.chunk.page_start,
                                            "page_end": item.chunk.page_end,
                                            "slide_no": item.chunk.slide_no,
                                            "text": ""
                                            if args.redact_passages
                                            else item.chunk.text,
                                            "text_chars": len(item.chunk.text),
                                            "text_sha256": hashlib.sha256(
                                                item.chunk.text.encode()
                                            ).hexdigest(),
                                            "units": sorted(hit),
                                            "distance": item.distance,
                                            "keyword_score": item.keyword_score,
                                            "fusion_score": item.fusion_score,
                                            "rerank_score": item.rerank_score,
                                        }
                                        for item, hit in zip(
                                            result.ranked, hits, strict=True
                                        )
                                    ],
                                    "rejected": result.rejected,
                                    "evidence_ids": [
                                        item.chunk.id for item in result.evidence
                                    ],
                                }
                            )
                            indexed = expected
                            if expected - evidence_hits:
                                all_visible = db.execute(
                                    PgVectorStore(db, scope, settings).statement()
                                ).all()
                                indexed = (
                                    set().union(
                                        *(
                                            matched_units(
                                                Candidate(c, d),
                                                case["expected"],
                                                inverse[d.id],
                                            )
                                            for c, d in all_visible
                                        )
                                    )
                                    if all_visible
                                    else set()
                                )
                                recalled = (
                                    set().union(
                                        *(
                                            matched_units(
                                                item,
                                                case["expected"],
                                                inverse[item.document.id],
                                            )
                                            for item in result.recalled
                                        )
                                    )
                                    if result.recalled
                                    else set()
                                )
                                reranked = set().union(*hits) if hits else set()
                                row["failure_stage"] = (
                                    "chunk_or_parse_anchor_missing"
                                    if expected - indexed
                                    else "not_recalled"
                                    if expected - recalled
                                    else "rerank_candidate_cutoff"
                                    if expected - reranked
                                    else "top_k_threshold_or_context_budget"
                                )
                            if any(
                                not scope_ok(item, scope)
                                for branch in result.branches.values()
                                for item in branch
                            ):
                                raise PermissionError("Trace scope violation")
                            row["trace"] = retrieval_trace(
                                result,
                                inverse,
                                case["expected"],
                                indexed,
                                redact=args.redact_passages,
                            )
                            row["subquestion_evidence"] = row["trace"][
                                "evidence_losses"
                            ]
                            if args.end_to_end:
                                # Retrieval evaluation just issued this query.
                                # Do not let that prefill masquerade as a first
                                # real API request; repeats are benchmarked apart.
                                if args.score_cache:
                                    score_cache.clear()
                                headers = {
                                    "Authorization": "Bearer "
                                    + create_token(scope.owner_id)
                                }
                                # Tokens remain in memory, outside serialized results.
                                runtime_case = {
                                    **case,
                                    "_other_token": create_token(owners["b"]),
                                }
                                row.update(
                                    end_to_end(
                                        client,
                                        runtime_case,
                                        scope,
                                        headers,
                                        db,
                                        pricing,
                                        document_keys=inverse,
                                        redact=args.redact_passages,
                                    )
                                )
                        except Exception as exc:
                            db.rollback()
                            row.update(
                                {"status": "error", "error_type": type(exc).__name__}
                            )
                            if isinstance(exc, PermissionError):
                                row["scope_violations"] = 1
                        rows.append(row)
                        strategy_rows.append(row)
                        (output / "cases.json").write_text(
                            json.dumps(rows, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        print(
                            f"{strategy} {i + 1}/{len(cases)} {row['status']}",
                            flush=True,
                        )
                        # A failed model load must not be retried on every question.
                        if strategy == "rerank" and row["status"] == "error":
                            metadata["skips"].append(
                                {
                                    "strategy": strategy,
                                    "remaining": len(cases) - i - 1,
                                    "reason": row["error_type"],
                                }
                            )
                            break
                    summaries[strategy] = summarize(strategy_rows)
    finally:
        for name in type(settings).model_fields:
            setattr(settings, name, getattr(original, name))
        (output / "summary.json").write_text(
            json.dumps(
                {"metadata": metadata, "strategies": summaries},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        write_review(rows, output / "human_review.csv")
        lines = [
            "# Stage 3 run",
            "",
            "Human factuality and citation support: pending.",
            "",
            "| Strategy | n | errors | Recall@K | Hit@K | MRR | "
            "False refusal | No-answer refusal |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for name, summary in summaries.items():
            lines.append(
                "| "
                + " | ".join(
                    str(v)
                    for v in [
                        name,
                        summary["n"],
                        summary["errors"],
                        summary["recall"],
                        summary["hit"],
                        summary["mrr"],
                        summary["false_refusal_rate"],
                        summary["no_answer_refusal_rate"],
                    ]
                )
                + " |"
            )
        (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
