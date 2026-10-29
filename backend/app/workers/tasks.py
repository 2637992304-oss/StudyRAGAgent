import hashlib
import logging
import threading
import time
from uuid import UUID

from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    before_task_publish,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
    worker_shutdown,
    worker_shutting_down,
)
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability import JsonFormatter, configure_logging, request_id
from app.db.models import Chunk, Document, IngestionJob
from app.db.session import get_engine
from app.ingestion.parser import make_chunks, parse_document
from app.providers.embeddings import embed_texts
from app.providers.local_storage import LocalStorage
from app.workers.recovery import Heartbeat, claim, live, locked_job

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


def set_stage(job_id: str, stage: str, progress: int, token: str) -> bool:
    logger.info("ingestion_stage", extra={"job_id": job_id, "status": stage})
    with Session(get_engine()) as db, db.begin():
        doc, job = locked_job(db, job_id)
        if not live(job, token):
            return False
        job.stage, job.progress = stage, progress
        return True


def ingest_document(job_id: str) -> None:
    token = claim(job_id)
    if token is None:
        return
    with Heartbeat(job_id, token):
        _ingest_owned(job_id, token)


def _ingest_owned(job_id, token):
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
        parts = parse_document(filename, content)
        warnings = getattr(parts, "warnings", [])
        parser_version = getattr(
            parts,
            "version",
            "stage4-vision-v1"
            if filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            else "stage3-v3-page-ocr",
        )
        if filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            parser_version = ("stage4-vision-v1:" + get_settings().llm_model)[:40]
        else:
            parser_version += (":layout" if pdf_layout else "") + (
                ":heads" if pair_headings else ""
            )
        chunks = make_chunks(parts, pair_headings=pair_headings, pdf_layout=pdf_layout)
        if not chunks:
            raise ValueError("解析结果为空")
        if not set_stage(job_id, "embedding", 50, token):
            return
        vectors = embed_texts([chunk["text"] for chunk in chunks])
        with Session(get_engine()) as db, db.begin():
            doc, job = locked_job(db, job_id)
            if not live(job, token):
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
                        parser_version=parser_version,
                        embedding=vector,
                        **chunk,
                    )
                )
            job.status = job.stage = "completed"
            job.progress, job.error = 100, None
            job.execution_token = job.lease_until = None
            doc.status = "ready"
            doc.parse_warnings, doc.parser_version = warnings, parser_version
            doc.parse_quality = "partial" if warnings else "extracted"
    except Exception as exc:
        logger.error("Ingestion failed job=%s type=%s", job_id, type(exc).__name__)
        with Session(get_engine()) as db, db.begin():
            doc, job = locked_job(db, job_id)
            if live(job, token):
                job.status = job.stage = doc.status = "failed"
                job.error = "资料处理失败，请检查格式、OCR或模型配置后重试"
                job.execution_token = job.lease_until = None
                doc.parse_quality = "failed"
                doc.parse_warnings = getattr(exc, "warnings", [])
                doc.parser_version = getattr(exc, "version", None)


@celery_app.task(name="studyrag.ingest")
def ingest_task(job_id: str) -> None:
    ingest_document(job_id)


_RECOVERY_STOP = threading.Event()
_RECOVERY_THREAD = None


@worker_process_init.connect
def reset_child_connections(**kwargs):
    # Recovery runs in the parent; replacement prefork children need their own pool.
    get_engine().dispose(close=False)
    get_engine.cache_clear()


@worker_ready.connect
def start_recovery(**kwargs):
    global _RECOVERY_THREAD
    if _RECOVERY_THREAD is not None and _RECOVERY_THREAD.is_alive():
        return
    from app.workers.recovery import run

    _RECOVERY_STOP.clear()
    _RECOVERY_THREAD = threading.Thread(
        target=run, args=(_RECOVERY_STOP,), daemon=True, name="ingestion-recovery"
    )
    _RECOVERY_THREAD.start()


@worker_shutting_down.connect
@worker_shutdown.connect
def stop_recovery(**kwargs):
    _RECOVERY_STOP.set()
    if _RECOVERY_THREAD is not None:
        _RECOVERY_THREAD.join(timeout=5)
