from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Chunk, Course, Document, IngestionJob, User
from app.providers.local_storage import LocalStorage
from app.workers import recovery, tasks


@pytest.fixture
def isolated_job(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "isolated.db").as_posix())
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tasks, "get_engine", lambda: engine)
    monkeypatch.setattr(recovery, "get_engine", lambda: engine)
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "files")
    monkeypatch.setattr(
        tasks, "embed_texts", lambda texts: [[1.0] + [0.0] * 511 for _ in texts]
    )
    key, uid, cid, did, jid = (
        uuid4(),
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
    )
    LocalStorage(get_settings().storage_dir).write(key, b"self authored note")
    with Session(engine) as db, db.begin():
        db.add(User(id=uid, email=f"{uid}@example.test", password_hash="test"))
        db.add(Course(id=cid, owner_id=uid, name="test"))
        db.add(
            Document(
                id=did,
                owner_id=uid,
                course_id=cid,
                filename="note.txt",
                mime_type="text/plain",
                document_type="笔记",
                storage_key=str(key),
                sha256="0" * 64,
                size_bytes=18,
                active_job_id=jid,
            )
        )
        db.add(IngestionJob(id=jid, document_id=did, owner_id=uid))
    yield engine, did, jid
    engine.dispose()


def expire(engine, jid):
    with Session(engine) as db, db.begin():
        db.get(IngestionJob, jid).lease_until = datetime.now(UTC) - timedelta(seconds=1)


def test_commit_before_delivery_and_duplicate_execution(isolated_job):
    engine, did, jid = isolated_job
    recovery.recover_once(publish=tasks.ingest_document)
    tasks.ingest_document(jid)
    with Session(engine) as db:
        assert db.get(Document, did).status == "ready"
        assert len(list(db.scalars(select(Chunk)))) == 1
        assert db.get(IngestionJob, jid).attempts == 1


def test_parse_warnings_survive_ingestion_and_api(isolated_job, monkeypatch):
    from app.api import document_data
    from app.ingestion.parser import ParsedParts, Part

    engine, did, jid = isolated_job
    parsed = ParsedParts()
    parsed.append(Part("available native text", page=1))
    parsed.warnings.append("第 1 页OCR未完成")
    monkeypatch.setattr(tasks, "parse_document", lambda *_: parsed)
    tasks.ingest_document(jid)
    with Session(engine) as db:
        result = document_data(db.get(Document, did))
    assert result["status"] == "ready" and result["parse_quality"] == "partial"
    assert result["parse_warnings"] == parsed.warnings
    assert result["parser_version"] == parsed.version


def test_upload_and_retry_use_durable_delivery(isolated_job, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.auth import create_token
    from app.db.session import get_db
    from app.main import app

    engine, did, _ = isolated_job
    with Session(engine) as db:
        doc = db.get(Document, did)
        uid, cid = doc.owner_id, doc.course_id

    def dependency():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    monkeypatch.setattr(tasks.ingest_task, "delay", tasks.ingest_document)
    original = tasks.parse_document
    monkeypatch.setattr(
        tasks,
        "parse_document",
        lambda *_: (_ for _ in ()).throw(ValueError("test failure")),
    )
    try:
        client = TestClient(app)
        headers = {"Authorization": "Bearer " + create_token(uid)}
        response = client.post(
            f"/api/v1/courses/{cid}/documents",
            headers=headers,
            data={"document_type": "笔记"},
            files={"file": ("authored.txt", b"authored note", "text/plain")},
        )
        assert response.status_code == 202 and response.json()["status"] == "failed"
        uploaded = response.json()["document_id"]
        monkeypatch.setattr(tasks, "parse_document", original)
        response = client.post(f"/api/v1/documents/{uploaded}/retry", headers=headers)
        assert response.status_code == 202 and response.json()["status"] == "ready"
        with Session(engine) as db:
            current = db.get(IngestionJob, response.json()["job_id"])
            assert current.delivery_attempts == 1 and current.status == "completed"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_lost_confirmation_fencing_and_attempt_limit(isolated_job, monkeypatch):
    engine, did, jid = isolated_job
    recovery.dispatch(jid, lambda _: None)  # accepted, worker not started
    with Session(engine) as db, db.begin():
        db.get(IngestionJob, jid).dispatched_at = datetime.now(UTC) - timedelta(
            minutes=2
        )
    sent = []
    recovery.recover_once(publish=sent.append)
    assert sent == [jid]
    old = recovery.claim(jid)
    assert old and recovery.claim(jid) is None
    assert recovery.renew(jid, old)
    recovery.recover_once(publish=sent.append)
    assert sent == [jid]  # healthy processing is not redelivered
    expire(engine, jid)
    recovery.recover_once(publish=lambda _: None)
    new = recovery.claim(jid)
    assert new != old and not recovery.renew(jid, old)
    assert not tasks.set_stage(jid, "embedding", 50, old)
    monkeypatch.setattr(get_settings(), "ingestion_max_attempts", 2)
    expire(engine, jid)
    recovery.recover_once(publish=lambda _: None)
    with Session(engine) as db:
        assert db.get(Document, did).status == "failed"
    tasks.ingest_document(jid)
    with Session(engine) as db:
        assert not list(db.scalars(select(Chunk)))


def test_deleted_document_cannot_be_recovered(isolated_job):
    engine, did, jid = isolated_job
    token = recovery.claim(jid)
    with Session(engine) as db, db.begin():
        db.delete(db.get(Document, did))
    assert not recovery.renew(jid, token)
    recovery.recover_once(publish=lambda _: pytest.fail("deleted job dispatched"))
    tasks.ingest_document(jid)


@pytest.mark.parametrize("late_error", [False, True])
def test_old_execution_cannot_overwrite_new_success(
    isolated_job, monkeypatch, late_error
):
    engine, did, jid = isolated_job
    calls = 0

    def embed(texts):
        nonlocal calls
        calls += 1
        if calls == 1:
            expire(engine, jid)
            recovery.recover_once(publish=lambda _: None)
            tasks.ingest_document(jid)
            if late_error:
                raise RuntimeError("late failure must not change new result")
        return [[1.0] + [0.0] * 511 for _ in texts]

    monkeypatch.setattr(tasks, "embed_texts", embed)
    tasks.ingest_document(jid)
    with Session(engine) as db:
        assert db.get(Document, did).status == "ready"
        assert db.get(IngestionJob, jid).attempts == 2
        assert len(list(db.scalars(select(Chunk)))) == 1


def test_long_task_heartbeat_prevents_recovery(isolated_job, monkeypatch):
    import time

    engine, did, jid = isolated_job
    monkeypatch.setattr(get_settings(), "ingestion_lease_seconds", 3)

    def embed(texts):
        time.sleep(4)
        recovery.recover_once(publish=lambda _: pytest.fail("healthy task redelivered"))
        return [[1.0] + [0.0] * 511 for _ in texts]

    monkeypatch.setattr(tasks, "embed_texts", embed)
    tasks.ingest_document(jid)
    with Session(engine) as db:
        assert db.get(Document, did).status == "ready"
        assert db.get(IngestionJob, jid).attempts == 1


@pytest.mark.parametrize("phase", ["parsing", "embedding", "before_write"])
def test_real_process_exit_recovers(isolated_job, tmp_path, phase):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    engine, did, jid = isolated_job
    marker = tmp_path / "embedding-started"
    env = {
        **os.environ,
        "DATABASE_URL": str(engine.url),
        "STORAGE_DIR": str(get_settings().storage_dir),
        "LLM_API_KEY": "",
        "TAVILY_API_KEY": "",
        "GITHUB_CREDENTIALS": "{}",
    }
    code = """
import sys, time
from pathlib import Path
from app.workers import tasks
def block(*args):
    Path(sys.argv[2]).write_text('started')
    time.sleep(120)
if sys.argv[3] == 'parsing':
    tasks.parse_document = block
elif sys.argv[3] == 'embedding':
    tasks.embed_texts = block
else:
    tasks.embed_texts = lambda texts: [[1.0] + [0.0] * 511 for _ in texts]
    original = tasks.locked_job
    calls = 0
    def gate(*args):
        global calls
        calls += 1
        if calls == 2:
            block()
        return original(*args)
    tasks.locked_job = gate
tasks.ingest_document(sys.argv[1])
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, jid, str(marker), phase],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while (
            not marker.exists() and child.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert marker.exists(), "isolated worker never reached embedding"
    finally:
        child.kill()
        child.wait(timeout=10)
    with Session(engine) as db:
        assert db.get(Document, did).status == "processing"
    expire(engine, jid)
    recovery.recover_once(publish=tasks.ingest_document)
    with Session(engine) as db:
        assert db.get(Document, did).status == "ready"
        assert db.get(IngestionJob, jid).attempts == 2
        assert len(list(db.scalars(select(Chunk)))) == 1
