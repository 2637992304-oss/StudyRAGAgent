import hashlib
import logging
import time
from uuid import UUID

from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    before_task_publish,
    task_postrun,
    task_prerun,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability import JsonFormatter, configure_logging, request_id
from app.db.models import Chunk, Document, IngestionJob
from app.db.session import get_engine
from app.ingestion.parser import make_chunks, parse_document
from app.providers.embeddings import embed_texts
from app.providers.local_storage import LocalStorage

logger = logging.getLogger(__name__)
configure_logging()
celery_app = Celery("studyrag", broker=get_settings().redis_url)
celery_app.conf.update(task_ignore_result=True)


@after_setup_logger.connect
@after_setup_task_logger.connect
def format_worker_logs(logger=None, **kwargs):
    if logger is not None:
        for handler in logger.handlers:
            handler.setFormatter(JsonFormatter())


@before_task_publish.connect
def correlate_publish(headers=None, **kwargs):
    if headers is not None:
        headers["request_id"] = request_id.get()


@task_prerun.connect
def log_task_start(task_id=None, task=None, **kwargs):
    task.request.correlation_token = request_id.set(
        (task.request.headers or {}).get("request_id", "-")
    )
    task.request.started_monotonic = time.monotonic()
    logger.info("task_started", extra={"task_id": task_id})


@task_postrun.connect
def log_task_end(task_id=None, task=None, state=None, **kwargs):
    started = getattr(task.request, "started_monotonic", time.monotonic())
    logger.info(
        "task_finished",
        extra={
            "task_id": task_id,
            "status": state,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    token = getattr(task.request, "correlation_token", None)
    if token is not None:
        request_id.reset(token)


def set_stage(job_id: str, stage: str, progress: int) -> bool:
    logger.info("ingestion_stage", extra={"job_id": job_id, "status": stage})
    with Session(get_engine()) as db, db.begin():
        job = db.get(IngestionJob, job_id)
        if not job or job.status == "completed":
            return False
        doc = db.get(Document, job.document_id)
        if not doc or doc.active_job_id != job_id or doc.status == "deleting":
            return False
        job.status = "processing"
        job.stage = stage
        job.progress = progress
        doc.status = "processing"
        return True


def ingest_document(job_id: str) -> None:
    if not set_stage(job_id, "parsing", 10):
        return
    with Session(get_engine()) as db:
        job = db.get(IngestionJob, job_id)
        doc = db.get(Document, job.document_id) if job else None
        if doc is None:
            return
        filename, key = doc.filename, doc.storage_key
    try:
        content = LocalStorage(get_settings().storage_dir).read(UUID(key))
        pair_headings = get_settings().ingestion_pair_headings
        pdf_layout = get_settings().ingestion_pdf_layout
        chunks = make_chunks(
            parse_document(filename, content),
            pair_headings=pair_headings,
            pdf_layout=pdf_layout,
        )
        if not chunks:
            raise ValueError("解析结果为空")
        if not set_stage(job_id, "embedding", 50):
            return
        vectors = embed_texts([chunk["text"] for chunk in chunks])
        with Session(get_engine()) as db, db.begin():
            doc = db.scalar(
                select(Document).where(Document.id == job.document_id).with_for_update()
            )
            if not doc or doc.active_job_id != job_id or doc.status == "deleting":
                return
            job = db.get(IngestionJob, job_id)
            if job is None or job.status == "completed":
                return
            db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                db.add(
                    Chunk(
                        owner_id=doc.owner_id,
                        course_id=doc.course_id,
                        document_id=doc.id,
                        chunk_index=index,
                        content_hash=hashlib.sha256(chunk["text"].encode()).hexdigest(),
                        embedding_model=get_settings().embedding_model,
                        parser_version=("stage4-vision-v1:" + get_settings().llm_model)[
                            :40
                        ]
                        if filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                        else "stage3-v4-pdf-layout"
                        if pdf_layout and filename.lower().endswith(".pdf")
                        else "stage3-v3-heading-pairs"
                        if pair_headings
                        else "stage3-v3-page-ocr",
                        embedding=vector,
                        **chunk,
                    )
                )
            job.status = "completed"
            job.stage = "completed"
            job.progress = 100
            job.error = None
            doc.status = "ready"
    except Exception as exc:
        logger.error("Ingestion failed job=%s type=%s", job_id, type(exc).__name__)
        with Session(get_engine()) as db, db.begin():
            job = db.get(IngestionJob, job_id)
            if job:
                doc = db.scalar(
                    select(Document)
                    .where(Document.id == job.document_id)
                    .with_for_update()
                )
                if doc and doc.active_job_id == job_id and doc.status != "deleting":
                    job.status = "failed"
                    job.stage = "failed"
                    job.error = (
                        "图片识别失败，请检查模型配置或重试"
                        if filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                        else str(exc)[:1000]
                    )
                    doc.status = "failed"


@celery_app.task(name="studyrag.ingest")
def ingest_task(job_id: str) -> None:
    ingest_document(job_id)
