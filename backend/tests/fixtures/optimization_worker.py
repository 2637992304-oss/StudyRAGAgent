"""Disposable acceptance worker: real PostgreSQL/Redis/Celery, constant vectors."""

import os
import time
from pathlib import Path


def main():
    assert os.environ.get("OPT12_VALIDATION") == "1"
    from app.providers import embeddings

    embeddings.embed_texts = lambda texts: [[1.0] + [0.0] * 511 for _ in texts]
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.models import Document, IngestionJob
    from app.workers import tasks

    assert get_settings().database_url.endswith("/opt12_validation")
    control = Path("/validation-control")
    original_parse = tasks.parse_document
    original_embed = tasks.embed_texts
    original_locked = tasks.locked_job
    lock_counts = {}
    current_name = {}

    def pause_once(name):
        marker = control / (name + ".paused")
        try:
            with marker.open("x") as f:
                f.write("paused")
        except FileExistsError:
            return
        time.sleep(120)

    def parse(name, content):
        current_name["value"] = name
        if name.startswith("parsing-"):
            pause_once(name)
        return original_parse(name, content)

    def embed(texts):
        name = current_name.get("value", "")
        if name.startswith("embedding-"):
            pause_once(name)
        return original_embed(texts)

    def locked(db, jid):
        lock_counts[jid] = lock_counts.get(jid, 0) + 1
        if lock_counts[jid] == 2:
            name = db.scalar(
                select(Document.filename)
                .join(IngestionJob, IngestionJob.document_id == Document.id)
                .where(IngestionJob.id == jid)
            )
            if name and name.startswith("before_write-"):
                pause_once(name)
        return original_locked(db, jid)

    tasks.parse_document = parse
    tasks.embed_texts = embed
    tasks.locked_job = locked
    tasks.celery_app.worker_main(
        ["worker", "--loglevel=warning", "--concurrency=2", "--hostname=opt12@%h"]
    )


if __name__ == "__main__":
    main()
