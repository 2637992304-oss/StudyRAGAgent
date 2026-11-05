"""Durable indexing with account/job locks and version/token publication fences."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import MemoryIndexJob, MemoryItem, MemoryItemEmbedding, now
from app.db.session import get_engine
from app.memory import service as legacy
from app.memory import text_service as service
from app.providers.embeddings import embed_texts


def execute(ident):
    if get_settings().memory_engine != "text":
        return
    with Session(get_engine()) as db, db.begin():
        job = db.get(MemoryIndexJob, ident)
        item = db.get(MemoryItem, job.item_id) if job else None
        if item is None:
            return
        legacy.lock(db, item.owner_id)
        db.refresh(item)
        job = db.scalar(
            select(MemoryIndexJob)
            .where(MemoryIndexJob.id == ident)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.status not in {"queued", "running"} or (
            job.status == "running"
            and job.lease_until
            and legacy.utc(job.lease_until) > now()
        ):
            return
        if (
            item.version != job.version
            or not service.effective_time(item)
            or not service.sources_live(db, item)
        ):
            job.status = "invalidated"
            return
        if job.attempts >= 2:
            job.status = "failed"
            return
        token = str(uuid4())
        job.status, job.token, job.lease_until = (
            "running",
            token,
            now() + timedelta(seconds=180),
        )
        job.attempts += 1
        content, version, owner = item.content, item.version, item.owner_id
    try:
        (vector,) = embed_texts([content])
        error = None
    except Exception as exc:
        vector, error = None, type(exc).__name__
    with Session(get_engine()) as db, db.begin():
        legacy.lock(db, owner)
        job = db.scalar(
            select(MemoryIndexJob).where(MemoryIndexJob.id == ident).with_for_update()
        )
        item = db.get(MemoryItem, job.item_id) if job else None
        if not job or job.token != token or job.status != "running":
            return
        if (
            not item
            or item.version != version
            or not service.effective_time(item)
            or not service.sources_live(db, item)
            or legacy.utc(job.lease_until) <= now()
        ):
            job.status = "invalidated"
        elif error:
            job.status = "queued" if job.attempts < 2 else "failed"
            job.data = {"error_type": error}
        else:
            embedding = db.get(MemoryItemEmbedding, item.id)
            if embedding is None:
                embedding = MemoryItemEmbedding(item_id=item.id)
                db.add(embedding)
            embedding.version, embedding.embedding = version, vector
            embedding.content_hash = service.content_hash(content)
            embedding.model = get_settings().embedding_model
            job.status = "completed"
        job.token, job.lease_until = None, None


def dispatch():
    if get_settings().memory_engine != "text":
        return
    from app.workers.tasks import memory_index_task

    with Session(get_engine()) as db:
        for job in db.scalars(
            select(MemoryIndexJob)
            .where(MemoryIndexJob.status.in_(["queued", "running"]))
            .limit(get_settings().memory_queue_limit)
        ):
            if (
                job.status == "queued"
                or not job.lease_until
                or legacy.utc(job.lease_until) <= now()
            ):
                memory_index_task.delay(job.id)
