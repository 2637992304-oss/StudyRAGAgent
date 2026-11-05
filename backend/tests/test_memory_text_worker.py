"""Real process death + durable index lease recovery, separate PG and Redis DB."""

import os
import subprocess
import sys
import time
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_memory import library, mem, stage4  # noqa: F401
from test_memory_text_flow import create, text_mem  # noqa: F401

from app.db.models import MemoryIndexJob, MemoryItemEmbedding, now
from app.memory.text_index import dispatch


@pytest.mark.skipif(
    "/text_worker" not in os.environ.get("DATABASE_URL", ""),
    reason="dedicated worker database required",
)
def test_actual_index_worker_kill_and_recover(text_mem, tmp_path):  # noqa: F811
    item = create(text_mem).json()["items"][0]
    env = {
        **os.environ,
        "MEMORY_ENGINE": "text",
        "MEMORY_PREFERENCES_ENABLED": "true",
        "MEMORY_TEXT_WORKER_CONTROL": str(tmp_path),
        "INGESTION_RECOVERY_INTERVAL": "1",
    }
    processes = []
    handle = (tmp_path / "index-worker.log").open("w", encoding="utf-8")

    def start():
        process = subprocess.Popen(
            [sys.executable, "tests/fixtures/memory_text_worker.py"],
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append(process)
        return process

    def wait(predicate):
        until = time.monotonic() + 45
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(0.1)
        raise AssertionError("index worker condition not reached")

    try:
        first = start()
        dispatch()
        wait(lambda: (tmp_path / "entered").exists())
        first.kill()
        first.wait(timeout=10)
        with Session(text_mem[-2]) as db, db.begin():
            job = db.scalar(
                select(MemoryIndexJob).where(MemoryIndexJob.item_id == item["id"])
            )
            assert job.status == "running" and job.attempts == 1
            assert db.get(MemoryItemEmbedding, item["id"]) is None
            job.lease_until = now() - timedelta(seconds=1)
            ident = job.id
        (tmp_path / "release").touch()
        start()

        def completed():
            with Session(text_mem[-2]) as db:
                return db.get(MemoryIndexJob, ident).status == "completed"

        wait(completed)
        dispatch()
        with Session(text_mem[-2]) as db:
            assert db.get(MemoryIndexJob, ident).attempts == 2
            assert db.get(MemoryItemEmbedding, item["id"]).version == item["version"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        handle.close()
