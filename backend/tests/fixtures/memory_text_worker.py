"""Isolated index worker, deterministic embedding with a real kill barrier."""

import os
import time
from pathlib import Path

from app.memory import text_index
from app.workers.tasks import celery_app

control = Path(os.environ["MEMORY_TEXT_WORKER_CONTROL"])
if (
    not os.environ.get("STAGE7_VALIDATION_RUN")
    or "/text_worker" not in os.environ["DATABASE_URL"]
):
    raise RuntimeError("dedicated_worker_database_required")


def embed(texts):
    (control / "entered").touch()
    while not (control / "release").exists():
        time.sleep(0.05)
    return [[1.0] + [0.0] * 511 for _ in texts]


text_index.embed_texts = embed
if __name__ == "__main__":
    celery_app.worker_main(
        [
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--loglevel=WARNING",
            "--without-gossip",
            "--without-mingle",
        ]
    )
