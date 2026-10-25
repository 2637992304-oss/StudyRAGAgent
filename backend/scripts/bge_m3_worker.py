"""Optional isolated CPU worker. No backend/database imports or production writes.

Run with the separate Sentence Transformers environment, not backend/.venv.
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--max-length", type=int, default=512)
    args = parser.parse_args()
    import torch
    from huggingface_hub import snapshot_download
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    # Public weights only; never send an inherited HF token for this task.
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    request = json.loads(args.input.read_text(encoding="utf-8"))
    started = time.perf_counter()
    snapshot = snapshot_download(
        "BAAI/bge-m3",
        revision=args.revision,
        cache_dir=str(args.cache),
        token=False,
        allow_patterns=[
            "pytorch_model.bin",
            "*.json",
            "*.safetensors",
            "*.model",
            "*.txt",
            "1_Pooling/*",
        ],
        max_workers=2,
    )
    download_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    model = SentenceTransformer(
        snapshot, device="cpu", local_files_only=True, trust_remote_code=False
    )
    model.max_seq_length = args.max_length
    load_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    vectors = model.encode(
        request["passages"],
        batch_size=2,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()
    passage_ms = (time.perf_counter() - started) * 1000
    queries, query_ms = {}, {}
    for query in request["queries"]:
        started = time.perf_counter()
        queries[query] = model.encode(
            [query], normalize_embeddings=True, show_progress_bar=False
        ).tolist()[0]
        query_ms[query] = (time.perf_counter() - started) * 1000
    import psutil

    peak = psutil.Process().memory_info()
    result = dict(
        model="BAAI/bge-m3",
        revision=args.revision,
        dimension=1024,
        device="cpu",
        dtype="float32",
        max_length=args.max_length,
        threads=2,
        batch_size=2,
        snapshot_resolve_or_download_ms=download_ms,
        load_ms=load_ms,
        passage_encode_ms=passage_ms,
        query_encode_ms=query_ms,
        peak_rss_bytes=getattr(peak, "peak_wset", peak.rss),
        passages_sha256=hashlib.sha256(
            json.dumps(request["passages"], ensure_ascii=False).encode()
        ).hexdigest(),
        passage_vectors=vectors,
        query_vectors=queries,
    )
    args.output.write_text(json.dumps(result), encoding="utf-8")
    print("BGE-M3 CPU encoding completed:", len(vectors), "passages", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("BGE worker error:", type(exc).__name__, flush=True)
        raise SystemExit(1) from None
