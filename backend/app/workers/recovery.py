"""The job row is the durable outbox. Delivery is at-least-once, execution fenced."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Document, IngestionJob
from app.db.session import get_engine

logger = logging.getLogger(__name__)


def utc(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def locked_job(db, job_id):
    doc_id = db.scalar(
        select(IngestionJob.document_id).where(IngestionJob.id == job_id)
    )
    if not doc_id:
        return None, None
    # Same lock order as deletion and final writes: document before job.
    doc = db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
    job = db.scalar(
        select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
    )
    if not doc or not job or doc.active_job_id != job_id or doc.status == "deleting":
        return None, None
    return doc, job


def live(job, token):
    return bool(
        job
        and job.status == "processing"
        and job.execution_token == token
        and job.lease_until
        and utc(job.lease_until) > datetime.now(UTC)
    )


def fail(doc, job, message):
    doc.status = job.status = "failed"
    job.stage, job.error = "failed", message
    job.execution_token = job.lease_until = None


def claim(job_id):
    settings = get_settings()
    with Session(get_engine()) as db, db.begin():
        doc, job = locked_job(db, job_id)
        if not job or job.status != "queued":
            return None
        if job.attempts >= settings.ingestion_max_attempts:
            fail(doc, job, "处理多次中断，请检查服务后重试")
            return None
        token = str(uuid4())
        # Conditional update also protects adapters without row locks.
        result = db.execute(
            update(IngestionJob)
            .where(IngestionJob.id == job_id, IngestionJob.status == "queued")
            .values(
                execution_token=token,
                status="processing",
                stage="parsing",
                progress=10,
                attempts=IngestionJob.attempts + 1,
                lease_until=datetime.now(UTC)
                + timedelta(seconds=settings.ingestion_lease_seconds),
            )
        )
        if result.rowcount != 1:
            return None
        doc.status = "processing"
        return token


def renew(job_id, token):
    with Session(get_engine()) as db, db.begin():
        return (
            db.execute(
                update(IngestionJob)
                .where(
                    IngestionJob.id == job_id,
                    IngestionJob.status == "processing",
                    IngestionJob.execution_token == token,
                    IngestionJob.lease_until > datetime.now(UTC),
                )
                .values(
                    lease_until=datetime.now(UTC)
                    + timedelta(seconds=get_settings().ingestion_lease_seconds)
                )
            ).rowcount
            == 1
        )


class Heartbeat:
    def __init__(self, job_id, token):
        self.job_id, self.token = job_id, token
        self.stop = threading.Event()

    def __enter__(self):
        def pulse():
            while not self.stop.wait(
                max(0.5, get_settings().ingestion_lease_seconds / 3)
            ):
                try:
                    if not renew(self.job_id, self.token):
                        return
                except Exception:
                    # Final writes still check expiry after transient errors.
                    logger.warning("ingestion_heartbeat_unavailable")

        self.thread = threading.Thread(
            target=pulse, daemon=True, name="ingestion-heartbeat"
        )
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=1)


def dispatch(job_id, publish, engine=None):
    """Persist delivery reservation first. A crash at either side is recoverable."""
    settings, now = get_settings(), datetime.now(UTC)
    with Session(engine or get_engine()) as db, db.begin():
        doc, job = locked_job(db, job_id)
        if not job or job.status != "queued":
            return False
        if (
            job.dispatched_at
            and utc(job.dispatched_at)
            + timedelta(seconds=settings.ingestion_delivery_seconds)
            > now
        ):
            return False
        if job.delivery_attempts >= settings.ingestion_max_deliveries:
            fail(doc, job, "任务投递多次失败，请检查队列后重试")
            return False
        job.dispatched_at, job.delivery_attempts = now, job.delivery_attempts + 1
    try:
        publish(job_id)
        return True
    except Exception:
        # Keep queued: reservation expiry schedules another attempt even if API exits.
        logger.warning("ingestion_delivery_deferred", extra={"job_id": job_id})
        return False


def recover_once(publish=None):
    if publish is None:
        from app.workers.tasks import ingest_task

        publish = ingest_task.delay
    settings, now = get_settings(), datetime.now(UTC)
    with Session(get_engine()) as db:
        ids = list(
            db.scalars(
                select(IngestionJob.id)
                .where(
                    or_(
                        and_(
                            IngestionJob.status == "queued",
                            or_(
                                IngestionJob.dispatched_at.is_(None),
                                IngestionJob.dispatched_at
                                <= now
                                - timedelta(
                                    seconds=settings.ingestion_delivery_seconds
                                ),
                            ),
                        ),
                        and_(
                            IngestionJob.status == "processing",
                            or_(
                                IngestionJob.lease_until <= now,
                                and_(
                                    IngestionJob.lease_until.is_(None),
                                    IngestionJob.updated_at
                                    <= now
                                    - timedelta(
                                        seconds=settings.ingestion_lease_seconds
                                    ),
                                ),
                            ),
                        ),
                    )
                )
                .order_by(IngestionJob.created_at)
                .limit(1000)
            )
        )
    for job_id in ids:
        with Session(get_engine()) as db, db.begin():
            doc, job = locked_job(db, job_id)
            if not job:
                continue
            # Old processing rows have no lease; grant an initial grace from updated_at.
            expires = utc(job.lease_until or job.updated_at) + (
                timedelta(0)
                if job.lease_until
                else timedelta(seconds=settings.ingestion_lease_seconds)
            )
            if job.status == "processing" and expires <= now:
                if job.attempts >= settings.ingestion_max_attempts:
                    fail(doc, job, "处理多次中断，请检查服务后重试")
                else:
                    job.status = doc.status = "queued"
                    job.stage, job.error = "recovering", "上次处理中断，正在重新排队"
                    job.execution_token = job.lease_until = job.dispatched_at = None
        dispatch(job_id, publish)


def run(stop):
    while not stop.is_set():
        try:
            recover_once()
        except Exception as exc:
            logger.warning("ingestion_recovery_unavailable type=%s", type(exc).__name__)
        stop.wait(get_settings().ingestion_recovery_interval)


if __name__ == "__main__":
    import sys

    if "--once" in sys.argv:
        recover_once()
    else:
        run(threading.Event())
