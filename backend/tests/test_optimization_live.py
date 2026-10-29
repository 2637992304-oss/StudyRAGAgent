"""Opt-in acceptance against disposable PostgreSQL, Redis and a real Celery worker.

The worker uses deterministic embeddings. These tests prove engineering behavior,
not embedding/model quality. Never point them at an existing learning database.
"""

import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_stage4 import Scripted, call
from test_table_evidence import library as relation_rows

from app.api import chat as chat_api
from app.core.auth import create_token
from app.core.config import get_settings
from app.db.models import ChatSession, Chunk, Course, Document, IngestionJob, User
from app.db.session import get_engine
from app.evaluation.runtime import observer
from app.main import app
from app.providers.llm import Delta
from app.providers.local_storage import LocalStorage
from app.workers.tasks import ingest_task

pytestmark = pytest.mark.skipif(
    os.environ.get("OPT12_LIVE") != "1", reason="explicit disposable services required"
)
WORKER = "studyrag-opt12-worker"
LABEL = "opt12-20261029"


def worker_command(*args):
    label = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "codex.validation"}}',
            WORKER,
        ],
        text=True,
    ).strip()
    assert label == LABEL, "refusing to operate an unowned container"
    subprocess.run(["docker", *args, WORKER], check=True, capture_output=True)


@pytest.fixture
def live_account():
    settings = get_settings()
    assert settings.database_url == (
        "postgresql+psycopg://opt12:opt12-test-only@127.0.0.1:55439/opt12_validation"
    ), "disposable database URL required"
    assert settings.redis_url == "redis://127.0.0.1:56389/0"
    engine = get_engine()
    with Session(engine) as db:
        assert db.scalar(text("select current_database()")) == "opt12_validation"
    uid, cid, other = str(uuid4()), str(uuid4()), str(uuid4())
    with Session(engine) as db, db.begin():
        db.add_all(
            [
                User(id=uid, email=f"{uid}@example.test", password_hash="disabled"),
                User(id=other, email=f"{other}@example.test", password_hash="disabled"),
            ]
        )
        db.flush()
        db.add(Course(id=cid, owner_id=uid, name="self-authored validation"))
    client = TestClient(app)
    try:
        yield client, {"Authorization": "Bearer " + create_token(uid)}, uid, cid, other
    finally:
        with Session(engine) as db, db.begin():
            for chat in db.scalars(
                select(ChatSession).where(ChatSession.owner_id == uid)
            ):
                db.delete(chat)
            db.flush()
            for doc in db.scalars(select(Document).where(Document.owner_id == uid)):
                LocalStorage(settings.storage_dir).delete(UUID(doc.storage_key))
                db.delete(doc)
            db.flush()
            db.delete(db.get(Course, cid))
            db.flush()
            for key in (uid, other):
                db.delete(db.get(User, key))


@pytest.mark.parametrize("mode", ["program", "disabled", "source_deleted", "cancelled"])
def test_current_agent_real_pgvector_table_path(live_account, monkeypatch, mode):
    from scripts.evaluate_stage3 import end_to_end

    client, headers, uid, cid, other = live_account
    settings = get_settings()
    monkeypatch.setattr(settings, "retrieval_verified_tables", mode != "disabled")
    monkeypatch.setattr(settings, "retrieval_strategy", "dense")
    monkeypatch.setattr(settings, "llm_api_key", "controlled-provider-not-a-real-key")
    monkeypatch.setattr(chat_api, "embed_texts", lambda _: [[1.0] + [0.0] * 511])
    did = str(uuid4())
    items = relation_rows()
    with Session(get_engine()) as db, db.begin():
        db.add(
            Document(
                id=did,
                owner_id=uid,
                course_id=cid,
                filename="relations.md",
                document_type="笔记",
                mime_type="text/markdown",
                storage_key=str(uuid4()),
                sha256="0" * 64,
                size_bytes=100,
                status="ready",
            )
        )
        db.flush()
        for i, item in enumerate(items):
            db.add(
                Chunk(
                    owner_id=uid,
                    course_id=cid,
                    document_id=did,
                    chunk_index=i,
                    text=item.chunk.text,
                    content_hash="1" * 64,
                    parser_version="authored",
                    embedding_model=settings.embedding_model,
                    embedding=[1.0] + [0.0] * 511,
                )
            )
    turns = [[call("Ada salary")]]
    if mode == "disabled":
        turns += [
            [Delta("Ada的工资为60000 [4]", finish_reason="stop")],
            [Delta('{"supported":true}', finish_reason="stop")],
        ]
    execution = []

    class Controlled(Scripted):
        def stream(self, *args, **kwargs):
            execution[:] = [kwargs.get("execution")]
            yield from super().stream(*args, **kwargs)

    provider = Controlled(turns)
    monkeypatch.setattr(chat_api, "get_llm_provider", lambda: provider)
    if mode in {"source_deleted", "cancelled"}:
        seen = []

        def capture(event):
            seen.append(event)
            if event["kind"] == "table_verification":
                if mode == "cancelled":
                    execution[0].cancelled.set()
                else:
                    with Session(get_engine()) as db, db.begin():
                        db.get(Document, did).status = "deleting"

        token = observer.set(capture)
        try:
            sid = client.post(
                "/api/v1/chat/sessions", headers=headers, json={"course_id": cid}
            ).json()["id"]
            response = client.post(
                f"/api/v1/chat/sessions/{sid}/messages",
                headers=headers,
                json={
                    "content": "严格根据资料，Ada的工资和预算是多少？",
                    "answer_scope": "strict",
                },
            )
        finally:
            observer.reset(token)
        assert "60000" not in response.text
        assert (
            "cancelled" if mode == "cancelled" else "expired_evidence"
        ) in response.text
        return
    with Session(get_engine()) as db:
        result = end_to_end(
            client,
            {
                "question": "严格根据资料，Ada的工资和预算是多少？",
                "categories": ["table"],
                "key_facts": ["60000"],
                "_other_token": create_token(other),
            },
            ChatSession(owner_id=uid, course_id=cid, document_ids=[did]),
            headers,
            db,
            None,
        )
    assert result["citations"] and all(
        c["matches_actual_prompt"] and c["foreign_denied"] for c in result["citations"]
    )
    assert result["context_capture"] == "captured"
    assert result["answer_method"] == (
        "verified_table_v1" if mode == "program" else "llm"
    )
    assert result["model_turns"] == (1 if mode == "program" else 3)
    if mode == "program":
        assert "60000" in result["answer"] and "50000" in result["answer"]


def wait_document(did, expected="ready", seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with Session(get_engine()) as db:
            doc = db.get(Document, did)
            if doc.status == expected:
                return
            if doc.status == "failed" and expected != "failed":
                pytest.fail("isolated ingestion failed")
        time.sleep(0.2)
    pytest.fail("isolated ingestion deadline exceeded")


def create_job(uid, cid, filename):
    did, jid, key = str(uuid4()), str(uuid4()), uuid4()
    LocalStorage(get_settings().storage_dir).write(
        UUID(str(key)), b"self authored note"
    )
    with Session(get_engine()) as db, db.begin():
        db.add(
            Document(
                id=did,
                owner_id=uid,
                course_id=cid,
                filename=filename,
                document_type="笔记",
                mime_type="text/plain",
                storage_key=str(key),
                sha256="0" * 64,
                size_bytes=18,
                active_job_id=jid,
            )
        )
        db.flush()
        db.add(IngestionJob(id=jid, owner_id=uid, document_id=did))
    return did, jid


@pytest.mark.parametrize("phase", ["parsing", "embedding", "before_write"])
def test_real_celery_kill_and_recovery(live_account, phase):
    _, _, uid, cid, _ = live_account
    name = f"{phase}-{uuid4()}.txt"
    did, jid = create_job(uid, cid, name)
    marker = Path(os.environ["OPT12_CONTROL"]) / (name + ".paused")
    end = time.monotonic() + 40
    while not marker.exists() and time.monotonic() < end:
        time.sleep(0.2)
    assert marker.exists(), "worker did not reach controlled pause"
    worker_command("kill", "--signal", "KILL")
    with Session(get_engine()) as db, db.begin():
        job = db.get(IngestionJob, jid)
        old_token = job.execution_token
        assert job.status == "processing"
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    worker_command("start")
    wait_document(did)
    with Session(get_engine()) as db:
        job = db.get(IngestionJob, jid)
        assert job.attempts == 2 and job.execution_token is None
        assert old_token and job.status == "completed"
        assert len(list(db.scalars(select(Chunk).where(Chunk.document_id == did)))) == 1


def test_real_duplicate_delivery_and_maintenance(live_account, monkeypatch):
    import sys

    from scripts import backup_payload

    _, _, uid, cid, _ = live_account
    did, jid = create_job(uid, cid, "duplicates.txt")
    ingest_task.delay(jid)
    ingest_task.delay(jid)
    wait_document(did)
    with Session(get_engine()) as db:
        assert db.get(IngestionJob, jid).attempts == 1
        assert len(list(db.scalars(select(Chunk).where(Chunk.document_id == did)))) == 1
    worker_command("stop", "--time", "15")
    pending, _ = create_job(uid, cid, "maintenance.txt")
    monkeypatch.setattr(sys, "argv", ["backup_payload", "quiescent"])
    try:
        with pytest.raises(SystemExit, match="Unfinished"):
            backup_payload.main()
        time.sleep(2)
        with Session(get_engine()) as db:
            assert db.get(Document, pending).status == "queued"
    finally:
        worker_command("start")
    wait_document(pending)
    backup_payload.main()


def test_real_migration_preserves_existing_document(live_account):
    from alembic import command
    from alembic.config import Config

    _, _, uid, cid, _ = live_account
    worker_command("stop", "--time", "15")
    did, jid = create_job(uid, cid, "migration-sample.txt")
    with Session(get_engine()) as db, db.begin():
        db.get(Document, did).status = "ready"
        job = db.get(IngestionJob, jid)
        job.status, job.stage = "completed", "completed"
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    try:
        command.downgrade(cfg, "0007_session_titles")
        with Session(get_engine()) as db:
            assert (
                db.scalar(
                    text("select filename from documents where id=:id"), {"id": did}
                )
                == "migration-sample.txt"
            )
        command.upgrade(cfg, "head")
        command.check(cfg)
        with Session(get_engine()) as db:
            doc = db.get(Document, did)
            job = db.get(IngestionJob, jid)
            assert doc.status == "ready" and doc.parse_warnings == []
            assert doc.parse_quality is None and job.attempts == 0
    finally:
        command.upgrade(cfg, "head")
        worker_command("start")
