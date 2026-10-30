"""Real network API, queue, worker interruption and new Agent policies."""

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, IngestionJob
from app.db.session import get_engine

pytestmark = pytest.mark.skipif(
    os.environ.get("OPT56_LIVE") != "1", reason="explicit opt56 services required"
)


class SafeHeaders(dict):
    def __repr__(self):
        return "{Authorization: [test credential redacted]}"


@pytest.fixture
def account():
    prefix = os.environ["OPT34_CONTAINER_PREFIX"]
    identity = os.environ["OPT34_RUN_ID"]
    container = json.loads(
        subprocess.check_output(["docker", "inspect", prefix + "-pg"], text=True)
    )[0]
    assert container["Config"]["Labels"]["studyrag.opt34"] == identity
    engine = get_engine()
    assert engine.url.host == "127.0.0.1" and engine.url.username == "opt34"
    assert (
        str(engine.url.port)
        == container["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
    )
    with httpx.Client(base_url=os.environ["OPT56_API_URL"], timeout=60) as client:
        assert client.get("/api/v1/validation-identity").json()["run_id"] == identity
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "opt56-" + uuid4().hex + "@example.test",
                "password": "self authored acceptance password",
            },
        )
        assert response.status_code == 201
        headers = SafeHeaders(Authorization="Bearer " + response.json()["access_token"])
        course = client.post(
            "/api/v1/courses", headers=headers, json={"name": "自制验收课程"}
        ).json()["id"]
        yield client, headers, course


def upload(account, name):
    client, headers, course = account
    response = client.post(
        f"/api/v1/courses/{course}/documents",
        headers=headers,
        data={"document_type": "笔记"},
        files={
            "file": (
                name,
                "自制验收规则：有效期17秒，校验失败时不得复用。".encode(),
                "text/plain",
            )
        },
    )
    assert response.status_code == 202
    return response.json()["document_id"]


def wait_ready(document):
    end = time.monotonic() + 60
    while time.monotonic() < end:
        with Session(get_engine()) as db:
            doc = db.get(Document, document)
            assert doc.status != "failed"
            if doc.status == "ready":
                return
        time.sleep(0.2)
    pytest.fail("ingestion did not finish in isolated services")


def test_wire_upload_worker_pgvector_finalize_review_and_sse(account):
    document = upload(account, "opt56-normal-" + uuid4().hex + ".txt")
    wait_ready(document)
    client, headers, course = account
    session = client.post(
        "/api/v1/chat/sessions",
        headers=headers,
        json={"course_id": course, "document_ids": [document]},
    ).json()["id"]
    response = client.post(
        f"/api/v1/chat/sessions/{session}/messages",
        headers=headers,
        json={"content": "严格根据资料说明自制验收规则", "answer_scope": "strict"},
    )
    assert response.status_code == 200
    events = [
        (block.splitlines()[0][7:], json.loads(block.split("data: ", 1)[1]))
        for block in response.text.strip().split("\n\n")
    ]
    assert events[-1][0] == "done"
    history = client.get(
        f"/api/v1/chat/sessions/{session}/messages", headers=headers
    ).json()
    answer = history[-1]
    assert "不得复用" in answer["content"] and len(answer["citations"]) == 1
    assert [c["purpose"] for c in answer["measurements"]["calls"]] == [
        "agent",
        "finalize",
        "review",
    ]
    assert answer["measurements"]["usage"]["state"] == "complete"
    assert (
        len(
            [
                e
                for e in answer["measurements"]["context_events"]
                if e["kind"] == "context_budget"
            ]
        )
        == 3
    )
    assert (
        answer["measurements"]["server_first_body_ms"]
        >= answer["measurements"]["model_first_output_ms"]
    )
    original_file = client.get(f"/api/v1/documents/{document}/file", headers=headers)
    assert (
        original_file.status_code == 200
        and "不得复用" in original_file.content.decode()
    )
    client.delete(f"/api/v1/documents/{document}", headers=headers)
    citation = answer["citations"][0]["citation_id"]
    assert (
        client.get(f"/api/v1/citations/{citation}", headers=headers).status_code == 404
    )


def test_actual_worker_kill_recovery_does_not_duplicate_chunks(account):
    name = "opt56-kill-" + uuid4().hex + ".txt"
    document = upload(account, name)
    marker = Path(os.environ["OPT56_CONTROL"]) / (name + ".paused")
    end = time.monotonic() + 40
    while not marker.exists() and time.monotonic() < end:
        time.sleep(0.2)
    assert marker.exists()
    worker = os.environ["OPT56_WORKER"]
    container = json.loads(
        subprocess.check_output(["docker", "inspect", worker], text=True)
    )[0]
    assert worker == os.environ["OPT34_CONTAINER_PREFIX"] + "-worker"
    assert container["Config"]["Labels"]["studyrag.opt34"] == os.environ["OPT34_RUN_ID"]
    subprocess.run(
        ["docker", "kill", "--signal", "KILL", worker], check=True, capture_output=True
    )
    subprocess.run(["docker", "start", worker], check=True, capture_output=True)
    wait_ready(document)
    with Session(get_engine()) as db:
        job = db.scalar(
            select(IngestionJob).where(IngestionJob.document_id == document)
        )
        assert job.attempts == 2 and job.execution_token is None
        assert (
            len(list(db.scalars(select(Chunk).where(Chunk.document_id == document))))
            == 1
        )
