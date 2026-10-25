"""Measure actual cached-model CPU thread/batch settings on frozen DEV candidates."""

import argparse
import json
import time
from pathlib import Path

from app.core.config import get_settings
from app.evaluation.metrics import latency
from app.retrieval import FastEmbedReranker, cross_encoder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    rows = [
        row
        for row in json.loads((args.run / "cases.json").read_text(encoding="utf-8"))
        if row["status"] == "ok"
    ][: args.limit]
    if any(row["split"] != "dev" for row in rows):
        raise ValueError("Only frozen development candidates are allowed")
    trials = []
    for threads, batch in [(2, 8), (2, 4), (4, 4), (4, 2), (1, 4)]:
        settings = get_settings().model_copy(
            update={"reranker_threads": threads, "reranker_batch_size": batch}
        )
        ranker = FastEmbedReranker(settings)
        start = time.perf_counter()
        ranker.score("warmup", ["公开文本模型预热"])
        load_ms = (time.perf_counter() - start) * 1000
        durations, drift = [], []
        for row in rows:
            start = time.perf_counter()
            scores = ranker.score(row["question"], [c["text"] for c in row["ranked"]])
            durations.append((time.perf_counter() - start) * 1000)
            drift.extend(
                abs(score - c["rerank_score"])
                for score, c in zip(scores, row["ranked"], strict=True)
            )
        trial = {
            "threads": threads,
            "batch_size": batch,
            "load_and_warmup_ms": load_ms,
            "inference_ms": latency(durations),
            "max_score_drift": max(drift),
            "ids": [row["id"] for row in rows],
        }
        trials.append(trial)
        print(trial, flush=True)
        cross_encoder.cache_clear()
    args.output.write_text(
        json.dumps(
            {
                "method": "real CPU; no source truncation; warmed model",
                "trials": trials,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
