"""Only for the explicitly disposable optimization56 service runner."""

import os
import time
from pathlib import Path


def main():
    assert os.environ.get("OPT56_VALIDATION") == "1"
    from app.providers import embeddings

    embeddings.embed_texts = lambda texts: [[1.0] + [0.0] * 511 for _ in texts]
    from app.core.config import get_settings
    from app.workers import tasks

    assert get_settings().database_url.endswith("/validation")
    original = tasks.parse_document

    def parse(name, content):
        if name.startswith("opt56-kill-"):
            marker = Path("/validation-control") / (name + ".paused")
            try:
                with marker.open("x") as file:
                    file.write("paused")
            except FileExistsError:
                pass
            else:
                time.sleep(120)
        return original(name, content)

    tasks.parse_document = parse
    tasks.celery_app.worker_main(
        ["worker", "--loglevel=warning", "--concurrency=1", "--hostname=opt56@%h"]
    )


if __name__ == "__main__":
    main()
