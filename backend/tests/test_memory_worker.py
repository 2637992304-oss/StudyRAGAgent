"""Actual Celery process interruption and lease recovery in isolated services."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_memory import SummaryProvider, library, mem, stage4, summary_job  # noqa: F401

from app.db.models import MemoryJob
from app.memory.summary import dispatch


@pytest.mark.skipif(
    not os.environ.get("STAGE7_VALIDATION_RUN"),
    reason="requires isolated PG/Redis runner",
)
def test_real_celery_kill_and_recover(mem, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(
        __import__("app.core.config", fromlist=["get_settings"]).get_settings(),
        "memory_summary_timeout",
        1,
    )
    ident = summary_job(mem, monkeypatch)
    env = {
        **os.environ,
        "MEMORY_SUMMARY_ENABLED": "true",
        "MEMORY_SUMMARY_TIMEOUT": "1",
        "MEMORY_WORKER_CONTROL": str(tmp_path),
        "INGESTION_RECOVERY_INTERVAL": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    processes = []
    stream = (tmp_path / "worker.log").open("w", encoding="utf-8")

    def start():
        proc = subprocess.Popen(
            [sys.executable, "tests/fixtures/memory_worker.py"],
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append(proc)
        return proc

    def wait(predicate, timeout=45):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.15)
        raise AssertionError("worker condition not reached")

    try:
        first = start()
        dispatch()
        wait(lambda: (tmp_path / "entered").exists())
        first.kill()
        first.wait(timeout=10)
        with Session(mem[-2]) as db:
            row = db.get(MemoryJob, ident)
            assert row.status == "running" and row.attempts == 1
            assert row.measurements["outcome"] == "running_usage_unknown"
            assert row.measurements["attempt"] is None
        (tmp_path / "release").touch()
        start()

        def completed():
            with Session(mem[-2]) as db:
                return (
                    db.scalar(select(MemoryJob.status).where(MemoryJob.id == ident))
                    == "completed"
                )

        wait(completed)
        with Session(mem[-2]) as db:
            row = db.get(MemoryJob, ident)
            assert row.attempts == 2
            assert row.measurements["attempt"] is not None
            assert row.measurements["previous"]["outcome"] == "running_usage_unknown"
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        stream.close()
