"""Disposable Celery worker with a deterministic, controllably blocked provider."""

import json
import os
import time
from pathlib import Path

from app.providers import llm
from app.providers.llm import Delta
from app.workers.tasks import celery_app

control = Path(os.environ["MEMORY_WORKER_CONTROL"])


class Provider:
    def stream(self, messages, **kwargs):
        (control / "entered").touch()
        while not (control / "release").exists():
            time.sleep(0.05)
        ident = json.loads(messages[-1]["content"])["messages"][0]["id"]
        yield Delta(
            json.dumps({"task_goal": [{"text": "复习事务", "source_ids": [ident]}]}),
            finish_reason="stop",
        )


llm.get_llm_provider = Provider
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
