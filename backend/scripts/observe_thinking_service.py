"""Optional acceptance launcher: audit only non-sensitive outbound parameters.

Use --service api or worker. This never records messages, images or reasoning.
Normal application launch remains unchanged.
"""

import argparse
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.providers.llm import call_observer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=["api", "worker"], required=True)
    parser.add_argument("--audit", required=True)
    args = parser.parse_args()
    path = Path(args.audit).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def record(data):
        with lock, path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now(UTC).isoformat(), **data}) + "\n")

    call_observer.set(record)
    if args.service == "api":
        import uvicorn

        from app.main import app

        uvicorn.run(app, host="127.0.0.1", port=8000)
    else:
        from app.workers.tasks import celery_app

        celery_app.worker_main(
            ["worker", "--pool=solo", "--concurrency=1", "--loglevel=WARNING"]
        )


if __name__ == "__main__":
    main()
