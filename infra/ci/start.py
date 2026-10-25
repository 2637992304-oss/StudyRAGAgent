"""CI-only model substitute. Never copied into the production image.

Constant embeddings test wiring and scope, not semantic retrieval quality.
"""

import sys

from app.providers import embeddings

embeddings.embed_texts = lambda texts: [[1.0] + [0.0] * 511 for _ in texts]

if sys.argv[1] == "api":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, access_log=False)
elif sys.argv[1] == "worker":
    from app.workers.tasks import celery_app

    celery_app.worker_main(["worker", "--loglevel=warning", "--pool=solo"])
else:
    raise SystemExit("Expected api or worker")
